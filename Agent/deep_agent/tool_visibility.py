"""按可信运行上下文筛选本次模型请求已有的工具。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.tools import BaseTool

from .context import AgentRunContext


def is_tool_visible(tool_name: str, context: AgentRunContext | None) -> bool:
    """任一适用限制禁止时隐藏；授权不读取模型消息或 State。"""

    if tool_name == "web_evidence_search":
        return context is not None and context.web_search_enabled is True
    return True


def filter_visible_tools(
    tools: Sequence[BaseTool | dict[str, Any]],
    context: AgentRunContext | None,
) -> list[BaseTool | dict[str, Any]]:
    """仅筛选已有工具，保留顺序、对象和其他 middleware 的移除结果。"""

    visible = []
    for tool in tools:
        if isinstance(tool, Mapping):
            function = tool.get("function")
            name = (
                function.get("name", "")
                if isinstance(function, Mapping)
                else tool.get("name", "")
            )
        else:
            name = tool.name
        if is_tool_visible(name, context):
            visible.append(tool)
    return visible


class ToolVisibilityMiddleware(AgentMiddleware):
    """每次异步模型调用独立过滤，不持有 Job 开关或共享工具列表。"""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        context = getattr(request.runtime, "context", None)
        tools = filter_visible_tools(request.tools, context)
        return await handler(request.override(tools=tools))
