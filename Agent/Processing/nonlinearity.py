"""数据集级非线性度信号。

为什么输出 ratio 而不是 S：S = mean(η²−r²) 是原始量，理论有界 ~[−1,1] 但实际无
参照上界，孤立看一个 0.05 说不出强弱。null_p99 是"变量之间完全没关系时 S 的 99%
分位"——即噪声上限，实测 25 个 d5 数据集稳定在 0.0109~0.0116（跨集只差 6%），主要
由分箱偏置这个与数据无关的常数决定。ratio = S / null_p99 即信噪比：观测到的非线性
结构是噪声上限的几倍；ratio ≥ 1 等价于单边 p=0.01 检验。

ratio < 1 只说明没测出超过噪声的非线性结构，不等于线性——弱非线性会被噪声盖住
（d5 里 λ=0.25 的五个 seed 有 3 个落在 1 以下）。

零假设口径：每列各自独立打乱（不是打乱行，也不是对所有变量对共用同一个相对置换）。
后一种"向量化"构造均值一致但尾部最多差 11% 且非单向，证明不了等价，所以不用。

n 钳在 1000：null_p99 随 n 骤降（实测 n=1000 → 0.011、2000 → 0.0047、1e5 → 0.0001），
所以 null_p99 必须按数据集现算、不能写死；而 d5 本身就是 n=1000，钳在 1000 就永远
落在已验证区间内。
"""

from __future__ import annotations

import itertools
from typing import Any, Mapping

import numpy as np
import pandas as pd

_BINS = 10
_N_PERM = 1000
_MAX_ROWS = 1000
_MAX_VARS = 12
_MIN_ROWS = 100
_RATIO_LINE = 1.0

# payload 只放会被消费的字段：这份 dict 会随 analysis_parameters 进 multiple 个 prompt。
# score(S) 和 null_p99 不进 payload —— score 可由 ratio×null_p99 还原，null_p99 是
# 噪声底、对模型是纯噪声。
PAYLOAD_KEYS = frozenset({"ratio", "verdict", "n_vars", "n_rows", "sampled", "reason"})

# 指标只在 p=5~12 上验证过：p≥30 时非边对被共同祖先污染，指标本身失效；且开销按
# C(p,2) 涨。
_VALIDATED_MAX_VARS = 12
_VALIDATED_MAX_ROWS = 1000


def _insufficient(reason: str, n_vars: int = 0, n_rows: int = 0) -> dict[str, Any]:
    return {
        "ratio": None,
        "verdict": "insufficient",
        "n_vars": int(n_vars),
        "n_rows": int(n_rows),
        "sampled": False,
        "reason": reason,
    }


def _ranks(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="stable")
    ranks = np.empty(len(x), dtype=float)
    ranks[order] = np.arange(len(x))
    return ranks


def _pair_degree(x: np.ndarray, y: np.ndarray, bins: np.ndarray) -> float:
    """变量对 (x, y) 的 η² − r²。x 的箱号由调用方传入，y 用原值。"""

    counts = np.bincount(bins, minlength=_BINS)
    keep = counts > 0
    y_mean = y.mean()
    total = float(((y - y_mean) ** 2).sum())
    if total <= 0:
        return 0.0
    group_means = np.bincount(bins, weights=y, minlength=_BINS)[keep] / counts[keep]
    eta2 = float((counts[keep] * (group_means - y_mean) ** 2).sum() / total)

    x_centered, y_centered = x - x.mean(), y - y_mean
    denominator = np.sqrt((x_centered * x_centered).sum() * (y_centered * y_centered).sum())
    r2 = float((x_centered * y_centered).sum() / denominator) ** 2 if denominator > 0 else 0.0
    return eta2 - r2


def _score(values: np.ndarray, bins: list[np.ndarray]) -> float:
    pairs = itertools.combinations(range(values.shape[1]), 2)
    return float(np.mean([_pair_degree(values[:, i], values[:, j], bins[i]) for i, j in pairs]))


def _null_p99(
    values: np.ndarray,
    bins: list[np.ndarray],
    rng: np.random.Generator,
    n_perm: int,
) -> float:
    """每列各自独立打乱后重算 S，取 99 分位。"""

    n, p = values.shape
    samples = np.empty(n_perm, dtype=float)
    for index in range(n_perm):
        perms = [rng.permutation(n) for _ in range(p)]
        shuffled = np.column_stack([values[perm, k] for k, perm in enumerate(perms)])
        shuffled_bins = [bins[k][perm] for k, perm in enumerate(perms)]
        samples[index] = _score(shuffled, shuffled_bins)
    return float(np.quantile(samples, 0.99))


def _candidate_columns(data_summary: Mapping[str, Any] | None) -> list[str]:
    """复用 get_data_summary 的类型推断：连续、非常量、非疑似 ID。

    排除 binary / categorical_numeric：K=10 等量分箱在二元列上退化（只剩 2 个非空箱）。
    排除 possible_id：ID 列是均匀噪声，只会挤掉真变量名额。
    """

    profiles = (data_summary or {}).get("column_profiles")
    if not isinstance(profiles, Mapping):
        return []
    declared = (data_summary or {}).get("columns")
    order = [str(column) for column in declared] if declared else [str(key) for key in profiles]
    return [
        column
        for column in order
        if isinstance(profiles.get(column), Mapping)
        and profiles[column].get("inferred_type") == "continuous"
        and not profiles[column].get("possible_id")
    ]


def _measure(
    df: pd.DataFrame,
    data_summary: Mapping[str, Any] | None,
    *,
    n_perm: int,
    max_rows: int,
    max_vars: int,
    seed: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """返回 (payload, 溯源)。溯源只给自检/排查用，不进 payload。"""

    candidates = [column for column in _candidate_columns(data_summary) if column in df.columns]
    if len(candidates) < 2:
        return _insufficient("fewer_than_two_continuous_columns", len(candidates)), {}

    frame = df.loc[:, candidates]
    sampled = False
    if len(candidates) > max_vars:
        picked = np.random.default_rng(seed + 1).choice(len(candidates), size=max_vars, replace=False)
        frame = frame.iloc[:, np.sort(picked)]
        sampled = True

    frame = frame.dropna()
    if len(frame) > max_rows:
        picked = np.random.default_rng(seed).choice(len(frame), size=max_rows, replace=False)
        frame = frame.iloc[np.sort(picked)]
        sampled = True

    n_rows = len(frame)
    if n_rows < _MIN_ROWS:
        return _insufficient("fewer_than_100_complete_rows", frame.shape[1], n_rows), {}

    values = frame.to_numpy(dtype=float)
    spread = values.std(axis=0)
    usable = spread > 0
    used_columns = [column for column, keep in zip(frame.columns, usable) if keep]
    values = values[:, usable]
    if len(used_columns) < 2:
        return _insufficient("fewer_than_two_non_constant_columns", len(used_columns), n_rows), {}

    values = (values - values.mean(axis=0)) / values.std(axis=0)
    bins = [(_ranks(values[:, k]) * _BINS // n_rows).astype(int) for k in range(len(used_columns))]

    score = _score(values, bins)
    null_p99 = _null_p99(values, bins, np.random.default_rng(seed), n_perm)
    if not np.isfinite(null_p99) or null_p99 <= 0:
        return _insufficient("degenerate_null_distribution", len(used_columns), n_rows), {}

    ratio = score / null_p99
    payload = {
        "ratio": round(float(ratio), 4),
        "verdict": "nonlinear" if ratio >= _RATIO_LINE else "linear",
        "n_vars": len(used_columns),
        "n_rows": n_rows,
        "sampled": sampled,
        "reason": None,
    }
    return payload, {
        "score": score,
        "null_p99": null_p99,
        "columns": used_columns,
        "n_vars": len(used_columns),
        "n_rows": n_rows,
        "sampled": sampled,
        "n_perm": n_perm,
    }


def measure_nonlinearity(
    df: pd.DataFrame,
    data_summary: Mapping[str, Any] | None,
    *,
    n_perm: int = _N_PERM,
    max_rows: int = _MAX_ROWS,
    max_vars: int = _MAX_VARS,
    seed: int = 0,
) -> dict[str, Any]:
    """给出数据集级的线性/非线性判定，写进 ``analysis_parameters["nonlinearity"]``。

    返回的 dict 恰好是 :data:`PAYLOAD_KEYS` 六个键。**绝不抛异常**：调用点在
    ``fold_node`` 的 ``try`` 块之外，而那块一旦抛异常会走 ``interrupt()`` 挂起等
    用户输入，指标算错绝不能把 job 引到那条路上。
    """

    try:
        payload, _provenance = _measure(
            df,
            data_summary,
            n_perm=n_perm,
            max_rows=max_rows,
            max_vars=max_vars,
            seed=seed,
        )
        return payload
    except Exception as exc:  # noqa: BLE001 - 兜底是硬要求
        return _insufficient(f"unexpected_error:{type(exc).__name__}")


def _self_check() -> None:
    """合成数据自检。不碰 DB/LLM。"""

    from Agent.Processing.fold_processing import get_data_summary

    def measure(values: np.ndarray) -> tuple[dict[str, Any], dict[str, Any]]:
        frame = pd.DataFrame(values, columns=[f"x{i}" for i in range(values.shape[1])])
        return _measure(frame, get_data_summary(frame), n_perm=_N_PERM, max_rows=_MAX_ROWS,
                        max_vars=_MAX_VARS, seed=0)

    rng = np.random.default_rng(0)

    linear = rng.normal(size=(_MAX_ROWS, 5))
    linear[:, 1] = 0.9 * linear[:, 0] + 0.3 * rng.normal(size=_MAX_ROWS)
    linear[:, 3] = -0.7 * linear[:, 2] + 0.5 * rng.normal(size=_MAX_ROWS)
    linear_payload, linear_prov = measure(linear)
    assert linear_payload["verdict"] == "linear", linear_payload
    assert linear_payload["ratio"] < _RATIO_LINE, linear_payload

    # 标定看门狗：n 已钳在 1000，null_p99 在 25 个 d5 集上只差 6%（0.0109~0.0116）
    assert 0.009 <= linear_prov["null_p99"] <= 0.014, linear_prov["null_p99"]

    nonlinear = rng.normal(size=(_MAX_ROWS, 5))
    nonlinear[:, 1] = nonlinear[:, 0] ** 2 + 0.2 * rng.normal(size=_MAX_ROWS)
    nonlinear[:, 3] = np.sin(2.0 * nonlinear[:, 2]) + 0.2 * rng.normal(size=_MAX_ROWS)
    nonlinear_payload, nonlinear_prov = measure(nonlinear)
    assert nonlinear_payload["verdict"] == "nonlinear", nonlinear_payload
    assert nonlinear_payload["ratio"] >= _RATIO_LINE, nonlinear_payload
    assert 0.009 <= nonlinear_prov["null_p99"] <= 0.014, nonlinear_prov["null_p99"]

    # 退化输入一律 insufficient 且不抛
    single = pd.DataFrame({"x0": rng.normal(size=_MAX_ROWS)})
    assert measure_nonlinearity(single, get_data_summary(single))["verdict"] == "insufficient"

    constant = pd.DataFrame({"x0": np.ones(_MAX_ROWS), "x1": np.ones(_MAX_ROWS)})
    assert measure_nonlinearity(constant, get_data_summary(constant))["verdict"] == "insufficient"

    short = pd.DataFrame(rng.normal(size=(50, 5)), columns=[f"x{i}" for i in range(5)])
    short_payload = measure_nonlinearity(short, get_data_summary(short))
    assert short_payload["verdict"] == "insufficient", short_payload
    assert short_payload["reason"] == "fewer_than_100_complete_rows"

    empty = pd.DataFrame({"x0": [np.nan] * _MAX_ROWS, "x1": [np.nan] * _MAX_ROWS})
    assert measure_nonlinearity(empty, get_data_summary(empty))["verdict"] == "insufficient"

    # 抽样截断必须如实标记
    wide = rng.normal(size=(_MAX_ROWS, 14))
    wide_frame = pd.DataFrame(wide, columns=[f"x{i}" for i in range(14)])
    wide_payload = measure_nonlinearity(wide_frame, get_data_summary(wide_frame))
    assert wide_payload["sampled"] is True, wide_payload
    assert wide_payload["n_vars"] == _VALIDATED_MAX_VARS, wide_payload

    tall_frame = pd.DataFrame(rng.normal(size=(1500, 5)), columns=[f"x{i}" for i in range(5)])
    tall_payload = measure_nonlinearity(tall_frame, get_data_summary(tall_frame))
    assert tall_payload["sampled"] is True, tall_payload
    assert tall_payload["n_rows"] == _VALIDATED_MAX_ROWS, tall_payload

    # payload 只含约定字段：防止哪天把 score/null_p99 又塞回去，悄悄涨 prompt 体积
    assert set(linear_payload) == PAYLOAD_KEYS, set(linear_payload)
    assert set(measure_nonlinearity(single, get_data_summary(single))) == PAYLOAD_KEYS

    print("linear   :", linear_payload, "null_p99=%.4f" % linear_prov["null_p99"])
    print("nonlinear:", nonlinear_payload, "null_p99=%.4f" % nonlinear_prov["null_p99"])
    print("非线性度自检通过")


if __name__ == "__main__":
    _self_check()
