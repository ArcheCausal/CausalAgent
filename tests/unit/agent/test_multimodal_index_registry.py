"""多模态 active/previous/candidate 保留观测契约。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from Agent.knowledge_base.multimodal.index import ActiveIndexRegistry
from Agent.knowledge_base.multimodal.pipeline import MultimodalKnowledgeBaseMaintenance


def _publish(registry: ActiveIndexRegistry, root: Path, version: str) -> None:
    """向测试用 registry 写入一个最小 pointer。"""
    registry.publish(
        index_root=root,
        index_version=version,
        collection_name=f"collection_{version}",
        manifest_sha256=f"hash_{version}",
        embedding={"provider": "test", "model": "test"},
    )


def test_publish_preserves_previous_pointer_and_status_never_deletes_candidates():
    """发布新版本保留 previous，并且状态检查不删除候选目录。"""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "indexes"
        root.mkdir()
        for version in ("mm_first", "mm_second", "mm_candidate_a", "mm_candidate_b"):
            (root / version).mkdir()
        registry = ActiveIndexRegistry(Path(directory) / "runtime" / "active_index.json")

        _publish(registry, root, "mm_first")
        assert registry.read_previous() is None
        _publish(registry, root, "mm_second")

        snapshot = registry.retention_snapshot(root)
        assert snapshot["active"]["index_version"] == "mm_second"
        assert snapshot["previous"]["index_version"] == "mm_first"
        assert snapshot["candidates"] == ["mm_candidate_a", "mm_candidate_b"]
        assert snapshot["candidate_overflow"] is True
        assert (root / "mm_candidate_a").is_dir()
        assert (root / "mm_candidate_b").is_dir()

        service = object.__new__(MultimodalKnowledgeBaseMaintenance)
        service.registry = registry
        service.index_root = root
        status = service.status()
        assert status["active"]["index_version"] == "mm_second"
        assert status["previous"]["index_version"] == "mm_first"
        assert status["candidates"] == ["mm_candidate_a", "mm_candidate_b"]
