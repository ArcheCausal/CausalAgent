"""连接数据处理子图、独立校验和已校验数据集绑定。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph._internal._constants import (
    CONFIG_KEY_CHECKPOINTER,
    CONFIG_KEY_CHECKPOINT_ID,
    CONFIG_KEY_CHECKPOINT_MAP,
    CONFIG_KEY_REPLAY_STATE,
    CONFIG_KEY_RESUMING,
    CONFIG_KEY_TASK_ID,
)
from langgraph.errors import GraphBubbleUp, GraphInterrupt

from Agent.deep_agent.context import AgentRunContext, AnalysisInputRef
from Agent.causal_agent.analysis_context import rebuild_context_index
from Agent.deep_agent_tools.identity import build_data_processing_run_id
from Agent.deep_agent_tools.interaction import (
    DATA_PROCESSING_PLAN_CONFIRMATION_TYPE,
    DATA_PROCESSING_QUESTION_TYPE,
    DataProcessingQuestionRequest,
)
from Agent.deep_agent_tools.models import canonical_json_bytes
from Agent.data_processing.persistence import (
    require_current_plan_confirmation,
    require_candidate_parameters,
    bind_candidate,
    read_bound_dataset_identity,
    read_candidate, read_research_inputs,
    record_validation,
)
from Agent.data_processing.lifecycle import confirmed_version, require_candidate_binding
from Agent.data_processing.contracts import ResearchPlanConfirmationRequest
from Agent.data_processing.parameters import (
    PROCESSING_STATE_VERSION, ParameterContractError, UnsupportedProcessingCheckpointError, parameter_identity,
    research_parameters, validate_parameter_history,
)
from Database.analysis_contexts import apply_final_parameters_context
from Agent.data_processing.validation import validate_candidate
from Agent.execution_control import JobExecutionRevoked
from Agent.llm_structured_output import StructuredOutputError


DATA_PROCESSING_CHECKPOINT_NAMESPACE = "data_processing_v1"
MAX_VALIDATION_RETRIES = 3
MAX_COMPLETION_RETRIES = 3


class DataProcessingCompletionMiddleware(AgentMiddleware):
    """在同一子图调用内纠正结束请求，保留现有模型和工具预算。"""

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        messages = state.get("messages") or []
        index = next((i for i in range(len(messages) - 1, -1, -1)
                      if isinstance(messages[i], AIMessage)), None)
        if index is None:
            return None
        answered = {message.tool_call_id for message in messages[index + 1:] if isinstance(message, ToolMessage)}
        calls = [call for call in messages[index].tool_calls if call["id"] not in answered]
        if len(calls) <= 1:
            return None
        count = int(state.get("data_processing_completion_retry_count") or 0)
        if count >= MAX_COMPLETION_RETRIES:
            raise ParameterContractError("processing tool sequencing correction limit exceeded")
        # ToolNode 同批工具读取同一个 State，依赖前一步写入的调用必须分轮执行。
        return {"jump_to": "model", "data_processing_completion_retry_count": count + 1, "messages": [
            ToolMessage(tool_call_id=call["id"], name=call["name"], status="error",
                        content="本批工具未执行。数据处理工具必须每轮只调用一个，保存状态后再继续。")
            for call in calls
        ]}

    @hook_config(can_jump_to=["model", "tools"])
    async def aafter_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        context = runtime.context
        if not isinstance(context, AgentRunContext) or context.trusted_identity is None:
            raise RuntimeError("processing completion requires trusted Job context")
        await context.ensure_active()
        identity = context.trusted_identity
        source_hash = identity.source_input_identity()
        version = confirmed_version(state, job_id=str(identity.job_id), source_hash=source_hash)
        tool_name = None
        args: dict[str, Any] = {}
        from Agent.data_processing.agent import _research_ready
        if not _research_ready(state):
            instruction = "请先检查冻结输入的完整列结构，再用 set_research_spec 提交研究参数并处理最新输入。"
        elif state.get("data_processing_plan_parameter_identity") != parameter_identity(state["analysis_parameters"]):
            instruction = "请用 set_data_processing_plan 保存绑定当前研究参数的方案，再继续确认和执行。"
        elif not state.get("data_processing_metadata") or not state.get("data_processing_draft_script"):
            instruction = "请先使用 set_data_processing_plan 保存完整处理方案，并保存脚本，再展示方案请求确认。"
        elif version is None:
            tool_name = "request_data_processing_plan_confirmation"
        else:
            await asyncio.to_thread(
                require_current_plan_confirmation, context, source_hash=source_hash, plan_version=version,
            )
            await context.check_after_call()
            candidate_id = state.get("data_processing_candidate_id")
            if not candidate_id:
                instruction = "当前脚本尚未执行并保存候选。请调用 execute_processing_script，成功后才能结束；不得交付历史候选。"
            else:
                candidate = await asyncio.to_thread(
                    read_candidate, context, dataset_id=candidate_id, source_hash=source_hash,
                )
                await context.check_after_call()
                require_candidate_parameters(
                    candidate["metadata"], job_id=str(identity.job_id),
                    parameters=state.get("analysis_parameters"), changes=state.get("analysis_parameter_changes"),
                )
                require_candidate_binding(state, candidate, job_id=str(identity.job_id), source_hash=source_hash)
                return None
        count = int(state.get("data_processing_completion_retry_count") or 0)
        if count >= MAX_COMPLETION_RETRIES:
            raise ParameterContractError("processing completion correction limit exceeded")
        update = {"data_processing_completion_retry_count": count + 1}
        if tool_name:
            update.update(jump_to="tools", messages=[AIMessage(content="", tool_calls=[{
                "id": "processing-completion-" + uuid4().hex, "name": tool_name, "args": args,
            }])])
        else:
            update.update(jump_to="model", messages=[HumanMessage(content=instruction)])
        return update


def _validation_retry_message(candidate_id: Any, report: Mapping[str, Any]) -> HumanMessage:
    """仅向处理子图反馈本次校验事实，不进入公开事件或普通日志。"""
    details = {
        "dataset_id": candidate_id,
        "errors": report.get("errors", []),
        "target": report.get("target"),
        "treatment": report.get("treatment"),
        "research_roles": report.get("research_roles", {}),
        "role_columns": report.get("role_columns", {}),
        "columns": report.get("columns", []),
        "algorithm_compatibility": report.get("algorithm_compatibility", []),
    }
    return HumanMessage(
        id=f"validation-retry-{candidate_id}",
        content=(
            "独立校验未通过。以下是研究角色、方案角色列映射、本次执行列名、候选 CSV 实际列名和确定性错误。"
            "研究角色名称与实际列名不同或映射错误时，使用 set_data_processing_plan 修正 role_columns，确认后重新执行；不要改写原始研究角色或让用户重复选择。"
            "请判断是角色列映射缺失或错误、脚本遗漏已确认字段、用户信息不足，还是参数提取与已有明确答复矛盾。"
            "脚本遗漏可在原确认范围内修复；有新的语义歧义才向用户澄清。"
            "参数提取错误不代表校验器强制要求角色，不要要求用户重复作同一个选择，"
            "请通过 set_research_spec 提交实际参数纠正，再更新并确认方案。不得忽略校验或默认清空角色继续分析；"
            "遵守重试上限，未解决时如实说明。不要声称未实际执行的修正已完成。\n"
            + json.dumps(details, ensure_ascii=False, separators=(",", ":"))
        ),
    )


def _interrupts(snapshot: Any) -> list[Any]:
    if snapshot is None:
        return []
    public = getattr(snapshot, "interrupts", None)
    if public:
        return list(public)
    return [
        item
        for task in (getattr(snapshot, "tasks", None) or ())
        for item in (getattr(task, "interrupts", None) or ())
    ]


def _public_interrupt(value: Any) -> dict[str, Any]:
    raw = value.value if hasattr(value, "value") else value
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except ValueError:
            decoded = None
        if isinstance(decoded, Mapping):
            raw = decoded
    if not isinstance(raw, Mapping):
        raise RuntimeError("data-processing Agent produced an invalid question")
    interaction_type = raw.get("interaction_type")
    if interaction_type == DATA_PROCESSING_QUESTION_TYPE:
        request = DataProcessingQuestionRequest(
            prompt=str(raw.get("message") or raw.get("prompt") or ""),
            question_id=str(raw.get("question_id") or ""),
            expires_at=str(raw.get("expires_at") or ""),
        )
        return request.public_payload()
    if interaction_type == DATA_PROCESSING_PLAN_CONFIRMATION_TYPE:
        request = ResearchPlanConfirmationRequest(
            prompt=str(raw.get("message") or raw.get("prompt") or ""),
            question_id=str(raw.get("question_id") or ""),
            expires_at=str(raw.get("expires_at") or ""),
            plan_version=str(raw.get("plan_version") or ""),
            plan=raw.get("plan"),
        )
        return request.public_payload()
    # Future child interrupts remain visible only if they use the public Job contract.
    if raw.get("type") == "interaction":
        payload = dict(raw)
        if not isinstance(payload.get("message"), str):
            raise RuntimeError("data-processing Agent produced an invalid interaction")
        return payload
    raise RuntimeError("data-processing Agent produced an unsupported interrupt type")


def _specification_briefs(registry: Any) -> list[dict[str, Any]]:
    specs = registry.specs if registry is not None else ()
    return [
        {
            "capability_id": spec.capability_id,
            "name": spec.public_name,
            "available": bool(spec.enabled and spec.availability == "available"),
            "data_requirements": list(spec.data_requirements),
            "requires_numeric_columns": spec.requires_numeric_columns,
            "requires_complete_cases": spec.requires_complete_cases,
            "native_missing_handling": spec.native_missing_handling,
            "supports_encoded_categories": spec.supports_encoded_categories,
            "min_rows": spec.min_rows,
            "min_columns": spec.min_columns,
            "assumptions": list(spec.assumptions),
        }
        for spec in specs
    ]


def _emit_progress(runtime: Any, *, key: str, summary: str) -> None:
    writer = getattr(runtime, "stream_writer", None)
    if callable(writer):
        writer(
            {
                "type": "progress",
                "node_name": "data_processing",
                "summary": summary,
                "_event_key": key,
            }
        )


def _emit_validation_progress(
    runtime: Any, *, job_attempt: int, dataset_id: int, accepted: bool,
) -> None:
    """按真实图任务区分校验阶段，并为同一任务保留稳定重放键。"""
    writer = getattr(runtime, "stream_writer", None)
    if callable(writer):
        task_id = runtime.execution_info.task_id
        writer(
            {
                "type": "progress",
                "node_name": "validate_dataset",
                "summary": (
                    "候选数据集通过独立校验。"
                    if accepted
                    else "候选数据集未通过独立校验，正在按检查结果修正。"
                ),
                "_event_key": f"dataset-validation:{job_attempt}:{dataset_id}:{task_id}",
            }
        )


async def run_data_processing_child(
    agent: Any,
    *,
    config: Mapping[str, Any] | None,
    job_id: str,
    run_id: str,
    context: AgentRunContext,
    initial_input: Mapping[str, Any],
    retry_input: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """在父任务内恢复真实子图，让原生 interrupt 冒泡并保留答复索引。"""
    child_config = dict(config or {})
    configurable = dict(child_config.get("configurable") or {})
    if CONFIG_KEY_TASK_ID not in configurable:
        raise RuntimeError("data-processing child must run inside a parent graph task")
    # Keep the parent task/scratchpad for native resume propagation, but never
    # select a checkpoint from the parent's lineage in this semantic namespace.
    for key in (
        CONFIG_KEY_CHECKPOINT_ID,
        CONFIG_KEY_CHECKPOINT_MAP,
        CONFIG_KEY_REPLAY_STATE,
        CONFIG_KEY_RESUMING,
    ):
        configurable.pop(key, None)
    configurable["thread_id"] = str(job_id)
    configurable["checkpoint_ns"] = (
        f"{DATA_PROCESSING_CHECKPOINT_NAMESPACE}:{run_id.partition(':')[2]}"
    )
    checkpointer = configurable.get(CONFIG_KEY_CHECKPOINTER) or agent.checkpointer
    if checkpointer is None or isinstance(checkpointer, bool):
        raise RuntimeError("data-processing child requires a concrete checkpointer")
    # aget_state otherwise interprets checkpoint_ns as a discoverable subgraph
    # path instead of reading this explicit namespace from the shared saver.
    configurable[CONFIG_KEY_CHECKPOINTER] = checkpointer
    child_config["configurable"] = configurable
    child_config["metadata"] = {
        **dict(child_config.get("metadata") or {}),
        "data_processing_run_id": run_id,
    }
    snapshot = await agent.aget_state(child_config)
    await context.check_after_call()
    values = getattr(snapshot, "values", None)
    if initial_input.get("data_processing_state_version") == PROCESSING_STATE_VERSION:
        if isinstance(values, Mapping) and values:
            if (values.get("data_processing_state_version") != PROCESSING_STATE_VERSION
                    or values.get("data_processing_run_id") != run_id):
                raise UnsupportedProcessingCheckpointError("legacy or mismatched active processing checkpoint")
            validate_parameter_history(
                values.get("analysis_parameters"), values.get("analysis_parameter_changes"), job_id=job_id,
            )
        else:
            # Old goal-dependent namespaces must not be silently abandoned.
            async for saved in checkpointer.alist({"configurable": {"thread_id": str(job_id)}}):
                namespace = saved.config["configurable"].get("checkpoint_ns", "")
                if namespace.startswith(DATA_PROCESSING_CHECKPOINT_NAMESPACE + ":") and (
                    saved.checkpoint.get("channel_values", {}).get("data_processing_state_version")
                    != PROCESSING_STATE_VERSION
                ):
                    raise UnsupportedProcessingCheckpointError("legacy active processing checkpoint requires explicit restart")
    if _interrupts(snapshot):
        # The parent Command(resume=...) reaches the child through its inherited
        # scratchpad/resume map. A second manual interrupt would consume it twice.
        child_input = None
    elif isinstance(values, Mapping) and values:
        protected = {"analysis_parameters", "analysis_parameter_changes",
                     "data_processing_semantic_identity", "data_processing_confirmed_plan_version",
                     "data_processing_confirmed_plan", "data_processing_metadata",
                     "data_processing_draft_script", "data_processing_candidate_id",
                     "data_processing_candidate_revision", "data_processing_execution_report",
                     "data_processing_completion_retry_count",
                     "data_processing_state_version", "data_processing_run_id",
                     "data_processing_answer_receipts", "data_processing_input_sources",
                     "data_processing_pending_input_id", "data_processing_research_spec_ready",
                     "data_processing_input_columns", "data_processing_plan_parameter_identity"}
        if retry_input and protected.intersection(retry_input):
            raise ParameterContractError("retry input cannot overwrite persisted child parameters")
        if getattr(snapshot, "next", ()):
            child_input = None
        elif retry_input is None:
            return values
        else:
            child_input = retry_input
    else:
        child_input = initial_input
    try:
        result = await agent.ainvoke(child_input, config=child_config, context=context)
    except GraphInterrupt as exc:
        # Preserve child interrupt IDs so scalar and ID-addressed resumes both
        # reach the original child task. Only normalize the public question.
        raise GraphInterrupt(
            tuple(replace(item, value=_public_interrupt(item)) for item in exc.args[0])
        ) from exc
    await context.check_after_call()
    if not isinstance(result, Mapping):
        raise RuntimeError("data-processing Agent returned invalid State")
    return result


async def data_processing_parent_node(
    state: Mapping[str, Any],
    *,
    runtime: Any,
    config: Mapping[str, Any] | None,
    agent: Any,
    registry: Any,
) -> dict[str, Any]:
    """稳定恢复子图并将候选产物引用投影到父图。"""

    context = getattr(runtime, "context", None)
    if not isinstance(context, AgentRunContext) or context.trusted_identity is None:
        raise RuntimeError("data-processing Agent requires trusted Job runtime context")
    await context.ensure_active()
    identity = context.trusted_identity
    if identity.frozen_input is None or identity.frozen_input.user_file_id is None or identity.frozen_input.object_id is None:
        return {"data_processing_failure_code": "missing_frozen_input"}
    source_hash = identity.source_input_identity()
    run_id = build_data_processing_run_id(job_id=identity.job_id, source_input_identity=source_hash)

    bound = await asyncio.to_thread(read_bound_dataset_identity, identity)
    if bound is not None:
        await context.check_after_call()
        plan_binding = bound["metadata"]["processing_plan_identity"]
        execution_report = state.get("data_processing_execution_report")
        if execution_report is not None and (
            execution_report.get("candidate_id") != bound["dataset_id"]
            or execution_report.get("data_hash") != bound["data_hash"]
            or execution_report.get("source_hash") != source_hash
            or execution_report.get("plan_version") != plan_binding["plan_version"]
        ):
            raise ParameterContractError("restored execution report conflicts with bound dataset")
        return {
            "data_processing_state_version": PROCESSING_STATE_VERSION,
            "analysis_parameters": bound["analysis_parameters"],
            "analysis_question": bound["analysis_parameters"].get("analysis_question") or "",
            "analysis_parameter_changes": bound["analysis_parameter_changes"],
            "analysis_context": bound["analysis_context"],
            "analysis_context_index": rebuild_context_index(
                state.get("analysis_context_index"), state.get("analysis_context"),
                bound["analysis_context"]["analysis_context_id"],
            ),
            "context_resolution": {"status": "final_parameters_restored",
                "analysis_context_id": bound["analysis_context"]["analysis_context_id"]},
            "data_processing_semantic_identity": plan_binding["semantic_identity"],
            "data_processing_confirmed_plan_version": plan_binding["plan_version"],
            "prepared_dataset_id": int(bound["dataset_id"]),
            "prepared_dataset_hash": str(bound["data_hash"]),
            "data_processing_candidate_id": int(bound["dataset_id"]),
            "data_processing_execution_report": execution_report,
            "dataset_validation_status": "accepted",
            "data_processing_run_id": run_id,
        }

    inputs = await asyncio.to_thread(read_research_inputs, context)
    await context.check_after_call()
    initial_source = inputs[0]
    goal = initial_source["text"]
    specs = _specification_briefs(registry)
    context_brief = {key: (state.get("analysis_context") or {}).get(key)
                     for key in ("target", "treatment", "analysis_question", "summary")}
    goal += "\n可参考的已有研究上下文（本次要求优先）：" + json.dumps(context_brief, ensure_ascii=False)
    semantic_payload = canonical_json_bytes(
        {"goal": goal, "specifications": specs}
    )
    semantic_identity = hashlib.sha256(semantic_payload).hexdigest()
    child_context = replace(
        context,
        data_processing_goal_identity=semantic_identity,
    )
    from Agent.data_processing.agent import build_initial_message

    initial_input = {
        "analysis_parameters": research_parameters({"effect_request": None}),
        "analysis_parameter_changes": [],
        "data_processing_state_version": PROCESSING_STATE_VERSION,
        "data_processing_run_id": run_id,
        "data_processing_answer_receipts": {},
        "data_processing_input_sources": {str(initial_source["source_input_id"]): initial_source},
        "data_processing_pending_input_id": initial_source["source_input_id"],
        "data_processing_research_spec_ready": False,
        "data_processing_input_columns": [],
        "data_processing_plan_parameter_identity": None,
        "messages": [
            HumanMessage(content=build_initial_message(goal=goal, specifications=specs) + "\n可信初始输入来源：" + str(initial_source["source_input_id"]) + "；当前参数修订号：0")
        ],
        "data_processing_draft_script": "",
        "data_processing_candidate_id": 0,
        "data_processing_candidate_revision": 0,
        "data_processing_metadata": {},
        "data_processing_semantic_identity": semantic_identity,
        "data_processing_confirmed_plan_version": None,
        "data_processing_confirmed_plan": None,
        "data_processing_execution_report": None,
        "data_processing_completion_retry_count": 0,
    }
    retry_report = state.get("data_processing_retry_report")
    retry_input = None
    if isinstance(retry_report, Mapping):
        candidate_id = state.get("data_processing_candidate_id")
        retry_input = {
            "messages": [_validation_retry_message(candidate_id, retry_report)]
        }

    progress_task_id = (config or {}).get("configurable", {}).get(CONFIG_KEY_TASK_ID)
    if not progress_task_id:
        raise RuntimeError("data-processing child must run inside a parent graph task")
    _emit_progress(
        runtime,
        key=f"data-processing-start:{run_id}:{identity.attempt_count}:{progress_task_id}",
        summary="正在检查原始数据并准备经过独立校验的分析数据集。",
    )

    try:
        child_state = await run_data_processing_child(
            agent,
            config=config,
            job_id=str(identity.job_id),
            run_id=run_id,
            context=child_context,
            initial_input=initial_input,
            retry_input=retry_input,
        )
    except (JobExecutionRevoked, GraphBubbleUp, ParameterContractError):
        raise
    except StructuredOutputError as exc:
        return {
            "data_processing_run_id": run_id,
            "data_processing_failure_code": (
                "parameter_extraction_failed"
                if exc.node_name == "data_processing_parameter_extraction"
                else "processing_failed"
            ),
            "data_processing_retry_report": None,
        }
    except Exception as exc:
        code = "sandbox_unavailable" if "sandbox" in type(exc).__name__.lower() else "processing_failed"
        return {
            "data_processing_run_id": run_id,
            "data_processing_failure_code": code,
            "data_processing_retry_report": None,
        }

    candidate_id = child_state.get("data_processing_candidate_id")
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id <= 0:
        return {
            "data_processing_run_id": run_id,
            "data_processing_failure_code": "candidate_missing",
            "data_processing_retry_report": None,
        }
    parameters = child_state.get("analysis_parameters")
    changes = validate_parameter_history(
        parameters, child_state.get("analysis_parameter_changes"), job_id=str(identity.job_id),
    )
    from Agent.data_processing.agent import _research_ready
    if not _research_ready(child_state, require_plan=True):
        raise ParameterContractError("processing completed with unresolved parameter ambiguity")
    candidate = await asyncio.to_thread(
        read_candidate, identity, dataset_id=candidate_id, source_hash=source_hash,
    )
    require_candidate_parameters(
        candidate["metadata"], job_id=str(identity.job_id), parameters=parameters, changes=changes,
    )
    binding = candidate["metadata"]["processing_plan_identity"]
    if (binding["semantic_identity"] != child_state.get("data_processing_semantic_identity")
            or binding["plan_version"] != child_state.get("data_processing_confirmed_plan_version")):
        raise ParameterContractError("candidate belongs to an earlier processing plan")
    require_candidate_binding(child_state, candidate, job_id=str(identity.job_id), source_hash=source_hash)
    result = await asyncio.to_thread(
        apply_final_parameters_context, user_id=identity.user_id, session_id=str(identity.session_id),
        job_id=str(identity.job_id), worker_id=identity.worker_id,
        attempt_count=identity.attempt_count, lease_epoch=identity.lease_epoch,
        analysis_parameters=parameters, dataset_id=candidate_id,
    )
    await context.check_after_call()
    if result is None:
        raise ParameterContractError("final parameters require a frozen input context")
    return {
        "data_processing_state_version": PROCESSING_STATE_VERSION,
        "analysis_parameters": dict(parameters),
        "analysis_question": parameters.get("analysis_question") or "",
        "analysis_parameter_changes": changes,
        "analysis_context": result["context"],
        "analysis_context_index": rebuild_context_index(
            state.get("analysis_context_index"), state.get("analysis_context"), result["analysis_context_id"],
        ),
        "context_resolution": {"status": "final_parameters_bound",
                               "analysis_context_id": result["analysis_context_id"],
                               "action": result["action"]},
        "data_processing_semantic_identity": binding["semantic_identity"],
        "data_processing_confirmed_plan_version": binding["plan_version"],
        "data_processing_run_id": run_id,
        "data_processing_candidate_id": candidate_id,
        "data_processing_candidate_revision": int(
            child_state.get("data_processing_candidate_revision") or 0
        ),
        "data_processing_execution_report": child_state["data_processing_execution_report"],
        "data_processing_failure_code": None,
        "data_processing_retry_report": None,
    }


async def validate_dataset_node(
    state: Mapping[str, Any],
    *,
    runtime: Any,
    registry: Any,
) -> dict[str, Any]:
    """从 MySQL 强读候选文件，独立执行结构、语义和算法输入检查。"""

    context = getattr(runtime, "context", None)
    if not isinstance(context, AgentRunContext) or context.trusted_identity is None:
        raise RuntimeError("dataset validation requires trusted Job runtime context")
    identity = context.trusted_identity
    if state.get("dataset_validation_status") == "accepted" and state.get(
        "prepared_dataset_id"
    ):
        bound = await asyncio.to_thread(read_bound_dataset_identity, identity)
        if bound is None or int(bound["dataset_id"]) != int(state["prepared_dataset_id"]):
            raise RuntimeError("checkpoint dataset binding does not match MySQL")
        require_candidate_parameters(
            bound["metadata"], job_id=str(identity.job_id),
            parameters=state.get("analysis_parameters"), changes=state.get("analysis_parameter_changes"),
        )
        await context.check_after_call()
        return {
            "dataset_validation_status": "accepted",
            "dataset_validation_report": state.get("dataset_validation_report") or {},
        }
    dataset_id = state.get("data_processing_candidate_id")
    if isinstance(dataset_id, bool) or not isinstance(dataset_id, int) or dataset_id <= 0:
        return {
            "dataset_validation_status": "rejected",
            "dataset_validation_report": {
                "accepted": False,
                "errors": [{"code": "candidate_missing", "message": "没有可校验的数据处理产物"}],
            },
            "data_processing_retry_count": int(state.get("data_processing_retry_count") or 0),
        }
    await context.ensure_active()
    candidate = await asyncio.to_thread(
        read_candidate,
        identity,
        dataset_id=dataset_id,
        source_hash=identity.source_input_identity(),
    )
    require_candidate_parameters(
        candidate["metadata"], job_id=str(identity.job_id),
        parameters=state.get("analysis_parameters"), changes=state.get("analysis_parameter_changes"),
    )
    if (candidate["metadata"]["processing_plan_identity"]["semantic_identity"]
            != state.get("data_processing_semantic_identity") or
            candidate["metadata"]["processing_plan_identity"]["plan_version"]
            != state.get("data_processing_confirmed_plan_version")):
        raise ParameterContractError("candidate processing plan differs from the final parent state")
    provider_settings = getattr(context.sandbox_provider, "settings", None)
    max_bytes = int(getattr(provider_settings, "max_output_file_bytes", 64 * 1024 * 1024))
    specifications = registry.specs if registry is not None else ()
    report = validate_candidate(
        data_bytes=candidate["data_bytes"],
        script_bytes=candidate["script_bytes"],
        metadata=candidate["metadata"],
        analysis_parameters=state.get("analysis_parameters") or {},
        specifications=specifications,
        max_bytes=max_bytes,
        expected_parameter_identity=parameter_identity(state["analysis_parameters"]),
    )
    await context.check_after_call()
    await asyncio.to_thread(
        record_validation,
        identity,
        dataset_id=dataset_id,
        source_hash=identity.source_input_identity(),
        report=report,
        accepted=bool(report["accepted"]),
    )
    retry_count = int(state.get("data_processing_retry_count") or 0)
    if not report["accepted"]:
        retry_count += 1
    _emit_validation_progress(
        runtime, job_attempt=identity.attempt_count, dataset_id=dataset_id,
        accepted=bool(report["accepted"]),
    )
    return {
        "dataset_validation_status": "accepted" if report["accepted"] else "rejected",
        "dataset_validation_report": report,
        "data_processing_retry_count": retry_count,
        "data_processing_retry_report": None if report["accepted"] else report,
    }


def validation_router(state: Mapping[str, Any]) -> str:
    if state.get("data_processing_failure_code"):
        return "failed"
    if state.get("dataset_validation_status") == "accepted":
        return "bind"
    if int(state.get("data_processing_retry_count") or 0) <= MAX_VALIDATION_RETRIES:
        return "retry"
    return "failed"


async def bind_dataset_node(
    state: Mapping[str, Any],
    *,
    runtime: Any,
) -> dict[str, Any]:
    """在校验通过后，按当前 Job lease 原子绑定唯一分析输入。"""

    context = getattr(runtime, "context", None)
    if not isinstance(context, AgentRunContext) or context.trusted_identity is None:
        raise RuntimeError("dataset binding requires trusted Job runtime context")
    if state.get("dataset_validation_status") != "accepted":
        raise RuntimeError("unvalidated candidate cannot be bound")
    identity = context.trusted_identity
    dataset_id = state.get("data_processing_candidate_id")
    bound = await asyncio.to_thread(
        bind_candidate,
        identity,
        dataset_id=dataset_id,
        source_hash=identity.source_input_identity(),
        analysis_parameters=state["analysis_parameters"],
        analysis_parameter_changes=state["analysis_parameter_changes"],
    )
    await context.check_after_call()
    rebound = await asyncio.to_thread(read_bound_dataset_identity, identity)
    if not rebound or int(rebound["dataset_id"]) != int(dataset_id):
        raise RuntimeError("bound dataset could not be re-read from MySQL")
    analysis_input = identity.analysis_input
    if analysis_input is None:
        raise RuntimeError("trusted Job identity has no analysis input reference")
    analysis_input.bind(
        dataset_id=int(rebound["dataset_id"]),
        object_id=int(rebound["data_object_id"]),
        content_hash=str(rebound["data_hash"]),
        filename=str(rebound["filename"]),
        semantic_kinds=rebound["semantic_kinds"],
    )
    return {
        "prepared_dataset_id": int(rebound["dataset_id"]),
        "prepared_dataset_hash": str(rebound["data_hash"]),
        "dataset_validation_status": "accepted",
        "prepared_dataset_metadata": bound["metadata"],
    }


def dataset_failure_node(state: Mapping[str, Any]) -> dict[str, Any]:
    """以明确原因结束，不把未通过校验的数据交给因果分析。"""

    failure_labels = {
        "missing_frozen_input": "请先上传并选择一个 CSV 文件，再开始因果分析",
        "sandbox_unavailable": "数据处理沙盒服务当前不可用",
        "processing_failed": "数据处理阶段未能完成",
        "parameter_extraction_failed": "参数解析失败，未能从用户答复中提取有效的研究参数",
        "candidate_missing": "数据处理 Agent 未提交可校验的数据集",
        "question_limit_reached": "需要澄清的问题超过本次任务允许次数",
    }
    code = str(state.get("data_processing_failure_code") or "")
    reasons = [
        str(item.get("message"))
        for item in (state.get("dataset_validation_report") or {}).get("errors", [])
        if isinstance(item, Mapping) and item.get("message")
    ]
    reason = failure_labels.get(code, "候选数据集未通过独立校验")
    if reasons:
        reason += "：" + "；".join(reasons[:5])
    return {
        "messages": [
            AIMessage(content=f"{reason}。本次未启动因果分析。", name="data_processing"),
        ],
        "data_processing_failure_message": reason,
        "data_processing_failure_code": code or "validation_budget_exhausted",
    }
