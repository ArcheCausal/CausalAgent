"""把 LangGraph v2 内部流转换为稳定的公开任务事件。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import time
from typing import Any

from Agent.deep_agent_tools.identity import build_deep_agent_step_id


NODE_DESCRIPTIONS = {
    "agent": "分析用户意图",
    "fold": "加载文件并验证数据",
    "preprocess": "预处理数据",
    "deep_agent": "执行 Deep Agent 分析",
    "finalization_gate": "校验最终分析决策",
    "mcp": "执行因果分析",
    "rag": "检索知识库",
    "web_search": "联网搜索",
    "postprocess": "校验并修正因果图",
    "report": "生成分析报告",
    "normal_chat": "生成回答",
    "inquiry_answer": "回答报告追问",
}
TEXT_STREAM_NODES = {"normal_chat", "inquiry_answer", "report"}
TOOL_STAGE_NODES = {
    "mcp_planner": "mcp",
    "mcp_tool_node": "mcp",
    "mcp_result_parser": "mcp",
    "rag_question_planner": "rag",
    "rag_tool_node": "rag",
    "rag_result_parser": "rag",
    "rag_finalize": "rag",
}
DECISION_PROGRESS = {
    "fold": "已识别为因果分析请求",
    "normal_chat": "已识别为普通问答",
    "inquiry_answer": "已识别为报告追问",
    "postprocess": "已识别为已有结果的后续处理",
    "preprocess": "文件与数据验证完成",
    "agent": "需要补充分析输入",
}
DECISION_FIELDS = {
    "agent": "route_decision",
    "fold": "fold_decision",
}
_SAFE_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_SAFE_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_SAFE_STEP_ID = re.compile(r"^[0-9a-f]{24}$")
_ALGORITHM_TOOL_NAMES = {
    "causal.pc": "causal_pc",
    "causal.olc": "causal_olc",
    "causal.direct_lingam": "causal_direct_lingam",
}
_DECISION_TOOL_KINDS = {
    "causal_pc": "algorithm",
    "causal_olc": "algorithm",
    "causal_direct_lingam": "algorithm",
    "rag_evidence_search": "evidence",
    "web_evidence_search": "evidence",
}
_MAX_DECISION_ARGS = 16_384
_MAX_DECISION_SUMMARY = 1_200


def _opaque_id(*parts: Any) -> str:
    """根据内部标识生成不暴露 attempt/task 内容的稳定 ID。"""
    raw = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def sanitize_public_error(error: Any) -> str:
    """把内部异常归类为有限的用户可见错误，避免泄露连接和路径信息。"""
    name = error if isinstance(error, str) else type(error).__name__
    normalized = str(name).lower()
    if "timeout" in normalized:
        return "调用超时"
    if any(token in normalized for token in ("connection", "connect", "network")):
        return "服务连接失败"
    if any(token in normalized for token in ("rate", "limit")):
        return "服务当前繁忙"
    if any(token in normalized for token in ("permission", "auth")):
        return "服务授权失败"
    return "节点执行失败"


# 运行日志的故障域分类。注意与 Agent/causal_agent/graph_utils.py 自定义流里的
# `error_kind`（原始异常类名）区分：两者在不同命名空间，此处统一用 error_category。
ERROR_CATEGORY_PROVIDER = "provider_error"
ERROR_CATEGORY_PROTOCOL = "protocol_error"
ERROR_CATEGORY_CHECKPOINT = "checkpoint_error"
ERROR_CATEGORY_RUNTIME_CONTRACT = "runtime_contract_error"
ERROR_CATEGORY_INTERNAL = "internal_error"

# 判定顺序即优先级：provider SDK 的 RateLimitError/AuthenticationError/APITimeoutError
# 的 MRO 里都含 APIStatusError，若把服务端错误提前判定，这些子类会被误判成 server_error。
_ERROR_NAME_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("checkpoint",), ERROR_CATEGORY_CHECKPOINT, "checkpoint_unavailable"),
    (("contract",), ERROR_CATEGORY_RUNTIME_CONTRACT, "invalid_runtime_context"),
    (("protocol",), ERROR_CATEGORY_PROTOCOL, "protocol_error"),
    (
        ("ratelimit", "rate_limit", "toomanyrequest"),
        ERROR_CATEGORY_PROVIDER,
        "rate_limited",
    ),
    (("quota", "billing"), ERROR_CATEGORY_PROVIDER, "quota_billing"),
    (
        ("auth", "permission", "apikey", "unauthorized", "forbidden"),
        ERROR_CATEGORY_PROVIDER,
        "auth_failed",
    ),
    (("timeout",), ERROR_CATEGORY_PROVIDER, "timeout"),
    (
        ("connection", "connect", "network", "transport"),
        ERROR_CATEGORY_PROVIDER,
        "connection_unavailable",
    ),
    (
        ("apistatus", "servererror", "internalserver", "serviceunavailable", "overloaded"),
        ERROR_CATEGORY_PROVIDER,
        "server_error",
    ),
)


@dataclass(frozen=True)
class FailureDiagnostic:
    """graph 终态失败的内部诊断，只在内进程传递，不进入持久化事件。"""

    error_category: str
    reason_code: str
    exc_info: tuple[type[BaseException], BaseException, Any] | None


def _exception_class_names(error: Any) -> str:
    """取异常自身及其基类的类名小写串，不读取异常文本。"""
    if isinstance(error, str):
        return error.lower()
    exception_type = error if isinstance(error, type) else type(error)
    bases = getattr(exception_type, "__mro__", None) or (exception_type,)
    return " ".join(getattr(base, "__name__", "").lower() for base in bases)


def _safe_exc_info(error: Any) -> tuple[type[BaseException], BaseException, Any] | None:
    """生成日志运行时可直接消费的 exc_info 三元组，缺失时返回 None。"""
    if not isinstance(error, BaseException):
        return None
    return type(error), error, error.__traceback__


def classify_graph_failure(error: Any) -> FailureDiagnostic:
    """把 graph 终态异常映射为稳定故障域与原因码，不读取异常文本。

    只按异常类名（含基类）归类，因此不会把连接串、路径或用户数据带进分类结果。
    未识别的异常保持既有的 ``node_error`` 语义。
    """
    names = _exception_class_names(error)
    for tokens, error_category, reason_code in _ERROR_NAME_RULES:
        if any(token in names for token in tokens):
            return FailureDiagnostic(error_category, reason_code, _safe_exc_info(error))
    return FailureDiagnostic(
        ERROR_CATEGORY_INTERNAL,
        "node_error",
        _safe_exc_info(error),
    )


class LangGraphEventAdapter:
    """把 LangGraph v2 多流事件转换为稳定、可持久化的公开事件协议。"""

    def __init__(self, job_id: str | None, job_attempt: int):
        """初始化单次 job attempt 的阶段、重试和文字流状态。"""
        self.job_id = job_id or "untracked"
        self.job_attempt = job_attempt
        self.steps: dict[str, dict[str, Any]] = {}
        self.active_by_node: dict[str, str] = {}
        self.failed_attempts: dict[str, str] = {}
        self.streams: dict[str, dict[str, Any]] = {}
        self.decision_streams: dict[str, dict[str, Any]] = {}
        self._lifecycle_tools: set[str] = set()
        self._lifecycle_started_tools: set[str] = set()

    def _base(self, event_type: str, step: dict[str, Any]) -> dict[str, Any]:
        """构造所有阶段事件共享的持久化字段。"""
        return {
            "type": event_type,
            "step_id": step["step_id"],
            "node_name": step["node_name"],
            "title": NODE_DESCRIPTIONS[step["node_name"]],
            "attempt": self.job_attempt,
        }

    def _active_step(self, node_name: str) -> dict[str, Any] | None:
        """获取指定父图节点当前尚未结束的阶段实例。"""
        task_id = self.active_by_node.get(node_name)
        return self.steps.get(task_id) if task_id else None

    def _parent_tool_step(self, node_name: str) -> dict[str, Any] | None:
        """把子图内部节点映射到当前 MCP/RAG 父阶段。"""
        parent_name = TOOL_STAGE_NODES.get(node_name)
        return self._active_step(parent_name) if parent_name else None

    def _lifecycle_step(self, data: dict[str, Any]) -> dict[str, Any] | None:
        """用父阶段或可信 supplied step_id 关联工具生命周期事件。"""

        active = self._active_step("deep_agent")
        if active is not None:
            return active
        supplied_step_id = str(data.get("step_id") or "")
        if not _SAFE_STEP_ID.fullmatch(supplied_step_id):
            return None
        synthetic_key = f"lifecycle:{supplied_step_id}"
        step = self.steps.get(synthetic_key)
        if step is None:
            step = {
                "step_id": supplied_step_id,
                "node_name": "deep_agent",
                "started_at": time.monotonic(),
            }
            self.steps[synthetic_key] = step
        return step

    def _task_event(self, namespace: Any, data: Any) -> list[dict[str, Any]]:
        """将根图 tasks 开始/结束转换为阶段生命周期事件。"""
        if namespace or not isinstance(data, dict):
            return []
        task_id = str(data.get("id") or "")
        node_name = data.get("name")
        if not task_id or node_name not in NODE_DESCRIPTIONS:
            return []
        if "input" in data:
            step_id = (
                build_deep_agent_step_id(
                    job_id=self.job_id,
                    attempt_count=self.job_attempt,
                    task_id=task_id,
                )
                if node_name == "deep_agent"
                else _opaque_id(self.job_id, self.job_attempt, task_id)
            )
            step = {
                "step_id": step_id,
                "node_name": node_name,
                "started_at": time.monotonic(),
            }
            self.steps[task_id] = step
            self.active_by_node[node_name] = task_id
            return [self._base("node_start", step)]
        step = self.steps.get(task_id)
        if not step:
            return []
        event = self._base("node_end", step)
        event["duration"] = round(
            max(0.0, time.monotonic() - step["started_at"]),
            2,
        )
        event["status"] = "failed" if data.get("error") else "completed"
        if data.get("error"):
            event["message"] = sanitize_public_error(data.get("error"))
        if self.active_by_node.get(node_name) == task_id:
            self.active_by_node.pop(node_name, None)
        return [event]

    def _custom_event(self, data: Any) -> list[dict[str, Any]]:
        """只有失败后再次开始时，才把内部 attempt 转换为真正的重试。"""
        if not isinstance(data, dict):
            return []
        event_type = data.get("type")
        if event_type == "message_chunk":
            return self._message_event(
                {
                    "type": "messages",
                    "ns": data.get("ns") or (),
                    "data": data.get("data"),
                }
            )
        if event_type == "progress":
            # 阶段级公开说明：只接受已登记节点名和纯文本 summary，并按该节点
            # 当前活跃阶段绑定；step_id 缺省时由适配器补齐。
            node_name = data.get("node_name")
            summary = data.get("summary")
            if not isinstance(node_name, str) or node_name not in NODE_DESCRIPTIONS:
                return []
            if not isinstance(summary, str) or not summary or len(summary) > 1200:
                return []
            step = self._active_step(node_name)
            if step is None:
                return []
            event = self._base("progress", step)
            event["summary"] = summary
            supplied_step_id = str(data.get("step_id") or "")
            if _SAFE_STEP_ID.fullmatch(supplied_step_id):
                event["step_id"] = supplied_step_id
            supplied_event_key = str(data.get("_event_key") or "")
            if supplied_event_key and len(supplied_event_key) <= 255:
                event["_event_key"] = supplied_event_key
            return [event]
        if event_type == "decision":
            decision_kind = data.get("decision_kind")
            summary = data.get("summary")
            if decision_kind not in {"algorithm", "evidence", "final"}:
                return []
            if not isinstance(summary, str) or not summary or len(summary) > 1200:
                return []
            step = (
                self._active_step("deep_agent")
                if decision_kind in {"algorithm", "evidence"}
                else self._active_step("finalization_gate")
            )
            if step is None:
                return []
            event = self._base("decision", step)
            event["decision_kind"] = decision_kind
            event["summary"] = summary
            if decision_kind in {"algorithm", "evidence"}:
                event["tool_name"] = self._safe_public_tool_name(
                    data.get("tool_name")
                )
            confidence = data.get("confidence")
            if confidence in {"low", "medium", "high"}:
                event["confidence"] = confidence
            supplied_event_key = str(data.get("_event_key") or "")
            if supplied_event_key and len(supplied_event_key) <= 255:
                event["_event_key"] = supplied_event_key
            return [event]
        if event_type in {"tool_call_start", "tool_call_result"}:
            step = self._lifecycle_step(data)
            tool_name = self._safe_public_tool_name(data.get("tool_name"))
            if event_type == "tool_call_result":
                # 结果事件可能在 graph stream 的 task 事件之后才到达；先记住
                # 已完成的逻辑工具，避免后续聚合 update 再造一条结果事件。
                if step is not None:
                    self._lifecycle_tools.add(tool_name)
            elif step is not None:
                self._lifecycle_started_tools.add(tool_name)
            if not step:
                return []
            event = self._base(event_type, step)
            supplied_step_id = str(data.get("step_id") or "")
            if _SAFE_STEP_ID.fullmatch(supplied_step_id):
                event["step_id"] = supplied_step_id
            event["tool_name"] = tool_name
            supplied_event_key = str(data.get("_event_key") or "")
            if supplied_event_key and len(supplied_event_key) <= 255:
                event["_event_key"] = supplied_event_key
            if event_type == "tool_call_start":
                event["argument_keys"] = []
            else:
                event["summary"] = self._tool_result_summary(data)
                event["status"] = self._safe_result_status(data)
                safe_error_code = self._safe_error_code(data)
                if safe_error_code:
                    event["safe_error_code"] = safe_error_code
            return [event]
        task_id = str(data.get("task_id") or "")
        node_name = data.get("node_name")
        if event_type == "node_attempt_failed" and task_id:
            self.failed_attempts[task_id] = sanitize_public_error(
                data.get("error_kind")
            )
            return []
        if event_type != "node_attempt_start" or not task_id:
            return []
        failure = self.failed_attempts.pop(task_id, None)
        if not failure:
            return []
        step = (
            self.steps.get(task_id)
            or self._active_step(node_name)
            or self._parent_tool_step(node_name)
        )
        if not step:
            return []
        discarded = self.streams.pop(step["step_id"], None)
        event = self._base("node_retry", step)
        event["message"] = failure
        if discarded:
            event["discard_stream_id"] = discarded["stream_id"]
        return [event]

    @staticmethod
    def _tool_call(message: Any) -> tuple[str | None, list[str]]:
        """只读取工具名和参数字段名，不返回参数值。"""
        calls = getattr(message, "tool_calls", None) or []
        if not calls:
            return None, []
        call = calls[0]
        if isinstance(call, dict):
            name = call.get("name")
            args = call.get("args")
        else:
            name = getattr(call, "name", None)
            args = getattr(call, "args", None)
        keys = sorted(str(key) for key in args)[:12] if isinstance(args, dict) else []
        return name, keys

    @staticmethod
    def _safe_result_status(result: Any) -> str:
        """把内部结果状态压缩为公共事件允许的有限状态。"""
        if isinstance(result, dict):
            status = result.get("status")
            success = result.get("success")
        else:
            status = getattr(result, "status", None)
            success = getattr(result, "success", None)
        normalized = str(getattr(status, "value", status) or "").strip()
        if normalized in {"valid", "available", "no_results", "succeeded"}:
            return "succeeded"
        if normalized in {"not_ready", "disabled"}:
            return "not_ready"
        if normalized in {"timed_out", "timeout"}:
            return "timed_out"
        if normalized in {"canceled", "cancelled"}:
            return "canceled"
        if normalized in {
            "invalid_input",
            "not_applicable",
            "execution_failed",
            "unavailable",
            "protocol_error",
            "failed",
        }:
            return "failed"
        return "succeeded" if success is not False else "failed"

    @staticmethod
    def _safe_error_code(result: Any) -> str | None:
        """只透传格式受限的安全错误码，不透传异常文本或诊断对象。"""
        if isinstance(result, dict):
            diagnostics = result.get("diagnostics")
            candidate = result.get("safe_error_code")
            if candidate is None and isinstance(diagnostics, dict):
                candidate = diagnostics.get("safe_error_code")
        else:
            diagnostics = getattr(result, "diagnostics", None)
            candidate = getattr(result, "safe_error_code", None)
            if candidate is None:
                candidate = getattr(diagnostics, "safe_error_code", None)
        candidate = str(getattr(candidate, "value", candidate) or "").strip()
        return candidate if _SAFE_ERROR_CODE.fullmatch(candidate) else None

    @classmethod
    def _tool_result_summary(cls, result: Any) -> str:
        """根据受控状态和错误码生成可区分、无敏感信息的用户文案。"""

        status = cls._safe_result_status(result)
        safe_error_code = cls._safe_error_code(result)
        raw_status = (
            result.get("status")
            if isinstance(result, dict)
            else getattr(result, "status", None)
        )
        raw_status = str(getattr(raw_status, "value", raw_status) or "").lower()
        if status == "succeeded":
            return "调用完成"
        if status == "canceled":
            return "调用已取消"
        if status == "timed_out":
            return "调用超时"
        if safe_error_code == "WEB_SEARCH_DISABLED":
            return "未启用"
        if (
            raw_status == "unavailable"
            or status == "not_ready"
            or safe_error_code
            in {"RAG_RETRIEVAL_UNAVAILABLE", "WEB_SEARCH_UNAVAILABLE"}
        ):
            return "暂不可用"
        return "调用失败"

    @staticmethod
    def _safe_algorithm_tool_name(result: Any) -> str:
        capability = (
            result.get("capability_id")
            if isinstance(result, dict)
            else getattr(result, "capability_id", None)
        )
        return _ALGORITHM_TOOL_NAMES.get(str(capability), "算法工具")

    @staticmethod
    def _safe_public_tool_name(value: Any) -> str:
        """只允许已约定格式的公开工具名，拒绝把内部名称原样外带。"""

        name = str(value or "").strip()
        return name if _SAFE_TOOL_NAME.fullmatch(name) else "工具"

    def _deep_agent_result_events(
        self,
        step: dict[str, Any],
        output: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """把 Deep Agent 聚合结果投影为不含引用/正文的工具完成事件。"""
        results = output.get("deep_agent_algorithm_results")
        if not isinstance(results, dict):
            return []
        events: list[dict[str, Any]] = []
        for result in results.values():
            tool_name = self._safe_algorithm_tool_name(result)
            if tool_name in self._lifecycle_tools:
                continue
            event = self._base("tool_call_result", step)
            event.update(
                {
                    "tool_name": tool_name,
                    "summary": self._tool_result_summary(result),
                    "status": self._safe_result_status(result),
                }
            )
            safe_error_code = self._safe_error_code(result)
            if safe_error_code:
                event["safe_error_code"] = safe_error_code
            events.append(event)
        return events

    @staticmethod
    def _partial_json_string(raw: str, key: str) -> str:
        """从尚未闭合的 JSON 参数中安全提取一个字符串前缀。"""

        decision_offset = raw.find('"public_decision"')
        if decision_offset < 0:
            return ""
        match = re.search(
            rf'"{re.escape(key)}"\s*:\s*"',
            raw[decision_offset:],
        )
        if match is None:
            return ""
        start = decision_offset + match.end()
        end = start
        escaped = False
        while end < len(raw):
            char = raw[end]
            if char == '"' and not escaped:
                break
            if char == "\\" and not escaped:
                escaped = True
            else:
                escaped = False
            end += 1
        encoded = raw[start:end]
        for length in range(len(encoded), -1, -1):
            try:
                value = json.loads('"' + encoded[:length] + '"')
            except (TypeError, ValueError):
                continue
            return value if isinstance(value, str) else ""
        return ""

    def _decision_chunk_events(self, message: Any) -> list[dict[str, Any]]:
        """把工具调用参数中的公开决策摘要转换为真实增量事件。"""

        chunks = getattr(message, "tool_call_chunks", None) or []
        if not chunks:
            return []
        step = self._active_step("deep_agent")
        if step is None:
            return []
        events: list[dict[str, Any]] = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            index = chunk.get("index")
            call_key = (
                f"index:{index}"
                if index is not None
                else f"id:{chunk.get('id') or 'unknown'}"
            )
            key = f"{step['step_id']}:{call_key}"
            existing = self.decision_streams.get(key)
            tool_name = str(
                chunk.get("name") or (existing or {}).get("tool_name") or ""
            ).strip()
            decision_kind = _DECISION_TOOL_KINDS.get(tool_name)
            if decision_kind is None:
                continue
            if existing is None:
                existing = {
                    "tool_name": tool_name,
                    "raw_args": "",
                    "summary": "",
                    "stream_id": _opaque_id(
                        self.job_id,
                        self.job_attempt,
                        "decision",
                        key,
                    ),
                    "sequence": 0,
                }
                self.decision_streams[key] = existing
            elif tool_name and not existing.get("tool_name"):
                existing["tool_name"] = tool_name

            raw_args = chunk.get("args")
            if isinstance(raw_args, dict):
                raw_args = json.dumps(
                    raw_args,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            if not isinstance(raw_args, str) or not raw_args:
                continue
            previous_raw = str(existing.get("raw_args") or "")
            combined_raw = (
                raw_args
                if raw_args.startswith(previous_raw)
                else previous_raw + raw_args
            )[:_MAX_DECISION_ARGS]
            existing["raw_args"] = combined_raw
            summary = self._partial_json_string(combined_raw, "summary")[
                :_MAX_DECISION_SUMMARY
            ]
            previous_summary = str(existing.get("summary") or "")
            if not summary or len(summary) <= len(previous_summary):
                continue
            delta = summary[len(previous_summary):]
            existing["summary"] = summary
            existing["sequence"] += 1
            event = self._base("decision_chunk", step)
            event.update(
                {
                    "stream_id": existing["stream_id"],
                    "sequence": existing["sequence"],
                    "delta": delta,
                    "decision_kind": decision_kind,
                    "tool_name": self._safe_public_tool_name(tool_name),
                }
            )
            events.append(event)
        return events

    def _update_event(self, namespace: Any, data: Any) -> list[dict[str, Any]]:
        """从显式 State 和规范化结果生成 decision、progress 与工具摘要。"""
        if not isinstance(data, dict):
            return []
        events: list[dict[str, Any]] = []
        for node_name, output in data.items():
            if not isinstance(output, dict):
                continue
            step = self._active_step(node_name)
            if step:
                decision_field = DECISION_FIELDS.get(node_name)
                decision = output.get(decision_field) if decision_field else None
                if decision:
                    progress = self._base("progress", step)
                    progress["summary"] = DECISION_PROGRESS.get(
                        decision,
                        "路由判断完成",
                    )
                    events.append(progress)
                    event = self._base("decision", step)
                    event["summary"] = (
                        f"已决定进入：{NODE_DESCRIPTIONS.get(decision, decision)}"
                    )
                    events.append(event)
                if node_name == "deep_agent":
                    events.extend(self._deep_agent_result_events(step, output))
            tool_step = self._parent_tool_step(node_name)
            if not tool_step:
                continue
            messages = output.get("messages") or []
            latest = messages[-1] if messages else None
            tool_name, argument_keys = self._tool_call(latest)
            if tool_name and tool_name not in self._lifecycle_started_tools:
                event = self._base("tool_call_start", tool_step)
                event.update(
                    {
                        "tool_name": self._safe_public_tool_name(tool_name),
                        "argument_keys": argument_keys,
                    }
                )
                events.append(event)
            if TOOL_STAGE_NODES[node_name] == "mcp":
                result = output.get("causal_analysis_result")
            elif node_name == "rag_finalize":
                result = output.get("rag_output")
            else:
                result = output.get("knowledge_base_result")
            if isinstance(result, dict):
                metadata = result.get("_tool_call")
                if not isinstance(metadata, dict):
                    metadata = {}
                event = self._base("tool_call_result", tool_step)
                event.update(
                    {
                        "tool_name": self._safe_public_tool_name(
                            metadata.get("name") or tool_name
                        ),
                        "summary": self._tool_result_summary(result),
                        "status": self._safe_result_status(result),
                    }
                )
                safe_error_code = self._safe_error_code(result)
                if safe_error_code:
                    event["safe_error_code"] = safe_error_code
                events.append(event)
        return events

    def _message_event(self, data: Any) -> list[dict[str, Any]]:
        """转换公开正文 token 或工具参数中的公开决策摘要 token。"""
        if not isinstance(data, (tuple, list)) or len(data) != 2:
            return []
        chunk, metadata = data
        if not isinstance(metadata, dict):
            return []
        decision_events = self._decision_chunk_events(chunk)
        if decision_events:
            return decision_events
        node_name = metadata.get("langgraph_node")
        content = getattr(chunk, "content", None)
        if (
            node_name not in TEXT_STREAM_NODES
            or not isinstance(content, str)
            or not content
        ):
            return []
        step = self._active_step(node_name)
        if not step:
            return []
        stream = self.streams.get(step["step_id"])
        if not stream:
            stream = {
                "stream_id": _opaque_id(step["step_id"], time.monotonic_ns()),
                "sequence": 0,
            }
            self.streams[step["step_id"]] = stream
        stream["sequence"] += 1
        event = self._base("text_chunk", step)
        event.update(
            {
                "stream_id": stream["stream_id"],
                "sequence": stream["sequence"],
                "delta": content,
            }
        )
        return [event]

    def convert(self, chunk: Any) -> list[dict[str, Any]]:
        """转换一个 v2 StreamPart；无法识别的内部流会被忽略。"""
        if not isinstance(chunk, dict):
            return []
        stream_type = chunk.get("type")
        namespace = chunk.get("ns") or ()
        data = chunk.get("data")
        if stream_type == "tasks":
            return self._task_event(namespace, data)
        if stream_type == "custom":
            return self._custom_event(data)
        if stream_type == "updates":
            return self._update_event(namespace, data)
        if stream_type == "messages":
            return self._message_event(data)
        return []
