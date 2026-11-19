"""从 stream-json 日志提取事件与工具调用。"""
from __future__ import annotations

import csv
import json
from pathlib import Path

HEADER = ["case", "dataset", "event", "tool", "input_preview"]


def load_events(path) -> list[dict]:
    events: list[dict] = []
    path = Path(path)
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def tool_rows(events, case_name: str, dataset: str) -> list[dict]:
    rows = []
    for index, event in enumerate(events):
        content = (event.get("message") or {}).get("content") or ()
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                rows.append({
                    "case": case_name,
                    "dataset": dataset,
                    "event": index,
                    "tool": block.get("name", ""),
                    "input_preview": json.dumps(block.get("input") or {}, ensure_ascii=False)[:200],
                })
    return rows


def append_csv(path, rows) -> None:
    if not rows:
        return
    path = Path(path)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig" if new else "utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        if new:
            writer.writeheader()
        writer.writerows(rows)
