"""Inspect and replay one continuous source-row range without changing its values."""

from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from io import BytesIO, TextIOWrapper
from itertools import zip_longest
import math
import re
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from Agent.deep_agent_tools.time_series import VerifiedTimeAxis


_MAX_ROWS = 100_000
_MAX_CELLS = 1_000_000
_MAX_COLUMNS = 1_000
_MAX_SEGMENTS_PER_PAGE = 20
_MICROSECONDS = 1_000_000
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)?",
    re.ASCII,
)
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?", re.ASCII)


class TimeAxisError(ValueError):
    """The source or candidate cannot represent the selected time series."""


class TimeSeriesSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    time_column: str = Field(min_length=1, max_length=255)
    start_row: int = Field(ge=1, le=_MAX_ROWS, description="首条原始数据记录号，从 1 开始，不含标题")
    end_row: int = Field(ge=2, le=_MAX_ROWS, description="最后一条原始数据记录号，包含该记录")
    sampling_period_seconds: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def check_range(self) -> TimeSeriesSelection:
        if self.end_row <= self.start_row:
            raise ValueError("片段至少需要两条原始数据记录")
        _period_microseconds(self.sampling_period_seconds)
        return self


class TimeSeriesWindow(TimeSeriesSelection):
    """Program-computed window for the plan checksum and public confirmation card."""

    start_time: str = Field(pattern=f"^{_TIMESTAMP.pattern}$", max_length=32)
    end_time: str = Field(pattern=f"^{_TIMESTAMP.pattern}$", max_length=32)
    row_count: int = Field(ge=2, le=_MAX_ROWS)
    selection_rule: Literal["source_row_range"] = "source_row_range"

    @model_validator(mode="after")
    def check_count(self) -> TimeSeriesWindow:
        if self.row_count != self.end_row - self.start_row + 1:
            raise ValueError("片段行数与起止记录号不一致")
        return self

    def verified_axis(self, source_hash: str) -> dict[str, Any]:
        return VerifiedTimeAxis(
            **self.model_dump(exclude={"start_row", "end_row"}), source_sha256=source_hash,
        ).model_dump(mode="json")


def _read_csv(data: bytes) -> tuple[list[str], list[list[str]]]:
    try:
        with TextIOWrapper(BytesIO(data), encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, strict=True)
            columns = next(reader)
            if (not columns or len(columns) > _MAX_COLUMNS
                    or not all(column.strip() for column in columns)
                    or len(set(columns)) != len(columns)):
                raise TimeAxisError("时序 CSV 的列名为空、重复或超过列数限制")
            rows = []
            for row in reader:
                if len(row) != len(columns):
                    raise TimeAxisError("时序 CSV 存在字段数不一致的数据记录")
                if len(rows) >= _MAX_ROWS or (len(rows) + 1) * len(columns) > _MAX_CELLS:
                    raise TimeAxisError("时序 CSV 超过完整扫描的行数或单元格限制")
                rows.append(row)
            return columns, rows
    except (csv.Error, StopIteration, UnicodeError) as exc:
        raise TimeAxisError("时序数据无法按 UTF-8 CSV 解析") from exc


def _parse_timestamp(value: str) -> datetime | None:
    value = value.strip()
    if not _TIMESTAMP.fullmatch(value):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else parsed
    except (ValueError, OverflowError):
        return None


def _delta_microseconds(delta: timedelta) -> int:
    return (delta.days * 86_400 + delta.seconds) * _MICROSECONDS + delta.microseconds


def _period_microseconds(seconds: float) -> int:
    micros = Decimal(str(seconds)) * _MICROSECONDS
    if not micros.is_finite() or micros <= 0 or micros != micros.to_integral_value():
        raise TimeAxisError("采样周期必须是正数，精度不得小于 1 微秒")
    return int(micros)


def _time_column_index(columns: list[str], time_column: str) -> int:
    if time_column not in columns:
        raise TimeAxisError("指定的时间列不在冻结 CSV 中")
    return columns.index(time_column)


def _adjacent_intervals(times: list[datetime | None]) -> list[int | None]:
    return [
        _delta_microseconds(current - previous)
        if previous is not None and current is not None and previous.tzinfo == current.tzinfo else None
        for previous, current in zip(times, times[1:])
    ]


def _segment(start: int, end: int, time_column: str, times: list[datetime | None], period: int) -> TimeSeriesWindow:
    first, last = times[start], times[end]
    assert first is not None and last is not None
    return TimeSeriesWindow(
        time_column=time_column, start_row=start + 1, end_row=end + 1,
        start_time=first.isoformat(), end_time=last.isoformat(), row_count=end - start + 1,
        sampling_period_seconds=period / _MICROSECONDS,
    )


def inspect_time_axis(
    source: bytes, *, time_column: str, sampling_period_seconds: float | None = None,
    segment_offset: int = 0, interval_offset: int = 0,
) -> dict[str, Any]:
    """Scan every timestamp in file order; only returned diagnostic lists are paginated."""
    columns, rows = _read_csv(source)
    index = _time_column_index(columns, time_column)
    times = [_parse_timestamp(row[index]) for row in rows]
    counts = Counter(time for time in times if time is not None)
    intervals = _adjacent_intervals(times)
    distribution = Counter(delta for delta in intervals if delta is not None and delta > 0)
    frequencies = sorted(distribution.items(), key=lambda item: (-item[1], item[0]))
    period = (_period_microseconds(sampling_period_seconds) if sampling_period_seconds is not None
              else frequencies[0][0] if frequencies else None)
    segments = []
    start = None
    if period is not None:
        for index, timestamp in enumerate(times):
            eligible = timestamp is not None and counts[timestamp] == 1
            if start is not None and (not eligible or intervals[index - 1] != period):
                if index - 1 > start:
                    segments.append((start, index - 1))
                start = None
            if eligible and start is None:
                start = index
        if start is not None and len(times) - 1 > start:
            segments.append((start, len(times) - 1))

    def window(bounds: tuple[int, int]) -> dict[str, Any]:
        return _segment(*bounds, time_column, times, period).model_dump(mode="json")

    def next_offset(offset: int, total: int) -> int | None:
        following = offset + _MAX_SEGMENTS_PER_PAGE
        return following if following < total else None

    longest = max(segments, key=lambda bounds: bounds[1] - bounds[0], default=None)
    return {
        "time_column": time_column, "row_count": len(rows),
        "parse_failure_count": len(rows) - sum(counts.values()),
        "duplicate_count": sum(count - 1 for count in counts.values()),
        "reverse_interval_count": sum(delta is not None and delta < 0 for delta in intervals),
        "incomparable_interval_count": intervals.count(None),
        "sampling_period_seconds": None if period is None else period / _MICROSECONDS,
        "gap_count": sum(delta is not None and period is not None and delta > period for delta in intervals),
        "other_positive_interval_count": sum(count for delta, count in distribution.items() if delta != period),
        "interval_distribution": [{"seconds": delta / _MICROSECONDS, "count": count}
                                  for delta, count in frequencies[interval_offset:interval_offset + _MAX_SEGMENTS_PER_PAGE]],
        "next_interval_offset": next_offset(interval_offset, len(frequencies)),
        "segment_count": len(segments),
        "segments": [window(bounds) for bounds in segments[segment_offset:segment_offset + _MAX_SEGMENTS_PER_PAGE]],
        "next_segment_offset": next_offset(segment_offset, len(segments)),
        "longest_segment": None if longest is None else window(longest),
    }


def _source_plan(
    source: bytes, selection: TimeSeriesSelection, metadata: Mapping[str, Any],
) -> tuple[TimeSeriesWindow, list[int], list[list[str]], list[str]]:
    columns, rows = _read_csv(source)
    time_index = _time_column_index(columns, selection.time_column)
    start, end = selection.start_row - 1, selection.end_row - 1
    if end >= len(rows):
        raise TimeAxisError("片段的结束记录超出了冻结 CSV")
    times = [_parse_timestamp(row[time_index]) for row in rows]
    period = _period_microseconds(selection.sampling_period_seconds)
    selected = times[start:end + 1]
    counts = Counter(times)
    if (any(timestamp is None or counts[timestamp] != 1 for timestamp in selected)
            or any(delta != period for delta in _adjacent_intervals(selected))):
        raise TimeAxisError("所选原始片段包含无效或重复时间、时区口径不一致、逆序或采样间隔中断")

    output = metadata.get("analysis_columns")
    mapping = metadata.get("measurement_columns", {})
    if (not isinstance(output, (tuple, list)) or not output
            or any(not isinstance(name, str) for name in output) or len(set(output)) != len(output)
            or selection.time_column in output or not isinstance(mapping, Mapping)
            or mapping.get(selection.time_column, selection.time_column) is not None
            or set(mapping) - set(columns) or metadata.get("derived_measurement_semantics")):
        raise TimeAxisError("时序方案必须排除时间列，只允许保留或重命名原始测量列")
    indices: dict[str, int] = {}
    for index, column in enumerate(columns):
        destination = mapping.get(column, column)
        if destination is None:
            continue
        if not isinstance(destination, str) or destination not in output or destination in indices:
            raise TimeAxisError("时序方案的列映射必须一对一；每个排除的原始列必须显式标记为 null")
        indices[destination] = index
    if set(indices) != set(output):
        raise TimeAxisError("每个时序分析列必须对应一个冻结原始测量列")
    if set(metadata.get("variable_semantics", {})) - set(output):
        raise TimeAxisError("时序方案的字段语义只能描述 analysis_columns 内的指标，不能包含已排除的时间列或辅助列")
    return _segment(start, end, selection.time_column, times, period), [indices[name] for name in output], rows[start:end + 1], list(output)


def _numeric(value: str) -> Decimal:
    value = value.strip()
    if not _NUMBER.fullmatch(value):
        raise TimeAxisError("时序片段的测量值必须是无缺失的有限数值")
    try:
        result = Decimal(value)
        as_float = float(result)
        if math.isfinite(as_float) and (as_float != 0 or result.is_zero()):
            return result
    except (InvalidOperation, OverflowError):
        pass
    raise TimeAxisError("时序片段的测量值超出可分析的有限数值范围")


def prepare_time_series_plan(
    source: bytes, *, selection: TimeSeriesSelection, metadata: Mapping[str, Any],
) -> TimeSeriesWindow:
    window, indices, rows, _ = _source_plan(source, selection, metadata)
    for row in rows:
        for index in indices:
            _numeric(row[index])
    return window


def build_time_series_script(window: TimeSeriesWindow, metadata: Mapping[str, Any]) -> str:
    """Generate the replay script; the sandbox runs it after Job plan confirmation."""
    output = list(metadata["analysis_columns"])
    mapping = {destination: source for source, destination in metadata["measurement_columns"].items()
               if destination is not None}
    source_columns = [mapping.get(name, name) for name in output]
    return f'''import csv
from itertools import islice
from pathlib import Path
import sys

source, output = sys.argv[1:] or ("/workspace/raw/original.csv", "/workspace/output/prepared.csv")
with Path(source).open(encoding="utf-8-sig", newline="") as original:
    reader = csv.reader(original, strict=True)
    header = next(reader)
    positions = [header.index(name) for name in {source_columns!r}]
    with Path(output).open("w", encoding="utf-8", newline="") as prepared:
        writer = csv.writer(prepared)
        writer.writerow({output!r})
        writer.writerows([row[position] for position in positions]
                         for row in islice(reader, {window.start_row - 1}, {window.end_row}))
'''


def verify_time_series_candidate(
    *, source: bytes, source_hash: str, candidate: bytes, metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild the confirmed slice from frozen bytes, independent of the replay script."""
    if not source or hashlib.sha256(source).hexdigest() != source_hash:
        raise TimeAxisError("冻结原始 CSV 与 Job 记录的来源哈希不一致")
    try:
        confirmed = TimeSeriesWindow.model_validate(metadata.get("time_series"))
    except ValueError as exc:
        raise TimeAxisError("缺少有效的已确认时序片段") from exc
    window, indices, source_rows, expected_columns = _source_plan(source, confirmed, metadata)
    if window != confirmed:
        raise TimeAxisError("实际原始时间片段与已确认的起止时间、周期或行数不一致")
    columns, candidate_rows = _read_csv(candidate)
    if columns != expected_columns:
        raise TimeAxisError("时序候选列或列顺序与已确认映射不一致")
    for original, actual in zip_longest(source_rows, candidate_rows):
        if original is None or actual is None:
            raise TimeAxisError("时序候选行数与已确认片段不一致")
        if any(_numeric(original[index]) != _numeric(value) for index, value in zip(indices, actual)):
            raise TimeAxisError("时序候选的测量值或行顺序与冻结原始片段不一致")
    return window.verified_axis(source_hash)
