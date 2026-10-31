"""结构化终态的静态校验和一次性修正预算。"""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Any

from Agent.deep_agent_tools.identity import build_invocation_id, current_input_identity
from Agent.deep_agent_tools.algorithm_specs import get_algorithm_spec
from Agent.deep_agent_tools.functional_form import estimation_pair
from Agent.deep_agent_tools.graph_validation import validate_graph_structure
from Agent.deep_agent_tools.runtime_updates import normalize_tool_status
from Agent.deep_agent_tools.models import (
    AlgorithmResult,
    FinalAnalysisDecision,
    InvocationRecord,
    validate_result_ref_for_invocation,
)
from Agent.deep_agent.prompts import ANALYSIS_ROUTE


SCIENTIFIC_LIMITATIONS = {
    "effect_question_without_diagnose": "效应可估计性诊断未完成；候选结构不能替代效应识别。",
    "effect_question_without_estimation": "效应诊断已完成，但估计未实际执行；当前仅展示候选结构。",
    "analysis_route_without_web_invocation": "本次已开启联网检索，但未实际执行；分析缺少该通道的外部证据核对。",
}


class StructuredResponseError(ValueError):
    """模型没有提交可验证的 ``structured_response``。

    ``rule`` 是稳定的失败规则标识。只有模型可以自行修正的失败才带 rule，
    身份、账本或状态一致性问题保持为空，避免把内部完整性问题包装成
    可以把修正责任交还给模型的指令。
    """

    def __init__(self, message: str, *, rule: str | None = None) -> None:
        super().__init__(message)
        self.rule = rule


def validate_structured_response(value: Any) -> FinalAnalysisDecision:
    """只接受 ToolStrategy 产出的对象或等价 JSON mapping。"""

    if isinstance(value, FinalAnalysisDecision):
        return value.model_copy(deep=True)
    if isinstance(value, Mapping):
        try:
            return FinalAnalysisDecision.model_validate(value)
        except Exception as exc:  # Pydantic 的细节不能透传到公共输出
            raise StructuredResponseError(
                "structured_response schema validation failed",
                rule="decision_schema_invalid",
            ) from exc
    raise StructuredResponseError(
        "structured_response is required",
        rule="decision_missing",
    )


def validate_decision_references(
    decision: FinalAnalysisDecision,
    *,
    algorithm_result_refs: Set[str],
    evidence_refs: Set[str] = frozenset(),
) -> FinalAnalysisDecision:
    """验证静态 schema 之外的引用闭包，不做科学判断。"""

    result_refs = set(algorithm_result_refs)
    assessment_refs = {item.result_ref for item in decision.result_assessments}
    if not assessment_refs.issubset(result_refs):
        raise StructuredResponseError(
            "decision references an unknown algorithm result",
            rule="assessment_ref_unknown_result",
        )
    if decision.primary_result_ref and decision.primary_result_ref not in result_refs:
        raise StructuredResponseError(
            "primary_result_ref is not an available result",
            rule="primary_ref_unknown_result",
        )
    for ref in (decision.primary_graph_result_ref, decision.primary_effect_result_ref):
        if ref is not None and ref not in result_refs:
            raise StructuredResponseError("primary reference is unavailable", rule="primary_ref_unknown_result")
    for proposal in decision.revision_proposals:
        if proposal.result_ref not in result_refs:
            raise StructuredResponseError(
                "revision proposal references an unknown result",
                rule="proposal_ref_unknown_result",
            )
        if not set(proposal.evidence_refs).issubset(evidence_refs):
            raise StructuredResponseError(
                "revision proposal references unknown evidence",
                rule="proposal_evidence_ref_unknown",
            )
    for conflict in decision.conflicts:
        if not set(conflict.result_refs).issubset(result_refs):
            raise StructuredResponseError(
                "conflict references an unknown result",
                rule="conflict_ref_unknown_result",
            )
    return decision.model_copy(deep=True)


class FinalizationGate:
    """只用程序事实校验 Deep Agent 的最终决策。

    Gate 不调用模型，也不修改结果、Ledger 或 graph。它只接受当前可信
    ``job/attempt/lease`` 的结果，并把 decision 中的动态引用与完整算法
    Action Ledger 对账；因此 stale worker 的结果不能通过最终报告入口。
    """

    def __init__(self, *, registry: Any | None = None) -> None:
        self.registry = registry
        self._algorithm_tool_names = frozenset(
            entry.spec.tool_name
            for entry in getattr(registry, "entries", ())
        )
        self._estimation_tool_names = frozenset(
            entry.spec.tool_name
            for entry in getattr(registry, "entries", ())
            if getattr(entry.spec, "kind", None) == "causal_estimation"
        ) or frozenset({"causal_backdoor_ols", "causal_g_formula"})

    @staticmethod
    def _identity_value(identity: Any, name: str) -> Any:
        value = getattr(identity, name, None)
        if value is None:
            raise StructuredResponseError("trusted job identity is incomplete")
        return value

    @staticmethod
    def _current_input_identity(identity: Any) -> str:
        """读取当前有效的冻结输入摘要，兼容可刷新的 invocation 级引用。"""
        value = current_input_identity(identity)
        if not value:
            raise StructuredResponseError("trusted job identity is incomplete")
        return value

    @staticmethod
    def _source_input_identity(identity: Any) -> str:
        resolver = getattr(identity, "source_input_identity", None)
        value = resolver() if callable(resolver) else None
        if not isinstance(value, str) or not value:
            raise StructuredResponseError("trusted source input identity is incomplete")
        return value

    @staticmethod
    def _analysis_dataset_id(identity: Any) -> int:
        analysis_input = getattr(identity, "analysis_input", None)
        value = getattr(analysis_input, "dataset_id", None)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise StructuredResponseError("trusted analysis dataset identity is incomplete")
        return value

    @staticmethod
    def _coerce_results(value: Mapping[str, Any] | None) -> dict[str, AlgorithmResult]:
        results: dict[str, AlgorithmResult] = {}
        for key, raw_result in dict(value or {}).items():
            try:
                result = AlgorithmResult.model_validate(raw_result)
            except Exception as exc:
                raise StructuredResponseError("algorithm result state is invalid") from exc
            if key != result.result_ref:
                raise StructuredResponseError("algorithm result map key is invalid")
            results[key] = result
        return results

    @staticmethod
    def _coerce_ledger(value: Mapping[str, Any] | None) -> dict[str, InvocationRecord]:
        ledger: dict[str, InvocationRecord] = {}
        for key, raw_record in dict(value or {}).items():
            try:
                record = InvocationRecord.model_validate(raw_record)
            except Exception as exc:
                raise StructuredResponseError("action ledger state is invalid") from exc
            if key != record.invocation_id:
                raise StructuredResponseError("action ledger map key is invalid")
            ledger[key] = record
        return ledger

    @staticmethod
    def _expected_result_final_status(status: str) -> str:
        return normalize_tool_status(status)

    def _is_algorithm_record(self, record: InvocationRecord) -> bool:
        if self._algorithm_tool_names:
            return record.tool_name in self._algorithm_tool_names
        return record.tool_name.startswith("causal_")

    def _current_ledger(
        self,
        ledger: Mapping[str, InvocationRecord],
        *,
        job_id: str,
        source_input_identity: str,
        analysis_dataset_id: int,
    ) -> dict[str, InvocationRecord]:
        current: dict[str, InvocationRecord] = {}
        for record in ledger.values():
            try:
                expected_id = build_invocation_id(
                    job_id=job_id,
                    response_identity=record.response_identity,
                    provider_call_id=record.provider_call_id,
                )
            except (TypeError, ValueError) as exc:
                raise StructuredResponseError("action ledger identity is invalid") from exc
            if expected_id != record.invocation_id:
                if record.job_id == job_id:
                    # 当前 Job 上的记录却无法由其 response/provider identity
                    # 重建出 invocation_id，不能把它当作普通 stale 历史吞掉。
                    raise StructuredResponseError("action ledger identity is invalid")
                # 这不是一个当前 Job 的调用；stale/foreign 记录仍可保留在
                # checkpoint，但不得被当前 decision 引用或计入 no_valid。
                continue
            if (
                record.job_id is None
                or record.attempt_count is None
                or record.lease_epoch is None
                or record.worker_id is None
                or record.input_identity is None
            ):
                raise StructuredResponseError(
                    "action ledger ownership is incomplete"
                )
            if record.job_id != job_id:
                # invocation_id 已经指向当前 Job，却声明了另一个 Job 的所有权，
                # 这是跨 Job 污染而不是可忽略的 stale 记录。
                raise StructuredResponseError("action ledger Job ownership mismatch")
            if not self._is_algorithm_record(record):
                continue
            if (
                record.source_input_identity != source_input_identity
                or record.analysis_dataset_id != analysis_dataset_id
            ):
                raise StructuredResponseError(
                    "action ledger analysis dataset identity mismatch"
                )
            if record.final_status == "pending" or not record.attempts:
                raise StructuredResponseError("algorithm action ledger is not terminal")
            current[record.invocation_id] = record
        return current

    def _has_current_terminal_tool_invocation(
        self,
        ledger: Mapping[str, InvocationRecord],
        *,
        tool_name: str,
        job_id: str,
        attempt_count: int,
        lease_epoch: int,
        worker_id: str,
        input_identity: str,
        terminal_statuses: Set[str] | None = None,
    ) -> bool:
        """检查指定工具是否在当前 Job attempt 中完成过一次真实调用。"""

        accepted_statuses = terminal_statuses or {
            "succeeded", "not_ready", "failed", "timed_out",
        }
        for record in ledger.values():
            if record.tool_name != tool_name:
                continue
            try:
                expected_id = build_invocation_id(
                    job_id=job_id,
                    response_identity=record.response_identity,
                    provider_call_id=record.provider_call_id,
                )
            except (TypeError, ValueError) as exc:
                raise StructuredResponseError("action ledger identity is invalid") from exc
            if expected_id != record.invocation_id:
                if record.job_id == job_id:
                    raise StructuredResponseError("action ledger identity is invalid")
                continue
            if (
                record.job_id is None
                or record.attempt_count is None
                or record.lease_epoch is None
                or record.worker_id is None
                or record.input_identity is None
            ):
                raise StructuredResponseError("action ledger ownership is incomplete")
            if record.job_id != job_id:
                raise StructuredResponseError("action ledger Job ownership mismatch")
            if (
                record.attempt_count != attempt_count
                or record.lease_epoch != lease_epoch
                or record.worker_id != worker_id
                or record.input_identity != input_identity
            ):
                continue
            if record.final_status == "pending" or not record.attempts:
                raise StructuredResponseError("evidence action ledger is not terminal")
            latest_attempt = max(
                record.attempts.values(),
                key=lambda attempt: (attempt.retry_ordinal, attempt.revision),
            )
            if latest_attempt.status != record.final_status:
                raise StructuredResponseError(
                    "evidence action ledger terminal status mismatch"
                )
            if record.final_status in accepted_statuses:
                return True
        return False

    def _current_diagnose_records(
        self,
        ledger: Mapping[str, InvocationRecord],
        *,
        job_id: str,
        attempt_count: int,
        lease_epoch: int,
        worker_id: str,
        input_identity: str,
        source_input_identity: str,
        analysis_dataset_id: int,
        research_identity: str,
    ) -> list[InvocationRecord]:
        """诊断记录不是算法记录，必须直接扫描完整 ledger。"""

        records: list[InvocationRecord] = []
        for record in ledger.values():
            if record.tool_name != "effect_diagnose":
                continue
            try:
                expected_id = build_invocation_id(
                    job_id=job_id,
                    response_identity=record.response_identity,
                    provider_call_id=record.provider_call_id,
                )
            except (TypeError, ValueError) as exc:
                raise StructuredResponseError("action ledger identity is invalid") from exc
            if expected_id != record.invocation_id:
                if record.job_id == job_id:
                    raise StructuredResponseError("action ledger identity is invalid")
                continue
            if record.job_id != job_id:
                raise StructuredResponseError("action ledger Job ownership mismatch")
            if (
                record.attempt_count != attempt_count
                or record.lease_epoch != lease_epoch
                or record.worker_id != worker_id
                or record.input_identity != input_identity
                or record.source_input_identity != source_input_identity
                or record.analysis_dataset_id != analysis_dataset_id
            ):
                continue
            if record.research_identity != research_identity:
                raise StructuredResponseError("diagnosis research identity differs from confirmed parameters")
            if record.final_status == "pending" or not record.attempts:
                raise StructuredResponseError("diagnose action ledger is not terminal")
            latest = max(
                record.attempts.values(),
                key=lambda attempt: (attempt.retry_ordinal, attempt.revision),
            )
            if latest.status != record.final_status:
                raise StructuredResponseError(
                    "diagnose action ledger terminal status mismatch"
                )
            records.append(record)
        return records

    def _has_current_estimation_for_pair(
        self,
        ledger: Mapping[str, InvocationRecord],
        *,
        pair: tuple[str, str] | None,
        diagnosed_graphs: Set[str],
        attempt_count: int,
        lease_epoch: int,
        worker_id: str,
        input_identity: str,
        effect_request: Any,
    ) -> bool:
        """只有当前查询、且源图已被诊断的估计调用才算一次真实尝试。"""

        if pair is None:
            return False
        treatment, outcome = pair
        for record in ledger.values():
            query = record.effect_query
            if (
                record.tool_name not in self._estimation_tool_names
                or query is None
                or (query.treatment, query.outcome) != (treatment, outcome)
                or query.graph_result_ref not in diagnosed_graphs
                or query.effect_request != effect_request
            ):
                continue
            if (
                record.attempt_count != attempt_count
                or record.lease_epoch != lease_epoch
                or record.worker_id != worker_id
                or record.input_identity != input_identity
            ):
                continue
            if record.final_status in {
                "succeeded", "not_ready", "not_applicable", "failed", "timed_out",
            }:
                return True
        return False

    def _validate_result_ledger_pair(
        self,
        result: AlgorithmResult,
        record: InvocationRecord,
        *,
        identity: Any,
    ) -> None:
        try:
            validate_result_ref_for_invocation(
                result.result_ref,
                result.invocation_id,
            )
        except ValueError as exc:
            raise StructuredResponseError("algorithm result reference is invalid") from exc
        if record.provider_call_id != result.provider_call_id:
            raise StructuredResponseError("result and ledger provider call IDs differ")
        if record.tool_name != self._tool_name_for_result(result):
            raise StructuredResponseError("result and ledger tool names differ")
        expected_id = build_invocation_id(
            job_id=identity.job_id,
            response_identity=record.response_identity,
            provider_call_id=record.provider_call_id,
        )
        if expected_id != result.invocation_id or record.invocation_id != result.invocation_id:
            raise StructuredResponseError("result invocation identity is invalid")
        if record.result_ref not in (None, result.result_ref):
            raise StructuredResponseError("result and ledger result references differ")
        expected_status = self._expected_result_final_status(result.status)
        if record.final_status != expected_status:
            raise StructuredResponseError("result and ledger terminal status differ")
        provenance = result.provenance
        research_identity = getattr(getattr(identity, "analysis_input", None), "research_identity", None)
        if not research_identity or provenance.research_identity != research_identity or record.research_identity != research_identity:
            raise StructuredResponseError("result research identity differs from confirmed parameters")
        if provenance.job_id != str(identity.job_id):
            raise StructuredResponseError("result does not belong to the trusted Job")
        if provenance.attempt_count != int(identity.attempt_count):
            raise StructuredResponseError("result attempt does not match the trusted Job")
        if provenance.lease_epoch != int(identity.lease_epoch):
            raise StructuredResponseError("result lease does not match the trusted Job")
        if provenance.input_identity != self._current_input_identity(identity):
            raise StructuredResponseError("result input identity does not match the Job")
        if provenance.source_input_identity != self._source_input_identity(identity):
            raise StructuredResponseError("result source input identity does not match the Job")
        if provenance.analysis_dataset_id != self._analysis_dataset_id(identity):
            raise StructuredResponseError("result analysis dataset does not match the Job")
        if record.source_input_identity != provenance.source_input_identity:
            raise StructuredResponseError("result and ledger source input identities differ")
        if record.analysis_dataset_id != provenance.analysis_dataset_id:
            raise StructuredResponseError("result and ledger analysis datasets differ")
        latest_attempt = max(
            record.attempts.values(),
            key=lambda attempt: (attempt.retry_ordinal, attempt.revision),
        )
        expected_attempt_status = {
            "succeeded": "succeeded",
            "not_ready": "not_ready",
            "timed_out": "timed_out",
            "not_applicable": "not_applicable",
            "skipped": "skipped",
            "failed": "failed",
        }[expected_status]
        if latest_attempt.status != expected_attempt_status:
            raise StructuredResponseError("result and ledger attempt status differ")

    def _tool_name_for_result(self, result: AlgorithmResult) -> str:
        if self.registry is None:
            return get_algorithm_spec(result.capability_id).tool_name
        try:
            entry = self.registry.get(result.capability_id)
        except Exception as exc:
            raise StructuredResponseError("result capability is not registered") from exc
        if entry.spec.version != result.capability_version:
            raise StructuredResponseError("result capability version is not registered")
        if result.provenance.spec_digest != entry.spec.spec_digest:
            raise StructuredResponseError("result spec digest is not registered")
        return entry.spec.tool_name

    def validate(
        self,
        *,
        decision: Any,
        algorithm_results: Mapping[str, Any] | None,
        action_ledger: Mapping[str, Any] | None,
        trusted_identity: Any,
        rag_evidence: Mapping[str, Any] | None = None,
        web_evidence: Mapping[str, Any] | None = None,
        route_decision: str | None = None,
        web_search_enabled: bool | None = None,
        analysis_parameters: Mapping[str, Any] | None = None,
        analysis_question: str | None = None,
        scientific_limitations: list[dict[str, str]] | None = None,
    ) -> FinalAnalysisDecision:
        """校验并返回可安全交给 report 的 decision 副本。"""

        normalized_decision = validate_structured_response(decision)

        def incomplete_check(message: str, *, rule: str) -> None:
            # 未传收集器的独立严格校验入口保留原有合同；报告入口显式收集缺口。
            if scientific_limitations is None:
                raise StructuredResponseError(message, rule=rule)
            scientific_limitations.append({"code": rule, "message": SCIENTIFIC_LIMITATIONS[rule]})

        results = self._coerce_results(algorithm_results)
        ledger = self._coerce_ledger(action_ledger)
        job_id = str(self._identity_value(trusted_identity, "job_id"))
        current_attempt = int(self._identity_value(trusted_identity, "attempt_count"))
        current_lease = int(self._identity_value(trusted_identity, "lease_epoch"))
        current_worker = str(self._identity_value(trusted_identity, "worker_id"))
        current_input = self._current_input_identity(trusted_identity)
        current_source = self._source_input_identity(trusted_identity)
        current_dataset_id = self._analysis_dataset_id(trusted_identity)
        current_ledger = self._current_ledger(
            ledger,
            job_id=job_id,
            source_input_identity=current_source,
            analysis_dataset_id=current_dataset_id,
        )
        current_ledger = {
            key: record
            for key, record in current_ledger.items()
            if record.attempt_count == current_attempt
            and record.lease_epoch == current_lease
            and record.worker_id == current_worker
            and record.input_identity == current_input
            and record.source_input_identity == current_source
            and record.analysis_dataset_id == current_dataset_id
        }

        eligible: dict[str, AlgorithmResult] = {}
        for result in results.values():
            provenance = result.provenance
            if (
                provenance.job_id == job_id
                and provenance.attempt_count
                == int(self._identity_value(trusted_identity, "attempt_count"))
                and provenance.lease_epoch
                == int(self._identity_value(trusted_identity, "lease_epoch"))
            ):
                eligible[result.result_ref] = result

        for result in eligible.values():
            record = current_ledger.get(result.invocation_id)
            if record is None:
                raise StructuredResponseError("eligible result has no matching ledger record")
            self._validate_result_ledger_pair(
                result,
                record,
                identity=trusted_identity,
            )

        # 不能只从结果查账本；执行前已登记但结果丢失的调用也必须拒绝。
        result_by_invocation = {result.invocation_id: result for result in eligible.values()}
        for invocation_id, record in current_ledger.items():
            latest = max(record.attempts.values(), key=lambda item: (item.retry_ordinal, item.revision))
            if latest.status in {"queued", "running"} or latest.status != record.final_status:
                raise StructuredResponseError("algorithm action ledger is not terminal")
            if invocation_id not in result_by_invocation:
                raise StructuredResponseError("registered algorithm invocation has no terminal result")

        evidence_refs = {
            *dict(rag_evidence or {}).keys(),
            *dict(web_evidence or {}).keys(),
        }
        normalized_decision = validate_decision_references(
            normalized_decision,
            algorithm_result_refs=set(eligible),
            evidence_refs=evidence_refs,
        )

        valid_refs: set[str] = set()
        for result in eligible.values():
            if result.status != "valid" or result.standardized_graph is None:
                continue
            spec = get_algorithm_spec(result.capability_id)
            # 估计结果携带的是入参源图，图语义属于源算法，只能按图自身语义校验；
            # 估计 capability 的 graph_semantics 描述的是输入要求，不是输出契约。
            semantics = (
                result.standardized_graph.graph_semantics
                if spec.kind == "causal_estimation"
                else spec.graph_semantics
            )
            if validate_graph_structure(
                result.standardized_graph, graph_semantics=semantics
            ).valid:
                valid_refs.add(result.result_ref)
        # 预检查拒绝、上下文变化或用户选择诊断都可以是有依据的“不适用”；
        # 这类结果视为已结束，不再要求模型制造一次必然失败的算法执行。
        non_viable_refs = {
            result.result_ref
            for result in eligible.values()
            if result.status in {"not_applicable", "skipped"}
        }
        assessment_by_ref = {
            assessment.result_ref: assessment
            for assessment in normalized_decision.result_assessments
        }
        assessment_required = {result.result_ref for result in eligible.values() if result.status == "valid"}
        if not assessment_required.issubset(assessment_by_ref):
            raise StructuredResponseError(
                "each eligible valid result needs one assessment",
                rule="valid_result_without_assessment",
            )
        for assessment in normalized_decision.result_assessments:
            result = eligible[assessment.result_ref]
            if result.status == "valid" and result.result_ref not in valid_refs and assessment.disposition != "discarded":
                raise StructuredResponseError("graph structure violates the method contract", rule="primary_graph_invalid")
            if (
                result.status not in {"valid", "not_applicable", "skipped"}
                and assessment.disposition != "discarded"
            ):
                raise StructuredResponseError(
                    "non-valid results may only be discarded",
                    rule="non_valid_result_not_discarded",
                )
            if (
                result.status in {"not_applicable", "skipped"}
                and assessment.disposition in {"primary_graph", "primary_effect"}
            ):
                raise StructuredResponseError(
                    "a non-viable result cannot be the primary result",
                    rule="primary_result_not_valid",
                )

        if normalized_decision.outcome == "algorithm_supported":
            primary = normalized_decision.primary_result_ref
            if primary not in valid_refs:
                raise StructuredResponseError(
                    "primary result must be an eligible valid result",
                    rule="primary_result_not_valid",
                )
        elif normalized_decision.outcome == "evidence_only":
            if any(
                assessment_by_ref[ref].disposition != "discarded"
                for ref in valid_refs
            ):
                raise StructuredResponseError(
                    "evidence_only must discard every eligible valid result",
                    rule="evidence_only_with_valid_result",
                )
        elif normalized_decision.outcome == "no_valid_algorithm":
            if valid_refs:
                raise StructuredResponseError(
                    "no_valid_algorithm has an eligible valid result",
                    rule="no_valid_algorithm_with_valid_result",
                )
            if not current_ledger and not non_viable_refs:
                raise StructuredResponseError(
                    "no_valid_algorithm requires a real algorithm invocation",
                    rule="no_valid_algorithm_without_invocation",
                )

        # 分析路由下不允许“一个算法都没跑”就交证据型结论：这类报告没有主图，
        # 也不是用户要的结果。算法真的返回未就绪或失败时会留下算法结果，不受影响。
        if (
            route_decision == ANALYSIS_ROUTE
            and not results
            and not non_viable_refs
            and normalized_decision.outcome
            in {"evidence_only", "no_valid_algorithm"}
        ):
            raise StructuredResponseError(
                "analysis route requires at least one algorithm result",
                rule="analysis_route_without_algorithm_result",
            )

        # 用户明确要求估计效应时不替模型选算法，只要求先留下诊断判定，
        # 再按诊断结论决定是否必须有一次真实估计调用。
        current_diagnoses = self._current_diagnose_records(
            ledger,
            job_id=job_id,
            attempt_count=current_attempt,
            lease_epoch=current_lease,
            worker_id=current_worker,
            input_identity=current_input,
            source_input_identity=current_source,
            analysis_dataset_id=current_dataset_id,
            research_identity=getattr(trusted_identity.analysis_input, "research_identity", None),
        )
        pair = estimation_pair(analysis_parameters)
        effect_request = (analysis_parameters or {}).get("effect_request")
        current_pair_diagnoses = [
            record for record in current_diagnoses
            if _effect_query_matches(record, pair, effect_request)
        ]
        diagnose_statuses = {
            normalize_tool_status(record.final_status)
            for record in current_pair_diagnoses
        }
        effect_question = (
            route_decision == ANALYSIS_ROUTE
            and bool(valid_refs)
            and _requires_effect_estimation(analysis_parameters)
        )
        if effect_question and not diagnose_statuses & {"succeeded", "not_applicable"}:
            incomplete_check(
                "effect question requires an effect_diagnose decision",
                rule="effect_question_without_diagnose",
            )
        if effect_question and "succeeded" in diagnose_statuses and not (
            self._has_current_estimation_for_pair(
                current_ledger,
                pair=pair,
                diagnosed_graphs={
                    record.subject_ref
                    for record in current_pair_diagnoses
                    if normalize_tool_status(record.final_status) == "succeeded"
                },
                attempt_count=current_attempt,
                lease_epoch=current_lease,
                worker_id=current_worker,
                input_identity=current_input,
                effect_request=effect_request,
            )
        ):
            incomplete_check(
                "successful diagnosis requires a real estimation attempt",
                rule="effect_question_without_estimation",
            )

        estimation_refs = {
            result.result_ref
            for result in eligible.values()
            if result.status == "valid"
            and get_algorithm_spec(result.capability_id).kind == "causal_estimation"
        }
        # 科学方法由 Agent 选择；程序验证定义、来源和已知适用性。
        graph_ref = normalized_decision.primary_graph_result_ref
        effect_ref = normalized_decision.primary_effect_result_ref
        if graph_ref is not None:
            graph_result = eligible[graph_ref]
            if graph_ref not in valid_refs or get_algorithm_spec(graph_result.capability_id).kind != "causal_discovery":
                raise StructuredResponseError("primary graph must be a valid discovery result", rule="primary_graph_invalid")
        requested = isinstance(analysis_parameters, Mapping) and isinstance(analysis_parameters.get("effect_request"), Mapping)
        if effect_ref is not None:
            if not requested:
                raise StructuredResponseError("effect estimation was not requested", rule="effect_not_requested")
            if effect_ref not in estimation_refs:
                raise StructuredResponseError("primary effect must be a valid estimation", rule="primary_result_not_valid")
            result = eligible[effect_ref]
            effect = result.causal_effect
            record = current_ledger[result.invocation_id]
            query = record.effect_query
            if effect is None or query is None or effect.effect_request != analysis_parameters["effect_request"] or query.effect_request != effect.effect_request:
                raise StructuredResponseError("effect definition differs from confirmed request", rule="effect_definition_mismatch")
            if (effect.treatment, effect.outcome) != pair or (query.treatment, query.outcome) != pair:
                raise StructuredResponseError("effect roles differ from confirmed roles", rule="effect_definition_mismatch")
            if result.capability_id == "causal.g_formula" and (
                effect.outcome_model != query.estimator_config.get("outcome_model")
                or effect.bootstrap_n != query.estimator_config.get("bootstrap")
            ):
                raise StructuredResponseError("effect execution configuration differs from ledger")
            if effect.graph_result_ref != graph_ref or query.graph_result_ref != graph_ref:
                raise StructuredResponseError("primary effect source differs from primary graph")
            if not effect.sample_identity or not effect.adjustment_rule or effect.support.get("status") != "supported":
                raise StructuredResponseError("effect lacks sample or support evidence", rule="effect_support_missing")
            source = eligible[graph_ref]
            if result.standardized_graph != source.standardized_graph:
                raise StructuredResponseError("estimation graph differs from its source")
            if graph_ref not in {record.effect_query.graph_result_ref for record in current_pair_diagnoses
                                 if record.effect_query is not None and normalize_tool_status(record.final_status) == "succeeded"}:
                raise StructuredResponseError("estimation source graph has no successful diagnosis", rule="estimation_graph_not_diagnosed")
        else:
            if not requested:
                status = "not_requested" if isinstance(analysis_parameters, Mapping) and "effect_request" in analysis_parameters else "not_recorded"
                reason = "本次未请求效应估计。" if status == "not_requested" else "历史记录没有保存效应请求，不能推断估计意图。"
            else:
                status, reason = normalized_decision.effect_status, normalized_decision.effect_reason
                if status in {"not_requested", "not_recorded", "adopted"} or not reason:
                    raise StructuredResponseError("missing effect needs an explicit status and reason", rule="effect_absence_reason_missing")
                if status == "not_applicable" and not any(
                    item.status == "not_applicable"
                    and get_algorithm_spec(item.capability_id).kind == "causal_estimation"
                    and _effect_query_matches(
                        current_ledger[item.invocation_id], pair, effect_request,
                        graph_result_ref=graph_ref,
                    )
                    for item in eligible.values()
                ):
                    raise StructuredResponseError("not_applicable requires an estimation diagnostic for the selected graph and request", rule="effect_absence_reason_missing")
                if status == "not_identifiable" and not any(
                    normalize_tool_status(record.final_status) == "not_applicable"
                    and _effect_query_matches(record, pair, effect_request, graph_result_ref=graph_ref)
                    for record in current_pair_diagnoses
                ):
                    raise StructuredResponseError("not_identifiable requires a rejected diagnosis for the selected graph and request", rule="effect_absence_reason_missing")
            normalized_decision = normalized_decision.model_copy(update={"effect_status": status, "effect_reason": reason})

        if (
            route_decision == ANALYSIS_ROUTE
            and web_search_enabled is True
            and not self._has_current_terminal_tool_invocation(
                ledger,
                tool_name="web_evidence_search",
                job_id=job_id,
                attempt_count=current_attempt,
                lease_epoch=current_lease,
                worker_id=current_worker,
                input_identity=current_input,
            )
        ):
            incomplete_check(
                "analysis route requires at least one web evidence invocation",
                rule="analysis_route_without_web_invocation",
            )

        return normalized_decision


@dataclass
class FinalizationRetryController:
    """Gate 动态不一致后最多把修正机会交还 Deep Agent 一次。"""

    retry_limit: int = 1
    retries_used: int = 0

    def __post_init__(self) -> None:
        if self.retry_limit < 0:
            raise ValueError("retry_limit must be non-negative")
        if self.retries_used < 0 or self.retries_used > self.retry_limit:
            raise ValueError("retries_used is outside retry_limit")

    @property
    def can_retry(self) -> bool:
        return self.retries_used < self.retry_limit

    def consume(self) -> int:
        if not self.can_retry:
            raise StructuredResponseError("finalization retry budget exhausted")
        self.retries_used += 1
        return self.retries_used


def _effect_query_matches(
    record: InvocationRecord, pair: tuple[str, str] | None,
    effect_request: Any, *, graph_result_ref: str | None = None,
) -> bool:
    """调用必须证明同一变量对、已确认效应定义及指定的源图。"""

    query = record.effect_query
    if pair is None or query is None:
        return False
    if record.subject_ref is not None and record.subject_ref != query.graph_result_ref:
        return False
    if graph_result_ref is not None and query.graph_result_ref != graph_result_ref:
        return False
    return (query.treatment, query.outcome) == pair and query.effect_request == effect_request


def _requires_effect_estimation(parameters: Mapping[str, Any] | None) -> bool:
    """只有显式效应定义才启动效应流程。"""

    if not isinstance(parameters, Mapping):
        return False
    if not isinstance(parameters.get("effect_request"), Mapping):
        return False
    target = parameters.get("target")
    treatment = parameters.get("treatment")
    if not (
        isinstance(target, str) and target.strip()
        and isinstance(treatment, str) and treatment.strip()
    ) or target == treatment:
        return False
    columns = parameters.get("columns")
    if isinstance(columns, (list, tuple)) and columns and (
        target not in columns or treatment not in columns
    ):
        return False
    return True


# Gate 失败规则到公开修正提示的映射。这里只写契约层面的可执行纠正，
# 不携带内部标识、结果内容或异常正文，因此可以同时用于模型重试指令和
# 普通用户可见的公开事件。
FINALIZATION_RETRY_HINTS: dict[str, str] = {
    "primary_graph_invalid": "图结构违反方法约束的结果只能舍弃；不得自动删边或将双箭头拆成普通有向边。",
    "decision_missing": "必须提交结构化 FinalAnalysisDecision，不能只给文字结论。",
    "decision_schema_invalid": (
        "提交对象必须严格符合 FinalAnalysisDecision 的字段类型和取值，"
        "不要新增、省略或改写字段。"
    ),
    "assessment_ref_unknown_result": (
        "result_assessments 只能引用本次运行返回的算法结果；RAG/Web 证据引用"
        "只能放在 revision_proposals.evidence_refs。"
    ),
    "primary_ref_unknown_result": (
        "primary_result_ref 只能引用本次运行返回的算法结果。"
    ),
    "proposal_ref_unknown_result": (
        "revision_proposals.result_ref 只能引用本次运行返回的算法结果。"
    ),
    "proposal_evidence_ref_unknown": (
        "revision_proposals.evidence_refs 只能填 rag_evidence_search 或 "
        "web_evidence_search 返回的证据引用；算法结果之间互相印证请写进 rationale。"
    ),
    "conflict_ref_unknown_result": (
        "conflicts.result_refs 只能引用本次运行返回的算法结果。"
    ),
    "valid_result_without_assessment": (
        "每个有效算法结果都必须恰好有一条 result_assessments 记录。"
    ),
    "non_valid_result_not_discarded": (
        "状态不是有效的算法结果只能标记为 discarded。"
    ),
    "primary_result_not_valid": (
        "primary_result_ref 必须指向状态为有效的算法结果。"
    ),
    "evidence_only_with_valid_result": (
        "outcome=evidence_only 时，所有有效算法结果都必须标记为 discarded。"
    ),
    "no_valid_algorithm_with_valid_result": (
        "outcome=no_valid_algorithm 时不能存在有效算法结果。"
        "若本次问题要求估计处理变量对结果变量的效应，还必须先用有效图调用 "
        "effect_diagnose，并在诊断 succeeded 后完成一次估计调用。"
        "若你认为已有有效结果不可用，请改用 evidence_only，"
        "并把每个有效结果标记为 discarded；这不免除已经触发的 ATE 检查。"
    ),
    "no_valid_algorithm_without_invocation": (
        "outcome=no_valid_algorithm 必须建立在本次真实算法调用之上。"
    ),
    "analysis_route_without_algorithm_result": (
        "本次运行要求执行因果分析，但还没有任何算法调用结果。请至少调用一个算法工具"
        "（例如 causal_pc 或 causal_direct_lingam），并在 result_assessments 中给出它的"
        "取舍；如果算法工具返回未就绪或失败，按它的真实状态提交 outcome。"
    ),
    "effect_question_without_diagnose": (
        "本次问题要求估计处理变量对结果变量的效应，且已有有效因果图。"
        "请携带其中一张图的 graph_result_ref、treatment、outcome 调用一次 effect_diagnose；"
        "即使诊断结论是不可估计，也保留真实返回的 succeeded/not_applicable 状态。"
        "若最终不采用任何图，请改用 evidence_only 并把所有有效结果标记为 discarded。"
    ),
    "effect_question_without_estimation": (
        "effect_diagnose 返回 verdict=proceed。请用同一张图的 graph_result_ref、相同的 "
        "treatment/outcome 调用 recommended_tool_name（causal_backdoor_ols 或 "
        "causal_g_formula，能力 ID 见 recommended_capability_id），并保留真实终态；"
        "不能只用文字结论代替估计调用。"
    ),
    "estimation_graph_not_diagnosed": (
        "主结果所指的估计结果缺少对应的成功诊断记录。请先用同一张图调用 "
        "effect_diagnose，待其返回 succeeded 后再用该图估计。"
    ),
    "effect_not_requested": ("本次没有确认效应请求，请保留主图，主效应为空并说明未请求。"),
    "effect_definition_mismatch": ("估计请求与确认定义不同；不能改变问题，请采用与当前定义一致的结果或说明未采用原因。"),
    "effect_support_missing": ("当前估计缺少样本及支持证明，请保留有效主图，主效应为空并说明限制。"),
    "effect_absence_reason_missing": ("主效应为空时必须填写与执行事实一致的 effect_status 及 effect_reason，可保留独立的主图。"),
    "analysis_route_without_web_invocation": (
        "本次运行已开启联网搜索。请先调用一次 web_evidence_search 并等待真实返回，"
        "即使没有结果或服务暂不可用，也要保留该真实状态后再提交最终决策。"
    ),
}

_GENERIC_RETRY_INSTRUCTION = (
    "上一份结构化最终决策未通过程序事实校验。请保留已有工具结果，重新提交一个"
    "只引用真实结果、并为每个有效结果提供唯一取舍的 FinalAnalysisDecision；"
    "不要重新编造工具调用或结果。"
)


def finalization_rule_hint(rule: str | None) -> str | None:
    """返回可安全公开的修正提示；未知规则返回 ``None``。"""

    if not rule:
        return None
    return FINALIZATION_RETRY_HINTS.get(rule)


def build_finalization_retry_instruction(error: BaseException) -> str:
    """把 Gate 失败规则转成一条脱敏、可执行的修正指令。"""

    hint = finalization_rule_hint(getattr(error, "rule", None))
    if hint is None:
        return _GENERIC_RETRY_INSTRUCTION
    return f"{_GENERIC_RETRY_INSTRUCTION}具体规则：{hint}"

