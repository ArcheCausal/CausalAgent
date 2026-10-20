"""按当前契约重新生成 Deep Agent schema 快照。

只在模型或 Tool schema 按计划变更后运行；输出必须随对应代码一起评审。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.unit.agent.schema_manifest import build_full_snapshot, derive_manifest


SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
NEWLINE = chr(10)


def main() -> None:
    full = build_full_snapshot()
    manifest = derive_manifest(full)
    (SNAPSHOT_DIR / "deep_agent_schema_snapshot.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + NEWLINE,
        encoding="utf-8",
    )
    (SNAPSHOT_DIR / "deep_agent_full_schema_snapshot.json").write_text(
        json.dumps(full, ensure_ascii=False, indent=2) + NEWLINE,
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
