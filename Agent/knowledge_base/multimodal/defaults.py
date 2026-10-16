"""多模态生产默认配置的无网络解析层。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .contracts import sha256_bytes, stable_id


ROOT = Path(__file__).resolve().parents[3]
DEFAULTS_PATH = Path(__file__).with_name("production_defaults.json")


def load_production_defaults(path: Path = DEFAULTS_PATH) -> dict[str, Any]:
    """读取并校验冻结配置的必要字段。"""
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema_version") != "multimodal_production_defaults_v1":
        raise ValueError("unsupported multimodal production defaults schema")
    if not config.get("sources") or not config.get("evaluation", {}).get("dataset_path"):
        raise ValueError("production defaults require sources and an evaluation dataset")
    if not config.get("controlled_source_directories"):
        # 旧配置没有受控目录字段时，只能把其声明路径的父目录作为兼容边界。
        config["controlled_source_directories"] = sorted({Path(str(source["path"])).parent.as_posix() for source in config["sources"]})
    if any(not isinstance(source.get("page_count"), int) or source["page_count"] < 1 for source in config["sources"]):
        raise ValueError("production defaults require positive source page counts")
    embedding = config.get("embedding")
    if not isinstance(embedding, dict) or embedding.get("mode") not in {"api", "local"}:
        raise ValueError("production defaults require an explicit embedding mode")
    if isinstance(embedding.get("dimension"), bool) or not isinstance(embedding.get("dimension"), int) or embedding["dimension"] < 1:
        raise ValueError("production defaults require a positive embedding dimension")
    local_embedding = config.get("local_embedding", {})
    if not isinstance(local_embedding, dict) or not isinstance(local_embedding.get("enabled", False), bool):
        raise ValueError("production defaults local_embedding.enabled must be boolean")
    return config


def canonical_source_id(source: dict[str, Any]) -> str:
    """返回来源配置声明的稳定身份，旧配置回退到规范键。"""
    value = source.get("source_id") or source.get("canonical_source_id")
    if isinstance(value, str) and value:
        return value
    return stable_id("source", {"canonical_key": source.get("canonical_key") or Path(str(source["path"])).stem.removesuffix("(1)")})


def canonical_document_id(source: dict[str, Any]) -> str:
    """返回正式 gold 使用的稳定 document_id，兼容旧文件名算法。"""
    value = source.get("document_id")
    if isinstance(value, str) and value.startswith("doc_"):
        return value
    return stable_id("doc", {"path": Path(str(source["path"])).name, "content_hash": source["sha256"]})


def resolve_production_sources(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """在受控目录内按 SHA-256 唯一解析正式来源并校验格式。"""
    config = config or load_production_defaults()
    controlled_roots: list[Path] = []
    for raw_root in config["controlled_source_directories"]:
        root = (ROOT / Path(raw_root)).resolve()
        try:
            root.relative_to(ROOT)
        except ValueError as exc:
            raise ValueError("controlled production source directory must stay inside the repository") from exc
        controlled_roots.append(root)

    resolved: list[dict[str, Any]] = []
    for source in config["sources"]:
        relative = Path(source["path"])
        expected_suffix = relative.suffix.lower()
        def inside(candidate: Path, root: Path) -> bool:
            try:
                candidate.resolve().relative_to(root)
                return True
            except ValueError:
                return False

        hits = [
            candidate
            for root in controlled_roots
            if root.is_dir()
            for candidate in root.rglob("*")
            if candidate.is_file() and inside(candidate, root) and sha256_bytes(candidate.read_bytes()) == source["sha256"]
        ]
        if len(hits) != 1:
            raise ValueError(
                f"production source hash must match exactly one controlled file: {relative.as_posix()} ({len(hits)} matches)"
            )
        path = hits[0].resolve()
        if path.suffix.lower() != expected_suffix:
            raise ValueError(f"production source extension mismatch: {path.name}")
        from .parsers import inspect_source

        issue = inspect_source(path)
        if issue is not None:
            raise ValueError(f"production source signature mismatch: {path.name}")
        resolved.append(
            {
                "source_id": canonical_source_id(source),
                "document_id": canonical_document_id(source),
                "path": path,
                "configured_path": relative.as_posix(),
                "relative_path": path.relative_to(ROOT).as_posix(),
                "content_hash": source["sha256"],
                "page_count": source["page_count"],
                "remote_authorized": bool(source.get("remote_authorized", False)),
            }
        )
    return resolved


def production_source_paths(config: dict[str, Any] | None = None) -> list[Path]:
    """返回受控目录内唯一命中的正式来源路径。"""
    return [source["path"] for source in resolve_production_sources(config)]


def resolve_production_source_metadata(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """只读清单元数据解析正式来源身份，文件缺失时标记不可用而不抛异常。

    该函数不扫描受控目录、不读取来源内容，也不校验文件签名和哈希，因此可以在
    没有安装正式 PDF 的环境（例如只挂载了评测卷的预发应用容器）中列出正式来源。
    发布、评测和远程视觉授权仍必须使用严格语义的
    :func:`resolve_production_sources`，不能用本函数的输出代替正式身份校验。
    """
    config = config or load_production_defaults()
    resolved: list[dict[str, Any]] = []
    for source in config["sources"]:
        configured = Path(str(source["path"]))
        candidate: Path | None = None
        try:
            inside = (ROOT / configured).resolve()
            inside.relative_to(ROOT)
            candidate = inside
        except (OSError, ValueError):
            candidate = None
        available = bool(candidate is not None and candidate.is_file())
        size_bytes: int | None = None
        if available:
            try:
                size_bytes = candidate.stat().st_size
            except OSError:
                size_bytes = None
        resolved.append(
            {
                "source_id": canonical_source_id(source),
                "document_id": canonical_document_id(source),
                "configured_path": configured.as_posix(),
                "content_hash": source["sha256"],
                "page_count": source["page_count"],
                "path": candidate,
                "available": available,
                "size_bytes": size_bytes,
                "remote_authorized": bool(source.get("remote_authorized", False)),
            }
        )
    return resolved


def resolve_production_embedding_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """从受版本控制的策略解析正式 embedding；生产默认使用 API key。"""
    config = config or load_production_defaults()
    embedding = config["embedding"]
    mode = str(embedding.get("mode") or "")
    if mode == "api":
        env_config = config.get("embedding_env") or {}
        api_key_env = str(env_config.get("api_key_env") or "EMBEDDING_API_KEY")
        base_url_env = str(env_config.get("base_url_env") or "EMBEDDING_BASE_URL")
        model_env = str(env_config.get("model_env") or "EMBEDDING_MODEL")
        missing = [
            name
            for name, configured in (
                (api_key_env, bool(os.environ.get(api_key_env))),
                (base_url_env, bool(os.environ.get(base_url_env))),
            )
            if not configured
        ]
        endpoint_identity = os.environ.get(base_url_env, "")
        return {
            "status": "ready" if not missing else "missing",
            "mode": "api",
            "provider": embedding["provider"],
            "provider_setting": "frozen",
            "model": embedding["model"],
            "dimension": embedding["dimension"],
            "normalized": embedding["normalized"],
            "endpoint_identity": endpoint_identity,
            "api_key_env": api_key_env,
            "base_url_env": base_url_env,
            "model_env": model_env,
            "missing": missing,
            "message": "配置完整" if not missing else "缺少 " + ", ".join(missing),
        }
    if mode != "local" or not config.get("local_embedding", {}).get("enabled", False):
        raise ValueError("local production embedding is disabled")
    if embedding["provider"] != "huggingface":
        raise ValueError("only the frozen local huggingface embedding is supported")
    model_path = ROOT / "Agent" / "knowledge_base" / "models" / embedding["model"]
    path_exists = model_path.is_dir()
    actual_dimension = None
    model_config = model_path / "config.json"
    if model_config.is_file():
        actual_dimension = json.loads(model_config.read_text(encoding="utf-8")).get("hidden_size")
    if actual_dimension is not None and actual_dimension != embedding["dimension"]:
        raise ValueError("frozen embedding dimension does not match local model config")
    return {
        "status": "ready" if path_exists else "missing",
        "mode": "local",
        "provider": embedding["provider"],
        "provider_setting": "frozen",
        "model": embedding["model"],
        "dimension": embedding["dimension"],
        "normalized": embedding["normalized"],
        "path": str(model_path.resolve()),
        "path_exists": path_exists,
        "missing": [] if path_exists else [str(model_path)],
        "message": "本地模型目录存在" if path_exists else "冻结的本地 embedding 模型目录不存在",
    }
