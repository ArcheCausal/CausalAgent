"""直接验证内部签名、身份和时效边界，不调用替代服务。"""

import time

import pytest

from sandbox_controller.protocol import SandboxProtocolError, request_headers, verify_request


IDENTITY = {"job_id": "550e8400-e29b-41d4-a716-446655440000", "worker_id": "worker:1", "attempt_count": 2, "lease_epoch": 3}
TOKEN = "controller-protocol-test-token-000000000000"
PATH = "/v1/sessions/" + "a" * 64 + "/execute"


def signed_headers():
    return {key.lower(): value for key, value in request_headers(TOKEN, "POST", PATH, IDENTITY, b"{}").items()}


def test_signature_binds_execution_and_request_body():
    assert verify_request(TOKEN, "POST", PATH, signed_headers(), b"{}") == IDENTITY


@pytest.mark.parametrize("method,path,body", [("DELETE", PATH, b"{}"), ("POST", PATH + "x", b"{}"), ("POST", PATH, b"{ }")])
def test_changed_operation_is_not_authorized(method, path, body):
    with pytest.raises(SandboxProtocolError):
        verify_request(TOKEN, method, path, signed_headers(), body)


def test_expired_and_wrong_key_signatures_are_rejected():
    headers = signed_headers()
    with pytest.raises(SandboxProtocolError):
        verify_request(TOKEN, "POST", PATH, headers, b"{}", now=time.time() + 61)
    with pytest.raises(SandboxProtocolError):
        verify_request(TOKEN + "other", "POST", PATH, headers, b"{}")


@pytest.mark.parametrize("updates", [{"attempt_count": True}, {"lease_epoch": -1}, {"worker_id": ""}, {"job_id": "not-a-job"}, {"unknown": "field"}])
def test_malformed_identity_cannot_be_signed(updates):
    with pytest.raises(SandboxProtocolError):
        request_headers(TOKEN, "POST", PATH, {**IDENTITY, **updates}, b"{}")
