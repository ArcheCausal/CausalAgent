"""只对尚待执行的工具调用应用官方次数限制。"""

from typing import Any

from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import AIMessage, ToolMessage


class PendingToolCallLimitMiddleware(ToolCallLimitMiddleware):
    """ToolStrategy 在 after_model 前已完成的调用不再占用执行预算。

    官方限制器只查看最近的 AIMessage，不检查后续回执。结构化输出已写入
    ToolMessage 时，它仍可能追加同一 call_id 的超限回执。这里只向官方
    限制器投影未完成的调用，不修改实际消息、已有回执或次数上限。
    """

    @hook_config(can_jump_to=["end"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if isinstance(message, AIMessage):
                break
        else:
            return None

        completed = {
            item.tool_call_id
            for item in messages[index + 1:]
            if isinstance(item, ToolMessage)
        }
        if not completed:
            return super().after_model(state, runtime)

        pending = [call for call in message.tool_calls if call["id"] not in completed]
        if not pending:
            return None

        pending_message = message.model_copy(update={"tool_calls": pending})
        pending_state = {
            **state,
            "messages": [*messages[:index], pending_message, *messages[index + 1:]],
        }
        return super().after_model(pending_state, runtime)
