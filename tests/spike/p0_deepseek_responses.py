"""P0 real DeepSeek Responses API function-call contract spike.

The script intentionally prints only structural facts. Credentials, prompts,
provider IDs, model output, and token values are never printed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Agent.deep_agent_tools.models import FinalAnalysisDecision


@tool
def p0_alpha(value: str) -> str:
    """Return the alpha probe value."""

    return value


@tool
def p0_beta(value: str) -> str:
    """Return the beta probe value."""

    return value


def _build_model() -> ChatOpenAI:
    model_name = os.environ.get("DEEP_AGENT_MODEL") or os.environ.get("MODEL")
    base_url = os.environ.get("DEEP_AGENT_BASE_URL") or os.environ.get("BASE_URL")
    api_key = os.environ.get("DEEP_AGENT_API_KEY") or os.environ.get("API_KEY")
    if not all((model_name, base_url, api_key)):
        raise RuntimeError("DeepSeek configuration is incomplete")
    if model_name != "deepseek-v4-flash":
        raise RuntimeError("DeepSeek P0 requires deepseek-v4-flash")
    return ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=api_key,
        use_responses_api=True,
        output_version="responses/v1",
        reasoning={"effort": "none"},
        profile={"max_input_tokens": 1_000_000},
        streaming=False,
        timeout=90,
        max_retries=0,
    )


def _function_blocks(message: Any) -> list[dict[str, Any]]:
    return [
        block
        for block in (message.content if isinstance(message.content, list) else [])
        if isinstance(block, dict) and block.get("type") == "function_call"
    ]


def _single_call_probe(model: ChatOpenAI) -> dict[str, bool | int]:
    message = model.bind_tools([p0_alpha], tool_choice="required").invoke(
        [HumanMessage(content="Call p0_alpha exactly once with value p0.")]
    )
    return {
        "single_call": len(message.tool_calls) == 1
        and message.tool_calls[0].get("name") == "p0_alpha"
        and bool(message.tool_calls[0].get("id")),
        "single_response_id": isinstance(message.response_metadata.get("id"), str)
        and bool(message.response_metadata.get("id")),
        "single_function_block": len(_function_blocks(message)) == 1,
    }


def _multi_call_probe(model: ChatOpenAI) -> dict[str, bool | int]:
    human = HumanMessage(
        content=(
            "You must call both p0_alpha and p0_beta exactly once in this same "
            "response. Use value alpha for p0_alpha and beta for p0_beta. "
            "Return no prose."
        )
    )
    message = model.bind_tools(
        [p0_alpha, p0_beta],
        tool_choice="required",
    ).invoke([human])
    blocks = _function_blocks(message)
    names = {call.get("name") for call in message.tool_calls}
    block_by_name = {block.get("name"): block for block in blocks}
    mapped_call_ids = {
        call.get("name"): call.get("id")
        for call in message.tool_calls
        if call.get("name") and call.get("id")
    }
    provider_item_to_call = {
        block.get("id"): block.get("call_id")
        for block in blocks
        if block.get("id") and block.get("call_id")
    }
    provider_calls = set(provider_item_to_call.values())
    mapping_ok = all(
        block.get("id")
        and block.get("call_id")
        and block.get("call_id") == mapped_call_ids.get(block.get("name"))
        and provider_item_to_call.get(block.get("id")) == block.get("call_id")
        for block in blocks
    )
    tool_messages = [
        ToolMessage(
            content=f"result-{block['name']}",
            tool_call_id=block["call_id"],
        )
        for block in reversed(blocks)
    ]
    follow_up = [
        human,
        message,
        *tool_messages,
    ]
    final = model.invoke(follow_up)
    return {
        "multi_call_count": len(message.tool_calls),
        "multi_calls": len(message.tool_calls) == 2 and names == {"p0_alpha", "p0_beta"},
        "multi_distinct_call_ids": len(mapped_call_ids.values())
        == len(set(mapped_call_ids.values())),
        "multi_function_blocks": len(blocks) == 2
        and set(block_by_name) == {"p0_alpha", "p0_beta"},
        "response_id_mapped": isinstance(message.response_metadata.get("id"), str)
        and bool(message.response_metadata.get("id")),
        "item_and_call_ids_mapped": mapping_ok
        and len(provider_item_to_call) == len(blocks)
        and len(provider_calls) == len(blocks)
        and set(provider_calls) == set(mapped_call_ids.values()),
        "tool_outputs_reassociated": len(tool_messages) == len(provider_calls)
        and set(item.tool_call_id for item in tool_messages) == provider_calls,
        "follow_up_received": isinstance(final, AIMessage),
    }


def _structured_probe(model: ChatOpenAI) -> bool:
    structured = model.with_structured_output(
        FinalAnalysisDecision,
        method="function_calling",
    )
    result = structured.invoke(
        [
            HumanMessage(
                content=(
                    "Return an evidence_only FinalAnalysisDecision. Use no primary "
                    "result, no assessments, conflict_status none, "
                    "selection_rationale p0, confidence low, and an empty "
                    "confidence_basis."
                )
            )
        ]
    )
    return isinstance(result, FinalAnalysisDecision)


def main() -> None:
    try:
        model = _build_model()
        result: dict[str, Any] = {
            "model_target": True,
            **_single_call_probe(model),
            **_multi_call_probe(model),
            "structured_output": _structured_probe(model),
        }
        if not all(bool(value) for value in result.values()):
            raise RuntimeError("DeepSeek Responses P0 assertion failed")
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
    print(json.dumps({"status": "passed", **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
