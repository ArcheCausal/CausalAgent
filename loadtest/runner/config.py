"""压测客户端配置：入口地址、账号存储路径、运行产物目录与分级负载表。"""

from __future__ import annotations

import os
import time
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
LOADTEST_DIR = PACKAGE_DIR.parent
# 本地凭据与运行产物统一收在 local/ 下，该目录整体加入 .gitignore。
LOCAL_DIR = LOADTEST_DIR / "local"
DATA_DIR = LOCAL_DIR / "data"
RUNS_DIR = LOCAL_DIR / "runs"
EVIDENCE_DIR = LOCAL_DIR / "evidence"


def date_key(timestamp: float) -> str:
    """把运行开始时刻换算成本地日期键，日期边界跟随压测人员所在地时区。"""
    return time.strftime("%Y%m%d", time.localtime(timestamp))


def run_artifacts_dir(timestamp: float) -> tuple[Path, Path]:
    """返回某次运行使用的日期目录及其中的原始产物目录。

    同一天的全部运行共用 local/runs/<date>/ 与 local/evidence/<date>/；日期按本地
    日历日计算，跨午夜的运行由 run_id 前缀固定在起始日期。
    """
    day = date_key(timestamp)
    return RUNS_DIR / day, EVIDENCE_DIR / day


def date_from_run_id(run_id: str) -> str | None:
    """从 run_id 的 YYYYMMDD 前缀取日期；前缀不是合法日期时返回 None。"""
    prefix = run_id[:8]
    if len(prefix) != 8 or not prefix.isdigit():
        return None
    try:
        time.strptime(prefix, "%Y%m%d")
    except ValueError:
        return None
    return prefix


def run_dir_for(run_id: str, started_at: float | None = None) -> tuple[Path, Path]:
    """按 run_id 定位一次运行的产物目录。

    run_id 自带日期前缀时以它为准，编排脚本与客户端因此得到同一目录；调用方传入
    自定义 run_id 时退回开始时刻所在的本地日期。
    """
    day = date_from_run_id(run_id)
    if day is None:
        day = date_key(started_at if started_at else time.time())
    return RUNS_DIR / day, EVIDENCE_DIR / day


def latest_run_date() -> str | None:
    """返回最近一个含运行摘要的日期目录；没有任何日期目录时返回 None。

    未指定 run_id 的整理、附加与日志命令使用该日期，避免运行跨过午夜后误取当天
    空目录；多个日期目录之间仍然彼此独立。
    """
    if not RUNS_DIR.is_dir():
        return None
    days = sorted(
        path.name
        for path in RUNS_DIR.iterdir()
        if path.is_dir() and path.name.isdigit() and any(path.glob("*/summary.json"))
    )
    return days[-1] if days else None


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
    "cdfm_fixed": {
        "users": 1, "active_jobs": None, "sse": 8,
        "mode": "cdfm_fixed",
        "rows": 200, "seed": 20261020, "tasks": 4,
        "score_group": "cdfm_fixed",
    },
    "cdfm_rate": {
        "users": 1, "active_jobs": None, "sse": 8,
        "mode": "cdfm_rate", "rows": 200, "seed": 20261020,
        "tasks": 20, "arrival_rate": 0.1, "duration": 200,
        "arrival_workers": 8, "arrival_pending": 16,
        "score_group": "cdfm_rate",
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
    os.environ.get("LOADTEST_SSE_IDLE_READ_TIMEOUT_SECONDS", "45")
)
JOB_OBSERVE_DEADLINE_SECONDS = float(
    os.environ.get("LOADTEST_JOB_OBSERVE_DEADLINE_SECONDS", "1800")
)
DRAIN_DEADLINE_SECONDS = float(os.environ.get("LOADTEST_DRAIN_DEADLINE_SECONDS", "1800"))
DRAIN_CHECKPOINT_SECONDS = float(os.environ.get("LOADTEST_DRAIN_CHECKPOINT_SECONDS", "300"))

# 兼容旧编排器配置；客户端不再轮询推算排队时间，排队/执行耗时由主库计算。
QUEUE_OBSERVE_TIMEOUT_SECONDS = float(
    os.environ.get("LOADTEST_QUEUE_OBSERVE_TIMEOUT_SECONDS", "120")
)

# 等待 Job 并发额度的最长时间（秒）；额度用满时排队等待，而不是跳过创建。
JOB_SLOT_WAIT_SECONDS = float(os.environ.get("LOADTEST_JOB_SLOT_WAIT_SECONDS", "300"))

# 同一会话重复提交时，等待会话释放的最长时间，用来测量真实 API 的活动 Job 约束。
SESSION_SLOT_WAIT_SECONDS = float(
    os.environ.get("LOADTEST_SESSION_SLOT_WAIT_SECONDS", "30")
)

# SSE 的读取等待与总观察截止分别控制；重连次数包括网关错误和提前 EOF。
SSE_MAX_RECONNECTS = 3
SSE_RECONNECT_DELAY_SECONDS = 0.5
OBSERVATION_CHECK_INTERVAL_SECONDS = 0.05
CREATE_MAX_ATTEMPTS = 3
CDFM_ROWS = 200
CDFM_SEED = 20261020
ARRIVAL_WORKERS = 8
ARRIVAL_PENDING = 16
CDFM_MESSAGE = (
    "请仅使用 CDFM（causal.cdfm）对上传文件全部五个连续数值变量进行因果发现，"
    "使用全部数据行，不抽样，不调用其他因果发现算法，不进行网络搜索，生成因果图和报告。"
)
