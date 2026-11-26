"""真实模型与 Web 工具的显式验收，不创建业务 Job 或模拟外部返回。"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from deepagents.backends import StateBackend
import httpx
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.store.memory import InMemoryStore

from Agent.deep_agent.context import AgentRunContext, TrustedJobIdentity
from Agent.deep_agent.graph import DeepAgentGraphConfig, build_deep_agent
from Agent.deep_agent.model import DeepSeekResponsesChatOpenAI
from Agent.deep_agent.state import initial_deep_agent_state
from Agent.deep_agent_tools.adapters import build_default_adapters
from Agent.deep_agent_tools.algorithm_tools import build_algorithm_tools
from Agent.deep_agent_tools.mcp_algorithm_executor import McpAlgorithmExecutor
from Agent.deep_agent_tools.models import DataProfile, FinalAnalysisDecision
from Agent.deep_agent_tools.rag_evidence_tool import build_default_rag_evidence_tool
from Agent.deep_agent_tools.registry import build_default_registry
from Agent.deep_agent_tools.web_evidence_tool import build_default_web_evidence_tool
from app.agent.worker.mcp_client_pool import McpClientPool, McpClientPoolConfig
from config.secret_file import require_secret


async def run() -> dict:
    model_name = os.environ["DEEP_AGENT_MODEL"].strip()
    base_url = (os.getenv("DEEP_AGENT_BASE_URL") or os.environ["BASE_URL"]).strip()
    current_run = ContextVar("visibility_smoke_run")
    requests_by_run = {"enabled": [], "disabled": []}

    async def capture(request):
        payload = json.loads(request.content)
        # 保留真实出站工具名，继续使用原始 HTTP 传输，不记录提示词或凭据。
        requests_by_run[current_run.get()].append([
            tool.get("name") or tool.get("function", {}).get("name")
            for tool in payload.get("tools", [])
        ])

    async with httpx.AsyncClient(event_hooks={"request": [capture]}) as client:
        model_class = DeepSeekResponsesChatOpenAI if model_name.startswith("deepseek-") else ChatOpenAI
        model = model_class(
            api_key=require_secret("DEEP_AGENT_API_KEY"), base_url=base_url,
            model=model_name, streaming=True, use_responses_api=True,
            output_version="responses/v1",
            reasoning={"effort": os.getenv("DEEP_AGENT_REASONING_EFFORT", "high").strip()},
            http_async_client=client, timeout=60, max_retries=0,
        )
        pool = McpClientPool(McpClientPoolConfig.from_env())
        executor = McpAlgorithmExecutor(
            pool, signing_key=require_secret("CAUSAL_MCP_SIGNING_KEY_CURRENT"),
        )
        registry = build_default_registry(build_default_adapters(executor=executor))
        graph = build_deep_agent(
            model=model, domain_tools=[
                *build_algorithm_tools(registry),
                build_default_rag_evidence_tool(), build_default_web_evidence_tool(),
            ],
            backend=StateBackend(), store=InMemoryStore(), registry=registry,
            config=DeepAgentGraphConfig(
                model_name=model_name, context_window_tokens=1000000,
            ),
        )

        async def invoke(label, enabled):
            token = current_run.set(label)
            try:
                context = AgentRunContext(
                    execution_guard=None, web_search_enabled=enabled,
                    trusted_identity=TrustedJobIdentity(
                        job_id=str(uuid4()), session_id=str(uuid4()), user_id=7,
                        attempt_count=1, lease_epoch=1, worker_id="visibility-smoke",
                        input_identity="no-dataset-visibility-smoke",
                    ),
                )
                state = initial_deep_agent_state(
                    data_profile=DataProfile(row_count=0, column_count=0),
                    messages=[HumanMessage(content=(
                        "本次没有数据，不运行算法，不读取文件或知识库。请简短说明因果发现"
                        "中隐藏混杂的局限；遵守系统提示中的学术搜索条件，搜索时使用英文"
                        "causal discovery latent confounding，max_results=1。"
                        "没有数据时用 evidence_only 的 FinalAnalysisDecision 提交，"
                        "不要虚构算法结果或证据。"
                    ))],
                )
                result = await asyncio.wait_for(
                    graph.ainvoke(state, context=context), timeout=240,
                )
                decision = FinalAnalysisDecision.model_validate(result["structured_response"])
                tool_sets = requests_by_run[label]
                assert tool_sets
                assert all(("web_evidence_search" in names) is enabled for names in tool_sets)
                assert all("FinalAnalysisDecision" in names for names in tool_sets)
                web_messages = [
                    json.loads(message.content) for message in result["messages"]
                    if isinstance(message, ToolMessage) and message.name == "web_evidence_search"
                ]
                records = [record for record in result["action_ledger"].values()
                           if record.tool_name == "web_evidence_search"]
                assert bool(web_messages) is enabled
                assert bool(records) is enabled
                assert all(record.final_status != "pending" and record.attempts for record in records)
                statuses = [message["status"] for message in web_messages]
                return {
                    "run": label, "model_validation": "passed",
                    "structured_outcome": decision.outcome,
                    "model_requests": len(tool_sets), "request_tool_names": tool_sets,
                    "web_statuses": statuses,
                    "web_ledger_statuses": [record.final_status for record in records],
                    "searxng_success": any(status in {"available", "no_results"} for status in statuses),
                }
            except Exception as exc:
                # 只报告异常类型和状态码，不输出 SDK 错误正文、模型输入或凭据。
                return {
                    "run": label, "model_validation": "failed",
                    "error_type": type(exc).__name__,
                    "http_status": getattr(exc, "status_code", None),
                    "model_requests": len(requests_by_run[label]),
                    "request_tool_names": requests_by_run[label],
                }
            finally:
                current_run.reset(token)

        runs = await asyncio.gather(invoke("enabled", True), invoke("disabled", False))
    return {
        "runs": runs,
        "job_validation": "not_run",
        "postgres_recovery_validation": "not_run",
    }


if __name__ == "__main__":
    report = asyncio.run(run())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if all(item["model_validation"] == "passed"
                      for item in report["runs"]) else 1)
