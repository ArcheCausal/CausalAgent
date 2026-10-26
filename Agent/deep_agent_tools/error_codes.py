"""Deep Agent/MCP 共享的安全错误码。

错误码是跨 Adapter、Executor、Ledger 和后续公共事件适配器的稳定边界。
这里不保存异常原文，也不把服务端输出契约错误归类为用户输入错误。
"""

from __future__ import annotations

from enum import Enum


class SafeErrorCode(str, Enum):
    """首版允许跨边界传输的、无敏感细节的错误码。"""

    USER_INPUT_FILE_MISSING = "USER_INPUT_FILE_MISSING"
    USER_INPUT_SCHEMA_AMBIGUOUS = "USER_INPUT_SCHEMA_AMBIGUOUS"
    USER_INPUT_REQUIRED_FIELD_MISSING = "USER_INPUT_REQUIRED_FIELD_MISSING"
    ALGORITHM_INPUT_INVALID = "ALGORITHM_INPUT_INVALID"
    ALGORITHM_NOT_APPLICABLE = "ALGORITHM_NOT_APPLICABLE"
    ALGORITHM_NOT_READY = "ALGORITHM_NOT_READY"
    ALGORITHM_EXECUTION_FAILED = "ALGORITHM_EXECUTION_FAILED"
    ALGORITHM_TIMED_OUT = "ALGORITHM_TIMED_OUT"
    ALGORITHM_CANCELED = "ALGORITHM_CANCELED"
    ALGORITHM_RESULT_CONTRACT_INVALID = "ALGORITHM_RESULT_CONTRACT_INVALID"
    UNKNOWN_CAPABILITY = "UNKNOWN_CAPABILITY"
    DUPLICATE_INVOCATION = "DUPLICATE_INVOCATION"
    LEDGER_REVISION_CONFLICT = "LEDGER_REVISION_CONFLICT"
    MCP_CAPABILITY_VERSION_UNSUPPORTED = "MCP_CAPABILITY_VERSION_UNSUPPORTED"
    MCP_CAPACITY_EXHAUSTED = "MCP_CAPACITY_EXHAUSTED"
    MCP_AUTH_FAILED = "MCP_AUTH_FAILED"
    MCP_CONTEXT_INVALID = "MCP_CONTEXT_INVALID"
    MCP_LEASE_STALE = "MCP_LEASE_STALE"
    MCP_TRANSPORT_FAILED = "MCP_TRANSPORT_FAILED"


USER_INPUT_ERROR_CODES = frozenset(
    {
        SafeErrorCode.USER_INPUT_FILE_MISSING,
        SafeErrorCode.USER_INPUT_SCHEMA_AMBIGUOUS,
        SafeErrorCode.USER_INPUT_REQUIRED_FIELD_MISSING,
        SafeErrorCode.ALGORITHM_INPUT_INVALID,
    }
)

SERVER_OUTPUT_ERROR_CODES = frozenset(
    {
        SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID,
        SafeErrorCode.ALGORITHM_EXECUTION_FAILED,
        SafeErrorCode.MCP_TRANSPORT_FAILED,
    }
)


def is_user_input_error(code: SafeErrorCode | str) -> bool:
    """判断错误码是否能合理映射到 ``invalid_input``。"""

    try:
        normalized = SafeErrorCode(code)
    except ValueError:
        return False
    return normalized in USER_INPUT_ERROR_CODES


def is_server_output_error(code: SafeErrorCode | str) -> bool:
    """判断错误是否来自执行/输出契约，而非用户输入。"""

    try:
        normalized = SafeErrorCode(code)
    except ValueError:
        return False
    return normalized in SERVER_OUTPUT_ERROR_CODES

