"""使用真实 SDK 与项目异常验证终态分类；不创建客户端或发出网络请求。"""

import json

import httpx
import pytest
from langchain_core.exceptions import OutputParserException
from langgraph.errors import NodeError
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    LengthFinishReasonError,
    PermissionDeniedError,
    RateLimitError,
)
from openai.types.chat import ChatCompletion
from pydantic import TypeAdapter, ValidationError

from Agent.deep_agent.tool_call_protocol import ToolCallProtocolError
from Agent.deep_agent_tools.adapters.normalization import RunnerContractError
from Agent.llm_structured_output import NoToolCallError, StructuredOutputError
from app.agent.checkpoint_recovery import CheckpointRecoveryUnavailable
from app.agent.worker.event_adapter import classify_graph_failure


def _status_error(error_type, status_code):
    request = httpx.Request("POST", "https://provider.invalid/v1/chat/completions")
    return error_type(
        "private provider response",
        response=httpx.Response(status_code, request=request),
        body=None,
    )


def _structured_error(cause):
    return StructuredOutputError(
        node_name="report", schema_name="ReportDraft", cause=cause
    )


@pytest.mark.parametrize(
    ("error_type", "status_code", "reason_code"),
    [
        (RateLimitError, 429, "rate_limited"),
        (AuthenticationError, 401, "auth_failed"),
        (PermissionDeniedError, 403, "auth_failed"),
        (InternalServerError, 503, "server_error"),
    ],
)
@pytest.mark.parametrize("wrapper", ["direct", "cause", "context", "node"])
def test_real_provider_status_errors_survive_wrapping(
    error_type, status_code, reason_code, wrapper
):
    error = _status_error(error_type, status_code)
    if wrapper == "cause":
        outer = RuntimeError("node failed")
        outer.__cause__ = error
    elif wrapper == "context":
        outer = RuntimeError("node failed")
        outer.__context__ = error
    elif wrapper == "node":
        outer = NodeError(node="report", error=error)
    else:
        outer = error

    diagnostic = classify_graph_failure(outer)

    assert (diagnostic.error_category, diagnostic.reason_code) == (
        "provider_error", reason_code
    )
    if isinstance(outer, BaseException):
        assert diagnostic.exc_info == (type(outer), outer, outer.__traceback__)
    else:
        assert diagnostic.exc_info is None


@pytest.mark.parametrize(
    ("cause", "category", "reason_code"),
    [
        (
            APITimeoutError(httpx.Request("POST", "https://provider.invalid")),
            "provider_error",
            "timeout",
        ),
        (
            APIConnectionError(
                request=httpx.Request("POST", "https://provider.invalid")
            ),
            "provider_error",
            "connection_unavailable",
        ),
        (_status_error(RateLimitError, 429), "provider_error", "rate_limited"),
        (_status_error(BadRequestError, 400), "provider_error", "request_rejected"),
        (_status_error(InternalServerError, 503), "provider_error", "server_error"),
        (NoToolCallError(), "protocol_error", "tool_call_invalid"),
        (json.JSONDecodeError("invalid", "{", 1), "protocol_error", "json_invalid"),
        (OutputParserException("invalid"), "protocol_error", "output_parser_error"),
        (
            LengthFinishReasonError(
                completion=ChatCompletion(
                    id="completion", choices=[], created=0,
                    model="provider-model", object="chat.completion",
                )
            ),
            "protocol_error",
            "truncated",
        ),
    ],
)
def test_structured_output_uses_allowlisted_cause_without_exception_chain(
    cause, category, reason_code
):
    error = _structured_error(cause)
    assert error.__cause__ is None

    diagnostic = classify_graph_failure(error)

    assert (diagnostic.error_category, diagnostic.reason_code) == (category, reason_code)
    assert diagnostic.exc_info[1] is error


def test_real_validation_error_is_a_structured_protocol_failure():
    with pytest.raises(ValidationError) as caught:
        TypeAdapter(int).validate_python("private non-numeric input")

    diagnostic = classify_graph_failure(_structured_error(caught.value))

    assert (diagnostic.error_category, diagnostic.reason_code) == (
        "protocol_error", "schema_invalid"
    )


def test_nested_structured_failure_preserves_original_outer_traceback():
    provider_error = _status_error(AuthenticationError, 401)
    structured_error = _structured_error(provider_error)
    assert structured_error.safe_cause_code == "unknown"
    try:
        try:
            raise provider_error
        except AuthenticationError as cause:
            raise structured_error from cause
    except StructuredOutputError as cause:
        try:
            raise RuntimeError("node failed") from cause
        except RuntimeError as outer:
            original_traceback = outer.__traceback__
            diagnostic = classify_graph_failure(outer)
            assert diagnostic.exc_info[0] is RuntimeError
            assert diagnostic.exc_info[1] is outer
            assert diagnostic.exc_info[2] is original_traceback

    assert (diagnostic.error_category, diagnostic.reason_code) == (
        "provider_error", "auth_failed"
    )


@pytest.mark.parametrize(
    ("cause", "category", "reason_code"),
    [
        (ToolCallProtocolError(), "protocol_error", "protocol_error"),
        (CheckpointRecoveryUnavailable(), "checkpoint_error", "checkpoint_unavailable"),
        (RunnerContractError(), "runtime_contract_error", "invalid_runtime_context"),
    ],
)
def test_project_exception_classification_survives_generic_wrapper(
    cause, category, reason_code
):
    outer = RuntimeError("node failed")
    outer.__cause__ = cause

    diagnostic = classify_graph_failure(outer)

    assert (diagnostic.error_category, diagnostic.reason_code) == (category, reason_code)


def test_explicit_cause_takes_precedence_over_implicit_context():
    outer = RuntimeError()
    outer.__cause__ = ToolCallProtocolError()
    outer.__context__ = _status_error(RateLimitError, 429)

    diagnostic = classify_graph_failure(outer)

    assert diagnostic.error_category == "protocol_error"


def test_suppressed_context_is_not_used():
    outer = RuntimeError()
    outer.__context__ = _status_error(RateLimitError, 429)
    outer.__suppress_context__ = True

    diagnostic = classify_graph_failure(outer)

    assert (diagnostic.error_category, diagnostic.reason_code) == (
        "internal_error", "node_error"
    )


def test_recognized_outer_failure_keeps_its_existing_category():
    outer = CheckpointRecoveryUnavailable()
    outer.__cause__ = ConnectionError()

    diagnostic = classify_graph_failure(outer)

    assert diagnostic.error_category == "checkpoint_error"


def test_exception_chain_cycle_terminates():
    outer = RuntimeError()
    inner = ValueError()
    outer.__cause__ = inner
    inner.__cause__ = outer

    diagnostic = classify_graph_failure(outer)

    assert diagnostic.reason_code == "node_error"


@pytest.mark.parametrize(("wrapper_count", "reason_code"), [(31, "rate_limited"), (32, "node_error")])
def test_exception_chain_has_a_fixed_traversal_budget(wrapper_count, reason_code):
    outer = _status_error(RateLimitError, 429)
    for _ in range(wrapper_count):
        wrapper = RuntimeError()
        wrapper.__cause__ = outer
        outer = wrapper

    diagnostic = classify_graph_failure(outer)

    assert diagnostic.reason_code == reason_code


def test_exception_text_and_unrecognized_reason_codes_do_not_classify():
    error = _structured_error(RuntimeError())
    error.safe_cause_code = "private timeout auth rate_limit protocol checkpoint"
    outer = RuntimeError("RateLimitError AuthenticationError ToolCallProtocolError")
    outer.__cause__ = error

    diagnostic = classify_graph_failure(outer)

    assert (diagnostic.error_category, diagnostic.reason_code) == (
        "internal_error", "node_error"
    )


def test_non_exception_error_payload_is_not_treated_as_a_cause():
    context = NodeError(node="report", error="RateLimitError private payload")

    diagnostic = classify_graph_failure(context)

    assert diagnostic.reason_code == "node_error"
    assert diagnostic.exc_info is None
