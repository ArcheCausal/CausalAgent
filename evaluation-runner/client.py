"""被测服务的 HTTP 与 SSE 客户端。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import httpx


class ClientError(RuntimeError):
    """被测服务返回非预期状态。"""


class AuthError(ClientError):
    """登录/注册失败。"""


def parse_sse(lines):
    event_id = None
    event_type = None
    data: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line:
            if event_type:
                payload = json.loads("".join(data) or "{}")
                yield event_id, event_type, payload
            event_id, event_type, data = None, None, []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("id: "):
            event_id = int(line[4:]) if line[4:].isdigit() else None
        elif line.startswith("event: "):
            event_type = line[7:]
        elif line.startswith("data: "):
            data.append(line[6:])


def _json(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ClientError(f"响应不是 JSON: {response.status_code}") from exc
    if response.status_code >= 400 or payload.get("success") is False:
        raise ClientError(str(payload.get("error") or response.status_code))
    return payload


class AgentClient:
    def __init__(self, base_url: str, timeout: float = 60.0):
        self.http = httpx.Client(base_url=base_url, timeout=timeout, follow_redirects=True)
        self.csrf_token = ""

    def close(self) -> None:
        self.http.close()

    def _headers(self, idempotency_key: str | None = None) -> dict:
        headers = {}
        if self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def check_auth(self) -> dict:
        payload = _json(self.http.get("/api/check_auth"))
        self.csrf_token = payload.get("csrf_token") or self.csrf_token
        return payload

    def _register(self, username: str, password: str) -> None:
        payload = self.http.post(
            "/api/register", json={"username": username, "password": password}
        )
        if payload.status_code >= 400:
            raise AuthError(f"注册失败: {payload.status_code}")

    def login(self, username: str, password: str) -> None:
        try:
            payload = _json(self.http.post(
                "/api/login", json={"username": username, "password": password}
            ))
        except ClientError as exc:
            self._register(username, password)
            payload = _json(self.http.post(
                "/api/login", json={"username": username, "password": password}
            ))
        self.csrf_token = payload.get("csrf_token") or self.csrf_token

    def upload_csv(self, path: Path) -> int:
        content = Path(path).read_bytes()
        response = self.http.post(
            "/api/upload_file",
            files={"file": (Path(path).name, content, "text/csv")},
            headers=self._headers(),
        )
        return int(_json(response)["user_file_id"])

    def new_session(self) -> str:
        return str(_json(self.http.post("/api/new_chat", headers=self._headers()))["new_session_id"])

    def create_job(self, session_id: str, message: str, user_file_id: int) -> str:
        payload = _json(self.http.post(
            "/api/agent/jobs",
            json={
                "message": message,
                "session_id": session_id,
                "input_user_file_id": user_file_id,
                "web_search_enabled": False,
            },
            headers=self._headers(str(uuid.uuid4())),
        ))
        return str(payload["job_id"])

    def events(self, job_id: str, last_event_id: int = 0):
        with self.http.stream(
            "GET",
            f"/api/agent/jobs/{job_id}/events",
            params={"last_event_id": last_event_id},
            headers=self._headers(),
        ) as response:
            if response.status_code >= 400:
                raise ClientError(f"SSE 连接失败: {response.status_code}")
            for event_id, event_type, payload in parse_sse(response.iter_lines()):
                yield {"id": event_id, "type": event_type, "payload": payload}

    def resume(self, job_id: str, question_id: str, answer) -> None:
        _json(self.http.post(
            f"/api/agent/jobs/{job_id}/resume",
            json={"question_id": question_id, "answer": answer},
            headers=self._headers(str(uuid.uuid4())),
        ))

    def cancel(self, job_id: str) -> None:
        try:
            _json(self.http.post(
                f"/api/agent/jobs/{job_id}/cancel",
                headers=self._headers(str(uuid.uuid4())),
            ))
        except ClientError:
            pass
