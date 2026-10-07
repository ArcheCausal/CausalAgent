"""DirectLiNGAM runner 公共接口的行为测试。"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
from importlib.util import find_spec
import logging
import sys
import types

import pytest


def _install_causallearn_import_stubs() -> None:
    """在本机缺少 causal-learn 时，仅补齐被测模块的既有顶层导入。"""
    if find_spec("causallearn") is not None:
        return

    package_names = [
        "causallearn",
        "causallearn.search",
        "causallearn.search.ConstraintBased",
        "causallearn.search.FCMBased",
        "causallearn.utils",
        "causallearn.graph",
    ]
    for name in package_names:
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    pc_module = types.ModuleType("causallearn.search.ConstraintBased.PC")
    pc_module.pc = lambda *args, **kwargs: None
    sys.modules[pc_module.__name__] = pc_module

    cit_module = types.ModuleType("causallearn.utils.cit")
    cit_module.fisherz = object()
    sys.modules[cit_module.__name__] = cit_module

    endpoint_module = types.ModuleType("causallearn.graph.Endpoint")

    class Endpoint:
        """提供既有边格式化代码导入所需的最小端点常量。"""

        TAIL = object()
        ARROW = object()
        CIRCLE = object()

    endpoint_module.Endpoint = Endpoint
    sys.modules[endpoint_module.__name__] = endpoint_module

    graph_node_module = types.ModuleType("causallearn.graph.GraphNode")

    class GraphNode:
        """提供 causal-learn GraphNode 的最小节点名接口。"""

        def __init__(self, name):
            self.name = name

        def get_name(self):
            return self.name

    graph_node_module.GraphNode = GraphNode
    sys.modules[graph_node_module.__name__] = graph_node_module

    dag_module = types.ModuleType("causallearn.graph.Dag")

    class _DirectedEdge:
        """提供 _format_edges 所需的 causal-learn Edge 最小接口。"""

        def __init__(self, source, target):
            self.source = source
            self.target = target

        def get_node1(self):
            return self.source

        def get_node2(self):
            return self.target

        def get_endpoint1(self):
            return Endpoint.TAIL

        def get_endpoint2(self):
            return Endpoint.ARROW

        def __str__(self):
            return f"{self.source.get_name()} --> {self.target.get_name()}"

    class Dag:
        """记录 add_directed_edge 调用并返回可格式化边的最小 Dag。"""

        def __init__(self, nodes):
            self.nodes = nodes
            self.edges = []

        def add_directed_edge(self, source, target):
            self.edges.append(_DirectedEdge(source, target))

        def get_graph_edges(self):
            return list(self.edges)

    dag_module.Dag = Dag
    sys.modules[dag_module.__name__] = dag_module


_install_causallearn_import_stubs()

from Agent.causal import causalachieve


def _install_fake_lingam_module(monkeypatch, model_class) -> None:
    """在 causal-learn 第三方边界安装测试模型。"""
    lingam_module = types.ModuleType("causallearn.search.FCMBased.lingam")
    lingam_module.__version__ = "1.5.4"
    lingam_module.DirectLiNGAM = model_class
    monkeypatch.setitem(sys.modules, lingam_module.__name__, lingam_module)
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda distribution_name: "0.1.4.7",
    )


def test_direct_lingam_rejects_empty_csv() -> None:
    """空 CSV 必须通过 runner 公共接口返回稳定的输入错误。"""
    result = causalachieve.run_direct_lingam_analysis("")

    assert result["schema_version"] == "causal_discovery_v1"
    assert result["success"] is False
    assert result["algorithm"] == "direct_lingam"
    assert result["error_type"] == "InputValidationError"
    assert "CSV" in result["message"]
    assert "raw_results" not in result


@pytest.mark.parametrize(
    ("csv_data", "message_fragment"),
    [
        ("A,B\n1,2\n", "至少包含 2 行"),
        ("A\n1\n2\n", "至少包含 2 个变量"),
        (",B\n1,2\n3,4\n", "列名不能为空"),
        ("A,A\n1,2\n3,4\n", "列名必须唯一"),
        ("A,B\n1,x\n2,y\n", "必须是实数数值列"),
        ("A,B\ntrue,1\nfalse,2\n", "必须是实数数值列"),
        ("A,B\n1,\n2,3\n", "NaN"),
        ("A,B\n1,inf\n2,3\n", "无穷"),
        ("A,B\n1,2\n1,3\n", "常量列"),
    ],
)
def test_direct_lingam_rejects_invalid_csv(
    csv_data: str,
    message_fragment: str,
) -> None:
    """不满足数值数据契约的 CSV 必须在拟合前被拒绝。"""
    result = causalachieve.run_direct_lingam_analysis(csv_data)

    assert result["success"] is False
    assert result["error_type"] == "InputValidationError"
    assert message_fragment in result["message"]
    assert "raw_results" not in result


def test_direct_lingam_reports_unavailable_dependency(monkeypatch) -> None:
    """内置 LiNGAM module 不可导入时必须返回结构化能力错误。"""
    monkeypatch.setitem(
        sys.modules,
        "causallearn.search.FCMBased.lingam",
        None,
    )

    result = causalachieve.run_direct_lingam_analysis(
        "A,B\n1,3\n2,5\n3,8\n"
    )

    assert result["success"] is False
    assert result["error_type"] == "DependencyUnavailableError"
    assert "causal-learn" in result["message"]
    assert "raw_results" not in result


def test_direct_lingam_returns_structured_success_result(monkeypatch) -> None:
    """合法数据必须返回带方向、权重和因果顺序的完整成功结果。"""

    class SuccessfulDirectLiNGAM:
        """模拟 causal-learn 在固定两变量数据上的公开结果属性。"""

        def __init__(self, *, measure):
            self.measure = measure

        def fit(self, data):
            self.causal_order_ = [0, 1]
            self.adjacency_matrix_ = [[0.0, 0.0], [0.82, 0.0]]
            return self

    _install_fake_lingam_module(monkeypatch, SuccessfulDirectLiNGAM)

    result = causalachieve.run_direct_lingam_analysis(
        "A,B\n1,3\n2,5\n3,8\n"
    )

    assert result == {
        "schema_version": "causal_discovery_v1",
        "success": True,
        "algorithm": "direct_lingam",
        "implementation": {
            "package": "causal-learn",
            "version": "0.1.4.7",
            "module": "causallearn.search.FCMBased.lingam",
            "embedded_version": "1.5.4",
        },
        "parameters": {"measure": "pwling"},
        "matrix_convention": "target_to_source",
        "data": {
            "nodes": [
                {"id": "A", "label": "A"},
                {"id": "B", "label": "B"},
            ],
            "edges": [
                {
                    "from": "A",
                    "to": "B",
                    "arrows": "to",
                    "label": "0.82",
                    "weight": 0.82,
                }
            ],
        },
        "raw_results": {
            "adjacency_matrix": [[0.0, 0.0], [0.82, 0.0]],
            "edges": ["A --> B"],
            "causal_order": [0, 1],
            "causal_order_names": ["A", "B"],
        },
        "diagnostics": {
            "n_samples": 3,
            "n_features": 2,
            "warnings": [],
        },
        "message": "DirectLiNGAM 因果发现完成。",
        "analyzed_filename": None,
    }


def test_direct_lingam_converts_fit_failure_to_structured_error(monkeypatch) -> None:
    """第三方拟合异常不得越过 runner 公共边界或泄露内部文本。"""

    class FailingDirectLiNGAM:
        """模拟 causal-learn 在拟合阶段抛出内部异常。"""

        def __init__(self, *, measure):
            self.measure = measure

        def fit(self, data):
            raise RuntimeError("sensitive third-party failure")

    _install_fake_lingam_module(monkeypatch, FailingDirectLiNGAM)

    result = causalachieve.run_direct_lingam_analysis(
        "A,B\n1,3\n2,5\n3,8\n"
    )

    assert result["schema_version"] == "causal_discovery_v1"
    assert result["success"] is False
    assert result["algorithm"] == "direct_lingam"
    assert result["error_type"] == "AlgorithmExecutionError"
    assert "sensitive" not in result["message"]
    assert "raw_results" not in result


@pytest.mark.parametrize(
    ("causal_order", "adjacency_matrix"),
    [
        ([0.0, 1], [[0.0, 0.0], [0.82, 0.0]]),
        ([0, 1], [[0.0, 1.0], [0.82, 0.0]]),
        ([1, 0], [[0.0, 0.0], [0.82, 0.0]]),
    ],
)
def test_direct_lingam_rejects_invalid_base_results(
    monkeypatch,
    causal_order,
    adjacency_matrix,
) -> None:
    """因果顺序、DAG 性质或二者一致性非法时必须结构化失败。"""

    class InvalidResultDirectLiNGAM:
        """模拟 causal-learn 返回基础结果属性但内容不满足项目契约。"""

        def __init__(self, *, measure):
            self.measure = measure

        def fit(self, data):
            self.causal_order_ = causal_order
            self.adjacency_matrix_ = adjacency_matrix
            return self

    _install_fake_lingam_module(monkeypatch, InvalidResultDirectLiNGAM)

    result = causalachieve.run_direct_lingam_analysis(
        "A,B\n1,3\n2,5\n3,8\n"
    )

    assert result["success"] is False
    assert result["algorithm"] == "direct_lingam"
    assert result["error_type"] == "ResultValidationError"
    assert "raw_results" not in result


def test_direct_lingam_mcp_tool_delegates_to_runner(monkeypatch) -> None:
    """MCP server 必须暴露独立 DirectLiNGAM tool，并把 CSV 原样交给 runner。"""

    class FakeFastMCP:
        """记录被 @mcp.tool 注册的函数，保持装饰后仍可直接调用。"""

        registered_tools = []

        def __init__(self, name):
            self.name = name

        def tool(self):
            def decorator(function):
                self.registered_tools.append(function.__name__)
                return function

            return decorator

    fake_mcp_module = types.ModuleType("mcp")
    fake_server_module = types.ModuleType("mcp.server")
    fake_fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fake_fastmcp_module.FastMCP = FakeFastMCP
    monkeypatch.setitem(sys.modules, "mcp", fake_mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", fake_server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fake_fastmcp_module)

    class NullFileHandler(logging.NullHandler):
        """避免导入 MCP server 时写入本地日志文件。"""

        def __init__(self, *args, **kwargs):
            super().__init__()

    monkeypatch.setattr(logging, "FileHandler", NullFileHandler)
    sys.modules.pop("Agent.CausalAgentMCP.mcp_server", None)
    mcp_server = importlib.import_module("Agent.CausalAgentMCP.mcp_server")

    observed = {}

    def fake_runner(csv_data):
        observed["csv_data"] = csv_data
        return {"success": True, "algorithm": "direct_lingam"}

    monkeypatch.setattr(mcp_server, "run_direct_lingam_analysis", fake_runner)
    monkeypatch.setattr(
        mcp_server,
        "_load_csv",
        lambda user_id, job_id, input_user_file_id, input_object_id: "A,B\n1,2\n3,4\n",
    )
    result = asyncio.run(mcp_server.causal_direct_lingam(1, "job-1", 2, 3))

    assert "causal_direct_lingam" in FakeFastMCP.registered_tools
    assert "causal_olc" not in FakeFastMCP.registered_tools
    assert observed["csv_data"] == "A,B\n1,2\n3,4\n"
    assert result == {"success": True, "algorithm": "direct_lingam"}
