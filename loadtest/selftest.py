"""压测客户端的真实 HTTP 自检：用一个本地桩服务验证提交、观察与额度口径。

这个桩服务只承载接口形状与并发约束，不是被测系统；真实接口契约见 Document/api/agent-jobs.md。
自检要回答三件事：

1. 同一账号的两个真实会话可以同时接受两个 Job；
2. 同一会话的重复活动 Job 按真实 API 规则返回 409，不会被客户端当成新创建计数；
3. 每个账号的创建额度按“唯一成功创建的 Job”计算，幂等重放不占用额度。

用法：python selftest.py
"""

from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from runner import config  # noqa: E402
from runner.accounts import Account  # noqa: E402
from runner.stages import run_stage  # noqa: E402


class StubState:
    """桩服务的共享状态：会话、Job、事件与并发观测。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sessions: dict[str, str] = {}
        self.jobs: dict[str, dict] = {}
        self.events: dict[str, list[tuple[int, str, dict]]] = {}
        self.idempotency: dict[str, str] = {}
        self.next_event_id = 1
        self.peak_running = 0
        self.running_now = 0
        self.peak_open_streams = 0
        self.open_streams = 0
        self.terminal_counts: dict[str, int] = {}
        # Job 进入 running 后保持的时间；用来让多会话并发在观测窗口内可见。
        self.job_ttl = 15.0
        # Job 在 queued 状态停留的时间；用来验证长排队不会被当成 SSE 断线。
        self.queue_hold = 0.0

    def start_job(self, session_id: str, key: str) -> tuple[int, dict]:
        with self.lock:
            if key in self.idempotency:
                job_id = self.idempotency[key]
                return 200, {"success": True, "existing": True, "job_id": job_id}
            for job in self.jobs.values():
                if job["session_id"] == session_id and job["status"] in (
                    "queued",
                    "running",
                    "waiting_input",
                ):
                    return 409, {
                        "success": False,
                        "code": "active_job_conflict",
                        "error": "同一会话已有活动 Job",
                    }
            job_id = str(uuid.uuid4())
            self.jobs[job_id] = {
                "session_id": session_id,
                "status": "queued",
                "created": time.monotonic(),
            }
            self.idempotency[key] = job_id
            self.events[job_id] = []
            self.running_now += 1
            self.peak_running = max(self.peak_running, self.running_now)
            if self.queue_hold > 0:
                # 排队保持时间由 holding 线程处理，避免在已持锁时再次加锁。
                threading.Timer(
                    self.queue_hold, self._finish_queue_hold, args=(job_id,)
                ).start()
                threading.Timer(
                    self.queue_hold + self.job_ttl, self._finish, args=(job_id,)
                ).start()
            else:
                self._emit_locked(job_id, "node_start", {"node": "分析"})
                self.jobs[job_id]["status"] = "running"
                threading.Timer(
                    self.job_ttl, self._finish, args=(job_id,)
                ).start()
            return 202, {
                "success": True,
                "existing": False,
                "job_id": job_id,
                "status": "queued",
            }

    def _finish_queue_hold(self, job_id: str) -> None:
        """排队保持结束时把 Job 从 queued 置为 running 并发出首个进度事件。"""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job["status"] != "queued":
                return
            job["status"] = "running"
            self._emit_locked(job_id, "node_start", {"node": "分析"})

    def _finish(self, job_id: str) -> None:
        """在 Job 保持一段时间之后发出终态事件，模拟真实执行时长。"""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job["status"] not in ("queued", "running"):
                return
            self._emit_locked(job_id, "final_result", {"report": "ok"})
            self.terminal_counts["succeeded"] = self.terminal_counts.get("succeeded", 0) + 1

    def _emit_locked(self, job_id: str, event_type: str, payload: dict) -> None:
        """写入一个事件；调用方必须已经持有 self.lock。"""
        job = self.jobs[job_id]
        if event_type in ("final_result", "error", "canceled"):
            job["status"] = "succeeded" if event_type == "final_result" else "failed"
            self.running_now = max(0, self.running_now - 1)
        self.events[job_id].append((self.next_event_id, event_type, payload))
        self.next_event_id += 1


STATE = StubState()


def reset_state(job_ttl: float) -> None:
    """为一次独立自检重置桩服务状态，避免两次场景互相污染。"""
    global STATE
    STATE = StubState()
    STATE.job_ttl = job_ttl


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # 自检不输出访问日志
        return

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/api/check_auth":
            return self._json(200, {"success": True, "csrf_token": "csrf"})
        if path in ("/", "/dashboard"):
            return self._json(200, {"success": True})
        if path == "/api/sessions":
            return self._json(200, {"success": True, "data": list(STATE.sessions)})
        if path == "/api/files":
            return self._json(200, {"success": True, "data": []})
        if path == "/api/load_session":
            return self._json(200, {"success": True, "messages": []})
        if path == "/api/agent/jobs/active":
            with STATE.lock:
                jobs = [
                    {"job_id": job_id, "status": job["status"]}
                    for job_id, job in STATE.jobs.items()
                    if job["status"] in ("queued", "running", "waiting_input")
                ]
            return self._json(200, {"success": True, "jobs": jobs})
        if path.startswith("/api/agent/jobs/") and path.endswith("/events"):
            return self._events(path.split("/")[4])
        return self._json(404, {"success": False, "error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        if path == "/api/login":
            return self._json(200, {"success": True, "csrf_token": "csrf"})
        if path == "/api/new_chat":
            session_id = str(uuid.uuid4())
            with STATE.lock:
                STATE.sessions[session_id] = "loadtest"
            return self._json(200, {"success": True, "new_session_id": session_id})
        if path == "/api/upload_file":
            return self._json(200, {"success": True, "user_file_id": 1})
        if path == "/api/agent/jobs":
            body = self._read_json()
            key = self.headers.get("Idempotency-Key") or ""
            status, payload = STATE.start_job(body.get("session_id", ""), key)
            return self._json(status, payload)
        return self._json(404, {"success": False, "error": "not found"})

    def _events(self, job_id: str) -> None:
        cursor = int(self.headers.get("Last-Event-ID") or 0)
        with STATE.lock:
            STATE.open_streams += 1
            STATE.peak_open_streams = max(STATE.peak_open_streams, STATE.open_streams)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            # 真实服务在 Job 进行期间保持事件流打开，事件到达就推送；
            # 桩服务按同样方式等待，直到终态事件已发送或到达等待上限。
            deadline = time.monotonic() + 60.0
            while time.monotonic() < deadline:
                with STATE.lock:
                    pending = [
                        item
                        for item in STATE.events.get(job_id, [])
                        if item[0] > cursor
                    ]
                if pending:
                    for event_id, event_type, payload in pending:
                        frame = (
                            f"id: {event_id}\nevent: {event_type}\n"
                            f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                        )
                        self.wfile.write(frame.encode("utf-8"))
                        self.wfile.flush()
                        cursor = event_id
                        if event_type in ("final_result", "error", "canceled"):
                            return
                else:
                    time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with STATE.lock:
                STATE.open_streams -= 1


def main() -> int:
    import faulthandler

    # 自检卡住时把各线程栈打到 stderr，避免只看到空输出。
    faulthandler.dump_traceback_later(150, exit=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    accounts = [Account(username="loadtest_selftest_01", password="x")]

    # 两段场景各用独立档位名，避免第二段覆盖第一段的配置。
    scenario_names = ("selftest_multi_session", "selftest_ungated")
    originals = {
        name: dict(config.STAGES.get(name, {})) for name in scenario_names
    }
    config.STAGES["selftest_multi_session"] = {
        "users": 1,
        "active_jobs": 2,
        "sse": 2,
        "duration": 8,
        "mode": "mixed",
        "sessions_per_user": 2,
        "create_budget": 4,
    }
    config.STAGES["selftest_ungated"] = {
        "users": 1,
        "active_jobs": None,
        "sse": 4,
        "duration": 10,
        "mode": "mixed",
        "sessions_per_user": 4,
        "create_budget": 8,
    }
    config.MIX_WEIGHTS = {"read": 0.0, "page": 0.0, "upload": 0.0, "create_job": 1.0}
    config.THINK_MIN_SECONDS = 0.05
    config.THINK_MAX_SECONDS = 0.1
    config.QUEUE_OBSERVE_TIMEOUT_SECONDS = 2
    config.SESSION_SLOT_WAIT_SECONDS = 2
    config.DRAIN_DEADLINE_SECONDS = 60
    STATE.job_ttl = 20.0
    try:
        summary = run_stage("selftest_multi_session", accounts, base_url)
    finally:
        server.shutdown()

    # 第一段场景的桩服务状态在第二段开始前先固定下来，避免两次场景互相污染。
    first_scenario = {
        "peak_running": STATE.peak_running,
        "sessions_used": len({j["session_id"] for j in STATE.jobs.values()}),
    }
    ungate_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ungate_port = ungate_server.server_address[1]
    threading.Thread(target=ungate_server.serve_forever, daemon=True).start()
    reset_state(job_ttl=20.0)
    try:
        ungate = run_stage(
            "selftest_ungated",
            [Account(username="loadtest_selftest_02", password="x")],
            f"http://127.0.0.1:{ungate_port}",
        )
    finally:
        for name, snapshot in originals.items():
            if snapshot:
                config.STAGES[name] = snapshot
            else:
                config.STAGES.pop(name, None)
        ungate_server.shutdown()

    # 第三段场景：Job 长时间停留在 queued，验证它不会被当成 SSE 断线。
    queue_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    queue_port = queue_server.server_address[1]
    threading.Thread(target=queue_server.serve_forever, daemon=True).start()
    reset_state(job_ttl=6.0)
    STATE.queue_hold = 6.0
    config.STAGES["selftest_queue"] = {
        "users": 1,
        "active_jobs": 1,
        "sse": 1,
        "duration": 4,
        "mode": "mixed",
        "sessions_per_user": 1,
        "create_budget": 2,
    }
    config.QUEUE_OBSERVE_TIMEOUT_SECONDS = 2
    try:
        queued = run_stage(
            "selftest_queue",
            [Account(username="loadtest_selftest_03", password="x")],
            f"http://127.0.0.1:{queue_port}",
        )
    finally:
        config.STAGES.pop("selftest_queue", None)
        queue_server.shutdown()

    job = summary["job"]
    checks = [
        (
            "同一账号两个会话同时运行 Job",
            first_scenario["peak_running"] >= 2,
            f"桩服务观测到的同时运行峰值 {first_scenario['peak_running']}",
        ),
        (
            "两个会话都承载过 Job",
            first_scenario["sessions_used"] == 2,
            f"被使用的会话数 {first_scenario['sessions_used']}",
        ),
        (
            "账号级额度上限生效",
            job["created"] == 2 and job["budget_used_max"] == 2,
            f"成功创建 {job['created']}，额度使用峰值 {job['budget_used_max']}，"
            f"上限 4，会话忙跳过 {job['submissions_session_busy']} 次",
        ),
        (
            "会话忙时不会重复提交",
            job["submissions_session_busy"] >= 1,
            f"会话忙跳过 {job['submissions_session_busy']} 次",
        ),
        (
            "SSE 订阅数等于成功创建数",
            summary["sse"]["subscriptions"] == job["created"],
            f"订阅 {summary['sse']['subscriptions']}，创建 {job['created']}",
        ),
        (
            "无断线、无漏事件",
            summary["sse"]["disconnects"] == 0
            and summary["sse"]["verified_missing_events"] == 0
            and summary["sse"]["duplicate_events"] == 0,
            f"断线 {summary['sse']['disconnects']}，漏事件 "
            f"{summary['sse']['verified_missing_events']}，重复 {summary['sse']['duplicate_events']}",
        ),
        (
            "终态全部为成功",
            job["terminal_status_counts"].get("succeeded") == job["created"],
            f"终态分布 {job['terminal_status_counts']}",
        ),
        (
            "active_jobs=None 不阻塞创建",
            ungate["job"]["created"] >= 4
            and ungate["job"]["submissions_over_budget"] == 0,
            f"成功创建 {ungate['job']['created']}，超额跳过 "
            f"{ungate['job']['submissions_over_budget']}，活动 Job 峰值 "
            f"{ungate['peak_active_jobs']}",
        ),
        (
            "active_jobs=None 仍记录活动 Job 峰值",
            ungate["peak_active_jobs"] is not None and ungate["peak_active_jobs"] >= 1,
            f"活动 Job 峰值 {ungate['peak_active_jobs']}",
        ),
        (
            "排队超过观测窗口不算 SSE 断线",
            queued["sse"]["disconnects"] == 0
            and queued["job"]["queue_observe_timeouts"] >= 1
            and queued["job"]["observation_timeouts"] == 0,
            f"排队超时 {queued['job']['queue_observe_timeouts']}，"
            f"观察超时 {queued['job']['observation_timeouts']}，"
            f"断线 {queued['sse']['disconnects']}，终态 "
            f"{queued['job']['terminal_status_counts']}",
        ),
    ]
    failed = 0
    for name, passed, detail in checks:
        print(f"[{'通过' if passed else '未通过'}] {name}: {detail}")
        failed += 0 if passed else 1
    print(json.dumps({"peak_running": STATE.peak_running}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
