"""基于 OpenAI Tool Calls 协议的统一 Pydantic 结构化输出入口。"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Mapping, TypeVar

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    # ChatOpenAI 只用于参数类型标注；延迟到类型检查期导入，避免本模块在
    # 未安装 LangChain 的轻量测试环境里无法导入。
    from langchain_openai import ChatOpenAI

from Agent.execution_control import JobExecutionRevoked


SchemaT = TypeVar("SchemaT", bound=BaseModel)

# 模型没有返回可解析工具调用时的稳定原因代码。
NO_TOOL_CALL_CAUSE_CODE = "tool_call_invalid"

# 按异常类型把结构化输出失败归类为稳定代码；只使用类名，不读取异常正文。
_CAUSE_CODE_BY_EXCEPTION = {
    "validationerror": "schema_invalid",
    "jsondecodeerror": "json_invalid",
    "outputparserexception": "output_parser_error",
    "lengthfinishreasonerror": "truncated",
    "apitimeouterror": "timeout",
    "timeouterror": "timeout",
    "apiconnectionerror": "connection_error",
    "connecterror": "connection_error",
    "ratelimiterror": "rate_limited",
    "badrequesterror": "request_rejected",
    "internalservererror": "provider_error",
    "notoolcallerror": NO_TOOL_CALL_CAUSE_CODE,
}

# 统一入口只重试这类原因：模型本次产出的结构化结果不可用，重新调用有意义。
# 超时、限流、连接和供应商错误不在这里重试，仍由节点级错误路径处理。
_RETRYABLE_CAUSE_CODES = frozenset(
    {
        NO_TOOL_CALL_CAUSE_CODE,
        "schema_invalid",
        "json_invalid",
        "output_parser_error",
    }
)

# 统一入口的最大尝试次数：首次调用加一次有界重试。
STRUCTURED_OUTPUT_MAX_ATTEMPTS = 2

_LOC_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_MAX_LOC_SEGMENTS = 4


class NoToolCallError(RuntimeError):
    """表示模型没有返回任何可解析的工具调用。"""


def classify_structured_output_cause(cause: BaseException) -> str:
    """把结构化输出失败的底层异常归类为稳定代码。

    只依据异常类名判断，既避免把模型输出、prompt 或供应商正文写进日志，又让
    「模型没按要求返回结构化结果」和「网络/限流/上下文超限」可以区分。
    """
    name = type(cause).__name__.lower()
    return _CAUSE_CODE_BY_EXCEPTION.get(name, "unknown")


def _safe_location(loc: Any, *, extra_field: bool) -> str | None:
    """把校验错误位置压缩成日志安全的字段路径。

    额外字段名由模型自行编造，因此遇到 extra_forbidden 时只保留父级路径，
    末级固定写成 extra，避免把模型输出写进日志。
    """
    if not isinstance(loc, (list, tuple)):
        return None
    segments = [str(part) for part in loc]
    if extra_field and segments:
        segments = [*segments[:-1], "extra"]
    safe = [part for part in segments if _LOC_SEGMENT_PATTERN.fullmatch(part)]
    location = ".".join(safe[:_MAX_LOC_SEGMENTS])[:64]
    return location or None


def _validation_summary(cause: BaseException) -> dict[str, Any] | None:
    """生成不含取值和异常正文的校验摘要。"""
    if not isinstance(cause, ValidationError):
        return None
    errors = cause.errors()
    if not errors:
        return None
    first = errors[0]
    first_type = str(first.get("type") or "")
    summary: dict[str, Any] = {"count": len(errors)}
    if first_type:
        summary["first_type"] = first_type
    location = _safe_location(
        first.get("loc"),
        extra_field=first_type == "extra_forbidden",
    )
    if location:
        summary["first_loc"] = location
    return summary


class StructuredOutputError(RuntimeError):
    """封装结构化输出调用或 Pydantic 校验失败，并保留可审计元数据。"""

    def __init__(
        self,
        *,
        node_name: str,
        schema_name: str,
        cause: BaseException,
        attempts: int = 1,
    ) -> None:
        self.node_name = node_name
        self.schema_name = schema_name
        self.original_exception_type = type(cause).__name__
        self.safe_cause_code = classify_structured_output_cause(cause)
        self.structured_attempts = max(1, int(attempts))
        self.safe_validation = _validation_summary(cause)
        super().__init__(
            f"{node_name} 结构化输出失败: schema={schema_name}, "
            f"cause={self.original_exception_type}"
        )


def _build_structured_runnable(
    *,
    llm: ChatOpenAI,
    schema: type[SchemaT],
    prompt: Any,
    config: Any = None,
):
    """构造关闭 Thinking 且固定使用普通 function calling 的 Runnable。"""
    llm = llm.model_copy(
        update={
            "extra_body": {
                **(llm.extra_body or {}),
                "thinking": {"type": "disabled"},
            }
        }
    )
    runnable = prompt | llm.with_structured_output(
        schema,
        method="function_calling",
    )
    return runnable.with_config(config) if config is not None else runnable


def _validate_result(result: Any, schema: type[SchemaT]) -> SchemaT:
    """确保公共入口只向业务层返回通过校验的 Schema 实例。"""
    if result is None:
        raise NoToolCallError(f"{schema.__name__} 没有返回可解析的工具调用")
    if isinstance(result, schema):
        return result
    return schema.model_validate(result)


def _retry_allowed(error: StructuredOutputError) -> bool:
    """判断本次失败是否值得在统一入口内再调用一次。"""
    return (
        error.structured_attempts < STRUCTURED_OUTPUT_MAX_ATTEMPTS
        and error.safe_cause_code in _RETRYABLE_CAUSE_CODES
    )


def _ensure_retry_allowed() -> None:
    """重试前确认本次 Job 仍持有执行资格。

    守卫属于 worker 运行时，按需导入可以保持本模块不依赖 app 与数据库。
    """
    from app.agent.worker.execution_guard import raise_if_execution_revoked

    raise_if_execution_revoked()


def invoke_structured(
    *,
    llm: ChatOpenAI,
    schema: type[SchemaT],
    prompt: Any,
    inputs: Mapping[str, Any],
    node_name: str,
    config: Any = None,
) -> SchemaT:
    """同步执行结构化调用：结果不可用时重试一次，随后抛出 StructuredOutputError。"""
    attempts = 0
    while True:
        attempts += 1
        try:
            runnable = _build_structured_runnable(
                llm=llm,
                schema=schema,
                prompt=prompt,
                config=config,
            )
            return _validate_result(runnable.invoke(dict(inputs)), schema)
        except JobExecutionRevoked:
            raise
        except Exception as exc:
            error = StructuredOutputError(
                node_name=node_name,
                schema_name=schema.__name__,
                cause=exc,
                attempts=attempts,
            )
            if not _retry_allowed(error):
                raise error from exc
            _ensure_retry_allowed()


async def ainvoke_structured(
    *,
    llm: ChatOpenAI,
    schema: type[SchemaT],
    prompt: Any,
    inputs: Mapping[str, Any],
    node_name: str,
    config: Any = None,
) -> SchemaT:
    """异步执行结构化调用：结果不可用时重试一次，随后抛出 StructuredOutputError。"""
    attempts = 0
    while True:
        attempts += 1
        try:
            runnable = _build_structured_runnable(
                llm=llm,
                schema=schema,
                prompt=prompt,
                config=config,
            )
            return _validate_result(await runnable.ainvoke(dict(inputs)), schema)
        except JobExecutionRevoked:
            raise
        except Exception as exc:
            error = StructuredOutputError(
                node_name=node_name,
                schema_name=schema.__name__,
                cause=exc,
                attempts=attempts,
            )
            if not _retry_allowed(error):
                raise error from exc
            _ensure_retry_allowed()
