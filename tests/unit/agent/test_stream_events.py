import os
import unittest

from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.graph import END, StateGraph
from langgraph.types import RetryPolicy


TEST_ENV = {
    "SECRET_KEY": "test-secret",
    "API_KEY": "test-api-key",
    "BASE_URL": "https://example.test",
    "MODEL": "test-model",
    "MYSQL_HOST": "test-mysql",
    "MYSQL_USER": "test-user",
    "MYSQL_PASSWORD": "test-password",
    "MYSQL_DATABASE": "test-database",
}
for key, value in TEST_ENV.items():
    os.environ.setdefault(key, value)

from app.agent.worker.event_adapter import LangGraphEventAdapter  # noqa: E402
from app.agent.routes import _public_event_payload  # noqa: E402
from Agent.causal_agent.graph_utils import bind_node, bind_runnable_node  # noqa: E402
from Agent.causal_agent.state import CausalAgentState  # noqa: E402


def task_start(task_id: str, name: str) -> dict:
    """构造 LangGraph v2 tasks 开始事件。"""
    return {
        "type": "tasks",
        "ns": (),
        "data": {"id": task_id, "name": name, "input": {}, "triggers": []},
    }


class StreamEventAdapterTests(unittest.TestCase):
    """验证多流转换不会暴露内部内容，并保持阶段和文字实例有序。"""

    def setUp(self):
        self.adapter = LangGraphEventAdapter("job-1", 3)

    def test_repeated_node_tasks_get_distinct_step_ids(self):
        """父图循环再次进入同名节点时必须创建新的阶段实例。"""
        first = self.adapter.convert(task_start("task-a", "agent"))[0]
        second = self.adapter.convert(task_start("task-b", "agent"))[0]

        self.assertNotEqual(first["step_id"], second["step_id"])
        self.assertEqual(first["attempt"], 3)

    def test_only_public_answer_nodes_emit_text_chunks(self):
        """内部 planner 被过滤，公开回答和报告 token 保持顺序。"""
        self.adapter.convert(task_start("task-chat", "normal_chat"))
        planner = self.adapter.convert({
            "type": "messages",
            "ns": (),
            "data": (AIMessageChunk(content="hidden"), {"langgraph_node": "mcp_planner"}),
        })
        self.adapter.convert(task_start("task-report", "report"))
        report = self.adapter.convert({
            "type": "messages",
            "ns": (),
            "data": (AIMessageChunk(content="hidden"), {"langgraph_node": "report"}),
        })
        first = self.adapter.convert({
            "type": "messages",
            "ns": (),
            "data": (AIMessageChunk(content="你"), {"langgraph_node": "normal_chat"}),
        })[0]
        second = self.adapter.convert({
            "type": "messages",
            "ns": (),
            "data": (AIMessageChunk(content="好"), {"langgraph_node": "normal_chat"}),
        })[0]

        self.assertEqual(planner, [])
        self.assertEqual(report[0]["type"], "text_chunk")
        self.assertEqual(report[0]["delta"], "hidden")
        self.assertEqual((first["sequence"], second["sequence"]), (1, 2))
        self.assertEqual(first["stream_id"], second["stream_id"])

    def test_public_decision_tool_call_chunks_emit_decision_chunks(self):
        """工具参数流只提取公开决策摘要，不把半截 JSON 或隐藏内容外带。"""
        self.adapter.convert(task_start("task-deep", "deep_agent"))
        first = self.adapter.convert({
            "type": "messages",
            "ns": ("deep_agent:task-deep",),
            "data": (
                AIMessageChunk(
                    content="",
                    tool_call_chunks=[{
                        "name": "causal_pc",
                        "args": '{"public_decision":{"summary":"连续数据适合',
                        "id": "call-1",
                        "index": 0,
                    }],
                ),
                {"langgraph_node": "model"},
            ),
        })
        second = self.adapter.convert({
            "type": "messages",
            "ns": ("deep_agent:task-deep",),
            "data": (
                AIMessageChunk(
                    content="",
                    tool_call_chunks=[{
                        "name": None,
                        "args": '使用 PC。"}}',
                        "id": None,
                        "index": 0,
                    }],
                ),
                {"langgraph_node": "model"},
            ),
        })

        self.assertEqual(first[0]["type"], "decision_chunk")
        self.assertEqual(first[0]["decision_kind"], "algorithm")
        self.assertEqual(first[0]["tool_name"], "causal_pc")
        self.assertEqual(first[0]["delta"], "连续数据适合")
        self.assertEqual(second[0]["delta"], "使用 PC。")
        self.assertEqual((first[0]["sequence"], second[0]["sequence"]), (1, 2))
        self.assertEqual(first[0]["stream_id"], second[0]["stream_id"])
        self.assertNotIn("public_decision", repr(first + second))

    def test_retry_is_only_emitted_after_next_attempt_starts(self):
        """最终失败不算重试，只有失败后确实再次开始才产生 node_retry。"""
        self.adapter.convert(task_start("task-chat", "normal_chat"))
        chunk = self.adapter.convert({
            "type": "messages",
            "ns": (),
            "data": (AIMessageChunk(content="draft"), {"langgraph_node": "normal_chat"}),
        })[0]
        failed = self.adapter.convert({
            "type": "custom",
            "ns": (),
            "data": {
                "type": "node_attempt_failed",
                "task_id": "task-chat",
                "node_name": "normal_chat",
                "error_kind": "TimeoutError",
            },
        })
        retried = self.adapter.convert({
            "type": "custom",
            "ns": (),
            "data": {
                "type": "node_attempt_start",
                "task_id": "task-chat",
                "node_name": "normal_chat",
                "node_attempt": 1,
            },
        })[0]

        self.assertEqual(failed, [])
        self.assertEqual(retried["type"], "node_retry")
        self.assertEqual(retried["message"], "调用超时")
        self.assertEqual(retried["discard_stream_id"], chunk["stream_id"])
        self.assertNotIn("node_attempt", retried)

    def test_decision_uses_explicit_state_not_message_content(self):
        """展示消息内容变化不得影响显式 State 路由事件。"""
        self.adapter.convert(task_start("task-agent", "agent"))
        events = self.adapter.convert({
            "type": "updates",
            "ns": (),
            "data": {
                "agent": {
                    "messages": [AIMessage(content="route: report")],
                    "route_decision": "fold",
                }
            },
        })

        self.assertEqual([event["type"] for event in events], ["progress", "decision"])
        self.assertIn("因果分析", events[0]["summary"])
        self.assertIn("加载文件", events[1]["summary"])

    def test_inherited_decision_is_not_reemitted_on_tool_stage(self):
        """后续 MCP/RAG 阶段不能把继承的旧路由字段再次展示为新决策。"""
        for stage in ("mcp", "rag"):
            self.adapter.convert(task_start(f"task-{stage}", stage))
            events = self.adapter.convert({
                "type": "updates",
                "ns": (),
                "data": {
                    stage: {
                        "route_decision": "fold",
                        "fold_decision": "preprocess",
                    }
                },
            })

            self.assertEqual(events, [])

    def test_web_search_stage_emits_lifecycle_events(self):
        """web_search 子图作为父图阶段应产生开始/结束事件。"""
        started = self.adapter.convert(task_start("task-ws", "web_search"))[0]
        self.assertEqual(started["type"], "node_start")
        self.assertEqual(started["node_name"], "web_search")
        self.assertEqual(started["title"], "联网搜索")

        ended = self.adapter.convert({
            "type": "tasks",
            "ns": (),
            "data": {"id": "task-ws", "name": "web_search"},
        })[0]
        self.assertEqual(ended["type"], "node_end")
        self.assertEqual(ended["status"], "completed")

    def test_tool_details_expose_names_and_keys_but_not_values_or_results(self):
        """工具事件不得持久化凭据、地址、完整 JSON 或结果正文。"""
        self.adapter.convert(task_start("task-mcp", "mcp"))
        started = self.adapter.convert({
            "type": "updates",
            "ns": ("mcp:task-mcp",),
            "data": {
                "mcp_planner": {
                    "messages": [AIMessage(
                        content="",
                        tool_calls=[{
                            "id": "call-1",
                            "name": "causal_pc",
                            "args": {
                                "api_key": "secret-value",
                                "endpoint": "https://internal.example",
                            },
                        }],
                    )],
                }
            },
        })[0]
        finished = self.adapter.convert({
            "type": "updates",
            "ns": ("mcp:task-mcp",),
            "data": {
                "mcp_result_parser": {
                    "causal_analysis_result": {
                        "success": True,
                        "data": {"csv": "private file body"},
                        "_tool_call": {"name": "causal_pc"},
                    }
                }
            },
        })[0]

        self.assertEqual(started["argument_keys"], ["api_key", "endpoint"])
        self.assertNotIn("secret-value", repr(started))
        self.assertNotIn("internal.example", repr(started))
        self.assertEqual(finished["summary"], "调用完成")
        self.assertNotIn("private file body", repr(finished))

    def test_deep_agent_results_expose_controlled_status_only(self):
        """新 Deep Agent 结果只投影工具名、状态和安全错误码。"""
        self.adapter.convert(task_start("task-deep", "deep_agent"))
        events = self.adapter.convert({
            "type": "updates",
            "ns": (),
            "data": {
                "deep_agent": {
                    "deep_agent_algorithm_results": {
                        "result-1": {
                            "capability_id": "causal.pc",
                            "status": "execution_failed",
                            "diagnostics": {
                                "safe_error_code": "ALGORITHM_EXECUTION_FAILED",
                                "summary": "private diagnostics",
                            },
                            "raw_result_ref": "/private/path.json",
                        }
                    }
                }
            },
        })

        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0],
            {
                "type": "tool_call_result",
                "step_id": events[0]["step_id"],
                "node_name": "deep_agent",
                "title": "执行 Deep Agent 分析",
                "attempt": 3,
                "tool_name": "causal_pc",
                "summary": "调用失败",
                "status": "failed",
                "safe_error_code": "ALGORITHM_EXECUTION_FAILED",
            },
        )
        self.assertNotIn("private", repr(events[0]))

    def test_public_algorithm_and_final_decisions_bind_to_their_parent_steps(self):
        deep_step = self.adapter.convert(task_start("task-deep", "deep_agent"))[0]
        algorithm = self.adapter.convert({
            "type": "custom",
            "ns": (),
            "data": {
                "type": "decision",
                "decision_kind": "algorithm",
                "tool_name": "causal_pc",
                "summary": "连续数据适合使用 PC。",
            },
        })[0]
        evidence = self.adapter.convert({
            "type": "custom",
            "ns": (),
            "data": {
                "type": "decision",
                "decision_kind": "evidence",
                "tool_name": "rag_evidence_search",
                "summary": "检索知识库以核对方法假设。",
            },
        })[0]
        gate_step = self.adapter.convert(task_start("task-gate", "finalization_gate"))[0]
        final = self.adapter.convert({
            "type": "custom",
            "ns": (),
            "data": {
                "type": "decision",
                "decision_kind": "final",
                "summary": "PC：主结果。诊断更稳定。置信度：中等",
                "confidence": "medium",
            },
        })[0]

        self.assertEqual(algorithm["step_id"], deep_step["step_id"])
        self.assertEqual(algorithm["decision_kind"], "algorithm")
        self.assertEqual(algorithm["tool_name"], "causal_pc")
        self.assertEqual(evidence["step_id"], deep_step["step_id"])
        self.assertEqual(evidence["decision_kind"], "evidence")
        self.assertEqual(evidence["tool_name"], "rag_evidence_search")
        self.assertEqual(final["step_id"], gate_step["step_id"])
        self.assertEqual(final["decision_kind"], "final")
        self.assertEqual(final["confidence"], "medium")

        public = _public_event_payload(final)
        self.assertEqual(public["summary"], final["summary"])
        self.assertEqual(public["confidence"], "medium")

    def test_stage_progress_notices_bind_to_the_named_active_step(self):
        """Gate 失败说明与修正重试说明必须挂在各自阶段的活跃实例上。"""
        gate_step = self.adapter.convert(task_start("task-gate-1", "finalization_gate"))[0]
        gate_notice = self.adapter.convert(
            {
                "type": "custom",
                "ns": (),
                "data": {
                    "type": "progress",
                    "node_name": "finalization_gate",
                    "summary": "结构化最终决策未通过程序事实校验，已发起一次修正。",
                    "_event_key": "finalization-gate-retry:0:1",
                },
            }
        )[0]
        # 关闭 Gate 阶段后再进入第二次 Deep Agent，确保绑定到新的阶段实例。
        self.adapter.convert(
            {
                "type": "tasks",
                "ns": (),
                "data": {"id": "task-gate-1", "name": "finalization_gate", "output": {}},
            }
        )
        retry_step = self.adapter.convert(task_start("task-deep-2", "deep_agent"))[0]
        retry_notice = self.adapter.convert(
            {
                "type": "custom",
                "ns": (),
                "data": {
                    "type": "progress",
                    "node_name": "deep_agent",
                    "step_id": retry_step["step_id"],
                    "summary": "正在按校验要求修正最终决策：沿用已有工具结果，不重复调用工具。",
                    "_event_key": f"deep-agent-retry:0:{retry_step['step_id']}",
                },
            }
        )[0]

        self.assertEqual(gate_notice["type"], "progress")
        self.assertEqual(gate_notice["step_id"], gate_step["step_id"])
        self.assertEqual(gate_notice["node_name"], "finalization_gate")
        self.assertEqual(gate_notice["title"], "校验最终分析决策")
        self.assertEqual(retry_notice["step_id"], retry_step["step_id"])
        self.assertEqual(retry_notice["node_name"], "deep_agent")
        self.assertEqual(retry_notice["title"], "执行 Deep Agent 分析")

        public = _public_event_payload(gate_notice)
        self.assertEqual(public["summary"], gate_notice["summary"])
        self.assertNotIn("_event_key", public)

    def test_stage_progress_notices_reject_unregistered_nodes_and_blank_summary(self):
        """阶段说明只接受已登记节点和非空文本，避免任意文本外带。"""
        self.adapter.convert(task_start("task-deep-1", "deep_agent"))

        self.assertEqual(
            self.adapter.convert(
                {
                    "type": "custom",
                    "ns": (),
                    "data": {
                        "type": "progress",
                        "node_name": "private_node",
                        "summary": "不应外带",
                    },
                }
            ),
            [],
        )
        self.assertEqual(
            self.adapter.convert(
                {
                    "type": "custom",
                    "ns": (),
                    "data": {
                        "type": "progress",
                        "node_name": "deep_agent",
                        "summary": "",
                    },
                }
            ),
            [],
        )

    def test_sse_public_payload_removes_backend_attempt(self):
        """job attempt 可以持久化，但不能进入普通用户 SSE 协议。"""
        payload = {
            "type": "node_start",
            "attempt": 4,
            "step_id": "opaque",
            "prompt": "private prompt",
            "tool_result": {"csv": "private file body"},
        }
        public = _public_event_payload(payload)

        self.assertNotIn("attempt", public)
        self.assertNotIn("prompt", public)
        self.assertNotIn("tool_result", public)
        self.assertEqual(payload["attempt"], 4)

    def test_sse_public_payload_keeps_controlled_tool_status(self):
        payload = {
            "type": "tool_call_result",
            "step_id": "opaque",
            "node_name": "deep_agent",
            "title": "执行 Deep Agent 分析",
            "tool_name": "causal_pc",
            "summary": "调用失败",
            "status": "failed",
            "safe_error_code": "ALGORITHM_EXECUTION_FAILED",
            "raw_result": "private",
        }

        public = _public_event_payload(payload)

        self.assertEqual(public["status"], "failed")
        self.assertEqual(public["safe_error_code"], "ALGORITHM_EXECUTION_FAILED")
        self.assertNotIn("raw_result", public)

    def test_sse_public_payload_keeps_decision_delta_only(self):
        public = _public_event_payload({
            "type": "decision_delta",
            "step_id": "opaque",
            "node_name": "deep_agent",
            "title": "执行 Deep Agent 分析",
            "stream_id": "stream-1",
            "sequence": 1,
            "delta": "连续数据",
            "decision_kind": "algorithm",
            "tool_name": "causal_pc",
            "attempt": 4,
            "raw_arguments": "private",
        })

        self.assertEqual(public["delta"], "连续数据")
        self.assertNotIn("attempt", public)
        self.assertNotIn("raw_arguments", public)

    def test_canceled_tool_lifecycle_is_not_reported_as_succeeded(self):
        self.adapter.convert(task_start("task-deep", "deep_agent"))
        events = self.adapter.convert({
            "type": "custom",
            "ns": (),
            "data": {
                "type": "tool_call_result",
                "tool_name": "causal_pc",
                "status": "canceled",
            },
        })

        self.assertEqual(events[0]["status"], "canceled")
        self.assertEqual(events[0]["summary"], "调用已取消")

    def test_disabled_and_unavailable_tools_use_distinct_public_summaries(self):
        self.adapter.convert(task_start("task-deep", "deep_agent"))

        disabled = self.adapter.convert({
            "type": "custom",
            "ns": (),
            "data": {
                "type": "tool_call_result",
                "tool_name": "web_evidence_search",
                "status": "not_ready",
                "safe_error_code": "WEB_SEARCH_DISABLED",
            },
        })[0]
        unavailable = self.adapter.convert({
            "type": "custom",
            "ns": (),
            "data": {
                "type": "tool_call_result",
                "tool_name": "rag_evidence_search",
                "status": "failed",
                "safe_error_code": "RAG_RETRIEVAL_UNAVAILABLE",
            },
        })[0]

        self.assertEqual(disabled["summary"], "未启用")
        self.assertEqual(disabled["status"], "not_ready")
        self.assertEqual(unavailable["summary"], "暂不可用")
        self.assertEqual(unavailable["status"], "failed")


class RealLangGraphStreamTests(unittest.IsolatedAsyncioTestCase):
    """用锁定版 LangGraph 的真实图验证 tasks 与 custom attempt 流。"""

    async def test_tasks_wrap_whole_task_while_custom_exposes_retry(self):
        """一次内部 retry 应对应一组 task 生命周期和两次 attempt 开始。"""
        calls = 0

        async def flaky_node(state):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError("private endpoint must not leak")
            return {"messages": [AIMessage(content="done", name="normal_chat")]}

        graph = StateGraph(CausalAgentState)
        graph.add_node(
            "normal_chat",
            bind_node(flaky_node, event_node_name="normal_chat"),
            retry_policy=RetryPolicy(
                initial_interval=0.001,
                backoff_factor=1.0,
                max_interval=0.001,
                max_attempts=2,
                jitter=False,
                retry_on=lambda _exc: True,
            ),
        )
        graph.set_entry_point("normal_chat")
        graph.add_edge("normal_chat", END)
        compiled = graph.compile()

        raw = [
            event
            async for event in compiled.astream(
                {"messages": []},
                stream_mode=["updates", "custom", "tasks"],
                subgraphs=True,
                version="v2",
            )
        ]
        task_events = [event for event in raw if event["type"] == "tasks"]
        custom = [event["data"] for event in raw if event["type"] == "custom"]

        self.assertEqual(calls, 2)
        self.assertEqual(len(task_events), 2)
        self.assertEqual(
            [event["type"] for event in custom],
            ["node_attempt_start", "node_attempt_failed", "node_attempt_start"],
        )
        self.assertEqual(custom[0]["task_id"], custom[2]["task_id"])

        adapter = LangGraphEventAdapter("job-real", 1)
        public_events = [converted for event in raw for converted in adapter.convert(event)]
        self.assertEqual(
            [event["type"] for event in public_events],
            ["node_start", "node_retry", "node_end"],
        )
        self.assertEqual(public_events[1]["message"], "调用超时")

    async def test_runnable_wrapper_preserves_langgraph_config(self):
        """ToolNode 包装器必须把 LangGraph config 原样传给底层 Runnable。"""
        received_config = None

        class FakeRunnable:
            """记录 ainvoke config 的最小 ToolNode 替身。"""

            async def ainvoke(self, state, config):
                nonlocal received_config
                received_config = config
                return {"messages": [AIMessage(content="tool done")]}

        graph = StateGraph(CausalAgentState)
        graph.add_node(
            "mcp_tool_node",
            bind_runnable_node(FakeRunnable(), event_node_name="mcp_tool_node"),
        )
        graph.set_entry_point("mcp_tool_node")
        graph.add_edge("mcp_tool_node", END)
        compiled = graph.compile()

        events = [
            event
            async for event in compiled.astream(
                {"messages": []},
                {"configurable": {"marker": "kept"}},
                stream_mode=["custom", "tasks"],
                version="v2",
            )
        ]

        self.assertEqual(received_config["configurable"]["marker"], "kept")
        custom = [event["data"] for event in events if event["type"] == "custom"]
        self.assertEqual(custom[0]["type"], "node_attempt_start")
        self.assertEqual(custom[0]["node_name"], "mcp_tool_node")


if __name__ == "__main__":
    unittest.main()
