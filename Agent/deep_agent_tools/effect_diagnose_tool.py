"""Agent 侧因果效应可识别性诊断；只产出判定，不执行估计。"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from Agent.execution_control import JobExecutionRevoked
from pydantic import field_validator

from .identity import provenance_matches_identity
from .models import AlgorithmResult, ContractModel, StandardizedGraph
from .runtime_updates import (
    build_terminal_invocation,
    build_tool_command,
    emit_tool_lifecycle_event,
    resolve_runtime_invocation,
    with_tool_runtime_schema,
)


class _EffectDiagnoseInput(ContractModel):
    graph_result_ref: str
    treatment: str
    outcome: str

    @field_validator("graph_result_ref", "treatment", "outcome")
    @classmethod
    def validate_strings(cls, value: str, info: Any) -> str:
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError(f"{info.field_name} must be a non-blank string")
        return value


def _directed_children(graph: StandardizedGraph) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {node: [] for node in graph.nodes}
    for edge in graph.edges:
        if edge.edge_type == "directed":
            children[edge.source].append(edge.target)
    return children


def _is_acyclic(graph: StandardizedGraph) -> bool:
    children = _directed_children(graph)
    incoming = dict.fromkeys(graph.nodes, 0)
    for targets in children.values():
        for target in targets:
            incoming[target] += 1
    ready = deque(node for node, count in incoming.items() if count == 0)
    visited = 0
    while ready:
        node = ready.popleft()
        visited += 1
        for child in children[node]:
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
    return visited == len(graph.nodes)


def _is_ancestor(graph: StandardizedGraph, ancestor: str, target: str) -> bool:
    children = _directed_children(graph)
    seen: set[str] = set()
    stack = [ancestor]
    while stack:
        node = stack.pop()
        for child in children[node]:
            if child == target:
                return True
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return False


def _treatment_type(trusted: Any, treatment: str) -> str:
    analysis_input = getattr(trusted, "analysis_input", None)
    kinds = (
        getattr(analysis_input, "semantic_kinds", None)
        if analysis_input is not None
        else None
    )
    if isinstance(kinds, Mapping):
        value = kinds.get(treatment)
        if isinstance(value, str) and value:
            return value
    return "unknown"


def _row_count(state: Mapping[str, Any]) -> int | None:
    parameters = state.get("analysis_parameters")
    profile = parameters.get("data_profile") if isinstance(parameters, Mapping) else None
    value = profile.get("row_count") if isinstance(profile, Mapping) else None
    if value is None:
        direct = state.get("data_profile")
        value = (
            direct.get("row_count")
            if isinstance(direct, Mapping)
            else getattr(direct, "row_count", None)
        )
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


@dataclass
class EffectDiagnoseTool:
    name: str = "effect_diagnose"
    description: str = (
        "在本次运行已获得的有效因果图上检查处理变量对结果变量的效应是否可估计。"
        "只做结构诊断并推荐估计器，不执行估计。verdict=proceed 可以调用 "
        "recommended_estimator；verdict=reject 表示该图不适用，应保留 reasons 且不再强制估计；"
        "verdict=error 表示引用、身份或内部校验失败。"
    )

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": _EffectDiagnoseInput.model_json_schema(),
        }

    def _evaluate(
        self,
        *,
        runtime: Any,
        identity: Any,
        graph_result_ref: str,
        treatment: str,
        outcome: str,
    ) -> tuple[dict[str, Any], str, str | None]:
        trusted = identity.runtime_context.trusted_identity
        state = runtime.state if isinstance(runtime.state, Mapping) else {}
        treatment_type = _treatment_type(trusted, treatment)
        n_rows = _row_count(state)
        results = state.get("algorithm_results")

        def payload(verdict: str, checks: dict[str, bool], reasons: list[dict[str, str]]):
            return {
                "verdict": verdict,
                "treatment_type": treatment_type,
                "n_rows": n_rows,
                "checks": checks,
                "reasons": reasons,
                "recommended_estimator": "causal.backdoor_ols",
                "graph_result_ref": graph_result_ref,
                "treatment": treatment,
                "outcome": outcome,
            }

        raw = results.get(graph_result_ref) if isinstance(results, Mapping) else None
        if raw is None:
            return (
                payload("error", {}, [{
                    "code": "graph_result_missing",
                    "message": "所引用的因果图结果不存在。",
                }]),
                "failed",
                "EFFECT_DIAGNOSE_FAILED",
            )
        try:
            result = (
                raw if isinstance(raw, AlgorithmResult)
                else AlgorithmResult.model_validate(raw)
            )
        except Exception:
            return (
                payload("error", {}, [{
                    "code": "graph_result_invalid",
                    "message": "所引用的因果图结果无法解析。",
                }]),
                "failed",
                "EFFECT_DIAGNOSE_FAILED",
            )

        analysis_input = getattr(trusted, "analysis_input", None)
        dataset_id = (
            getattr(analysis_input, "dataset_id", None)
            if analysis_input is not None
            else None
        )
        graph = result.standardized_graph
        if (
            result.status != "valid"
            or graph is None
            or not graph.nodes
            or not provenance_matches_identity(result.provenance, trusted)
            or result.provenance.source_input_identity
            != trusted.source_input_identity()
            or result.provenance.analysis_dataset_id != dataset_id
        ):
            return (
                payload("error", {"reference_valid": False}, [{
                    "code": "reference_not_trusted",
                    "message": "该图结果不是本次运行、当前数据版本的有效结果。",
                }]),
                "failed",
                "EFFECT_DIAGNOSE_FAILED",
            )

        variables_present = (
            treatment != outcome
            and treatment in graph.nodes
            and outcome in graph.nodes
        )
        checks = {
            "reference_valid": True,
            "variables_present": variables_present,
            "directed_only": all(
                edge.edge_type == "directed" for edge in graph.edges
            ),
            "acyclic": _is_acyclic(graph),
            "treatment_is_ancestor": (
                variables_present and _is_ancestor(graph, treatment, outcome)
            ),
        }
        reasons: list[dict[str, str]] = []
        if not checks["variables_present"]:
            reasons.append({
                "code": "variables_not_estimable",
                "message": "处理变量与结果变量必须不同，且都出现在该图中。",
            })
        if not checks["directed_only"]:
            reasons.append({
                "code": "graph_not_directed",
                "message": "图中存在未定向或双箭头边，无法确定调整关系。",
            })
        if not checks["acyclic"]:
            reasons.append({
                "code": "graph_has_cycle",
                "message": "图包含有向环，无法按因果顺序估计效应。",
            })
        if checks["variables_present"] and not checks["treatment_is_ancestor"]:
            reasons.append({
                "code": "treatment_not_ancestor",
                "message": "处理变量不是结果变量的祖先，效应不可识别。",
            })
        if reasons:
            return payload("reject", checks, reasons), "not_applicable", None
        return payload("proceed", checks, []), "succeeded", None

    def to_langchain_tool(self) -> Any:
        try:
            from langchain.tools import ToolRuntime
            from langchain_core.tools import StructuredTool
        except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover
            raise RuntimeError("LangChain is not installed") from exc

        async def call(
            graph_result_ref: str,
            treatment: str,
            outcome: str,
            runtime: Any,
        ) -> Any:
            identity = resolve_runtime_invocation(runtime)
            await identity.runtime_context.ensure_active()
            started_at = datetime.now(timezone.utc)
            await emit_tool_lifecycle_event(
                runtime,
                event_type="tool_call_start",
                identity=identity,
                tool_name=self.name,
            )
            try:
                payload, attempt_status, safe_error_code = self._evaluate(
                    runtime=runtime,
                    identity=identity,
                    graph_result_ref=graph_result_ref,
                    treatment=treatment,
                    outcome=outcome,
                )
                await identity.runtime_context.ensure_active()
            except (asyncio.CancelledError, JobExecutionRevoked):
                await emit_tool_lifecycle_event(
                    runtime,
                    event_type="tool_call_result",
                    identity=identity,
                    tool_name=self.name,
                    status="canceled",
                )
                raise
            except Exception:
                payload = {
                    "verdict": "error",
                    "treatment_type": "unknown",
                    "n_rows": None,
                    "checks": {},
                    "reasons": [{
                        "code": "diagnose_internal_error",
                        "message": "诊断无法完成。",
                    }],
                    "recommended_estimator": "causal.backdoor_ols",
                    "graph_result_ref": graph_result_ref,
                    "treatment": treatment,
                    "outcome": outcome,
                }
                attempt_status, safe_error_code = "failed", "EFFECT_DIAGNOSE_FAILED"
            await emit_tool_lifecycle_event(
                runtime,
                event_type="tool_call_result",
                identity=identity,
                tool_name=self.name,
                status=attempt_status,
                safe_error_code=safe_error_code,
            )
            terminal = build_terminal_invocation(
                identity=identity,
                tool_name=self.name,
                attempt_status=attempt_status,
                started_at=started_at,
                subject_ref=graph_result_ref,
                safe_error_code=safe_error_code,
            )
            return build_tool_command(
                identity=identity,
                tool_name=self.name,
                payload=payload,
                ledger_record=terminal,
            )

        call.__annotations__["runtime"] = ToolRuntime
        runtime_args_schema = with_tool_runtime_schema(
            _EffectDiagnoseInput,
            tool_name=self.name,
            tool_runtime_type=ToolRuntime,
        )
        return StructuredTool.from_function(
            coroutine=call,
            name=self.name,
            description=self.description,
            args_schema=runtime_args_schema,
        )


def build_default_effect_diagnose_tool() -> EffectDiagnoseTool:
    return EffectDiagnoseTool()
