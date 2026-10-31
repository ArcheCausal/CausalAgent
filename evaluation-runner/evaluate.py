"""从报告取因果图并计算严格定向与骨架指标。"""
from __future__ import annotations

import re

VARIABLE_PATTERN = re.compile(r"^X(\d+)$")
UNDIRECTED_TYPES = {"undirected", "bidirected", "partially_directed", "partially_oriented"}


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


def score(edges: list[tuple[int, int, str]], gold: set[tuple[int, int]]) -> dict:
    directed = {(source, target) for source, target, kind in edges if kind == "directed"}
    undirected = {
        frozenset((source, target))
        for source, target, kind in edges
        if kind in UNDIRECTED_TYPES
    }
    directed_pairs = {frozenset(edge) for edge in directed}
    mirrored = {
        pair for pair in directed_pairs
        if len(pair) == 2 and all(
            (a, b) in directed for a, b in ((min(pair), max(pair)), (max(pair), min(pair)))
        )
    }
    directed -= {
        (source, target)
        for pair in mirrored
        for source, target in ((min(pair), max(pair)), (max(pair), min(pair)))
    }
    n_pred_edges = len(directed)
    n_undirected_edges = len(undirected) + len(mirrored)
    true_positive = len(directed & gold)
    recall = true_positive / len(gold) if gold else 0.0
    denominator = n_pred_edges + n_undirected_edges
    precision = true_positive / denominator if denominator else 0.0

    predicted_pairs = {frozenset(edge) for edge in directed} | undirected
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
