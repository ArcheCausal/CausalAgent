import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from langgraph.errors import NodeCancelledError
from langgraph.types import Command

from app.agent.checkpoint_recovery import checkpoint_identity
from app.agent.worker.event_adapter import sanitize_public_error
from app.agent.worker.graph_runner import ai_call_stream
from app.agent.worker.graph_runner import (
    _raise_wrapped_cancellation,
    ai_call_stream,
)
from app.agent.worker.execution_guard import JobExecutionRevoked
from Agent.Report.document import build_degraded_report_document


class APIStatusError(Exception):
    """模拟 provider SDK 的基类，用于锁定分类判定顺序。"""


class RateLimitError(APIStatusError):
    """模拟 provider 限流异常。"""


class AuthenticationError(APIStatusError):
    """模拟 provider 鉴权异常。"""


class CheckpointPostgresUnavailable(RuntimeError):
    """模拟 checkpoint 依赖不可用。"""


class RunnerContractError(ValueError):
    """模拟 runner 输出合同错误。"""


class ToolMessageProtocolError(Exception):
    """模拟 ToolMessage 协议错误。"""


class FakeGraph:
    """提供最小 compiled graph 协议，验证 Job root checkpoint 配置。"""

    def __init__(self, states):
        self.states = list(states)
        self.configs = []
        self.inputs = []

    async def aget_state(self, config):
        """按调用顺序返回 root StateSnapshot。"""
        self.configs.append(config)
        return self.states.pop(0)

    async def astream(self, input_data, config, **_kwargs):
        """记录执行输入，不产生额外公开事件。"""
        self.inputs.append((input_data, config))
        if False:
            yield {}


class FailingGraph(FakeGraph):
    """在流式执行中抛出指定异常，用于验证失败诊断。"""

    def __init__(self, error):
        super().__init__([_snapshot()])
        self.error = error

    async def astream(self, input_data, config, **_kwargs):
        """复现 LangGraph 把节点异常抛给调用方的行为。"""
        raise self.error
        yield {}  # pragma: no cover
class ErrorHandlerGraph(FakeGraph):
    """先在 updates 流里提交错误处理器的降级结果，再抛出原始任务异常。"""

    def __init__(self, *, states, handler_update=True):
        super().__init__(states)
        self.handler_update = handler_update

    async def astream(self, input_data, config, **_kwargs):
        """复现 langgraph 在错误处理器收敛后仍抛出任务异常的行为。"""
        if self.handler_update:
            yield {
                "type": "updates",
                "ns": (),
                "data": {"__error_handler__report": {"report_document": "degraded"}},
            }
        raise RuntimeError("report 节点执行失败")





def _snapshot(*, interrupts=(), public_interrupts=None, values=None):
    """构造 graph_runner 所需的最小 StateSnapshot。"""
    return SimpleNamespace(
        interrupts=public_interrupts,
        tasks=tuple(SimpleNamespace(interrupts=items) for items in interrupts),
        values=values or {"messages": []},
        created_at=None,
        metadata={},
        config={},
        next=(),
    )


async def _collect(graph, text="hello", *, claim_kind="initial", input_record=None, initial_input_record=None, **file_snapshot):
    """收集一次 Job 执行产生的公开事件；分析上下文读取用替身，单测不访问数据库。"""
    with patch(
        "app.agent.worker.graph_runner.load_active_context",
        return_value=None,
    ), patch(
        "app.agent.worker.graph_runner.load_context_index",
        return_value=[],
    ):
        return [
            event
            async for event in ai_call_stream(
                text,
                7,
                "user-7",
                "session-1",
                job_id="job-1",
                job_attempt=1,
                input_user_file_id=file_snapshot.get("input_user_file_id"),
                input_object_id=file_snapshot.get("input_object_id"),
                input_file_hash=file_snapshot.get("input_file_hash"),
                input_filename=file_snapshot.get("input_filename"),
                graph=graph,
                claim_kind=claim_kind,
                input_record=input_record,
                initial_input_record=initial_input_record,
            )
        ]


class GraphRunnerTests(unittest.IsolatedAsyncioTestCase):
    """验证 Job root State 查询、输入边界和 stale recovery。"""

    async def test_job_root_identity_is_not_a_subgraph_namespace(self):
        """普通问答的两次状态查询都必须使用 Job ID 根 identity。"""
        graph = FakeGraph([
            _snapshot(),
            _snapshot(values={"messages": []}),
        ])

        events = await _collect(graph)

        self.assertEqual(events[-1]["type"], "final_result")
        self.assertEqual(len(graph.configs), 2)
        for config in graph.configs:
            self.assertEqual(config["configurable"]["thread_id"], "job-1")
            self.assertNotIn("checkpoint_ns", config["configurable"])
            self.assertEqual(config["metadata"]["job_id"], "job-1")
            self.assertEqual(config["metadata"]["session_id"], "session-1")

    def test_langgraph_wrapped_cancellation_returns_to_worker_control_flow(self):
        """LangGraph 1.2.11 的 NodeCancelledError 不能变成普通 error 事件。"""
        cause = asyncio.CancelledError("worker stop")
        try:
            raise NodeCancelledError("agent") from cause
        except NodeCancelledError as error:
            with self.assertRaises(asyncio.CancelledError):
                _raise_wrapped_cancellation(error)

    def test_langgraph_wrapped_revocation_returns_to_worker_control_flow(self):
        cause = JobExecutionRevoked("revoked")
        try:
            raise NodeCancelledError("agent") from cause
        except NodeCancelledError as error:
            with self.assertRaises(JobExecutionRevoked):
                _raise_wrapped_cancellation(error)

    async def test_revocation_does_not_emit_error_or_final_result(self):
        class RevokedGraph(FakeGraph):
            async def astream(self, input_data, config, **_kwargs):
                raise JobExecutionRevoked("revoked")
                yield  # pragma: no cover

        graph = RevokedGraph([_snapshot()])
        with patch("app.agent.worker.graph_runner.process_final_result") as presenter:
            with self.assertRaises(JobExecutionRevoked):
                await _collect(graph)
        presenter.assert_not_called()

    def test_checkpoint_identity_requires_job_id_and_keeps_root_namespace_empty(self):
        """运行时和恢复查询共用 Job ID 根 identity，不能生成 unknown namespace。"""
        self.assertEqual(checkpoint_identity("job-1"), ("job-1", ""))
        with self.assertRaises(ValueError):
            checkpoint_identity(" ")

    async def test_public_snapshot_interrupts_are_used_for_resume(self):
        """优先读取 LangGraph 公共 interrupts 字段，兼容根 checkpoint 恢复。"""
        interrupt = SimpleNamespace(id="question-public", value="请补充信息")
        graph = FakeGraph([
            _snapshot(public_interrupts=(interrupt,)),
            _snapshot(public_interrupts=(interrupt,)),
        ])

        events = await _collect(
            graph,
            input_record={
                "input_type": "resume",
                "runtime_value": "补充回答",
                "stored_text": "补充回答",
                "chat_message_id": 42,
            },
        )

        self.assertEqual(events[-1]["type"], "interrupt")
        self.assertIsInstance(graph.inputs[0][0], Command)

    async def test_same_job_resume_uses_existing_state_and_structured_value(self):
        """同一 Job 的 interrupt 恢复必须继续原 State，并保留结构化回答。"""
        interrupt = SimpleNamespace(id="question-1", value={"target": "sales"})
        graph = FakeGraph([
            _snapshot(interrupts=((interrupt,),)),
            _snapshot(interrupts=((interrupt,),)),
        ])

        events = await _collect(
            graph,
            input_record={
                "input_type": "resume",
                "runtime_value": {"target": "revenue"},
                "stored_text": '{"target":"revenue"}',
                "chat_message_id": 42,
            },
        )

        self.assertEqual(events[-1]["type"], "interrupt")
        self.assertIsInstance(graph.inputs[0][0], Command)
        self.assertEqual(graph.inputs[0][0].resume, {"target": "revenue"})
        self.assertIs(graph.inputs[0][1], graph.configs[0])

    async def test_initial_state_uses_job_history_without_appending_current_message(self):
        """初始输入从 chat_message_id 截止的历史启动，当前消息不额外追加。"""
        history = [
            SimpleNamespace(type="human", content="旧问题"),
            SimpleNamespace(type="ai", content="旧回答"),
            SimpleNamespace(type="human", content="当前问题"),
        ]
        graph = FakeGraph([_snapshot(), _snapshot(values={"messages": []})])
        with patch(
            "app.agent.worker.graph_runner.get_job_chat_history",
            return_value=history,
        ):
            await _collect(
                graph,
                input_record={
                    "input_type": "initial",
                    "runtime_value": "当前问题",
                    "stored_text": "当前问题",
                    "chat_message_id": 99,
                },
            )

        self.assertEqual(graph.inputs[0][0]["messages"], history)
        self.assertEqual(
            [message.content for message in graph.inputs[0][0]["messages"]],
            ["旧问题", "旧回答", "当前问题"],
        )

    async def test_initial_state_groups_frozen_file_snapshot_under_file_summary(self):
        """新 Job 初始 State 将冻结文件四元组写入 file_summary。"""
        graph = FakeGraph([
            _snapshot(),
            _snapshot(values={"messages": []}),
        ])

        await _collect(
            graph,
            input_user_file_id=11,
            input_object_id=22,
            input_file_hash="a" * 64,
            input_filename="data.csv",
        )

        input_data = graph.inputs[0][0]
        self.assertEqual(
            input_data["file_summary"],
            {
                "user_file_id": 11,
                "object_id": 22,
                "file_hash": "a" * 64,
                "filename": "data.csv",
            },
        )
        self.assertNotIn("input_user_file_id", input_data)
        self.assertNotIn("input_object_id", input_data)
        self.assertNotIn("input_file_hash", input_data)
        self.assertNotIn("input_filename", input_data)

    async def test_initial_state_loads_active_context_and_history_index(self):
        """新 Job 初始 State 携带当前分析上下文投影和同一会话的历史上下文索引。"""
        graph = FakeGraph([_snapshot(), _snapshot(values={"messages": []})])
        context = {
            "analysis_context_id": "ctx-1",
            "filename": "sales.csv",
            "target": "销售额",
        }
        index = [
            {
                "analysis_context_id": "ctx-2",
                "filename": "data.csv",
                "target": "访问量",
            }
        ]
        with patch(
            "app.agent.worker.graph_runner.get_job_chat_history",
            return_value=[SimpleNamespace(type="human", content="当前问题")],
        ), patch(
            "app.agent.worker.graph_runner.load_active_context",
            return_value=context,
        ), patch(
            "app.agent.worker.graph_runner.load_context_index",
            return_value=index,
        ) as index_reader:
            events = [
                event
                async for event in ai_call_stream(
                    "当前问题",
                    7,
                    "user-7",
                    "session-1",
                    job_id="job-1",
                    job_attempt=1,
                    input_user_file_id=None,
                    input_object_id=None,
                    input_file_hash=None,
                    input_filename=None,
                    graph=graph,
                    claim_kind="initial",
                    input_record={
                        "input_type": "initial",
                        "runtime_value": "当前问题",
                        "stored_text": "当前问题",
                        "chat_message_id": 99,
                    },
                )
            ]

        self.assertEqual(events[-1]["type"], "final_result")

        index_reader.assert_called_once_with(
            7,
            "session-1",
            exclude_context_id="ctx-1",
        )
        input_data = graph.inputs[0][0]
        self.assertEqual(input_data["analysis_context"], context)
        self.assertEqual(
            input_data["analysis_context_index"][0]["analysis_context_id"],
            "ctx-2",
        )

    async def test_stale_recovery_with_checkpoint_uses_none_input(self):
        """stale recovery 有 checkpoint 时继续原 State，不追加原始问题。"""
        graph = FakeGraph([
            SimpleNamespace(
                tasks=(),
                values={"messages": ["existing"]},
                created_at="2026-08-08T00:00:00Z",
                metadata={"source": "loop"},
                config={"configurable": {"checkpoint_id": "cp-1"}},
                next=("agent",),
            ),
            _snapshot(values={"messages": []}),
        ])

        await _collect(graph, claim_kind="stale_recovery")

        self.assertIsNone(graph.inputs[0][0])

    async def test_stale_recovery_after_resume_uses_existing_checkpoint(self):
        """恢复输入执行中失联时，不能因最后一条输入是 resume 而误判失败。"""
        graph = FakeGraph([
            SimpleNamespace(
                tasks=(),
                values={"messages": ["existing"]},
                created_at="2026-08-08T00:00:00Z",
                metadata={"source": "loop"},
                config={"configurable": {"checkpoint_id": "cp-2"}},
                next=("agent",),
            ),
            _snapshot(values={"messages": []}),
        ])

        await _collect(
            graph,
            claim_kind="stale_recovery",
            input_record={
                "input_type": "resume",
                "runtime_value": "补充回答",
                "stored_text": "补充回答",
                "chat_message_id": 43,
            },
        )

        self.assertIsNone(graph.inputs[0][0])

    async def test_stale_recovery_without_checkpoint_reuses_initial_input(self):
        """stale recovery 没有 checkpoint 时只重用序号 0 的初始消息窗口。"""
        history = [SimpleNamespace(type="human", content="初始问题")]
        graph = FakeGraph([_snapshot(), _snapshot(values={"messages": []})])
        with patch(
            "app.agent.worker.graph_runner.get_job_chat_history",
            return_value=history,
        ):
            await _collect(
                graph,
                claim_kind="stale_recovery",
                input_record={
                    "input_type": "initial",
                    "runtime_value": "初始问题",
                    "stored_text": "初始问题",
                    "chat_message_id": 11,
                },
            )

        self.assertEqual(graph.inputs[0][0]["messages"], history)

    async def test_resume_without_pending_interrupt_is_rejected(self):
        """没有 interrupt 时不能把 resume 答案重新当作新问题执行。"""
        graph = FakeGraph([_snapshot()])
        with self.assertRaisesRegex(RuntimeError, "resume 输入没有对应"):
            await _collect(
                graph,
                input_record={
                    "input_type": "resume",
                    "runtime_value": "回答",
                    "stored_text": "回答",
                    "chat_message_id": 43,
                },
            )

    async def test_state_read_failure_does_not_fallback_to_new_conversation(self):
        """checkpoint 读取失败时不能静默丢失同一 Job 的上下文。"""

        class BrokenGraph(FakeGraph):
            async def aget_state(self, _config):
                raise RuntimeError("checkpoint unavailable")

        with self.assertRaisesRegex(RuntimeError, "checkpoint unavailable"):
            await _collect(BrokenGraph([]))

    async def test_graph_failure_keeps_public_message_and_carries_diagnostic(self):
        """公开文案保持脱敏，真实异常改由内部诊断字段继续传递。"""
        cases = (
            (RateLimitError("slow down"), "provider_error", "rate_limited"),
            (AuthenticationError("bad key"), "provider_error", "auth_failed"),
            (
                CheckpointPostgresUnavailable("db down"),
                "checkpoint_error",
                "checkpoint_unavailable",
            ),
            (
                RunnerContractError("bad runner output"),
                "runtime_contract_error",
                "invalid_runtime_context",
            ),
            (ToolMessageProtocolError("bad json"), "protocol_error", "protocol_error"),
            (RuntimeError("boom"), "internal_error", "node_error"),
        )
        for error, expected_kind, expected_reason in cases:
            with self.subTest(error=type(error).__name__):
                events = await _collect(FailingGraph(error))

                self.assertEqual([event["type"] for event in events], ["error"])
                event = events[0]
                self.assertEqual(event["message"], sanitize_public_error(error))
                self.assertEqual(event["attempt"], 1)

                diagnostic = event["_diagnostic"]
                self.assertEqual(diagnostic.error_category, expected_kind)
                self.assertEqual(diagnostic.reason_code, expected_reason)
                self.assertIs(diagnostic.exc_info[0], type(error))
                self.assertIs(diagnostic.exc_info[1], error)
                self.assertIsNotNone(diagnostic.exc_info[2])

    async def test_rate_limit_subclass_is_not_misclassified_as_server_error(self):
        """provider 子类的 MRO 含 APIStatusError，判定顺序不能让它退化成服务端错误。"""
        events = await _collect(FailingGraph(RateLimitError("slow down")))

        self.assertEqual(events[0]["_diagnostic"].reason_code, "rate_limited")


    async def test_node_error_handler_result_becomes_terminal_result(self):
        """节点错误处理器已提交降级结果时，worker 不能把整轮判成失败。"""
        report = build_degraded_report_document("报告生成失败：report 节点执行失败")
        graph = ErrorHandlerGraph(
            states=[
                _snapshot(),
                _snapshot(values={"messages": [], "report_document": report}),
            ]
        )

        events = await _collect(graph)

        self.assertEqual(events[-1]["type"], "final_result")
        self.assertEqual(events[-1]["data"]["type"], "report")
        self.assertEqual(
            events[-1]["data"]["document"]["report_id"],
            report.report_id,
        )
        self.assertTrue(all(event["type"] != "error" for event in events))

    async def test_node_error_without_handler_result_still_fails(self):
        """没有错误处理器结果时仍然按失败处理，不能凭状态猜测成功。"""
        graph = ErrorHandlerGraph(
            states=[_snapshot(), _snapshot(values={"messages": []})],
            handler_update=False,
        )

        events = await _collect(graph)

        self.assertEqual(events[-1]["type"], "error")
