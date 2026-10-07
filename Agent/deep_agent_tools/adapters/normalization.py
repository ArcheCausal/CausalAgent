"""把现有三个 runner 的返回值转换为统一 StandardizedGraph。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..error_codes import SafeErrorCode
from ..models import (
    AlgorithmExecutionCommand,
    AlgorithmResult,
    AlgorithmResultProvenance,
    Diagnostics,
    GraphEdge,
    McpInvocationContext,
    StandardizedGraph,
    build_diagnostics_from_runner_payload,
)


class RunnerContractError(ValueError):
    """runner 输出缺字段、节点引用无效或方向语义无法解释。"""


def _nodes_and_edges(payload: Mapping[str, Any]) -> tuple[list[str], list[GraphEdge]]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise RunnerContractError("runner result data is missing")
    raw_nodes = data.get("nodes")
    raw_edges = data.get("edges")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise RunnerContractError("runner result nodes/edges are missing")

    nodes: list[str] = []
    for raw_node in raw_nodes:
        node = raw_node.get("id") if isinstance(raw_node, Mapping) else raw_node
        if not isinstance(node, str) or not node.strip():
            raise RunnerContractError("runner returned an invalid node")
        if node in nodes:
            raise RunnerContractError("runner returned duplicate nodes")
        nodes.append(node)

    edges: list[GraphEdge] = []
    for raw_edge in raw_edges:
        if not isinstance(raw_edge, Mapping):
            raise RunnerContractError("runner returned an invalid edge")
        source = raw_edge.get("from", raw_edge.get("source"))
        target = raw_edge.get("to", raw_edge.get("target"))
        if not isinstance(source, str) or not isinstance(target, str):
            raise RunnerContractError("runner edge endpoints are missing")
        arrows = str(raw_edge.get("arrows") or "")
        dashes = bool(raw_edge.get("dashes"))
        if not arrows:
            edge_type = "undirected" if dashes or raw_edge.get("label") == "无向" else "partially_directed"
        elif arrows == "to":
            edge_type = "directed"
        elif arrows == "from":
            source, target = target, source
            edge_type = "directed"
        elif set(arrows.split(",")) == {"to", "from"}:
            edge_type = "bidirected"
        else:
            edge_type = "partially_directed"
        weight = raw_edge.get("weight")
        if weight is None:
            # 现有 OLC runner 将系数放在可视化 label 中；只接受完整数值
            # label，不能把“无向”或 causal-learn 的边描述误当成权重。
            label = raw_edge.get("label")
            if isinstance(label, (int, float)) and not isinstance(label, bool):
                weight = label
            elif isinstance(label, str) and label.strip():
                try:
                    weight = float(label.strip())
                except ValueError:
                    weight = None
        try:
            normalized_weight = None if weight is None else float(weight)
        except (TypeError, ValueError) as exc:
            raise RunnerContractError("runner edge weight is invalid") from exc
        if source not in nodes or target not in nodes:
            raise RunnerContractError("runner edge references an unknown node")
        edges.append(
            GraphEdge(
                source=source,
                target=target,
                edge_type=edge_type,
                weight=normalized_weight,
            )
        )
    return nodes, edges


def standardize_runner_graph(
    payload: Mapping[str, Any], *, capability_id: str
) -> StandardizedGraph:
    if payload.get("success") is not True:
        raise RunnerContractError("runner did not return success")
    nodes, edges = _nodes_and_edges(payload)
    semantics = {
        "causal.pc": "pdag",
        "causal.olc": "latent_mixed_graph",
        "causal.direct_lingam": "dag_target_to_source",
        "causal.cdfm": "directed_graph",
    }.get(capability_id, "algorithm_graph")
    return StandardizedGraph(graph_semantics=semantics, nodes=nodes, edges=edges)


def result_from_runner_payload(
    payload: Mapping[str, Any],
    *,
    command: AlgorithmExecutionCommand,
    context: McpInvocationContext,
    capability_version: str,
    runner_version: str | None = None,
) -> AlgorithmResult:
    """把旧 runner 的真实返回值转为 P1 AlgorithmResult。

    失败映射只使用错误类型类别，不把 runner 原始 message 传播到模型或公共
    事件。原始 payload 由上层 Adapter 负责写入受控虚拟文件。
    """

    if payload.get("success") is not True:
        error_type = str(payload.get("error_type") or "AlgorithmExecutionError")
        if error_type == "InputValidationError":
            status = "invalid_input"
            code = SafeErrorCode.ALGORITHM_INPUT_INVALID
        elif error_type == "DependencyUnavailableError":
            status = "not_ready"
            code = SafeErrorCode.ALGORITHM_NOT_READY
        elif error_type == "TimeoutError":
            status = "timed_out"
            code = SafeErrorCode.ALGORITHM_TIMED_OUT
        else:
            status = "execution_failed"
            code = SafeErrorCode.ALGORITHM_EXECUTION_FAILED
        return AlgorithmResult(
            result_ref=f"{command.invocation_id}:{command.result_index}",
            invocation_id=command.invocation_id,
            provider_call_id=command.provider_call_id,
            capability_id=command.capability_id,
            capability_version=capability_version,
            status=status,
            diagnostics=Diagnostics(safe_error_code=code),
            provenance=AlgorithmResultProvenance(
                job_id=context.job_id,
                attempt_count=context.attempt_count,
                lease_epoch=context.lease_epoch,
                input_identity=command.input_identity,
                spec_digest=command.spec_digest,
                invocation_id=command.invocation_id,
                capability_id=command.capability_id,
                capability_version=capability_version,
                algorithm_runner_version=runner_version,
            ),
        )

    try:
        graph = standardize_runner_graph(payload, capability_id=command.capability_id)
    except RunnerContractError:
        return AlgorithmResult(
            result_ref=f"{command.invocation_id}:{command.result_index}",
            invocation_id=command.invocation_id,
            provider_call_id=command.provider_call_id,
            capability_id=command.capability_id,
            capability_version=capability_version,
            status="execution_failed",
            diagnostics=Diagnostics(
                safe_error_code=SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID
            ),
            provenance=AlgorithmResultProvenance(
                job_id=context.job_id,
                attempt_count=context.attempt_count,
                lease_epoch=context.lease_epoch,
                input_identity=command.input_identity,
                spec_digest=command.spec_digest,
                invocation_id=command.invocation_id,
                capability_id=command.capability_id,
                capability_version=capability_version,
                algorithm_runner_version=runner_version,
            ),
        )

    return AlgorithmResult(
        result_ref=f"{command.invocation_id}:{command.result_index}",
        invocation_id=command.invocation_id,
        provider_call_id=command.provider_call_id,
        capability_id=command.capability_id,
        capability_version=capability_version,
        status="valid",
        standardized_graph=graph,
        diagnostics=build_diagnostics_from_runner_payload(payload),
        provenance=AlgorithmResultProvenance(
            job_id=context.job_id,
            attempt_count=context.attempt_count,
            lease_epoch=context.lease_epoch,
            input_identity=command.input_identity,
            spec_digest=command.spec_digest,
            invocation_id=command.invocation_id,
            capability_id=command.capability_id,
            capability_version=capability_version,
            algorithm_runner_version=runner_version,
        ),
    )
