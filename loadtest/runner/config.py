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
#
# 字段含义：
# - users：逻辑账号数。同一账号的并发会话数由 sessions_per_user 单独表示，不由 users 表示。
# - active_jobs：客户端创建 Job 的并发信号量容量；None 表示不设客户端上限，让服务器自己排队。
#   它只影响客户端是否阻塞创建，不改变服务器默认 worker 数量。
# - sse：客户端 SSE 订阅并发信号量容量，要求不小于 users * sessions_per_user，否则限制的是
#   客户端而不是服务器。
# - sessions_per_user：每个账号同时持有的真实会话数；每个会话仍然最多一个活动 Job。
# - create_budget：每个账号累计成功创建的唯一新 Job 上限；None 表示只受持续时间约束。
STAGES: dict[str, dict] = {
    "smoke": {"users": 1, "active_jobs": 1, "sse": 1, "flows": 1, "mode": "full_flow"},
    "baseline": {"users": 1, "active_jobs": 1, "sse": 1, "flows": 4, "mode": "full_flow"},
    "low": {"users": 2, "active_jobs": 1, "sse": 2, "duration": 300, "mode": "mixed"},
    "mid_low": {"users": 5, "active_jobs": 2, "sse": 4, "duration": 600, "mode": "mixed"},
    "mid": {"users": 10, "active_jobs": 4, "sse": 8, "duration": 600, "mode": "mixed"},
    "stability": {"users": 10, "active_jobs": 4, "sse": 8, "duration": 1800, "mode": "mixed"},
    "burst": {"users": 20, "active_jobs": 4, "sse": 8, "duration": 600, "mode": "mixed"},
    # 20 个账号同时进入任务场景，不限客户端活动 Job，观察服务器默认 worker 如何形成排队。
    "burst_ungated": {
        "users": 20,
        "active_jobs": None,
        "sse": 20,
        "duration": 600,
        "mode": "mixed",
    },
    # 30 个账号持续提交，每个账号最多成功创建 20 个唯一新 Job；同时运行数量由服务器决定。
    "queue_pressure": {
        "users": 30,
        "active_jobs": None,
        "sse": 30,
        "duration": 300,
        "mode": "mixed",
        "create_budget": 20,
    },
    # 同一账号两个真实会话同时运行两个 Job，用于验证会话级并发是否被服务端允许。
    "multi_session_smoke": {
        "users": 1,
        "active_jobs": 2,
        "sse": 2,
        "duration": 180,
        "mode": "mixed",
        "sessions_per_user": 2,
        "create_budget": 4,
    },
    # 10 个账号各开 2 个真实会话，同时运行 20 个 Job 并占用 20 条 SSE 连接。
    "multi_session_pressure": {
        "users": 10,
        "active_jobs": 20,
        "sse": 20,
        "duration": 600,
        "mode": "mixed",
        "sessions_per_user": 2,
    },
}

# 混合操作比例：查询、页面与登录状态、上传、创建 Job。
MIX_WEIGHTS = {"read": 0.60, "page": 0.20, "upload": 0.10, "create_job": 0.10}

# 每个操作之间的随机等待范围（秒）。
THINK_MIN_SECONDS = 2.0
THINK_MAX_SECONDS = 5.0

# 时间预算分四类，各自独立，不互相复用：
# 1. 普通 HTTP 请求超时；
# 2. SSE 空闲读取超时，表示一次读取允许等待多久，不是整个 Job 的观察期限；
# 3. 单个 Job 的总观察期限；
# 4. 压测停止后的排空期限。
HTTP_TIMEOUT_SECONDS = float(os.environ.get("LOADTEST_HTTP_TIMEOUT_SECONDS", "30"))
SSE_IDLE_READ_TIMEOUT_SECONDS = float(
    os.environ.get("LOADTEST_SSE_IDLE_READ_TIMEOUT_SECONDS", "1800")
)
JOB_OBSERVE_DEADLINE_SECONDS = float(
    os.environ.get("LOADTEST_JOB_OBSERVE_DEADLINE_SECONDS", "1800")
)
DRAIN_DEADLINE_SECONDS = float(os.environ.get("LOADTEST_DRAIN_DEADLINE_SECONDS", "300"))

# 排队观测：记录创建到进入 running 的耗时；超过该秒数仍未进入运行，仍继续观察并标注为长排队。
QUEUE_OBSERVE_TIMEOUT_SECONDS = float(
    os.environ.get("LOADTEST_QUEUE_OBSERVE_TIMEOUT_SECONDS", "120")
)

# 等待 Job 并发额度的最长时间（秒）；额度用满时排队等待，而不是跳过创建。
JOB_SLOT_WAIT_SECONDS = float(os.environ.get("LOADTEST_JOB_SLOT_WAIT_SECONDS", "300"))

# 同一会话重复提交时，等待会话释放的最长时间，用来测量真实 API 的活动 Job 约束。
SESSION_SLOT_WAIT_SECONDS = float(
    os.environ.get("LOADTEST_SESSION_SLOT_WAIT_SECONDS", "30")
)
