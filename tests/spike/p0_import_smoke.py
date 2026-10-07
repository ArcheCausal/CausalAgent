"""P0 clean-install import smoke for the Deep Agent/RAG dependency slice."""

from __future__ import annotations

import importlib
import json


MODULES = (
    "deepagents",
    "langchain",
    "langchain_core",
    "langchain_openai",
    "langgraph",
    "mcp",
    "httpx2",
    "ragas",
    "langchain_community.chat_models.vertexai",
)


def main() -> None:
    failures: list[str] = []
    for module_name in MODULES:
        try:
            importlib.import_module(module_name)
        except Exception:
            failures.append(module_name)
    if failures:
        print(json.dumps({"status": "failed", "modules": failures}))
        raise SystemExit(1)
    print(json.dumps({"status": "passed", "imports": len(MODULES)}))


if __name__ == "__main__":
    main()
