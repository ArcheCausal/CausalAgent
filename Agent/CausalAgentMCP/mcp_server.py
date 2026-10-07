"""因果分析 MCP server；通过 Job 冻结身份读取 MySQL 文件正文。"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any, Callable

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_DIR = os.path.dirname(CURRENT_DIR)
PROJECT_ROOT = os.path.dirname(AGENT_DIR)
for path in (PROJECT_ROOT, AGENT_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

from observability.logging_runtime import (
    configure_logging,
    current_environment,
    log_context,
    log_event,
)
LOGGER = logging.getLogger(__name__)

if __name__ == "__main__":
    configure_logging("mcp", current_environment(), logging.INFO)

try:
    from mcp.server.fastmcp import FastMCP

    from Agent.causal.causalachieve import (
        run_direct_lingam_analysis,
        run_olc_analysis,
        run_pc_analysis,
    )
    from Database.agent_connect import require_frozen_file_for_job

    mcp = FastMCP("causal-analyzer")
except Exception as exc:
    if __name__ == "__main__":
        log_event(
            LOGGER,
            "mcp.startup.failed",
            details={
                "phase": "module_initialization",
                "dependency": "mcp_runtime",
                "reason_code": "initialization_failed",
            },
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        raise SystemExit(1) from None
    raise


def _load_csv(
    user_id: int,
    job_id: str,
    input_user_file_id: int,
    input_object_id: int,
) -> str:
    """按可信运行时身份读取 Job  BLOB，仅在 MCP 进程内存中解码。"""
    file_row = require_frozen_file_for_job(
        user_id,
        job_id,
        input_user_file_id,
        input_object_id,
    )
    return file_row["file_content"].decode("utf-8")


def _error_result(message: str, error_type: str) -> dict[str, Any]:
    """构造不包含文件正文或异常原文的 MCP 失败结果。"""
    return {
        "success": False,
        "message": message,
        "error_type": error_type,
    }


async def _execute_tool(
    tool_name: str,
    runner: Callable[[str], dict],
    *,
    user_id: int,
    session_id: str | None,
    job_id: str,
    input_user_file_id: int,
    input_object_id: int,
    request_id: str | None,
    worker_slot: int | None,
) -> dict:
    """在 MCP 子进程内绑定可信上下文，并只记录大小、耗时和结果类别。"""
    started_at = time.perf_counter()
    input_bytes = 0
    with log_context(
        request_id=request_id,
        user_id=user_id,
        session_id=session_id,
        job_id=job_id,
        worker_slot=worker_slot,
        node="mcp_tool_node",
        tool=tool_name,
    ):
        try:
            csv_data = _load_csv(
                user_id,
                job_id,
                input_user_file_id,
                input_object_id,
            )
            input_bytes = len(csv_data.encode("utf-8"))
            result = runner(csv_data)
        except FileNotFoundError:
            log_event(
                LOGGER,
                "mcp.tool.failed",
                details={
                    "duration_ms": int((time.perf_counter() - started_at) * 1000),
                    "input_bytes": input_bytes,
                    "reason_code": "unavailable",
                },
            )
            return _error_result("任务文件不可用", "FrozenFileNotFound")
        except Exception:
            log_event(
                LOGGER,
                "mcp.tool.failed",
                details={
                    "duration_ms": int((time.perf_counter() - started_at) * 1000),
                    "input_bytes": input_bytes,
                    "reason_code": "tool_error",
                },
                exc_info=True,
            )
            return _error_result("执行分析时发生内部错误", "AnalysisError")

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        if isinstance(result, dict) and result.get("success") is False:
            log_event(
                LOGGER,
                "mcp.tool.failed",
                details={
                    "duration_ms": duration_ms,
                    "input_bytes": input_bytes,
                    "reason_code": "invalid_result",
                },
            )
        else:
            result_kind = "structured_result" if isinstance(result, dict) else "other_result"
            log_event(
                LOGGER,
                "mcp.tool.finished",
                details={
                    "duration_ms": duration_ms,
                    "input_bytes": input_bytes,
                    "result_kind": result_kind,
                },
            )
        return result


@mcp.tool()
async def causal_pc(
    user_id: int,
    job_id: str,
    input_user_file_id: int,
    input_object_id: int,
    request_id: str | None = None,
    session_id: str | None = None,
    worker_slot: int | None = None,
) -> dict:
    """
    使用PC算法对CSV数据执行因果发现分析。

    PC算法是一种基于约束的因果发现方法，通过测试条件独立性关系构建因果图，
    而不对因果机制的功能形式做出假设。

    算法运行分为两个阶段：
    1. 骨架学习(Skeleton Learning): 通过移除条件独立变量之间的边来构建无向图骨架
    2. 边定向(Edge Orientation): 使用碰撞检测(规则0)和Meek定向规则来定向边，
       生成部分有向无环图(PDAG)

    PC算法特别适用于以下场景：
    - 无需对功能形式进行先验知识的一般性因果发现
    - 仅提供条件独立性信息的场景
    - 使用快速邻接搜索优化的大规模问题


    Args:
        user_id: 由 Agent runtime 注入的当前用户 ID。
        job_id: 由 Agent runtime 注入的当前分析任务 ID。
        input_user_file_id: Job 创建时冻结的逻辑文件 ID。
        input_object_id: Job 创建时冻结的不可变文件对象 ID。

    文件正文由工具按 Job 冻结身份从文件库读取，模型不得传入 csv_data。

    Returns:
        一个包含分析结果的结构化字典，包括因果图结构和边的方向信息。
    """

    return await _execute_tool(
        "causal_pc",
        run_pc_analysis,
        user_id=user_id,
        session_id=session_id,
        job_id=job_id,
        input_user_file_id=input_user_file_id,
        input_object_id=input_object_id,
        request_id=request_id,
        worker_slot=worker_slot,
    )


async def causal_olc(
    user_id: int,
    job_id: str,
    input_user_file_id: int,
    input_object_id: int,
    request_id: str | None = None,
    session_id: str | None = None,
    worker_slot: int | None = None,
) -> dict:
    """
    使用OLC算法对CSV数据执行因果发现分析，专门处理存在隐藏混杂因素的场景。

    OLC(Overcomplete Learning for Causal discovery)算法适用于以下场景：
    - 预期存在隐藏混杂因素: 领域知识表明未观测变量影响多个测量变量
    - 虚假相关性存在: 变量之间存在强相关性，但无直接因果关系
    - 函数因果模型: 可以假设因果机制具有加性噪声模型
    - 连续变量: 数据由连续值变量组成（非离散）
    - 足够的样本量: 至少需要几百个样本以进行可靠的四阶累积量估计

    OLC算法不适用于：
    - 没有潜在混杂因素的纯观测场景（请改用PC算法）
    - 离散或分类变量
    - 非加性噪声模型
    - 非常小的样本量（<200个样本）

    Args:
        user_id: 由 Agent runtime 注入的当前用户 ID。
        job_id: 由 Agent runtime 注入的当前分析任务 ID。
        input_user_file_id: Job 创建时冻结的逻辑文件 ID。
        input_object_id: Job 创建时冻结的不可变文件对象 ID。

    文件正文由工具按 Job 冻结身份从文件库读取，模型不得传入 csv_data。

    Returns:
        一个包含分析结果的结构化字典，包括因果图结构和潜在混杂因素信息。
    """
    return await _execute_tool(
        "causal_olc",
        run_olc_analysis,
        user_id=user_id,
        session_id=session_id,
        job_id=job_id,
        input_user_file_id=input_user_file_id,
        input_object_id=input_object_id,
        request_id=request_id,
        worker_slot=worker_slot,
    )


@mcp.tool()
async def causal_direct_lingam(
    user_id: int,
    job_id: str,
    input_user_file_id: int,
    input_object_id: int,
    request_id: str | None = None,
    session_id: str | None = None,
    worker_slot: int | None = None,
) -> dict:
    """使用 DirectLiNGAM 对连续数值 CSV 数据执行因果发现分析。

    适用于线性、非高斯、误差独立且无潜在混杂的连续数值变量；文件正文由
    工具按 Job 冻结身份读取，模型不得传入 csv_data。

    Args:
        user_id: 由 Agent runtime 注入的当前用户 ID。
        job_id: 由 Agent runtime 注入的当前分析任务 ID。
        input_user_file_id: Job 创建时冻结的逻辑文件 ID。
        input_object_id: Job 创建时冻结的不可变文件对象 ID。
    """
    return await _execute_tool(
        "causal_direct_lingam",
        run_direct_lingam_analysis,
        user_id=user_id,
        session_id=session_id,
        job_id=job_id,
        input_user_file_id=input_user_file_id,
        input_object_id=input_object_id,
        request_id=request_id,
        worker_slot=worker_slot,
    )


def main() -> None:
    """启动 stdio MCP server；应用日志只写 stderr，stdout 留给协议。"""
    configure_logging("mcp", current_environment(), logging.INFO)
    log_event(
        LOGGER,
        "mcp.startup.ready",
    )
    try:
        mcp.run(transport="stdio")
    except Exception:
        log_event(
            LOGGER,
            "mcp.startup.failed",
            details={
                "phase": "stdio_transport",
                "dependency": "mcp_runtime",
                "reason_code": "runtime_failed",
            },
            exc_info=True,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
