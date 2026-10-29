"""DeepSeek Responses 思考模型的工具选择兼容。"""

from typing import Any

from langchain_openai import ChatOpenAI


class DeepSeekResponsesChatOpenAI(ChatOpenAI):
    """思考时使用 auto，工具 schema 与业务完成门禁仍由框架校验。

    DeepSeek 拒绝思考模式下的 required 和指定工具；LangChain ToolStrategy
    会主动绑定 any（随后映射为 required），因此在模型绑定边界处理此差异。
    """

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
        effort = (self.reasoning or {}).get("effort")
        if effort not in (None, "none") and tool_choice not in (None, "auto", "none", False):
            tool_choice = "auto"
        return super().bind_tools(tools, tool_choice=tool_choice, **kwargs)
