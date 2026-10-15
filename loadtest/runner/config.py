"""压测客户端配置：入口地址、账号存储路径、运行产物目录与分级负载表。"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
LOADTEST_DIR = PACKAGE_DIR.parent
# 本地凭据与运行产物统一收在 local/ 下，该目录整体加入 .gitignore。
LOCAL_DIR = LOADTEST_DIR / "local"
DATA_DIR = LOCAL_DIR / "data"
RUNS_DIR = LOCAL_DIR / "runs"
EVIDENCE_DIR = LOCAL_DIR / "evidence"
ACCOUNTS_PATH = LOCAL_DIR / "accounts.local.json"

DEFAULT_BASE_URL = "http://203.195.194.205:8088"
ACCOUNT_PREFIX = os.environ.get("LOADTEST_ACCOUNT_PREFIX", "loadtest_")
DEFAULT_PASSWORD = os.environ.get("LOADTEST_PASSWORD", "Loadtest-20261010-a1")


def base_url() -> str:
    """返回入口地址；可用 LOADTEST_BASE_URL 覆盖。"""
    return os.environ.get("LOADTEST_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


# 分级负载表；flows 只在 full_flow 档使用，duration 只在 mixed 档使用。
STAGES: dict[str, dict] = {
    "smoke": {"users": 1, "active_jobs": 1, "sse": 1, "flows": 1, "mode": "full_flow"},
    "baseline": {"users": 1, "active_jobs": 1, "sse": 1, "flows": 4, "mode": "full_flow"},
    "low": {"users": 2, "active_jobs": 1, "sse": 2, "duration": 300, "mode": "mixed"},
    "mid_low": {"users": 5, "active_jobs": 2, "sse": 4, "duration": 600, "mode": "mixed"},
    "mid": {"users": 10, "active_jobs": 4, "sse": 8, "duration": 600, "mode": "mixed"},
    "stability": {"users": 10, "active_jobs": 4, "sse": 8, "duration": 1800, "mode": "mixed"},
    "burst": {"users": 20, "active_jobs": 4, "sse": 8, "duration": 600, "mode": "mixed"},
}

# 混合操作比例：查询、页面与登录状态、上传、创建 Job。
MIX_WEIGHTS = {"read": 0.60, "page": 0.20, "upload": 0.10, "create_job": 0.10}

# 每个操作之间的随机等待范围（秒）。
THINK_MIN_SECONDS = 2.0
THINK_MAX_SECONDS = 5.0

# SSE 单次订阅的最长等待时间（秒）；超出按断线处理。
SSE_MAX_WAIT_SECONDS = float(os.environ.get("LOADTEST_SSE_MAX_WAIT_SECONDS", "900"))
# HTTP 单次请求超时（秒）。
HTTP_TIMEOUT_SECONDS = float(os.environ.get("LOADTEST_HTTP_TIMEOUT_SECONDS", "30"))

# 等待 Job 并发额度的最长时间（秒）；额度用满时排队等待，而不是跳过创建。
JOB_SLOT_WAIT_SECONDS = float(os.environ.get("LOADTEST_JOB_SLOT_WAIT_SECONDS", "300"))
