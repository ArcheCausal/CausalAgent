"""把 LangChain 每次 LLM 调用的 token 用量累计到当前 Job attempt。"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

USAGE_FIELDS = ("input_tokens", "output_tokens", "total_tokens")


class UsageCollector(BaseCallbackHandler):
    """线程安全地累加 usage_metadata；snapshot 返回累计快照。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._totals = {name: 0 for name in USAGE_FIELDS}

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        found = False
        for batch in getattr(response, "generations", None) or ():
            for generation in batch:
                message = getattr(generation, "message", None)
                usage = getattr(message, "usage_metadata", None)
                if isinstance(usage, Mapping):
                    self._add(usage)
                    found = True
        if found:
            return
        llm_output = getattr(response, "llm_output", None)
        if isinstance(llm_output, Mapping):
            token_usage = llm_output.get("token_usage")
            if isinstance(token_usage, Mapping):
                self._add(
                    {
                        "input_tokens": token_usage.get("prompt_tokens"),
                        "output_tokens": token_usage.get("completion_tokens"),
                        "total_tokens": token_usage.get("total_tokens"),
                    }
                )

    def _add(self, usage: Mapping[str, Any]) -> None:
        values: dict[str, int] = {}
        for name in USAGE_FIELDS:
            value = usage.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                value = 0
            values[name] = value
        if not any(values.values()):
            return
        with self._lock:
            for name, value in values.items():
                self._totals[name] += value

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._totals)
