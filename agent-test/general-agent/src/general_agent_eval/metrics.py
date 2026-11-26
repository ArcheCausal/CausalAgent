"""按有向预测边计算 P/R/F1，并计算骨架 P/R 和 CPDAG-SHD。"""
from __future__ import annotations

from functools import lru_cache

from causallearn.graph.Dag import Dag
from causallearn.graph.Edge import Edge
from causallearn.graph.Endpoint import Endpoint
from causallearn.graph.GraphNode import GraphNode
from causallearn.utils.DAG2CPDAG import dag2cpdag

UNDIRECTED_TYPES = {"undirected", "bidirected", "partially_directed", "partially_oriented"}


def _partition(edges):
    directed = {(s, t) for s, t, kind in edges if kind == "directed"}
    undirected = [frozenset((s, t)) for s, t, kind in edges if kind in UNDIRECTED_TYPES]
    directed_pairs = {frozenset(edge) for edge in directed}
    mirrored = {
        pair for pair in directed_pairs
        if len(pair) == 2
        and (min(pair), max(pair)) in directed
        and (max(pair), min(pair)) in directed
    }
    directed -= {
        (a, b) for pair in mirrored
        for a, b in ((min(pair), max(pair)), (max(pair), min(pair)))
    }
    return directed, undirected, mirrored


def score(edges, gold) -> dict:
    directed, undirected_edges, mirrored = _partition(edges)
    undirected = set(undirected_edges)
    n_pred_edges = len(directed)
    true_positive = len(directed & gold)
    recall = true_positive / len(gold) if gold else 0.0
    precision = true_positive / n_pred_edges if n_pred_edges else 0.0

    predicted_pairs = {frozenset(edge) for edge in directed} | undirected | mirrored
    gold_pairs = {frozenset(edge) for edge in gold}
    intersection = len(predicted_pairs & gold_pairs)
    return {
        "n_pred_edges": n_pred_edges,
        "recall": recall,
        "precision": precision,
        "skeleton_recall": intersection / len(gold_pairs) if gold_pairs else 0.0,
        "skeleton_precision": intersection / len(predicted_pairs) if predicted_pairs else 0.0,
    }


def f1_score(precision: float, recall: float) -> float:
    total = precision + recall
    return 2 * precision * recall / total if total else 0.0


def _cpdag_compelled(gold: frozenset[tuple[int, int]]) -> frozenset[tuple[int, int]]:
    if not gold:
        return frozenset()
    size = max(max(edge) for edge in gold) + 1
    nodes = [GraphNode(str(index)) for index in range(size)]
    dag = Dag(nodes)
    for source, target in gold:
        dag.add_edge(Edge(nodes[source], nodes[target], Endpoint.TAIL, Endpoint.ARROW))
    compelled = set()
    for edge in dag2cpdag(dag).get_graph_edges():
        if edge.get_endpoint1() == Endpoint.TAIL and edge.get_endpoint2() == Endpoint.ARROW:
            compelled.add((int(edge.get_node1().get_name()), int(edge.get_node2().get_name())))
    return frozenset(compelled)


@lru_cache(maxsize=512)
def _compelled_cached(gold: tuple[tuple[int, int], ...]):
    return _cpdag_compelled(frozenset(gold))


def compelled_edges(gold):
    return _compelled_cached(tuple(sorted(gold)))


def shd(edges, gold) -> int:
    """缺失/多余各 1；被迫定向边方向不符 1；可逆边方向自由。"""
    directed, undirected_list, mirrored = _partition(edges)
    predicted_pairs: set[frozenset] = set()
    orientation: dict[frozenset, tuple[int, int]] = {}
    ambiguous: set[frozenset] = set()

    for source, target in directed:
        pair = frozenset((source, target))
        predicted_pairs.add(pair)
        if len(pair) == 2:
            orientation[pair] = (source, target)
    for pair in undirected_list:
        predicted_pairs.add(pair)
        ambiguous.add(pair)
    for pair in mirrored:
        predicted_pairs.add(pair)
        ambiguous.add(pair)

    gold_pairs = {frozenset(edge) for edge in gold}
    compelled = {frozenset(edge): edge for edge in compelled_edges(gold)}
    mismatched = 0
    for pair in predicted_pairs & gold_pairs:
        required = compelled.get(pair)
        if required is None:
            continue
        if pair in ambiguous or orientation.get(pair) != required:
            mismatched += 1
    return (
        len(predicted_pairs - gold_pairs)
        + len(gold_pairs - predicted_pairs)
        + mismatched
    )
