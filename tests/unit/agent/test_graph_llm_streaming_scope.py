import ast
import unittest
from pathlib import Path


GRAPH_PATH = Path(__file__).resolve().parents[3] / "Agent" / "causal_agent" / "graph.py"


def _node_llm_bindings() -> list[tuple[str, str]]:
    """读取父图节点绑定的 LLM 变量名，约束流式能力的公开边界。"""
    module = ast.parse(GRAPH_PATH.read_text(encoding="utf-8"))
    bindings: list[tuple[str, str]] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "bind_node":
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
        event_name = keywords.get("event_node_name")
        llm = keywords.get("llm")
        if isinstance(event_name, ast.Constant) and isinstance(llm, ast.Name):
            bindings.append((str(event_name.value), llm.id))
    return bindings


def _last_binding(llm_bindings: list[tuple[str, str]], node_name: str) -> str:
    """返回同一节点名最后一次绑定的 LLM 变量名。"""
    matched = [llm for name, llm in llm_bindings if name == node_name]
    assert matched, node_name
    return matched[-1]


def _report_idle_timeouts() -> list[int]:
    """读取 report 节点注册时声明的 idle_timeout 秒数。"""
    module = ast.parse(GRAPH_PATH.read_text(encoding="utf-8"))
    timeouts: list[int] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add_node" or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or first.value != "report":
            continue
        for keyword in node.keywords:
            if keyword.arg != "timeout" or not isinstance(keyword.value, ast.Call):
                continue
            for inner in keyword.value.keywords:
                if inner.arg == "idle_timeout" and isinstance(inner.value, ast.Constant):
                    timeouts.append(inner.value.value)
    return timeouts


class TestGraphLlmStreamingScope(unittest.TestCase):
    def test_public_text_nodes_use_streaming_llm(self):
        bindings = _node_llm_bindings()

        self.assertEqual(_last_binding(bindings, "preprocess"), "llm")
        self.assertEqual(_last_binding(bindings, "postprocess"), "llm")
        self.assertEqual(_last_binding(bindings, "normal_chat"), "streaming_llm")
        self.assertEqual(_last_binding(bindings, "inquiry_answer"), "streaming_llm")

    def test_report_node_is_not_streaming_and_tolerates_slow_responses(self):
        """报告草稿是结构化输出，模型正文恒为空，因此不使用流式模型。"""
        report_bindings = [llm for name, llm in _node_llm_bindings() if name == "report"]

        self.assertTrue(report_bindings)
        self.assertEqual(set(report_bindings), {"llm"})
        self.assertEqual(_report_idle_timeouts(), [120, 120])


if __name__ == "__main__":
    unittest.main()
