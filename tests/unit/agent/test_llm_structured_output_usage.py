from pathlib import Path


STRUCTURED_CALLERS = (
    "Agent/causal_agent/nodes.py",
    "Agent/tool_node/rag_questions.py",
    "Agent/knowledge_base/query_rag.py",
    "Agent/Postprocessing/cycles_check/fix_cycles.py",
    "Agent/Postprocessing/evaluate_edge/evaluate_edge_llm.py",
)


def test_shared_adapter_exposes_sync_and_async_entry_points():
    """公共调用器同时提供同步与异步入口，业务代码不自行拼装结构化调用。"""
    from Agent import llm_structured_output

    assert callable(llm_structured_output.invoke_structured)
    assert callable(llm_structured_output.ainvoke_structured)
    assert "asyncio.run" not in Path("Agent/llm_structured_output.py").read_text(encoding="utf-8")


def test_all_business_structured_outputs_use_the_shared_invokers():
    """全部 Pydantic 业务调用点使用统一入口，旧工具兼容模块不再被引用。"""
    combined = "\n".join(Path(path).read_text(encoding="utf-8") for path in STRUCTURED_CALLERS)

    assert "invoke_structured(" in combined
    assert "ainvoke_structured(" in combined
    assert "with_compatible_structured_output" not in combined
    assert "JsonOutputParser" not in combined
    assert "Agent.tool_node.structured_output" not in combined
    assert "include_raw=True" not in combined


def test_structured_output_failure_exposes_only_a_safe_cause_code():
    """结构化输出失败只归类出稳定代码，不把异常正文带进调用方。"""
    import json

    from Agent.llm_structured_output import (
        NoToolCallError,
        StructuredOutputError,
        classify_structured_output_cause,
    )

    class ValidationError(Exception):
        """用类名模拟 pydantic 校验失败。"""

    assert classify_structured_output_cause(ValidationError("bad")) == "schema_invalid"
    assert (
        classify_structured_output_cause(json.JSONDecodeError("bad", "doc", 0))
        == "json_invalid"
    )
    assert classify_structured_output_cause(RuntimeError("boom")) == "unknown"
    assert (
        classify_structured_output_cause(NoToolCallError("empty"))
        == "tool_call_invalid"
    )

    error = StructuredOutputError(
        node_name="report",
        schema_name="ReportDraft",
        cause=ValidationError("doc.value: Input should be a valid integer"),
    )

    assert error.original_exception_type == "ValidationError"
    assert error.safe_cause_code == "schema_invalid"
    assert "Input should be a valid integer" not in str(error)
