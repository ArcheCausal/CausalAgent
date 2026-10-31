"""从报告取因果图并计算严格定向与骨架指标。"""
from __future__ import annotations

import math
import re

VARIABLE_PATTERN = re.compile(r"^X(\d+)$")
UNDIRECTED_TYPES = {"undirected", "bidirected", "partially_directed", "partially_oriented"}


def final_decision(records) -> tuple[str, str]:
    """取最终决策事件（finalization_gate）的摘要与置信度。"""
    for record in reversed(list(records)):
        payload = record.get("payload") or {}
        if record.get("type") == "decision" and payload.get("decision_kind") == "final":
            return str(payload.get("summary") or ""), str(payload.get("confidence") or "")
    return "", ""


def variable_index(value) -> int | None:
    match = VARIABLE_PATTERN.match(str(value or "").strip())
    return int(match.group(1)) if match else None


def _walk_blocks(blocks):
    for block in blocks or ():
        yield block
        yield from _walk_blocks(block.get("children"))


def find_graph_asset(document: dict) -> tuple[dict | None, str | None]:
    if not isinstance(document, dict):
        return None, "no_causal_graph"
    if document.get("report_mode") == "diagnostic":
        return None, "diagnostic_report"
    assets = document.get("assets") or {}
    for block in _walk_blocks(document.get("blocks") or ()):
        if block.get("type") == "causal_graph":
            asset = assets.get(block.get("asset_key"))
            return (asset, None) if isinstance(asset, dict) else (None, "no_causal_graph")
    for asset in assets.values():
        if isinstance(asset, dict) and "graph_id" in asset:
            return asset, None
    return None, "no_causal_graph"


def parse_edges(asset: dict, dim: int | None = None) -> tuple[list[tuple[int, int, str]], bool]:
    variables = {
        node.get("id"): node.get("variable")
        for node in asset.get("nodes") or ()
    }
    edges: list[tuple[int, int, str]] = []
    unmapped = False
    for edge in asset.get("edges") or ():
        source = variable_index(variables.get(edge.get("source")))
        target = variable_index(variables.get(edge.get("target")))
        if dim is not None and source is not None and target is not None:
            if source >= dim or target >= dim:
                source = target = None
        if source is None or target is None:
            unmapped = True
            continue
        edges.append((source, target, str(edge.get("edge_type") or "directed")))
    return edges, unmapped


def _partition(edges):
    """拆出有向边（去镜像对）、无向边列表（保留重复计数）与镜像对。"""
    directed = {(s, t) for s, t, kind in edges if kind == "directed"}
    undirected = [frozenset((s, t)) for s, t, kind in edges if kind in UNDIRECTED_TYPES]
    directed_pairs = {frozenset(edge) for edge in directed}
    mirrored = {
        pair for pair in directed_pairs
        if len(pair) == 2 and (min(pair), max(pair)) in directed and (max(pair), min(pair)) in directed
    }
    directed -= {
        (a, b) for pair in mirrored
        for a, b in ((min(pair), max(pair)), (max(pair), min(pair)))
    }
    return directed, undirected, mirrored


def f1_score(precision: float, recall: float) -> float:
    total = precision + recall
    return 2 * precision * recall / total if total else 0.0


def shd(edges, gold) -> int:
    directed, undirected_list, mirrored = _partition(edges)
    matched = directed & gold
    reversed_edges = {(s, t) for s, t in directed - gold if (t, s) in gold}
    extra = directed - matched - reversed_edges
    covered = set(matched) | {(t, s) for s, t in reversed_edges}
    skeleton_pairs = set(undirected_list) | mirrored
    covered |= {(s, t) for s, t in gold if frozenset((s, t)) in skeleton_pairs}
    missing = gold - covered
    return len(reversed_edges) + len(extra) + len(undirected_list) + len(mirrored) + len(missing)


def probability_matrix(asset: dict, dim: int) -> list[list[float]] | None:
    metadata = asset.get("metadata") or {}
    if metadata.get("score_type") != "probability":
        return None
    matrix = metadata.get("edge_score_matrix")
    nodes = asset.get("nodes") or []
    if not isinstance(matrix, list) or len(matrix) != dim or len(nodes) != dim:
        return None
    positions: list[int] = []
    for node in nodes:
        index = variable_index(node.get("variable"))
        if index is None or not 0 <= index < dim:
            return None
        positions.append(index)
    reordered = [[0.0] * dim for _ in range(dim)]
    for i, row in enumerate(matrix):
        if not isinstance(row, list) or len(row) != dim:
            return None
        for j, value in enumerate(row):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                return None
            reordered[positions[i]][positions[j]] = float(value)
    return reordered


def ranking_metrics(matrix, gold) -> dict:
    size = len(matrix)
    scores: list[float] = []
    labels: list[int] = []
    for i in range(size):
        for j in range(size):
            if i == j:
                continue
            scores.append(matrix[i][j])
            labels.append(1 if (i, j) in gold else 0)
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return {"auroc": None, "auprc": None}
    ranks = _average_ranks(scores)
    auroc = (sum(rank for rank, label in zip(ranks, labels) if label) - positives * (positives + 1) / 2) / (positives * negatives)
    return {"auroc": auroc, "auprc": _average_precision(scores, labels, positives)}


def _average_ranks(scores):
    order = sorted(range(len(scores)), key=lambda index: scores[index])
    ranks = [0.0] * len(scores)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and scores[order[end + 1]] == scores[order[start]]:
            end += 1
        rank = (start + end) / 2 + 1
        for position in range(start, end + 1):
            ranks[order[position]] = rank
        start = end + 1
    return ranks


def _average_precision(scores, labels, positives):
    order = sorted(range(len(scores)), key=lambda index: -scores[index])
    true_positive = false_positive = 0
    previous_recall = 0.0
    ap = 0.0
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and scores[order[end + 1]] == scores[order[start]]:
            end += 1
        group = order[start:end + 1]
        group_positive = sum(labels[index] for index in group)
        true_positive += group_positive
        false_positive += len(group) - group_positive
        recall = true_positive / positives
        precision = true_positive / (true_positive + false_positive)
        ap += (recall - previous_recall) * precision
        previous_recall = recall
        start = end + 1
    return ap


def score(edges: list[tuple[int, int, str]], gold: set[tuple[int, int]]) -> dict:
    directed, undirected_edges, mirrored = _partition(edges)
    undirected = set(undirected_edges)
    n_pred_edges = len(directed)
    n_undirected_edges = len(undirected_edges) + len(mirrored)
    true_positive = len(directed & gold)
    recall = true_positive / len(gold) if gold else 0.0
    denominator = n_pred_edges + n_undirected_edges
    precision = true_positive / denominator if denominator else 0.0

    predicted_pairs = {frozenset(edge) for edge in directed} | undirected | mirrored
    gold_pairs = {frozenset(edge) for edge in gold}
    intersection = len(predicted_pairs & gold_pairs)
    skeleton_recall = intersection / len(gold_pairs) if gold_pairs else 0.0
    skeleton_precision = intersection / len(predicted_pairs) if predicted_pairs else 0.0
    return {
        "n_pred_edges": n_pred_edges,
        "n_undirected_edges": n_undirected_edges,
        "recall": recall,
        "precision": precision,
        "skeleton_recall": skeleton_recall,
        "skeleton_precision": skeleton_precision,
    }
