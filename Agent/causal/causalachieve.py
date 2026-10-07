import numpy as np
import pandas as pd
import csv
import importlib
import importlib.metadata
import io

# CDMIR: OLC algorithm 
# 使用延迟导入 + 优雅降级，避免未安装时启动报错
_CDMIR_AVAILABLE = False
try:
    from cdmir.discovery.funtional_based.one_component.olc import olc
    _CDMIR_AVAILABLE = True
except ImportError:
    olc = None  # 占位，防止后续代码引用报错

DIRECT_LINGAM_SCHEMA_VERSION = "causal_discovery_v1"
DIRECT_LINGAM_ALGORITHM = "direct_lingam"
DIRECT_LINGAM_MEASURE = "pwling"


class _DirectLiNGAMInputError(ValueError):
    """表示可安全返回给调用方的 DirectLiNGAM 输入错误。"""


def _direct_lingam_failure(error_type: str, message: str) -> dict:
    """构造 DirectLiNGAM runner 的稳定失败结果。"""
    return {
        "schema_version": DIRECT_LINGAM_SCHEMA_VERSION,
        "success": False,
        "algorithm": DIRECT_LINGAM_ALGORITHM,
        "error_type": error_type,
        "message": message,
    }


def _parse_direct_lingam_csv(csv_data_string: str) -> tuple[np.ndarray, list[str]]:
    """解析并验证 DirectLiNGAM 所需的连续数值 CSV。"""
    try:
        raw_header = next(csv.reader(io.StringIO(csv_data_string)))
    except (csv.Error, StopIteration) as exc:
        raise _DirectLiNGAMInputError("CSV 数据无法解析或不包含表头。") from exc

    if raw_header:
        raw_header[0] = raw_header[0].lstrip("\ufeff")
    if any(not str(name).strip() for name in raw_header):
        raise _DirectLiNGAMInputError("CSV 列名不能为空。")
    if len(set(raw_header)) != len(raw_header):
        raise _DirectLiNGAMInputError("CSV 列名必须唯一。")

    try:
        frame = pd.read_csv(io.StringIO(csv_data_string))
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeError) as exc:
        raise _DirectLiNGAMInputError(f"CSV 数据无法解析：{exc}") from exc

    if frame.shape[1] < 2:
        raise _DirectLiNGAMInputError("CSV 至少包含 2 个变量。")
    if frame.shape[0] < 2:
        raise _DirectLiNGAMInputError("CSV 至少包含 2 行数据。")

    invalid_columns = [
        str(name)
        for name in frame.columns
        if pd.api.types.is_bool_dtype(frame[name].dtype)
        or not pd.api.types.is_numeric_dtype(frame[name].dtype)
    ]
    if invalid_columns:
        raise _DirectLiNGAMInputError(
            "DirectLiNGAM 的所有变量必须是实数数值列："
            + "、".join(invalid_columns)
        )

    data = frame.to_numpy(dtype=float, copy=True)
    if np.isnan(data).any():
        raise _DirectLiNGAMInputError("CSV 数据不能包含 NaN 或缺失值。")
    if np.isinf(data).any():
        raise _DirectLiNGAMInputError("CSV 数据不能包含正无穷或负无穷。")

    constant_columns = [
        str(frame.columns[index])
        for index in range(data.shape[1])
        if np.ptp(data[:, index]) == 0
    ]
    if constant_columns:
        raise _DirectLiNGAMInputError(
            "CSV 数据不能包含常量列：" + "、".join(constant_columns)
        )

    return data, [str(name) for name in frame.columns]


def _load_direct_lingam_module():
    """延迟导入 causal-learn 内置 LiNGAM module。"""
    return importlib.import_module("causallearn.search.FCMBased.lingam")


def _load_pc_dependencies():
    """延迟导入 PC 算法及其独立性检验依赖。"""
    pc_module = importlib.import_module("causallearn.search.ConstraintBased.PC")
    cit_module = importlib.import_module("causallearn.utils.cit")
    return pc_module.pc, cit_module.fisherz


def _load_endpoint_class():
    """延迟导入 causal-learn 的 Endpoint 枚举。"""
    endpoint_module = importlib.import_module("causallearn.graph.Endpoint")
    return endpoint_module.Endpoint


def _load_causal_graph_classes():
    """延迟导入构造统一 causal-learn Dag 所需的图类。"""
    dag_module = importlib.import_module("causallearn.graph.Dag")
    graph_node_module = importlib.import_module("causallearn.graph.GraphNode")
    return dag_module.Dag, graph_node_module.GraphNode


def _normalize_direct_lingam_causal_order(raw_order, feature_count: int) -> list[int]:
    """校验 DirectLiNGAM causal_order_ 是严格的变量索引排列。"""
    try:
        raw_indices = list(raw_order)
    except TypeError as exc:
        raise ValueError("causal_order_ 必须是可迭代的变量索引序列。") from exc

    if len(raw_indices) != feature_count:
        raise ValueError("causal_order_ 的长度与变量数不一致。")

    causal_order = []
    for index in raw_indices:
        if isinstance(index, (bool, np.bool_)) or not isinstance(index, (int, np.integer)):
            raise ValueError("causal_order_ 只能包含整数变量索引。")
        causal_order.append(int(index))

    if sorted(causal_order) != list(range(feature_count)):
        raise ValueError("causal_order_ 不是变量索引的完整排列。")

    return causal_order


def _normalize_direct_lingam_adjacency(raw_matrix, feature_count: int) -> np.ndarray:
    """校验 DirectLiNGAM adjacency_matrix_ 是有限、无自环的方阵。"""
    try:
        adjacency_matrix = np.asarray(raw_matrix, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("adjacency_matrix_ 必须是实数矩阵。") from exc

    if adjacency_matrix.shape != (feature_count, feature_count):
        raise ValueError("adjacency_matrix_ 的维度与变量数不一致。")
    if not np.isfinite(adjacency_matrix).all():
        raise ValueError("adjacency_matrix_ 包含非有限系数。")
    if not np.allclose(np.diag(adjacency_matrix), 0.0):
        raise ValueError("adjacency_matrix_ 对角线必须为零。")

    return adjacency_matrix


def _is_direct_lingam_dag(adjacency_matrix: np.ndarray) -> bool:
    """按 B[target, source] 的非零系数判断 DirectLiNGAM 结果是否为 DAG。"""
    feature_count = adjacency_matrix.shape[0]
    outgoing_edges = {index: [] for index in range(feature_count)}
    indegrees = [0] * feature_count

    for target_index, source_index in np.argwhere(adjacency_matrix != 0.0):
        if target_index == source_index:
            return False
        outgoing_edges[int(source_index)].append(int(target_index))
        indegrees[int(target_index)] += 1

    queue = [index for index, indegree in enumerate(indegrees) if indegree == 0]
    visited_count = 0
    while queue:
        source_index = queue.pop(0)
        visited_count += 1
        for target_index in outgoing_edges[source_index]:
            indegrees[target_index] -= 1
            if indegrees[target_index] == 0:
                queue.append(target_index)

    return visited_count == feature_count


def _assert_causal_order_matches_adjacency(
    causal_order: list[int],
    adjacency_matrix: np.ndarray,
) -> None:
    """确认 DirectLiNGAM 的因果顺序与带权邻接矩阵方向一致。"""
    order_position = {
        node_index: position
        for position, node_index in enumerate(causal_order)
    }
    for target_index, source_index in np.argwhere(adjacency_matrix != 0.0):
        if order_position[int(source_index)] >= order_position[int(target_index)]:
            raise ValueError("causal_order_ 与 adjacency_matrix_ 的有向边顺序不一致。")


def _direct_lingam_dag_edges(
    adjacency_matrix: np.ndarray,
    node_names: list[str],
) -> tuple[list[dict], list[str]]:
    """把 DirectLiNGAM 非零系数边写入 causal-learn Dag 并复用统一 edge formatter。"""
    Dag, GraphNode = _load_causal_graph_classes()
    graph_nodes = [GraphNode(name) for name in node_names]
    dag = Dag(graph_nodes)

    weight_by_edge = {}
    for target_index, source_index in np.argwhere(adjacency_matrix != 0.0):
        source_name = node_names[int(source_index)]
        target_name = node_names[int(target_index)]
        coefficient = float(adjacency_matrix[target_index, source_index])
        dag.add_directed_edge(graph_nodes[int(source_index)], graph_nodes[int(target_index)])
        weight_by_edge[(source_name, target_name)] = coefficient

    dag_edges = dag.get_graph_edges()
    edges_for_vis = _format_edges(dag_edges)
    for edge in edges_for_vis:
        edge_key = (edge.get("from"), edge.get("to"))
        if edge_key not in weight_by_edge:
            continue
        coefficient = weight_by_edge[edge_key]
        edge["label"] = format(coefficient, ".6g")
        edge["weight"] = coefficient

    return edges_for_vis, [str(edge).strip() for edge in dag_edges]


def _direct_lingam_success(
    data: np.ndarray,
    node_names: list[str],
    model,
    lingam_module,
) -> dict:
    """把 DirectLiNGAM 公开结果属性转换为项目成功契约。"""
    feature_count = len(node_names)
    causal_order = _normalize_direct_lingam_causal_order(
        model.causal_order_,
        feature_count,
    )
    adjacency_matrix = _normalize_direct_lingam_adjacency(
        model.adjacency_matrix_,
        feature_count,
    )
    if not _is_direct_lingam_dag(adjacency_matrix):
        raise ValueError("adjacency_matrix_ 按非零系数解释后不是 DAG。")
    _assert_causal_order_matches_adjacency(causal_order, adjacency_matrix)

    nodes_for_vis = [{"id": name, "label": name} for name in node_names]
    edges_for_vis, dag_edge_strings = _direct_lingam_dag_edges(
        adjacency_matrix,
        node_names,
    )

    try:
        causal_learn_version = importlib.metadata.version("causal-learn")
    except importlib.metadata.PackageNotFoundError:
        causal_learn_version = "unknown"

    return {
        "schema_version": DIRECT_LINGAM_SCHEMA_VERSION,
        "success": True,
        "algorithm": DIRECT_LINGAM_ALGORITHM,
        "implementation": {
            "package": "causal-learn",
            "version": causal_learn_version,
            "module": "causallearn.search.FCMBased.lingam",
            "embedded_version": str(getattr(lingam_module, "__version__", "unknown")),
        },
        "parameters": {"measure": DIRECT_LINGAM_MEASURE},
        "matrix_convention": "target_to_source",
        "data": {
            "nodes": nodes_for_vis,
            "edges": edges_for_vis,
        },
        "raw_results": {
            "adjacency_matrix": adjacency_matrix.tolist(),
            "edges": dag_edge_strings,
            "causal_order": causal_order,
            "causal_order_names": [node_names[index] for index in causal_order],
        },
        "diagnostics": {
            "n_samples": int(data.shape[0]),
            "n_features": int(data.shape[1]),
            "warnings": [],
        },
        "message": "DirectLiNGAM 因果发现完成。",
        "analyzed_filename": None,
    }


def run_direct_lingam_analysis(csv_data_string: str) -> dict:
    """从 CSV 字符串执行 DirectLiNGAM，并返回结构化分析结果。"""
    if not isinstance(csv_data_string, str) or not csv_data_string.strip():
        return _direct_lingam_failure(
            "InputValidationError",
            "CSV 数据不能为空。",
        )

    try:
        data, node_names = _parse_direct_lingam_csv(csv_data_string)
    except _DirectLiNGAMInputError as exc:
        return _direct_lingam_failure("InputValidationError", str(exc))

    try:
        lingam_module = _load_direct_lingam_module()
    except ImportError:
        return _direct_lingam_failure(
            "DependencyUnavailableError",
            "DirectLiNGAM 不可用：causal-learn 内置 LiNGAM module 无法加载。",
        )

    direct_lingam_class = getattr(lingam_module, "DirectLiNGAM", None)
    if direct_lingam_class is None:
        return _direct_lingam_failure(
            "DependencyUnavailableError",
            "DirectLiNGAM 不可用：当前 causal-learn 未提供 DirectLiNGAM。",
        )

    try:
        model = direct_lingam_class(measure=DIRECT_LINGAM_MEASURE)
        model.fit(data)
    except Exception:
        return _direct_lingam_failure(
            "AlgorithmExecutionError",
            "DirectLiNGAM 执行失败，请检查数据是否满足算法假设。",
        )

    try:
        return _direct_lingam_success(data, node_names, model, lingam_module)
    except Exception:
        return _direct_lingam_failure(
            "ResultValidationError",
            "DirectLiNGAM 返回了无法解析的结果。",
        )


def is_cdmir_available() -> bool:
    """检查 CDMIR 库是否可用"""
    return _CDMIR_AVAILABLE

def _format_edges(causallearn_edges):
    """将 Causal-learn 的边对象转换为 vis-network 兼容的格式。"""
    Endpoint = _load_endpoint_class()
    formatted_edges = []
    for edge in causallearn_edges:
        node1 = edge.get_node1()
        node2 = edge.get_node2()
        end1 = edge.get_endpoint1()
        end2 = edge.get_endpoint2()
        
        vis_edge = {
            'from': node1.get_name(),
            'to': node2.get_name(),
            # 使用边的字符串表示作为标签，方便调试
            'label': str(edge)
        }

        # 根据端点类型为 vis.js 设置箭头
        arrows = []
        if end2 == Endpoint.ARROW:
            arrows.append('to')
        if end1 == Endpoint.ARROW:
            arrows.append('from')
        
        if arrows:
            vis_edge['arrows'] = ','.join(arrows)

        # 如果边包含圆圈（代表不确定性），则使用虚线表示
        if end1 == Endpoint.CIRCLE or end2 == Endpoint.CIRCLE:
            vis_edge['dashes'] = True

        formatted_edges.append(vis_edge)
    return formatted_edges


def run_pc_analysis(csv_data_string: str, alpha: float = 0.05) -> dict:
    """
    对CSV格式的字符串数据运行PC因果发现算法。
    
    Returns:
        一个包含分析结果的字典，包括节点、边和邻接矩阵，
        用于在前端动态生成图表。
    """
    try:
        pc, fisherz = _load_pc_dependencies()
        string_io = io.StringIO(csv_data_string)
        df = pd.read_csv(string_io)
        
        if df.empty or len(df.columns) < 2:
            msg = "错误：CSV数据为空或列数少于2，无法进行因果分析。"
            return {"success": False, "message": msg}

        data = df.to_numpy()
        node_names = df.columns.tolist()

        cg = pc(data=data, alpha=alpha, indep_test=fisherz, node_names=node_names)

        # 提取结果
        edges = cg.G.get_graph_edges()
        
        # 准备前端需要的数据格式
        nodes_for_vis = [{'id': name, 'label': name} for name in node_names]
        edges_for_vis = _format_edges(edges)
        
        return {
            "success": True,
            "message": "因果分析成功完成。",
            "data": {
                "nodes": nodes_for_vis,
                "edges": edges_for_vis,
            },
            "raw_results": {
                "edges": [str(edge).strip() for edge in edges],
                "adjacency_matrix": cg.G.graph.tolist()
            },
            "analyzed_filename": None
        }

    except Exception as e:
        error_message = f"执行因果分析时发生错误: {e}"
        return {"success": False, "message": error_message}


def _format_olc_edges(adjacency_matrix: np.ndarray, coefficient_matrix: np.ndarray,
                       node_names: list) -> list:
    """
    将 OLC 算法的邻接矩阵转换为 vis-network 兼容的边格式。

    参数:
        adjacency_matrix: OLC 返回的邻接矩阵
            - 0: 无边
            - 1: 有向边 (row → column)
            - 2: 无向边（双向）
        coefficient_matrix: OLC 返回的系数矩阵，表示因果效应强度
        node_names: 所有节点名称（包括观测变量 + 潜变量）

    返回:
        vis-network 格式的边列表
    """
    Endpoint = _load_endpoint_class()
    formatted_edges = []
    n = adjacency_matrix.shape[0]

    # 用于记录已处理的无向边，避免重复添加
    processed_undirected = set()

    for i in range(n):
        for j in range(n):
            if i == j:
                continue

            edge_type = adjacency_matrix[i, j]

            if edge_type == 0:
                # 无边，跳过
                continue

            elif edge_type == 1:
                # 有向边: i → j
                coef = coefficient_matrix[j, i]  # 注意：系数矩阵是 [j, i] 存储 i→j 的系数
                vis_edge = {
                    'from': node_names[i],
                    'to': node_names[j],
                    'arrows': 'to',
                    'label': f'{coef:.3f}' if coef != 0 else ''
                }
                formatted_edges.append(vis_edge)

            elif edge_type == 2:
                # 无向边（双向）: i -- j
                # 为避免重复，只在 i < j 时添加
                edge_key = (min(i, j), max(i, j))
                if edge_key not in processed_undirected:
                    processed_undirected.add(edge_key)
                    vis_edge = {
                        'from': node_names[i],
                        'to': node_names[j],
                        'arrows': '',  # 无箭头表示无向
                        'dashes': True,  # 用虚线表示不确定性
                        'label': '无向'
                    }
                    formatted_edges.append(vis_edge)

    return formatted_edges


def run_olc_analysis(csv_data_string: str, alpha: float = 0.05, beta: float = 0.01) -> dict:
    """
    对 CSV 格式的字符串数据运行 OLC (One-Component Latent) 因果发现算法。

    OLC 算法特点:
        - 能够检测隐藏混杂因子（潜变量）
        - 适用于存在未观测变量影响多个观测变量的场景
        - 基于四阶累积量进行潜变量检测

    参数:
        csv_data_string: CSV 格式的字符串数据
        alpha: 主显著性水平，用于边定向（默认 0.05）
        beta: 次显著性水平，用于潜变量检测（默认 0.01，更严格）

    Returns:
        一个包含分析结果的字典。
        包括节点、边和邻接矩阵，用于在前端动态生成图表。
    """
    # 检查 CDMIR 是否可用
    if not _CDMIR_AVAILABLE:
        msg = "OLC 算法不可用：CDMIR 库未安装。请运行 'pip install git+https://github.com/DMIRLAB-Group/CDMIR.git' 安装。"
        return {"success": False, "message": msg, "algorithm": "olc"}

    try:
        string_io = io.StringIO(csv_data_string)
        df = pd.read_csv(string_io)

        if df.empty or len(df.columns) < 2:
            msg = "错误：CSV数据为空或列数少于2，无法进行因果分析。"
            return {"success": False, "message": msg}

        # OLC 需要 numpy 数组作为输入
        data = df.to_numpy()
        observed_node_names = df.columns.tolist()
        n_observed = len(observed_node_names)

        # OLC 返回两个矩阵：邻接矩阵和系数矩阵
        adjacency_matrix, coefficient_matrix = olc(
            data=data,
            alpha=alpha,
            beta=beta,
            verbose=False
        )

        # 检测是否发现了潜变量
        # OLC 输出的矩阵维度 = n_observed + n_latent
        n_total = adjacency_matrix.shape[0]
        n_latent = n_total - n_observed

        # 构建完整的节点名称列表（观测变量 + 潜变量）
        all_node_names = observed_node_names.copy()
        for i in range(n_latent):
            latent_name = f"L{i+1}"  # 潜变量命名为 L1, L2, ...
            all_node_names.append(latent_name)

        # 准备前端需要的数据格式
        nodes_for_vis = []
        for i, name in enumerate(all_node_names):
            node = {'id': name, 'label': name}
            # 为潜变量添加特殊标记，方便前端区分样式
            if i >= n_observed:
                node['group'] = 'latent'  # 标记为潜变量组
                node['shape'] = 'diamond'  # 可选：用菱形表示潜变量
            else:
                node['group'] = 'observed'
            nodes_for_vis.append(node)

        # 转换边格式
        edges_for_vis = _format_olc_edges(adjacency_matrix, coefficient_matrix, all_node_names)

        return {
            "success": True,
            "message": f"OLC 因果分析成功完成。检测到 {n_latent} 个潜变量。",
            "data": {
                "nodes": nodes_for_vis,
                "edges": edges_for_vis,
            },
            "raw_results": {
                "adjacency_matrix": adjacency_matrix.tolist(),
                "coefficient_matrix": coefficient_matrix.tolist(),
                "n_observed": n_observed,
                "n_latent": n_latent,
                "observed_names": observed_node_names,
                "latent_names": [f"L{i+1}" for i in range(n_latent)]
            },
            "analyzed_filename": None
        }

    except Exception as e:
        error_message = f"OLC: 执行因果分析时发生错误: {e}"
        return {"success": False, "message": error_message}
# 用于独立测试
if __name__ == '__main__':
    # 指定要测试的CSV文件路径（相对于项目根目录）
    test_csv_path = '杂项/test/1.csv'
    
    print(f"开始独立测试 run_pc_analysis，使用文件: {test_csv_path}")
    
    try:
        # 读取指定的CSV文件内容
        with open(test_csv_path, 'r', encoding='utf-8') as f:
            csv_string = f.read()
        
        # 运行分析函数
        results = run_pc_analysis(csv_string)
        
        # 导入json库并打印格式化后的结果
        import json
        print("\n分析结果 (JSON)")
        # 使用 indent=2 美化输出, ensure_ascii=False 以正确显示中文（如果未来有）
        print(json.dumps(results, indent=2, ensure_ascii=False))

    except FileNotFoundError:
        print("测试失败：找不到测试 CSV 文件。")
    except Exception:
        print("测试过程中发生未知错误。")
