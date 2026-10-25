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
    require_current_plan_confirmation,
    save_candidate,
)
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
        semantic_identity = extend_data_processing_semantic_identity(
            _semantic_identity(context, state),
            answer,
        )
        return Command(
            update={
                "messages": [
                    _tool_message(
                        runtime,
                        name="ask_data_processing_question",
                        content=json.dumps(
                            {"status": "answered", "answer": answer_text[:4000]},
                            ensure_ascii=False,
                        ),
                    )
                ],
                "data_processing_semantic_identity": semantic_identity,
                "data_processing_confirmed_plan_version": None,
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
        if matches is None:
            semantic_identity = extend_data_processing_semantic_identity(
                semantic_identity,
                answer,
            )
        return Command(
            update={
                "messages": [
                    _tool_message(
                        runtime,
                        name="request_data_processing_plan_confirmation",
                        content=json.dumps(
                            {"status": status, "answer": answer_text[:4000]},
                            ensure_ascii=False,
                        ),
                    )
                ],
                "data_processing_semantic_identity": semantic_identity,
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
        if state.get("data_processing_confirmed_plan_version") != plan_version:
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
            "独立校验节点会检查实际输出文件。收到校验报告后，根据报告修复脚本或向用户澄清。算法输入兼容不代表科学假设已经成立。",
            "候选数据集提交后，简短说明提交事实，不要输出文件正文或具体记录值。",
        ]
    )
    return create_deep_agent(
        model=model,
        tools=[
            _inspect_input_tool(),
            _write_script_tool(),
            _description_tool(),
            _question_tool(),
            _plan_confirmation_tool(),
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
