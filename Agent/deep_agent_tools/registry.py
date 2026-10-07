"""AlgorithmSpec 的只读索引与受控 Adapter 绑定。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .algorithm_specs import AlgorithmSpec, DEFAULT_ALGORITHM_SPECS


class RegistryConflictError(ValueError):
    """capability/tool 名称或绑定发生冲突。"""


class UnknownCapabilityError(LookupError):
    """请求了没有在静态 Registry 中登记的 capability。"""


@dataclass(frozen=True)
class RegistryEntry:
    """一个 Spec 与一个经代码审查的 Adapter 实例绑定。"""

    spec: AlgorithmSpec
    adapter: object


class AlgorithmRegistry:
    """只读查询面；不会从 MCP ``list_tools`` 或用户输入动态注册工具。"""

    def __init__(self, entries: Iterable[RegistryEntry] = ()) -> None:
        self._by_capability: dict[str, RegistryEntry] = {}
        self._by_tool_name: dict[str, RegistryEntry] = {}
        for entry in entries:
            self.register(spec=entry.spec, adapter=entry.adapter)

    def register(self, *, spec: AlgorithmSpec, adapter: object) -> RegistryEntry:
        """登记一对一 Spec/Adapter 绑定并执行启动期契约检查。"""

        if spec.capability_id in self._by_capability:
            raise RegistryConflictError(
                f"capability_id already registered: {spec.capability_id}"
            )
        if spec.tool_name in self._by_tool_name:
            raise RegistryConflictError(f"tool_name already registered: {spec.tool_name}")

        # 在注册时触发 schema 生成；失败应在 worker bootstrap/readiness 前暴露。
        spec.model_input_schema.model_json_schema()
        spec.result_contract.model_json_schema()
        entry = RegistryEntry(spec=spec, adapter=adapter)
        self._by_capability[spec.capability_id] = entry
        self._by_tool_name[spec.tool_name] = entry
        return entry

    def get(self, capability_id: str) -> RegistryEntry:
        try:
            return self._by_capability[capability_id]
        except KeyError as exc:
            raise UnknownCapabilityError(capability_id) from exc

    def get_by_tool_name(self, tool_name: str) -> RegistryEntry:
        try:
            return self._by_tool_name[tool_name]
        except KeyError as exc:
            raise UnknownCapabilityError(tool_name) from exc

    def __contains__(self, capability_id: object) -> bool:
        return capability_id in self._by_capability

    def __len__(self) -> int:
        return len(self._by_capability)

    @property
    def entries(self) -> tuple[RegistryEntry, ...]:
        return tuple(
            self._by_capability[key] for key in sorted(self._by_capability)
        )

    @property
    def specs(self) -> tuple[AlgorithmSpec, ...]:
        return tuple(entry.spec for entry in self.entries)

    @property
    def by_capability(self) -> Mapping[str, RegistryEntry]:
        """提供不允许调用方修改的 capability 索引视图。"""

        return MappingProxyType(dict(self._by_capability))

    def tool_schemas(self) -> list[dict[str, Any]]:
        """从已登记 Spec 生成模型可见 tool schema。"""

        return [entry.spec.build_tool_schema() for entry in self.entries]

    def spec_digests(self) -> dict[str, str]:
        return {
            entry.spec.capability_id: entry.spec.spec_digest for entry in self.entries
        }


def build_default_registry(
    adapters: Mapping[str, object],
    *,
    specs: Iterable[AlgorithmSpec] = DEFAULT_ALGORITHM_SPECS,
) -> AlgorithmRegistry:
    """使用主程序固定 Spec 和显式 Adapter 映射构造 Registry。"""

    registry = AlgorithmRegistry()
    for spec in specs:
        try:
            adapter = adapters[spec.capability_id]
        except KeyError as exc:
            raise UnknownCapabilityError(
                f"missing adapter for {spec.capability_id}"
            ) from exc
        registry.register(spec=spec, adapter=adapter)
    return registry

