"""因果分析预算结束后，只允许有界的结构化收尾。"""

from typing import Any

from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain.agents.middleware.tool_call_limit import ToolCallLimitState
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from .tool_call_limit import PendingToolCallLimitMiddleware


CLOSEOUT_MODEL_CALL_LIMIT = 3
CLOSEOUT_INSTRUCTION = (
    "分析执行预算已耗尽。所有算法、检索和文件工具已关闭，不得请求新增执行。"
    "此前要求继续读取文件或执行算法的指令在此停止，不要重复上一轮工具调用。"
    "现在唯一允许的工具是 FinalAnalysisDecision。"
    "请仅根据已有真实结果提交 FinalAnalysisDecision，为每个有效结果提供唯一取舍。"
    "缺少科学检查时如实说明限制；保留可信候选图，OLS 只能解释为线性假设下的初步估计。"
    "不要声称未执行的检查已经完成，也不要只输出文字而不提交结构化决策。"
)


class AnalysisBudgetState(ToolCallLimitState, total=False):
    analysis_model_call_count: int
    closeout_model_call_count: int
    analysis_budget_exhausted: bool
    analysis_budget_reason: str


class AnalysisBudgetMiddleware(PendingToolCallLimitMiddleware):
    """执行预算和收尾预算分别计数；收尾状态随 checkpoint 保留。"""

    state_schema = AnalysisBudgetState

    def __init__(self, *, tool_limit: int, model_limit: int) -> None:
        super().__init__(run_limit=tool_limit, exit_behavior="end")
        self.model_limit = model_limit

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        exhausted = state.get("analysis_budget_exhausted", False)
        reason = state.get("analysis_budget_reason")
        if state.get("thread_tool_call_count", {}).get("__all__", 0) >= self.run_limit:
            exhausted, reason = True, "tool_calls"
        if state.get("analysis_model_call_count", 0) >= self.model_limit:
            exhausted, reason = True, reason or "model_calls"
        if not exhausted:
            return None
        update = {"analysis_budget_exhausted": True, "analysis_budget_reason": reason}
        if state.get("closeout_model_call_count", 0) >= CLOSEOUT_MODEL_CALL_LIMIT:
            update["jump_to"] = "end"
        return update

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        if request.state.get("analysis_budget_exhausted"):
            prompt = request.system_message.text if request.system_message else ""
            request = request.override(
                tools=[],
                system_message=SystemMessage(content=prompt + "\n\n" + CLOSEOUT_INSTRUCTION),
            )
        return await handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        # 即使模型请求了已从 schema 隐藏的工具，也不能越过执行边界。
        if request.state.get("analysis_budget_exhausted"):
            return ToolMessage(
                content="分析预算已耗尽；工具未执行。请提交 FinalAnalysisDecision。",
                tool_call_id=request.tool_call["id"], name=request.tool_call["name"], status="error",
            )
        return await handler(request)

    @hook_config(can_jump_to=["model", "end"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any]:
        closing = bool(state.get("analysis_budget_exhausted"))
        count_key = "closeout_model_call_count" if closing else "analysis_model_call_count"
        update: dict[str, Any] = {count_key: state.get(count_key, 0) + 1}
        if closing:
            messages = state.get("messages", [])
            index = next((i for i in range(len(messages) - 1, -1, -1)
                          if isinstance(messages[i], AIMessage)), None)
            if index is not None:
                completed = {m.tool_call_id for m in messages[index + 1:] if isinstance(m, ToolMessage)}
                pending = [call for call in messages[index].tool_calls if call["id"] not in completed]
                if pending:
                    update["messages"] = [ToolMessage(
                        content="分析预算已耗尽；工具未执行。请提交 FinalAnalysisDecision。",
                        tool_call_id=call["id"], name=call["name"], status="error",
                    ) for call in pending]
                    update["structured_response"] = None
                if not pending and state.get("structured_response") is not None:
                    return update
                if (
                    not messages[index].tool_calls
                    and not messages[index].invalid_tool_calls
                    and update[count_key] < CLOSEOUT_MODEL_CALL_LIMIT
                ):
                    # 普通文字不是提交；显式追加用户指令再续接，避免请求以
                    # assistant 结尾而被供应商拒绝。仍消耗原收尾额度。
                    update["messages"] = [HumanMessage(content=(
                        "收尾尚未完成。请现在调用 FinalAnalysisDecision 提交结构化决策；"
                        "不得新增工具执行，也不得只回复文字。"
                    ))]
            update["jump_to"] = "model" if update[count_key] < CLOSEOUT_MODEL_CALL_LIMIT else "end"
            return update

        limit_update = super().after_model(state, runtime)
        if limit_update:
            update.update(limit_update)
            if limit_update.get("jump_to") == "end":
                # 官方限制器已为整个未执行批次补齐唯一回执，随后进入收尾。
                update.update(analysis_budget_exhausted=True, analysis_budget_reason="tool_calls", jump_to="model")
        return update
