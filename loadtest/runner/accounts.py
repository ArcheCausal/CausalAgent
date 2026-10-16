"""测试账号的注册与本地凭据存储。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from runner.config import ACCOUNT_PREFIX, ACCOUNTS_PATH, DEFAULT_PASSWORD, HTTP_TIMEOUT_SECONDS


@dataclass
class Account:
    """一个测试账号的本地记录。"""

    username: str
    password: str
    user_id: int | None = None


def account_username(index: int, stamp: str | None = None) -> str:
    """生成带测试标识的用户名，例如 loadtest_20261010_01。"""
    day = stamp or time.strftime("%Y%m%d")
    return f"{ACCOUNT_PREFIX}{day}_{index:02d}"


def load_accounts(path: Path | None = None) -> list[Account]:
    """读取本地凭据文件；不存在时返回空列表。"""
    target = path or ACCOUNTS_PATH
    if not target.exists():
        return []
    payload = json.loads(target.read_text(encoding="utf-8"))
    return [
        Account(
            username=item["username"],
            password=item["password"],
            user_id=item.get("user_id"),
        )
        for item in payload.get("accounts", [])
    ]


def save_accounts(accounts: list[Account], base_url: str, path: Path | None = None) -> Path:
    """把账号凭据写入本地文件；该文件已加入 .gitignore。"""
    target = path or ACCOUNTS_PATH
    payload = {
        "base_url": base_url,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "accounts": [
            {"username": item.username, "password": item.password, "user_id": item.user_id}
            for item in accounts
        ],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def ensure_accounts(
    base_url: str,
    count: int,
    path: Path | None = None,
    stamp: str | None = None,
) -> tuple[list[Account], list[str]]:
    """确保存在 count 个可用测试账号，返回账号列表与执行说明。"""
    accounts = load_accounts(path)
    notes: list[str] = []
    existing_names = {item.username for item in accounts}
    with httpx.Client(
        base_url=base_url, timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False
    ) as client:
        for index in range(1, count + 1):
            username = account_username(index, stamp)
            if username in existing_names:
                continue
            response = client.post(
                "/api/register",
                json={"username": username, "password": DEFAULT_PASSWORD},
            )
            if response.status_code == 200 and response.json().get("success"):
                accounts.append(Account(username=username, password=DEFAULT_PASSWORD))
                notes.append(f"已注册 {username}")
                continue
            body = _safe_json(response)
            if response.status_code == 400 and "已被注册" in str(body.get("error", "")):
                accounts.append(Account(username=username, password=DEFAULT_PASSWORD))
                notes.append(f"已存在 {username}")
                continue
            notes.append(f"注册失败 {username}: {response.status_code} {body.get('error')}")
    if path is not None or accounts:
        save_accounts(accounts, base_url, path)
    return accounts, notes


def verify_accounts(base_url: str, accounts: list[Account]) -> list[str]:
    """逐个登录验证账号可用，返回失败说明。"""
    problems: list[str] = []
    with httpx.Client(
        base_url=base_url, timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False
    ) as client:
        for item in accounts:
            response = client.post(
                "/api/login",
                json={"username": item.username, "password": item.password},
            )
            if response.status_code != 200 or not response.json().get("success"):
                problems.append(f"{item.username}: {response.status_code}")
    return problems


def _safe_json(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
