"""The lanes' one rule for a raised request: an overflow is the model's limit, anything else is errored."""

from __future__ import annotations

import email.message
import io
import urllib.error

import pytest

from orchestrant.benchmark.failures import classify_failure


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://x", code, "x", email.message.Message(), io.BytesIO(body)
    )


def test_a_4xx_saying_the_prompt_did_not_fit_is_an_overflow_with_its_body() -> None:
    body = b'{"error":{"message":"maximum context length is 4096 tokens"}}'
    failure = classify_failure(_http_error(400, body))
    assert failure.state == "overflow"
    assert failure.reason == f"HTTP 400: {body.decode()}"
    assert (failure.http_status, failure.response_body) == (400, body.decode())


@pytest.mark.parametrize(
    ("code", "body"),
    [
        (429, b"context too long"),
        (400, b"rate limit exceeded: too many tokens"),
        (402, b"quota: maximum tokens for this month"),
        (503, b"context too long"),
        (404, b"unknown model"),
    ],
    ids=["429", "rate-limit", "quota", "5xx", "unrelated-4xx"],
)
def test_a_throttled_failing_or_unrelated_reply_is_errored_and_keeps_its_body(
    code: int, body: bytes
) -> None:
    failure = classify_failure(_http_error(code, body))
    assert (failure.state, failure.reason) == ("errored", None)
    assert (failure.http_status, failure.response_body) == (code, body.decode())


def test_a_failure_without_http_is_errored_with_nothing_read() -> None:
    failure = classify_failure(ConnectionResetError("peer went away"))
    assert failure.state == "errored"
    assert failure.http_status is None and failure.response_body is None
