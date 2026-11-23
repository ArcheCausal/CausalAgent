"""泄漏审计：扫描模型工具调用里出现的敏感路径/关键词。"""
from __future__ import annotations

import json

# .npz 不列入：模型自己保存中间结果会误报
LEAK_TOKENS = ("cdfm_bench", "causalagent3", "rep00", "/home/", "~/")


def scan(events) -> list[dict]:
    hits = []
    for event in events:
        content = (event.get("message") or {}).get("content") or ()
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            text = json.dumps(block.get("input") or {}, ensure_ascii=False).lower()
            for token in LEAK_TOKENS:
                if token in text:
                    hits.append({
                        "tool": block.get("name"),
                        "token": token,
                        "input": text[:300],
                    })
    return hits
