from general_agent_eval import metrics


def test_perfect_prediction():
    gold = {(0, 1), (1, 2)}
    edges = [(0, 1, "directed"), (1, 2, "directed")]
    result = metrics.score(edges, gold)
    assert result["precision"] == 1.0
    assert result["recall"] == 1.0
    assert metrics.f1_score(result["precision"], result["recall"]) == 1.0
    assert metrics.shd(edges, gold) == 0


def test_missing_edge():
    gold = {(0, 1), (1, 2)}
    edges = [(0, 1, "directed")]
    result = metrics.score(edges, gold)
    assert result["recall"] == 0.5
    assert result["precision"] == 1.0
    assert metrics.shd(edges, gold) == 1


def test_extra_edge():
    gold = {(0, 1)}
    edges = [(0, 1, "directed"), (1, 2, "directed")]
    result = metrics.score(edges, gold)
    assert result["precision"] == 0.5
    assert result["recall"] == 1.0
    assert metrics.shd(edges, gold) == 1


def test_wrong_direction_on_compelled_edge():
    # 0->2 <-1 是 v-structure，两条边在 CPDAG 中被迫定向
    gold = {(0, 2), (1, 2)}
    edges = [(2, 0, "directed"), (1, 2, "directed")]
    result = metrics.score(edges, gold)
    assert result["recall"] == 0.5
    assert metrics.shd(edges, gold) == 1


def test_reversible_edge_direction_is_free():
    # 三节点链 0->1->2 的边在 CPDAG 中均可逆，方向反了不罚
    gold = {(0, 1), (1, 2)}
    edges = [(1, 0, "directed"), (1, 2, "directed")]
    assert metrics.shd(edges, gold) == 0


def test_skeleton_metrics():
    gold = {(0, 1)}
    edges = [(0, 1, "directed")]
    result = metrics.score(edges, gold)
    assert result["skeleton_precision"] == 1.0
    assert result["skeleton_recall"] == 1.0
