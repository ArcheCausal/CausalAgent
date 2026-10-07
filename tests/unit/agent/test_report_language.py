import unittest

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda

from Agent.Report.document import ReportDocument
from Agent.causal_agent import nodes


class _CapturingStructuredModel:
    """只替换模型提供方，保留节点真实的提示词构建和结构化输出入口。"""

    def __init__(self, captured: dict) -> None:
        self.captured = captured
        self.extra_body: dict = {}

    def model_copy(self, *, update=None, **kwargs):
        return self

    def with_structured_output(self, schema, **_kwargs):
        captured = self.captured

        async def invoke(prompt_value):
            captured["messages"] = prompt_value.to_messages()
            return schema.model_validate({"title": "报告标题", "blocks": []})

        return RunnableLambda(invoke)


class ReportLanguageTests(unittest.IsolatedAsyncioTestCase):
    def test_report_language_follows_user_request_with_chinese_fallback(self):
        instruction = nodes._report_language_instruction()

        self.assertIn("用户明确指定报告语言", instruction)
        self.assertIn("用户当前请求的主要语言", instruction)
        self.assertIn("默认使用中文", instruction)
        self.assertNotIn("请用英文回复", instruction)

    async def test_report_node_passes_language_policy_to_the_model(self):
        captured: dict = {}
        state = {
            "messages": [HumanMessage(content="请用中文生成报告")],
            "analysis_parameters": {},
            "file_summary": {},
            "preprocess_summary": "",
            "causal_analysis_result": {},
            "knowledge_base_result": {},
            "web_search_result": {},
            "postprocess_result": {},
        }

        result = await nodes.report_node(state, _CapturingStructuredModel(captured))

        system_prompt = captured["messages"][0].content
        self.assertIn("默认使用中文", system_prompt)
        self.assertIn("用户明确指定报告语言", system_prompt)
        self.assertNotIn("请用英文回复", system_prompt)
        self.assertEqual(captured["messages"][-1].content, "请用中文生成报告")
        self.assertIsInstance(result["report_document"], ReportDocument)
        self.assertEqual(result["report_document"].title, "报告标题")
