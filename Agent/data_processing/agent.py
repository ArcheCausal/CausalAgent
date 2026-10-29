"""Deep Agent tools for preparing one Job's analysis dataset in OpenSandbox."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

from deepagents import create_deep_agent
from deepagents.middleware import FilesystemMiddleware
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command, interrupt
from pydantic import Field

from Agent.deep_agent.context import AgentRunContext
from Agent.deep_agent.graph import (
    DeepAgentGraphConfig,
    _register_harness_profile,
    build_budget_middlewares,
)
from Agent.deep_agent.profile import DeepAgentProfile
from Agent.deep_agent.state import ProjectDeepAgentState
from Agent.deep_agent_tools.interaction import (
    DataProcessingPlanConfirmationRequest,
    DataProcessingPlanSummary,
    DataProcessingQuestionRequest,
    with_tool_runtime_schema,
)
from Agent.deep_agent_tools.models import ContractModel
from Agent.data_processing.persistence import (
    read_candidate,
    read_parameter_answer, read_research_inputs,
    require_current_plan_confirmation,
    save_candidate,
)
from Agent.data_processing.lifecycle import (
    clear_candidate, confirmed_version, execution_report, invalidate_plan, require_candidate_binding,
    update_plan, update_script,
)
from Agent.data_processing.parameters import (
    ResearchSpecInput, ParameterContractError, commit_research_spec,
    parameter_identity, sandbox_request_id,
)
from Agent.data_processing.plans import (
    build_processing_plan_version, _answer_object,
    extend_data_processing_semantic_identity,
    plan_confirmation_matches,
)
from Database.agent_connect import require_frozen_file_for_job


class EmptyToolInput(ContractModel):
    """工具无模型参数时使用的空输入契约。"""


class ScriptInput(ContractModel):
    """完整、可重放的数据处理 Python 脚本。"""

    script: str = Field(min_length=1, max_length=16000)


class ProcessingPlanInput(DataProcessingPlanSummary):
    """供用户确认的处理方案；不接收执行后说明。"""


class ExecutionReportInput(ContractModel):
    """当前候选的解释；文件事实由程序读取，不接收模型声明。"""

    explanation: str = Field(min_length=1, max_length=4000)


class QuestionInput(ContractModel):
    """需要用户澄清的研究语义问题。"""

    question: str = Field(min_length=1, max_length=3000)


def _tool_message(runtime: Any, *, name: str, content: str) -> ToolMessage:
    call_id = str(getattr(runtime, "tool_call_id", "") or "")
    if not call_id:
        raise RuntimeError("data-processing tool call ID is missing")
    return ToolMessage(content=content, tool_call_id=call_id, name=name)


async def _source_bytes(context: AgentRunContext) -> tuple[bytes, str]:
    identity = context.trusted_identity
    if identity is None or identity.frozen_input is None:
        raise RuntimeError("a trusted frozen CSV input is required")
    frozen = identity.frozen_input
    if frozen.user_file_id is None or frozen.object_id is None:
        raise RuntimeError("the Job has no frozen CSV file reference")
    row = await asyncio.to_thread(
        require_frozen_file_for_job,
        identity.user_id,
        str(identity.job_id),
        frozen.user_file_id,
        frozen.object_id,
    )
    content = bytes(row["file_content"])
    source_hash = str(row["file_hash"])
    if not content or source_hash != identity.source_input_identity():
        raise RuntimeError("the frozen source file does not match the trusted Job identity")
    return content, source_hash


def _context(runtime: Any, *, sandbox_required: bool = True) -> AgentRunContext:
    context = getattr(runtime, "context", None)
    if not isinstance(context, AgentRunContext):
        raise RuntimeError("data-processing tools require the worker runtime context")
    if context.trusted_identity is None or (sandbox_required and context.sandbox_provider is None):
        raise RuntimeError("OpenSandbox and trusted Job identity are required")
    return context


def _semantic_identity(context: AgentRunContext, state: Any) -> str:
    value = state.get("data_processing_semantic_identity") if isinstance(state, dict) else None
    if not isinstance(value, str) or not value:
        value = context.data_processing_goal_identity
    if not isinstance(value, str) or not value:
        raise RuntimeError("data-processing semantic identity is unavailable")
    return value


def _write_script_tool() -> Any:
    async def call(runtime: Any, **kwargs: Any) -> Any:
        payload = ScriptInput.model_validate(kwargs)
        return Command(
            update={
                "messages": [
                    _tool_message(
                        runtime,
                        name="write_processing_script",
                        content=json.dumps(
                            {
                                "status": "saved_in_job_checkpoint",
                                "script_bytes": len(payload.script.encode("utf-8")),
                            },
                            ensure_ascii=False,
                        ),
                    )
                ],
                **update_script(runtime.state, payload.script),
            }
        )

    call.__annotations__["runtime"] = ToolRuntime
    return StructuredTool.from_function(
        coroutine=call,
        name="write_processing_script",
        description=(
            "保存完整的 Python 处理脚本到当前 Job 的持久化 checkpoint。"
            "脚本只能读取 /workspace/raw/original.csv，并应写入"
            " /workspace/output/prepared.csv。"
        ),
        args_schema=with_tool_runtime_schema(
            ScriptInput,
            tool_name="write_processing_script",
            tool_runtime_type=ToolRuntime,
        ),
    )


def _plan_tool() -> Any:
    async def call(runtime: Any, **kwargs: Any) -> Any:
        payload = ProcessingPlanInput.model_validate(kwargs)
        metadata = payload.model_dump(mode="json")
        if not _research_ready(runtime.state):
            return _tool_message(runtime, name="set_data_processing_plan", content="先检查冻结数据并通过 set_research_spec 提交参数和处理待处理输入。")
        input_columns = runtime.state.get("data_processing_input_columns") or []
        if set(metadata.get("measurement_columns", {})) - set(input_columns):
            return _tool_message(runtime, name="set_data_processing_plan", content="measurement_columns 只能引用冻结输入的实际列名。")
        return Command(
            update={
                "messages": [
                    _tool_message(
                        runtime,
                        name="set_data_processing_plan",
                        content="处理方案已保存。方案变化后须重新确认；执行说明请使用 save_data_processing_execution_report。",
                    )
                ],
                **update_plan(runtime.state, metadata),
                "data_processing_plan_parameter_identity": parameter_identity(runtime.state["analysis_parameters"]),
            }
        )

    call.__annotations__["runtime"] = ToolRuntime
    return StructuredTool.from_function(
        coroutine=call,
        name="set_data_processing_plan",
        description=(
            "设置待确认处理方案的观察单位、字段含义、角色列映射、计划转换和限制。方案变化会清空确认与当前候选。"
            "role_columns 将已指定的 target、treatment 映射到处理后 CSV 的实际列名；研究角色名称保持原意，未指定时留空。"
            "执行后补充说明必须使用 save_data_processing_execution_report，不得改写方案。"
            "只写你能从用户说明或实际脚本确认的内容；不要推测字段含义。"
        ),
        args_schema=with_tool_runtime_schema(
            ProcessingPlanInput,
            tool_name="set_data_processing_plan",
            tool_runtime_type=ToolRuntime,
        ),
    )


def _execution_report_tool() -> Any:
    async def call(runtime: Any, **kwargs: Any) -> Any:
        payload = ExecutionReportInput.model_validate(kwargs)
        context = _context(runtime, sandbox_required=False)
        await context.ensure_active()
        state = runtime.state
        candidate_id = state.get("data_processing_candidate_id")
        if not candidate_id:
            return _tool_message(runtime, name="save_data_processing_execution_report",
                                 content="当前没有已执行并保存的候选。请先执行当前脚本。")
        identity = context.trusted_identity
        candidate = await asyncio.to_thread(
            read_candidate, context, dataset_id=candidate_id, source_hash=identity.source_input_identity(),
        )
        await context.check_after_call()
        require_candidate_binding(state, candidate, job_id=str(identity.job_id),
                                  source_hash=identity.source_input_identity())
        report = execution_report(
            candidate_id=candidate_id, revision=candidate["revision"],
            plan_version=state["data_processing_confirmed_plan_version"],
            source_hash=candidate["source_hash"], data_bytes=candidate["data_bytes"],
            script_bytes=candidate["script_bytes"], explanation=payload.explanation,
        )
        return Command(update={
            "data_processing_execution_report": report,
            "messages": [_tool_message(runtime, name="save_data_processing_execution_report",
                                       content="当前候选的执行说明已保存，方案确认保持有效；仍需独立校验。")],
        })

    call.__annotations__["runtime"] = ToolRuntime
    return StructuredTool.from_function(
        coroutine=call, name="save_data_processing_execution_report",
        description="补充当前候选的执行解释。行数、列名和 hash 由程序读取实际产物生成；不修改处理方案、确认或候选 metadata。",
        args_schema=with_tool_runtime_schema(
            ExecutionReportInput, tool_name="save_data_processing_execution_report", tool_runtime_type=ToolRuntime,
        ),
    )


def _research_spec_tool() -> Any:
    async def call(runtime: Any, **kwargs: Any) -> Any:
        payload = ResearchSpecInput.model_validate(kwargs)
        context = _context(runtime, sandbox_required=False)
        await context.ensure_active()
        state = runtime.state
        sources = state.get("data_processing_input_sources") or {}
        source = sources.get(str(payload.source_input_id))
        if source is None:
            raise ParameterContractError("research specification source is not a delivered trusted input")
        trusted = await asyncio.to_thread(read_research_inputs, context)
        await context.check_after_call()
        if not any(item["source_input_id"] == payload.source_input_id and _answer_object(item["text"]) == _answer_object(source["text"]) for item in trusted):
            raise ParameterContractError("research specification source conflicts with the Job input ledger")
        try:
            pending_source = state.get("data_processing_pending_input_id")
            if pending_source and payload.source_input_id != pending_source:
                raise ParameterContractError(f"process pending_input_id={pending_source} before referring to an earlier input")
            parameters, history = commit_research_spec(
                state["analysis_parameters"], state["analysis_parameter_changes"], spec=payload.spec,
                input_columns=state.get("data_processing_input_columns") or [],
                job_id=str(context.trusted_identity.job_id), source_input_id=payload.source_input_id,
                expected_revision=payload.expected_revision, reason=payload.reason,
            )
        except (ParameterContractError, ValueError) as exc:
            return _tool_message(runtime, name="set_research_spec", content=json.dumps({
                "status": "rejected", "error": str(exc), "revision": len(state["analysis_parameter_changes"]),
                "instruction": "依据已提供的真实输入纠正草稿；只有真实歧义才向用户提问。",
            }, ensure_ascii=False))
        changed = parameter_identity(parameters) != parameter_identity(state["analysis_parameters"])
        semantic_identity = _semantic_identity(context, state)
        update = {}
        if changed:
            semantic_identity = extend_data_processing_semantic_identity(semantic_identity, {
                "parameters": parameter_identity(parameters), "revision": len(history)})
            update = {**invalidate_plan(), "data_processing_plan_parameter_identity": None}
        receipts = dict(state.get("data_processing_answer_receipts") or {})
        receipts[str(payload.source_input_id)] = {"parameter_identity": parameter_identity(parameters)}
        pending = state.get("data_processing_pending_input_id")
        if pending == payload.source_input_id:
            pending = None
        return Command(update={
            **update, "analysis_parameters": parameters, "analysis_parameter_changes": history,
            "data_processing_semantic_identity": semantic_identity,
            "data_processing_answer_receipts": receipts,
            "data_processing_pending_input_id": pending,
            "data_processing_research_spec_ready": True,
            "messages": [_tool_message(runtime, name="set_research_spec", content=json.dumps({
                "status": "committed", "parameters": parameters, "revision": len(history),
                "pending_input_id": pending,
            }, ensure_ascii=False))],
        })
    call.__annotations__["runtime"] = ToolRuntime
    return StructuredTool.from_function(coroutine=call, name="set_research_spec",
        description="提交下一版完整研究参数。先检查冻结列结构；未修改字段保留当前值，取消用 null，测量声明完整替换。首次、用户修改和明确纠错使用同一入口。source_input_id 只能引用程序已提供的真实输入，expected_revision 使用当前修订号。全部原始列用 all_input_columns 声明，不能把范围写成列名。",
        args_schema=with_tool_runtime_schema(ResearchSpecInput, tool_name="set_research_spec", tool_runtime_type=ToolRuntime))


async def _receive_user_answer(runtime: Any, *, question_id: str, question: str, answer: Any) -> dict[str, Any]:
    """只接收可信答复并停止旧方案执行，语义理解回到同一 Agent。"""
    context = _context(runtime, sandbox_required=False)
    trusted = await asyncio.to_thread(read_parameter_answer, context, question_id=question_id, answer=answer)
    await context.check_after_call()
    sources = dict(runtime.state.get("data_processing_input_sources") or {})
    source_id = trusted["source_input_id"]
    answer_text = trusted["answer"]
    if not isinstance(answer_text, str):
        answer_text = json.dumps(answer_text, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    source = {"source_input_id": source_id, "text": answer_text, "question": question}
    if str(source_id) in sources:
        if sources[str(source_id)] != source:
            raise ParameterContractError("replayed input conflicts with its trusted source")
        return {}
    sources[str(source_id)] = source
    return {**invalidate_plan(), "data_processing_plan_parameter_identity": None,
        "data_processing_input_sources": sources, "data_processing_pending_input_id": source_id,
        "data_processing_semantic_identity": extend_data_processing_semantic_identity(
            _semantic_identity(context, runtime.state), {"source_input_id": source_id, "answer": answer_text}),
    }


def _research_ready(state: Any, *, require_plan: bool = False) -> bool:
    return bool(state.get("data_processing_research_spec_ready") and not state.get("data_processing_pending_input_id")
        and (not require_plan or state.get("data_processing_plan_parameter_identity") == parameter_identity(state["analysis_parameters"])))


def _question_tool() -> Any:
    async def call(runtime: Any, **kwargs: Any) -> Any:
        payload = QuestionInput.model_validate(kwargs)
        context = _context(runtime)
        identity = context.trusted_identity
        if identity is None:
            raise RuntimeError("trusted Job identity is required")
        call_id = str(getattr(runtime, "tool_call_id", "") or "")
        if not call_id:
            raise RuntimeError("data-processing question ID is missing")
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=300)
        request = DataProcessingQuestionRequest(
            prompt=payload.question,
            question_id=call_id,
            expires_at=expires_at.isoformat().replace("+00:00", "Z"),
        )
        answer = interrupt(request.public_payload())
        answer_text = answer if isinstance(answer, str) else json.dumps(
            answer, ensure_ascii=False, separators=(",", ":")
        )
        state = getattr(runtime, "state", None)
        if not isinstance(state, dict):
            raise RuntimeError("data-processing Agent State is unavailable")
        parameter_update = await _receive_user_answer(
            runtime, question_id=call_id, question=payload.question, answer=answer,
        )
        return Command(
            update={
                "messages": [
                    _tool_message(
                        runtime,
                        name="ask_data_processing_question",
                        content=json.dumps(
                            {"status": "answered", "answer": answer_text,
                             "pending_input_id": parameter_update.get("data_processing_pending_input_id"),
                             "instruction": "根据真实答复调用 set_research_spec，再制定或更新方案。"},
                            ensure_ascii=False,
                        ),
                    )
                ],
                **parameter_update,
            }
        )

    call.__annotations__["runtime"] = ToolRuntime
    return StructuredTool.from_function(
        coroutine=call,
        name="ask_data_processing_question",
        description=(
            "当观察单位、时间窗口、字段含义或聚合方式会改变研究对象时，"
            "暂停并向用户提出一个具体问题。不要用算法允许的清洗步骤代替语义确认。"
        ),
        args_schema=with_tool_runtime_schema(
            QuestionInput,
            tool_name="ask_data_processing_question",
            tool_runtime_type=ToolRuntime,
        ),
    )


def _plan_confirmation_tool() -> Any:
    async def call(runtime: Any, **_: Any) -> Any:
        context = _context(runtime)
        await context.ensure_active()
        identity = context.trusted_identity
        state = getattr(runtime, "state", None)
        if identity is None or not isinstance(state, dict):
            raise RuntimeError("data-processing plan state is unavailable")
        if not _research_ready(state, require_plan=True):
            return _tool_message(runtime, name="request_data_processing_plan_confirmation",
                                 content="请先用 set_research_spec 处理待处理输入，并用 set_data_processing_plan 保存绑定当前参数的方案。")
        script = state.get("data_processing_draft_script")
        metadata = state.get("data_processing_metadata")
        if not isinstance(script, str) or not script.strip() or not isinstance(metadata, dict):
            return _tool_message(
                runtime,
                name="request_data_processing_plan_confirmation",
                content=json.dumps(
                    {"status": "plan_required", "message": "请先完整记录处理脚本和方案说明。"},
                    ensure_ascii=False,
                ),
            )

        _source, source_hash = await _source_bytes(context)
        semantic_identity = _semantic_identity(context, state)
        plan_version = build_processing_plan_version(
            job_id=str(identity.job_id),
            source_hash=source_hash,
            semantic_identity=semantic_identity,
            metadata=metadata,
        )
        call_id = str(getattr(runtime, "tool_call_id", "") or "")
        if not call_id:
            raise RuntimeError("data-processing plan confirmation ID is missing")
        parameters = state["analysis_parameters"]
        labels = {"target": "结果变量", "treatment": "处理变量", "analysis_question": "研究问题",
                  "observation_unit": "观察单位", "time_window": "时间范围", "aggregation_scope": "聚合范围"}
        research_summary = "；".join(label + "：" + str(parameters.get(key) or "未指定")[:130]
                                     for key, label in labels.items())
        kinds = {}
        for declaration in parameters.get("measurement_semantics") or []:
            kinds[declaration["kind"]] = kinds.get(declaration["kind"], 0) + 1
        kind_labels = {"continuous": "连续值", "count": "计数", "ordinal": "有序类别",
                       "category": "类别", "binary": "二元值", "unknown": "未知"}
        research_summary += "；测量声明：" + ("、".join(kind_labels[kind] + str(count) + "列"
            for kind, count in kinds.items()) or "未指定")
        request = DataProcessingPlanConfirmationRequest(
            prompt="请确认当前研究参数与处理方案。研究参数：" + research_summary[:1000] + "。确认后执行；如需修改请说明。",
            question_id=call_id,
            expires_at=(
                datetime.now(timezone.utc) + timedelta(seconds=300)
            ).isoformat().replace("+00:00", "Z"),
            plan_version=plan_version,
            plan=DataProcessingPlanSummary.model_validate(metadata),
        )
        answer = interrupt(request.public_payload())
        await context.check_after_call()
        matches = plan_confirmation_matches(request.public_payload(), answer)
        answer_text = answer if isinstance(answer, str) else json.dumps(
            answer, ensure_ascii=False, separators=(",", ":")
        )
        if matches is True:
            await asyncio.to_thread(
                require_current_plan_confirmation, context, source_hash=source_hash, plan_version=plan_version,
            )
            await context.check_after_call()
            status = "confirmed"
            confirmation = plan_version
        elif matches is False:
            status = "stale_confirmation"
            confirmation = None
        else:
            status = "revision_requested"
            confirmation = None
        semantic_identity = _semantic_identity(context, state)
        parameter_update = {}
        if matches is None:
            plan_question = request.prompt + "\n当前展示的方案摘要：\n" + json.dumps(
                request.plan.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"),
            )
            parameter_update = await _receive_user_answer(
                runtime, question_id=call_id, question=plan_question, answer=answer,
            )
        return Command(
            update={
                "messages": [
                    _tool_message(
                        runtime,
                        name="request_data_processing_plan_confirmation",
                        content=json.dumps(
                            {"status": status, "answer": answer_text,
                             "pending_input_id": parameter_update.get("data_processing_pending_input_id"),
                             "instruction": "根据真实答复调用 set_research_spec，再制定或更新方案。"},
                            ensure_ascii=False,
                        ),
                    )
                ],
                "data_processing_semantic_identity": semantic_identity,
                **parameter_update,
                **(invalidate_plan() if confirmation is None else {}),
                "data_processing_confirmed_plan_version": confirmation,
                "data_processing_confirmed_plan": deepcopy(metadata) if confirmation else None,
            }
        )

    call.__annotations__["runtime"] = ToolRuntime
    return StructuredTool.from_function(
        coroutine=call,
        name="request_data_processing_plan_confirmation",
        description=(
            "在脚本和方案说明都已保存后，展示当前方案并等待用户明确确认。"
            "确认版本绑定当前 Job、冻结文件、研究语义和方案说明；用户确认的是方案所表达的处理语义，不是脚本正文。"
            "脚本失败或校验未通过时，可在已确认语义范围内修复脚本并复用确认；若需要新的研究语义选择或超出已确认的转换范围，必须先向用户澄清并重新确认。"
        ),
        args_schema=with_tool_runtime_schema(
            EmptyToolInput,
            tool_name="request_data_processing_plan_confirmation",
            tool_runtime_type=ToolRuntime,
        ),
    )


def _inspect_input_tool() -> Any:
    async def call(runtime: Any, **_: Any) -> Any:
        context = _context(runtime)
        await context.ensure_active()
        source, _ = await _source_bytes(context)
        identity = context.trusted_identity
        if identity is None:
            raise RuntimeError("trusted Job identity is required")
        async with context.sandbox_provider.open_session(
            job_id=str(identity.job_id),
            worker_id=identity.worker_id,
            attempt_count=identity.attempt_count,
            source_hash=identity.source_input_identity(),
            request_id=sandbox_request_id(
                tool_call_id=str(runtime.tool_call_id or ""), job_id=str(identity.job_id),
                source_hash=identity.source_input_identity(), purpose="inspect",
            ),
            lease_epoch=identity.lease_epoch,
            execution_guard=context.execution_guard,
            original_csv=source,
        ) as session:
            preview = await session.read_preview("raw/original.csv")
        await context.check_after_call()
        from Agent.data_processing.validation import _inspect_csv_shape
        columns, _rows = _inspect_csv_shape(source, max_rows=100_000, max_columns=1_000, max_cells=1_000_000)
        return Command(update={"data_processing_input_columns": columns, "messages": [
            _tool_message(runtime, name="inspect_frozen_dataset", content=json.dumps(
                {"status": "inspected", "columns": columns, "preview": preview}, ensure_ascii=False))]})

    call.__annotations__["runtime"] = ToolRuntime
    return StructuredTool.from_function(
        coroutine=call,
        name="inspect_frozen_dataset",
        description=(
            "在 OpenSandbox 内读取原始 CSV 的受限文本预览。"
            "预览可能包含真实数据；不要在最终回复中复述具体行值。"
        ),
        args_schema=with_tool_runtime_schema(
            EmptyToolInput,
            tool_name="inspect_frozen_dataset",
            tool_runtime_type=ToolRuntime,
        ),
    )


def _execute_script_tool() -> Any:
    async def call(runtime: Any, **_: Any) -> Any:
        context = _context(runtime)
        await context.ensure_active()
        identity = context.trusted_identity
        if identity is None:
            raise RuntimeError("trusted Job identity is required")
        state = getattr(runtime, "state", None)
        if not isinstance(state, dict):
            raise RuntimeError("data-processing Agent State is unavailable")
        if not _research_ready(state, require_plan=True):
            return _tool_message(runtime, name="execute_processing_script", content="研究参数、待处理输入或方案版本尚未就绪，不能执行。")
        script = state.get("data_processing_draft_script")
        metadata = state.get("data_processing_metadata")
        if not isinstance(script, str) or not script.strip():
            return _tool_message(
                runtime,
                name="execute_processing_script",
                content=json.dumps(
                    {"status": "script_required", "message": "请先保存完整处理脚本。"},
                    ensure_ascii=False,
                ),
            )
        if not isinstance(metadata, dict):
            return _tool_message(
                runtime,
                name="execute_processing_script",
                content=json.dumps(
                    {"status": "description_required", "message": "请先记录数据语义说明。"},
                    ensure_ascii=False,
                ),
            )
        source, source_hash = await _source_bytes(context)
        semantic_identity = _semantic_identity(context, state)
        plan_version = build_processing_plan_version(
            job_id=str(identity.job_id),
            source_hash=source_hash,
            semantic_identity=semantic_identity,
            metadata=metadata,
        )
        if not _research_ready(state, require_plan=True) or confirmed_version(
            state, job_id=str(identity.job_id), source_hash=source_hash,
        ) != plan_version:
            return _tool_message(
                runtime,
                name="execute_processing_script",
                content=json.dumps(
                    {
                        "status": "confirmation_required",
                        "message": "当前脚本与方案尚未得到用户确认，不能执行。",
                    },
                    ensure_ascii=False,
                ),
            )
        await asyncio.to_thread(
            require_current_plan_confirmation,
            context,
            source_hash=source_hash,
            plan_version=plan_version,
        )
        await context.check_after_call()
        async with context.sandbox_provider.open_session(
            job_id=str(identity.job_id),
            worker_id=identity.worker_id,
            attempt_count=identity.attempt_count,
            source_hash=identity.source_input_identity(),
            request_id=sandbox_request_id(
                tool_call_id=str(runtime.tool_call_id or ""), job_id=str(identity.job_id),
                source_hash=identity.source_input_identity(), purpose="execute",
            ),
            plan_version=plan_version,
            lease_epoch=identity.lease_epoch,
            execution_guard=context.execution_guard,
            original_csv=source,
        ) as session:
            await session.write_script("scripts/process.py", script)
            result = await session.run("python3 /workspace/scripts/process.py")
            if result.exit_code != 0:
                message = _tool_message(
                    runtime,
                    name="execute_processing_script",
                    content=json.dumps(
                        {
                            "status": "script_failed",
                            "exit_code": result.exit_code,
                            "stderr": result.stderr[-6000:],
                        },
                        ensure_ascii=False,
                    ),
                )
                return Command(update={**clear_candidate(), "messages": [message]})
            output = await session.read_output("output/prepared.csv")
        await context.check_after_call()
        artifact = await asyncio.to_thread(
            save_candidate,
            context,
            source_hash=source_hash,
            data_bytes=output,
            script_bytes=script.encode("utf-8"),
            metadata=metadata,
            plan_version=plan_version,
            semantic_identity=semantic_identity,
            analysis_parameters=state["analysis_parameters"],
            analysis_parameter_changes=state["analysis_parameter_changes"],
        )
        await context.check_after_call()
        summary = {
            "status": "candidate_saved",
            "dataset_id": artifact["dataset_id"],
            "revision": artifact["revision"],
            "output_bytes": len(output),
            "output_hash": hashlib.sha256(output).hexdigest(),
            "command_stdout": result.stdout[-3000:],
        }
        return Command(
            update={
                "messages": [
                    _tool_message(
                        runtime,
                        name="execute_processing_script",
                        content=json.dumps(summary, ensure_ascii=False),
                    )
                ],
                "data_processing_candidate_id": int(artifact["dataset_id"]),
                "data_processing_candidate_revision": int(artifact["revision"]),
                "data_processing_execution_report": execution_report(
                    candidate_id=int(artifact["dataset_id"]), revision=int(artifact["revision"]),
                    plan_version=plan_version, source_hash=source_hash, data_bytes=output,
                    script_bytes=script.encode("utf-8"),
                ),
            }
        )

    call.__annotations__["runtime"] = ToolRuntime
    return StructuredTool.from_function(
        coroutine=call,
        name="execute_processing_script",
        description=(
            "在全新 OpenSandbox 中重建工作区并执行已保存脚本。"
            "脚本成功后，固定输出 output/prepared.csv 会连同脚本和语义说明"
            "幂等保存到 MySQL，等待独立校验。"
        ),
        args_schema=with_tool_runtime_schema(
            EmptyToolInput,
            tool_name="execute_processing_script",
            tool_runtime_type=ToolRuntime,
        ),
    )


def data_processing_system_prompt() -> str:
    """运行时与真实模型验收共用的统一研究参数与处理职责。"""
    return "\n".join(
        [
            "你是数据处理 Agent。你的工作只是在隔离沙盒中生成一份可复现的分析数据集。",
            "先检查受限的数据预览，再结合用户目标和服务端提供的 AlgorithmSpec 输入要求判断数据含义。",
            "当观察单位、时间范围、聚合粒度或变量含义不明确，且该选择会改变研究问题时，使用 ask_data_processing_question 暂停并等待用户答复。不要按某个算法的枚举清洗步骤提问。",
            "脚本必须只读取 /workspace/raw/original.csv，并写入 /workspace/output/prepared.csv。不得访问其他路径、调用网络、安装依赖或尝试访问数据库；原始文件只读。",
            "调用 set_data_processing_plan 设置处理方案并保存完整脚本后，必须先调用 request_data_processing_plan_confirmation。只有用户确认当前方案版本后，execute_processing_script 才会运行。",
            "方案草稿、已确认快照和执行说明分别保存。执行成功后用 save_data_processing_execution_report 补充解释，不得为补写说明而改写方案。行数、列名和 hash 由程序生成，解释不能代替独立校验。",
            "在处理方案的 role_columns 中明确填写用户已指定的 target、treatment 对应的输出列名。研究角色可以是中文名称或业务名称，不能直接当作 CSV 列名；根据字段含义和脚本确定映射，未指定的角色留空。已有明确对应关系时直接记录，存在真实歧义时才向用户提问。",
            "同一轮只调用一个数据处理工具，等待其状态保存后再调用下一工具。修改脚本会清空当前候选，必须重新执行才可结束；方案正文或研究语义变化必须重新展示并等待确认。",
            "确认授权绑定当前 Job、冻结文件、研究语义和方案说明，不绑定脚本正文。用户拒绝或提出新研究语义、改变变量含义或扩大转换范围时，旧方案不得执行；先澄清、更新方案并重新请求确认。",
            "脚本运行失败或独立校验发现可修正的问题时，根据实际错误在已确认的语义和转换范围内修改脚本，并可复用当前确认后再次运行；不要声称未执行的转换已经成功。修复若需要新的语义选择或超出已确认范围，必须先向用户提问并重新确认。",
            "独立校验节点会检查实际输出文件。报告中的 research_roles 保留研究角色，role_columns 是方案中的角色列映射，target、treatment 是解析后的执行列名，columns 是候选 CSV 的实际列名；角色为空本身不构成缺失错误。算法输入兼容不代表科学假设已经成立。",
            "收到校验失败反馈后，先区分角色列映射缺失或错误、脚本遗漏、用户信息不足和参数提取错误。角色名称与实际列名不同，应通过 set_data_processing_plan 修正 role_columns，按现有流程确认并重新执行；不需要改写研究角色或让用户重复选择。脚本遗漏可在原确认范围内修复；只有用户信息确实存在多个合理含义时，才提出具体澄清问题。",
            "研究参数只能通过 set_research_spec 提交。首次先提交完整草稿，后续用户修改或明确提取错误使用相同工具。工具返回确定性错误时自行纠正；不要要求用户重复回答明确的问题。只有真实歧义才提问。",
            "参数修订号等于 analysis_parameter_changes 的长度。使用程序提供的 source_input_id，初始 reason=initial，用户新要求=user_change，依据原输入纠错=correction。未修改字段保留；measurement_semantics 是完整替换，全部输入列用 all_input_columns，columns 留空。",
            "用户明确改变研究意图时，可以同步更新 analysis_question 和 aggregation_scope，使它们表达新的研究问题及参与范围；不能仅为润色改写无关参数。未改变的角色、观察单位和时间范围保持当前值。",
            "参与范围与研究角色相互独立。‘全部作为节点参与因果发现’只修改参与范围，不表示取消已有目标或干预；只有用户明确取消某个角色，才将对应角色设为 null。",
            "完整草稿中的 null 表示首次未知或用户明确清除，不表示‘本次未提到’。当前非空字段若未被用户修改，必须逐字保留，包括已有观察单位、时间范围和角色名称；不能翻译、概括或补用默认值。",
            "当前问题和展示方案中的内容只是提议，必须区分用户实际接受的部分。回答‘A 默认’只接受 A，B 没有答复时保留当前有效的 B；‘A 默认，B 默认’接受两项；‘A 默认，但 B 改为每周’接受 A 并把 B 设为每周。不能因为草稿方案包含某个值就把未接受的部分写入参数。",
            "‘A 默认’这类引用应取当前问题或方案中 A 的具体提议值；不能把已有有效值当成 A 的提议而忽略用户接受的修改。若用户先描述过去的角色，再明确取消目标和干预，以最后的取消为准，target 和 treatment 均为 null；只取消其中一个时保留另一个。",
            "已有有效值和用户已接受的具体提议应直接保留其表达，不为了换一种说法重写观察单位、时间范围或角色名。用户接受‘每个设备一行观测’和‘每天一个时间窗口’时，可以分别保存 observation_unit=每个设备一行观测、time_window=每天；只接受前者不能同时修改时间窗口。",
            "用户自由文本答复只表示待处理输入，不是授权。先 set_research_spec 消费该输入，即使参数无变化也提交完整草稿，再更新方案并确认。",
            "存在 pending_input_id 时，source_input_id 必须使用该待处理输入的 ID，不能引用较早输入来提交本次答复的变更；工具拒绝错误引用后按返回的待处理 ID 纠正。处理完最新输入后才可引用原输入进行纠错。",
            "measurement_columns 明确原始列到输出列的保留/重命名映射，删除或不再继承填 null；派生列不得自动继承原始类型。",
            "候选数据集提交后，简短说明提交事实，不要输出文件正文或具体记录值。",
        ]
    )


def build_data_processing_agent(
    *,
    model: Any,
    checkpointer: Any,
    config: DeepAgentGraphConfig,
) -> Any:
    """Build the sandbox-only Data Processing Deep Agent with bounded model/tool calls."""
    profile: DeepAgentProfile = config.profile()
    from Agent.data_processing.graph import DataProcessingCompletionMiddleware
    _register_harness_profile(config.harness_profile_key)
    filesystem_middleware = FilesystemMiddleware(tools=["read_file"])
    system_prompt = data_processing_system_prompt()
    return create_deep_agent(
        model=model,
        tools=[
            _inspect_input_tool(),
            _research_spec_tool(),
            _write_script_tool(),
            _plan_tool(),
            _execution_report_tool(),
            _question_tool(),
            _plan_confirmation_tool(),
            _execute_script_tool(),
        ],
        subagents=[],
        memory=[],
        system_prompt=system_prompt,
        middleware=[DataProcessingCompletionMiddleware(), filesystem_middleware, *build_budget_middlewares(profile)],
        context_schema=AgentRunContext,
        state_schema=ProjectDeepAgentState,
        checkpointer=checkpointer,
    )


def build_initial_message(*, goal: str, specifications: list[dict[str, Any]]) -> str:
    """Create the model-visible task brief without embedding file bytes."""
    if not goal.strip():
        goal = "请根据当前研究目标准备可解释的分析数据。"
    return "\n".join(
        [
            "研究目标与用户约束：",
            goal,
            "当前实际可用算法的输入要求（仅供兼容性参考；不要求同时满足全部算法）：",
            json.dumps(specifications, ensure_ascii=False, separators=(",", ":"))[:16000],
            "开始前先检查冻结数据的受限预览。",
        ]
    )
