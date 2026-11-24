"""Contracts shared by dataset admission, PCMCI+ execution and result readers."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VerifiedTimeAxis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    time_column: str = Field(min_length=1)
    start_time: datetime
    end_time: datetime
    sampling_period_seconds: float = Field(gt=0, allow_inf_nan=False)
    row_count: int = Field(ge=2)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_rule: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_duration(self) -> VerifiedTimeAxis:
        if (self.start_time.utcoffset() is None) != (self.end_time.utcoffset() is None):
            raise ValueError("time axis endpoints use different timezone conventions")
        if not math.isclose(
            (self.end_time - self.start_time).total_seconds(),
            (self.row_count - 1) * self.sampling_period_seconds,
            rel_tol=1e-12, abs_tol=1e-6,
        ):
            raise ValueError("time axis duration does not match row count and sampling period")
        return self


def pcmci_sample_issue(rows: int, columns: int, tau_max: int) -> str | None:
    """ParCorr uses 2*tau_max cutoff; reserve degrees of freedom for conditioning."""
    effective_rows = rows - 2 * tau_max
    max_conditioning = columns * (2 * tau_max + 1) - 2
    if effective_rows <= max_conditioning + 2:
        return "连续片段过短：扣除最大滞后的两倍后，样本不足以支持偏相关检验；请缩短滞后或减少变量。"
    return None


class TimeSeriesRelation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    lag_steps: int = Field(ge=0)
    lag_seconds: float = Field(ge=0, allow_inf_nan=False)
    mark: Literal["-->", "o-o", "x-x"]
    p_value: float = Field(ge=0, le=1, allow_inf_nan=False)
    value: float = Field(ge=-1, le=1, allow_inf_nan=False)


class TimeSeriesResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    time_axis: VerifiedTimeAxis
    columns: tuple[str, ...] = Field(min_length=2)
    tau_min: int = Field(ge=0, le=1)
    tau_max: int = Field(ge=1, le=20)
    pc_alpha: float = Field(gt=0, lt=1)
    conditional_independence_test: Literal["parcorr"] = "parcorr"
    relations: tuple[TimeSeriesRelation, ...] = ()

    def report_snapshot(self) -> dict:
        """Keep the same float bits after a MySQL JSON round trip."""
        snapshot = self.model_dump(mode="json")
        snapshot["pc_alpha"] = repr(self.pc_alpha)
        snapshot["time_axis"]["sampling_period_seconds"] = repr(self.time_axis.sampling_period_seconds)
        for relation in snapshot["relations"]:
            for key in ("lag_seconds", "p_value", "value"):
                relation[key] = repr(relation[key])
        return snapshot

    @model_validator(mode="after")
    def check_relations(self) -> TimeSeriesResult:
        columns = set(self.columns)
        if len(columns) != len(self.columns) or any(not column.strip() for column in columns):
            raise ValueError("time series result columns must be unique and non-blank")
        if self.time_axis.time_column in columns:
            raise ValueError("the time column cannot be an analysis variable")
        seen: set[tuple[str, str, int]] = set()
        for edge in self.relations:
            if {edge.source, edge.target} - columns:
                raise ValueError("time series relation refers to an unknown variable")
            pair = (edge.source, edge.target)
            if edge.lag_steps == 0:
                if edge.source == edge.target:
                    raise ValueError("contemporaneous self-edge is invalid")
                pair = tuple(sorted(pair))
            elif edge.mark != "-->":
                raise ValueError("lagged PCMCI+ edge must point forward in time")
            key = (*pair, edge.lag_steps)
            if key in seen or not self.tau_min <= edge.lag_steps <= self.tau_max:
                raise ValueError("duplicate relation or lag outside the searched range")
            if not math.isclose(edge.lag_seconds, edge.lag_steps * self.time_axis.sampling_period_seconds):
                raise ValueError("lag duration does not match the verified sampling period")
            seen.add(key)
        return self


def decode_pcmci_result(payload: Mapping) -> TimeSeriesResult:
    """Decode the original runner matrices, keeping one record per same-time pair."""
    data = payload["raw_results"]
    columns = tuple(data["columns"])
    axis = VerifiedTimeAxis.model_validate(payload["time_axis"])
    config = payload["configuration"]
    graph, p_matrix, val_matrix = (data[key] for key in ("graph", "p_matrix", "val_matrix"))
    depth = config["tau_max"] + 1
    count = len(columns)
    if any(len(matrix) != count or any(
        len(row) != count or any(len(cell) != depth for cell in row) for row in matrix
    ) for matrix in (graph, p_matrix, val_matrix)):
        raise ValueError("PCMCI+ matrices must be [source, target, lag]")
    reverse = {"": "", "-->": "<--", "<--": "-->", "o-o": "o-o", "x-x": "x-x"}
    relations = []
    for i, source in enumerate(columns):
        for j, target in enumerate(columns):
            for lag, mark in enumerate(graph[i][j]):
                if lag == 0:
                    if mark not in reverse or graph[j][i][0] != reverse[mark] or (mark and i == j):
                        raise ValueError("PCMCI+ contemporaneous marks are inconsistent")
                    if mark == "<--" or (mark in {"o-o", "x-x"} and i > j):
                        continue
                if not mark:
                    continue
                relations.append(TimeSeriesRelation(
                    source=source, target=target, lag_steps=lag,
                    lag_seconds=lag * axis.sampling_period_seconds, mark=mark,
                    p_value=p_matrix[i][j][lag], value=val_matrix[i][j][lag],
                ))
    return TimeSeriesResult(
        time_axis=axis, columns=columns, tau_min=config["tau_min"], tau_max=config["tau_max"],
        pc_alpha=config["pc_alpha"], relations=tuple(relations),
    )
