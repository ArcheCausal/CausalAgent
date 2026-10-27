"""Deep Agent tools for preparing one Job's analysis dataset in OpenSandbox."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

from deepagents import create_deep_agent
from deepagents.middleware import FilesystemMiddleware
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.prompts import ChatPromptTemplate
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
    read_parameter_answer,
    require_current_plan_confirmation,
    save_candidate,
)
from Agent.data_processing.parameters import (
    ParameterExtraction, apply_parameter_patch, extraction_patch,
    parameter_identity, sandbox_request_id, validate_parameter_extraction,
)
from Agent.llm_structured_output import ainvoke_structured
from Agent.data_processing.plans import (
    build_processing_plan_version,
    extend_data_processing_semantic_identity,
    plan_confirmation_matches,
)
from Database.agent_connect import require_frozen_file_for_job


class EmptyToolInput(ContractModel):
    """工具无模型参数时使用的空输入契约。"""


class ScriptInput(ContractModel):
    """完整、可重放的数据处理 Python 脚本。"""

    script: str = Field(min_length=1, max_length=16000)


class DescriptionInput(ContractModel):
    """候选数据集的语义说明。"""

    summary: str = Field(min_length=1, max_length=1200)
    observation_unit: str = Field(min_length=1, max_length=500)
    transformations: tuple[str, ...] = Field(min_length=1, max_length=12)
    variable_semantics: dict[str, str] = Field(min_length=1, max_length=128)
    limitations: tuple[str, ...] = Field(default_factory=tuple, max_length=12)


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


def _context(runtime: Any) -> AgentRunContext:
    context = getattr(runtime, "context", None)
    if not isinstance(context, AgentRunContext):
        raise RuntimeError("data-processing tools require the worker runtime context")
    if context.sandbox_provider is None or context.trusted_identity is None:
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
                "data_processing_draft_script": payload.script,
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


def _description_tool() -> Any:
    async def call(runtime: Any, **kwargs: Any) -> Any:
        payload = DescriptionInput.model_validate(kwargs)
        metadata = payload.model_dump(mode="json")
        state = getattr(runtime, "state", None)
        confirmed_plan_version = (
            state.get("data_processing_confirmed_plan_version")
            if isinstance(state, dict)
            and state.get("data_processing_metadata") == metadata
            else None
        )
        return Command(
            update={
                "messages": [
                    _tool_message(
                        runtime,
                        name="describe_prepared_dataset",
                        content="数据集语义说明已保存到当前 Job checkpoint。",
                    )
                ],
                "data_processing_metadata": metadata,
                "data_processing_confirmed_plan_version": confirmed_plan_version,
            }
        )

    call.__annotations__["runtime"] = ToolRuntime
    return StructuredTool.from_function(
        coroutine=call,
        name="describe_prepared_dataset",
        description=(
            "记录候选数据集的观察单位、字段含义、已执行转换和限制。"
            "只写你能从用户说明或实际脚本确认的内容；不要推测字段含义。"
        ),
        args_schema=with_tool_runtime_schema(
            DescriptionInput,
            tool_name="describe_prepared_dataset",
            tool_runtime_type=ToolRuntime,
        ),
    )


def _parameter_extraction_prompt() -> ChatPromptTemplate:
    """为两种答复入口提供同一份参数职责、确认语义和证据说明要求。"""
    return ChatPromptTemplate.from_messages([
        ("system", "你只提取用户此次明确补充或修正的研究参数。未提及字段不要输出；明确清空时 value 为 null。"
         "target 表示用户指定的目标或结果变量；treatment 表示用户指定的处理或干预变量。"
         "分析范围、算法名称、处理要求和完整指令不能作为这两个字段的值。用户使用的变量名称不必与 CSV 列名相同，提取阶段不进行列名成员检查，也不自行映射名称。"
         "结合当前问题、本次答复和已有参数理解角色修订。明确指定时更新，未提及时保留，明确取消时只清空取消范围内的角色；不能因为答复没有角色名就清空已有角色。"
         "全部变量参与结构发现可以与已指定的目标或干预角色并存。仅扩大参与分析的变量范围不等于撤销角色，不能据此输出角色的 null 修改；必须有本次明确取消该角色的依据。"
         "例如，‘目标变量是收入’只更新 target 为‘收入’；‘全部作为节点参与因果发现’只说明分析范围，不生成角色值，也不无条件清空已有角色；"
         "‘不指定目标变量’或‘取消之前的目标，只做无目标的结构发现’明确清空 target，但不自动清空 treatment。"
         "value 保存用户本次指定、修正或接受的参数值，可以结合当前问题和已有参数理解。用户确认当前问题中的某个提议后，可以提取该提议中的具体参数；未接受的建议不能作为修改依据。"
         "updates 中每项修改必须包含 field、value、evidence，包含 value 为 null 的修改。"
         "measurement_semantics 只输出用户明确修订的列声明，代码会保留其他列；每条声明包含 column、kind、evidence，kind 只能为 count/ordinal/category/continuous/binary/unknown。"
         "外层 evidence 解释本次答复为何支持这项参数修改，可以引用答复原文，也可以概括用户的确认关系；它不要求逐字出现在答复中。测量声明内层 evidence 解释对应列的测量类型依据，允许与外层 evidence 复用说明；两层 evidence 都必须是非空文本。不要根据字段名推测语义。观察单位、时间范围、聚合范围分别写入 observation_unit、time_window、aggregation_scope。"
         "完整确认、部分确认和修改部分提议都只提取用户接受或修改的内容：如果当前问题提出 A 和 B，用户回复‘A 默认，B 默认’，提取 A、B 的具体提议值；用户回复‘A 默认’，只提取 A；用户回复‘A 默认，但 B 改为每周’，提取接受后的 A 和修改后的 B。未接受的提议不作为修改依据。"
         "如有实际歧义，设置 needs_clarification 和具体问题，不提交猜测。不增加额外语义审核调用。仅在处理含义或范围改变时设置 processing_scope_changed，脚本实现修正不改变范围。"),
        ("human", "结构示例：用户答复为‘次数是计数变量’时，返回完整结构：{example}。示例只说明结构，不能当作当前答复的证据。"),
        ("human", "当前参数：{parameters}\n当前问题：{question}\n用户答复：{answer}"),
    ]).partial(example=json.dumps({
        "updates": [{"field": "measurement_semantics",
                     "value": [{"column": "次数", "kind": "count", "evidence": "次数是计数变量"}],
                     "evidence": "次数是计数变量"}],
        "needs_clarification": False, "clarification_question": "",
        "processing_scope_changed": True,
    }, ensure_ascii=False))


async def _apply_user_answer(
    runtime: Any, *, model: Any, question_id: str, question: str, answer: Any,
) -> dict[str, Any]:
    """两个答复入口共用同一结构化提取及确定性提交边界。"""
    context = _context(runtime)
    state = runtime.state
    identity = context.trusted_identity
    trusted = await asyncio.to_thread(
        read_parameter_answer, context, question_id=question_id, answer=answer,
    )
    await context.check_after_call()
    source_input_id = trusted["source_input_id"]
    answer_text = trusted["answer"]
    if not isinstance(answer_text, str):
        answer_text = json.dumps(answer_text, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    answer_digest = hashlib.sha256(answer_text.encode("utf-8")).hexdigest()
    receipts = dict(state.get("data_processing_answer_receipts") or {})
    receipt_key = str(source_input_id)
    if receipt_key in receipts:
        if receipts[receipt_key]["answer_digest"] != answer_digest:
            raise RuntimeError("replayed parameter answer conflicts with its trusted input")
        return {}
    extracted = await ainvoke_structured(
        llm=model, schema=ParameterExtraction, prompt=_parameter_extraction_prompt(),
        inputs={"parameters": json.dumps(state["analysis_parameters"], ensure_ascii=False),
                "question": question, "answer": answer_text},
        node_name="data_processing_parameter_extraction",
        retry_with_validation_feedback=True,
        result_validator=validate_parameter_extraction,
    )
    await context.check_after_call()
    patch = extraction_patch(extracted, current_parameters=state["analysis_parameters"])
    parameters, history = apply_parameter_patch(
        state["analysis_parameters"], state["analysis_parameter_changes"], patch=patch,
        job_id=str(identity.job_id), source_input_id=source_input_id,
    )
    semantic_identity = _semantic_identity(context, state)
    changed = parameter_identity(parameters) != parameter_identity(state["analysis_parameters"])
    if changed or extracted.processing_scope_changed:
        semantic_identity = extend_data_processing_semantic_identity(
            semantic_identity, {"source_input_id": source_input_id, "parameters": parameter_identity(parameters),
                                "scope_answer_digest": answer_digest},
        )
    receipts[receipt_key] = {"answer_digest": answer_digest}
    return {
        "analysis_parameters": parameters, "analysis_parameter_changes": history,
        "data_processing_semantic_identity": semantic_identity,
        "data_processing_confirmed_plan_version": (None if changed or extracted.processing_scope_changed
            or extracted.needs_clarification else state.get("data_processing_confirmed_plan_version")),
        "data_processing_parameter_clarification_required": extracted.needs_clarification,
        "data_processing_parameter_clarification_question": extracted.clarification_question,
        "data_processing_answer_receipts": receipts,
    }


def _question_tool(*, model: Any) -> Any:
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
        parameter_update = await _apply_user_answer(
            runtime, model=model, question_id=call_id, question=payload.question, answer=answer,
        )
        return Command(
            update={
                "messages": [
                    _tool_message(
                        runtime,
                        name="ask_data_processing_question",
                        content=json.dumps(
                            {"status": "answered", "answer": answer_text[:4000],
                             "clarification_question": parameter_update.get("data_processing_parameter_clarification_question", "")},
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


def _plan_confirmation_tool(*, model: Any) -> Any:
    async def call(runtime: Any, **_: Any) -> Any:
        context = _context(runtime)
        await context.ensure_active()
        identity = context.trusted_identity
        state = getattr(runtime, "state", None)
        if identity is None or not isinstance(state, dict):
            raise RuntimeError("data-processing plan state is unavailable")
        if state.get("data_processing_parameter_clarification_required"):
            return _tool_message(runtime, name="request_data_processing_plan_confirmation",
                                 content="请先使用 ask_data_processing_question 澄清：" +
                                 state.get("data_processing_parameter_clarification_question", "研究语义不明确"))
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
        request = DataProcessingPlanConfirmationRequest(
            prompt="请检查这份处理方案。确认后系统才会执行脚本；如需修改，请在下方说明。",
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
            parameter_update = await _apply_user_answer(
                runtime, model=model, question_id=call_id, question=plan_question, answer=answer,
            )
        return Command(
            update={
                "messages": [
                    _tool_message(
                        runtime,
                        name="request_data_processing_plan_confirmation",
                        content=json.dumps(
                            {"status": status, "answer": answer_text[:4000],
                             "clarification_question": parameter_update.get("data_processing_parameter_clarification_question", "")},
                            ensure_ascii=False,
                        ),
                    )
                ],
                "data_processing_semantic_identity": semantic_identity,
                **parameter_update,
                "data_processing_confirmed_plan_version": confirmation,
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
        return _tool_message(
            runtime,
            name="inspect_frozen_dataset",
            content=json.dumps(
                {"status": "inspected", "preview": preview},
                ensure_ascii=False,
            ),
        )

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
        if state.get("data_processing_parameter_clarification_required") or state.get("data_processing_confirmed_plan_version") != plan_version:
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
                return _tool_message(
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


def build_data_processing_agent(
    *,
    model: Any,
    checkpointer: Any,
    config: DeepAgentGraphConfig,
) -> Any:
    """Build the sandbox-only Data Processing Deep Agent with bounded model/tool calls."""
    profile: DeepAgentProfile = config.profile()
    _register_harness_profile(config.harness_profile_key)
    filesystem_middleware = FilesystemMiddleware(tools=["read_file"])
    system_prompt = "\n".join(
        [
            "你是数据处理 Agent。你的工作只是在隔离沙盒中生成一份可复现的分析数据集。",
            "先检查受限的数据预览，再结合用户目标和服务端提供的 AlgorithmSpec 输入要求判断数据含义。",
            "当观察单位、时间范围、聚合粒度或变量含义不明确，且该选择会改变研究问题时，使用 ask_data_processing_question 暂停并等待用户答复。不要按某个算法的枚举清洗步骤提问。",
            "脚本必须只读取 /workspace/raw/original.csv，并写入 /workspace/output/prepared.csv。不得访问其他路径、调用网络、安装依赖或尝试访问数据库；原始文件只读。",
            "调用 describe_prepared_dataset 并保存完整脚本后，必须先调用 request_data_processing_plan_confirmation。只有用户确认当前方案版本后，execute_processing_script 才会运行。",
            "确认授权绑定当前 Job、冻结文件、研究语义和方案说明，不绑定脚本正文。用户拒绝或提出新研究语义、改变变量含义或扩大转换范围时，旧方案不得执行；先澄清、更新方案并重新请求确认。",
            "脚本运行失败或独立校验发现可修正的问题时，根据实际错误在已确认的语义和转换范围内修改脚本，并可复用当前确认后再次运行；不要声称未执行的转换已经成功。修复若需要新的语义选择或超出已确认范围，必须先向用户提问并重新确认。",
            "独立校验节点会检查实际输出文件。报告中的 target、treatment 是本次校验使用的参数，columns 是候选 CSV 的实际列名；角色为空本身不构成缺失错误。算法输入兼容不代表科学假设已经成立。",
            "收到校验失败反馈后，先区分脚本遗漏已确认方案要求的字段、用户信息不足和参数提取错误。脚本遗漏可在原确认范围内修复；只有用户信息确实存在多个合理含义时，才提出具体澄清问题。",
            "若用户已有明确答复与保存的角色参数矛盾，应识别为参数提取问题，不得解释成校验器强制要求角色，也不得要求用户再次作同一个研究选择。现有工具不能直接修订已提交参数，不得声称已经修复参数、忽略校验或默认清空角色继续执行；遵守现有重试上限，仍失败时如实说明。",
            "候选数据集提交后，简短说明提交事实，不要输出文件正文或具体记录值。",
        ]
    )
    return create_deep_agent(
        model=model,
        tools=[
            _inspect_input_tool(),
            _write_script_tool(),
            _description_tool(),
            _question_tool(model=model),
            _plan_confirmation_tool(model=model),
            _execute_script_tool(),
        ],
        subagents=[],
        memory=[],
        system_prompt=system_prompt,
        middleware=[filesystem_middleware, *build_budget_middlewares(profile)],
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
            goal[:6000],
            "当前实际可用算法的输入要求（仅供兼容性参考；不要求同时满足全部算法）：",
            json.dumps(specifications, ensure_ascii=False, separators=(",", ":"))[:16000],
            "开始前先检查冻结数据的受限预览。",
        ]
    )
