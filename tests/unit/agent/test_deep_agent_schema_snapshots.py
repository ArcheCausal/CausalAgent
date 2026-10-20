"""P1 Pydantic JSON Schema / Tool schema 固定快照。"""

from __future__ import annotations

import json
from pathlib import Path

from tests.unit.agent.schema_manifest import build_full_snapshot, derive_manifest


SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "deep_agent_schema_snapshot.json"
FULL_SNAPSHOT_PATH = (
    Path(__file__).parent / "snapshots" / "deep_agent_full_schema_snapshot.json"
)


def test_pydantic_and_tool_schema_snapshots_are_stable() -> None:
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert derive_manifest(build_full_snapshot()) == expected


def test_full_pydantic_and_tool_schema_snapshot_matches() -> None:
    expected = json.loads(FULL_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert build_full_snapshot() == expected
