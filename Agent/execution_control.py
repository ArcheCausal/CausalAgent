"""跨 worker 与私有 MCP 进程共享的执行控制异常。

该模块必须保持无 ``app``、LangChain 和数据库依赖，使 MCP 镜像能够导入
Deep Agent 的领域契约而不加载 worker runtime。
"""

from __future__ import annotations


class JobExecutionRevoked(RuntimeError):
    """表示当前 worker 已失去继续推进 Job 的资格。"""
