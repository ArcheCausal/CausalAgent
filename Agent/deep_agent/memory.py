"""受控虚拟文件、长期记忆 namespace 和 raw result backend。

这是 P2-U 的协议级实现。真实 Deep Agents ``StateBackend``/``StoreBackend``
在可选依赖存在时由 ``build_official_backend`` 装配；本文件的内存 backend
用于隔离测试并保持同样的路径、权限和 create-if-absent 语义。
"""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from Agent.deep_agent_tools.models import RawResultMetadata, build_raw_result_metadata, canonical_json_bytes
from .profile import (
    DEFAULT_FILESYSTEM_PERMISSIONS,
    FilesystemPermission,
    is_filesystem_write_allowed,
    normalize_virtual_path,
)


MEMORY_PATHS: tuple[str, ...] = (
    "/memories/preferences.md",
    "/memories/research_background.md",
)
MEMORY_NAMESPACE_ROOT: tuple[str, ...] = ("causalagent", "memory")
MEMORY_TEMPLATES: dict[str, bytes] = {
    "/memories/preferences.md": "# Preferences\n\n".encode("utf-8"),
    "/memories/research_background.md": "# Research background\n\n".encode("utf-8"),
}


class MemoryPermissionError(PermissionError):
    """模型写入未被精确 allow 的虚拟路径。"""


class MemoryConflictError(RuntimeError):
    """create-if-absent/CAS 发现已有内容。"""


class VirtualFileBackend(Protocol):
    def read(self, path: str) -> bytes | None:
        ...

    def write(self, path: str, content: bytes, *, overwrite: bool = True) -> None:
        ...


@dataclass
class StateBackend:
    """当前 graph invocation 的短期虚拟文件。"""

    files: MutableMapping[str, bytes] = field(default_factory=dict)

    def read(self, path: str) -> bytes | None:
        return self.files.get(normalize_virtual_path(path))

    def write(self, path: str, content: bytes, *, overwrite: bool = True) -> None:
        normalized = normalize_virtual_path(path)
        if not overwrite and normalized in self.files:
            raise MemoryConflictError(f"virtual file already exists: {normalized}")
        self.files[normalized] = bytes(content)

    def create_if_absent(self, path: str, content: bytes) -> bool:
        normalized = normalize_virtual_path(path)
        if normalized in self.files:
            return False
        self.files[normalized] = bytes(content)
        return True


@dataclass
class StoreBackend:
    """按可信 namespace 隔离的长期虚拟文件 Store。"""

    namespace: tuple[str, ...]
    store: MutableMapping[tuple[str, ...], MutableMapping[str, bytes]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        self.namespace = tuple(self.namespace)
        if not self.namespace or any(not part for part in self.namespace):
            raise ValueError("store namespace must contain non-empty parts")

    @property
    def files(self) -> MutableMapping[str, bytes]:
        return self.store.setdefault(self.namespace, {})

    def read(self, path: str) -> bytes | None:
        return self.files.get(normalize_virtual_path(path))

    def write(self, path: str, content: bytes, *, overwrite: bool = True) -> None:
        normalized = normalize_virtual_path(path)
        if not overwrite and normalized in self.files:
            raise MemoryConflictError(f"memory file already exists: {normalized}")
        self.files[normalized] = bytes(content)

    def create_if_absent(self, path: str, content: bytes) -> bool:
        normalized = normalize_virtual_path(path)
        if normalized in self.files:
            return False
        self.files[normalized] = bytes(content)
        return True


@dataclass
class CompositeBackend:
    """按路径把 memory 路由到 Store，其余文件路由到 State。"""

    default: StateBackend
    store_backend: StoreBackend
    permissions: tuple[FilesystemPermission, ...] = DEFAULT_FILESYSTEM_PERMISSIONS

    def _backend(self, path: str) -> StateBackend | StoreBackend:
        normalized = normalize_virtual_path(path)
        return self.store_backend if normalized.startswith("/memories/") else self.default

    def read(self, path: str) -> bytes | None:
        return self._backend(path).read(path)

    def write_trusted(
        self,
        path: str,
        content: bytes | str,
        *,
        overwrite: bool = True,
    ) -> None:
        """可信 Adapter/Bootstrap 写入，不经过模型 filesystem permission。"""

        payload = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        self._backend(path).write(path, payload, overwrite=overwrite)

    def create_if_absent(self, path: str, content: bytes | str) -> bool:
        payload = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        backend = self._backend(path)
        return backend.create_if_absent(path, payload)

    def model_read_file(self, path: str) -> str | None:
        payload = self.read(path)
        return None if payload is None else payload.decode("utf-8")

    def model_edit_file(self, path: str, content: str) -> None:
        normalized = normalize_virtual_path(path)
        if not is_filesystem_write_allowed(normalized, permissions=self.permissions):
            raise MemoryPermissionError(f"model write denied: {normalized}")
        self.write_trusted(normalized, content, overwrite=True)

    def registered_filesystem_tools(self) -> tuple[str, str]:
        """P2-U 只注册两个内置文件工具。"""

        return ("read_file", "edit_file")

    def write_raw_result(
        self,
        *,
        raw_result_ref: str,
        raw_result: Any,
        max_size_bytes: int = 2_000_000,
    ) -> RawResultMetadata:
        """可信 Adapter 写入 StateBackend 的 canonical raw JSON 并回读校验。"""

        metadata = build_raw_result_metadata(
            raw_result_ref=raw_result_ref,
            raw_result=raw_result,
        )
        if metadata.raw_result_size_bytes > max_size_bytes:
            raise ValueError("raw algorithm result exceeds the configured size limit")
        payload = canonical_json_bytes(raw_result)
        self.write_trusted(raw_result_ref, payload, overwrite=True)
        stored = self.read(raw_result_ref)
        if stored != payload:
            raise MemoryConflictError("raw result read-back mismatch")
        verified = build_raw_result_metadata(
            raw_result_ref=raw_result_ref,
            raw_result=raw_result,
        )
        if verified != metadata:
            raise MemoryConflictError("raw result metadata changed during read-back")
        return metadata


def memory_namespace_for_user(user_id: int) -> tuple[str, ...]:
    """按可信 user_id 构造长期记忆 Store namespace。"""

    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("user_id must be a positive integer")
    return (*MEMORY_NAMESPACE_ROOT, str(user_id))


def trusted_memory_namespace(value: Any) -> tuple[str, ...]:
    """从可信 runtime 身份提取 user namespace，不读取模型参数。"""

    identity = value
    # Deep Agents/StoreBackend 传入的是 LangGraph Runtime，而可信身份位于
    # runtime.context。不能退回模型可控的 tool 参数或普通 State 字段。
    if hasattr(value, "context"):
        context = getattr(value, "context", None)
        if context is not None:
            identity = context
    if hasattr(identity, "trusted_identity"):
        identity = getattr(identity, "trusted_identity")

    if hasattr(identity, "user_id"):
        user_id = getattr(identity, "user_id")
    elif isinstance(identity, int):
        user_id = identity
    else:
        # 保留官方 runtime.server_info.user.identity 的兼容读取，但只接受正整数。
        server_info = getattr(value, "server_info", None)
        user = getattr(server_info, "user", None)
        user_id = getattr(user, "identity", None)
    if isinstance(user_id, str) and user_id.isdigit():
        user_id = int(user_id)
    if not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("trusted runtime user identity is unavailable")
    return memory_namespace_for_user(user_id)


def initialize_memory_files(backend: CompositeBackend) -> tuple[str, ...]:
    """仅缺失时创建两个长期记忆文件，绝不覆盖已有内容。"""

    created: list[str] = []
    for path in MEMORY_PATHS:
        if backend.create_if_absent(path, MEMORY_TEMPLATES[path]):
            created.append(path)
    return tuple(created)


def build_in_memory_backend(
    *,
    user_id: int,
    state_files: MutableMapping[str, bytes] | None = None,
    store: MutableMapping[tuple[str, ...], MutableMapping[str, bytes]] | None = None,
) -> CompositeBackend:
    """构造隔离测试用 backend。传入 ``store`` 可模拟跨 Job/重启保留。"""

    namespace = memory_namespace_for_user(user_id)
    backend = CompositeBackend(
        default=StateBackend(files=state_files if state_files is not None else {}),
        store_backend=StoreBackend(
            namespace=namespace,
            store=store if store is not None else {},
        ),
    )
    initialize_memory_files(backend)
    return backend


def build_official_backend(*, store: Any, namespace: Callable[[Any], tuple[str, ...]]) -> Any:
    """惰性装配 Deep Agents 官方 backend；缺依赖时明确失败。"""

    try:
        from deepagents.backends import CompositeBackend as OfficialCompositeBackend
        from deepagents.backends import StateBackend as OfficialStateBackend
        from deepagents.backends import StoreBackend as OfficialStoreBackend
    except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover
        raise RuntimeError("Deep Agents backend dependencies are not installed") from exc

    return OfficialCompositeBackend(
        default=OfficialStateBackend(),
        routes={
            "/memories/": OfficialStoreBackend(namespace=namespace, store=store),
        },
    )
