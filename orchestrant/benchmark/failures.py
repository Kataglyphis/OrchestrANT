"""Why a lane's request raised: a prompt its context could not hold is the model's limit, anything else is errored."""

from __future__ import annotations

import re
from dataclasses import dataclass

from orchestrant.benchmark.client import http_error_detail


_OVERFLOW_BODY = re.compile(
    r"context|too (?:long|many tokens)"
    r"|(?:context|prompt|input)[^.]{0,40}exceed"
    r"|max(?:imum)?_? ?(?:tokens|length)"
    r"|(?:prompt|input|request) (?:is )?too",
    re.IGNORECASE,
)
# A throttled or unpaid lane says "exceeded" too; calling that an overflow publishes a false cause for rows nothing measured.
_NOT_OVERFLOW_BODY = re.compile(r"rate.?limit|quota|billing", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Failure:
    """One raised request: `overflow` leaves the denominator named as such, `errored` as the lane's fault."""

    state: str
    # The excerpt an overflow row shows; None when errored.
    reason: str | None = None
    http_status: int | None = None
    response_body: str | None = None


def classify_failure(exc: BaseException) -> Failure:
    """The state of a request that raised; an HTTP error's body is read here, once, and kept on the Failure.

    Only a 4xx whose body says the prompt did not fit is an overflow: the same ceiling that reads CUT when a
    server streams it. A 429, a 5xx, or a body naming a rate limit, quota or billing is errored.
    """
    detail = http_error_detail(exc)
    if detail is None:
        return Failure("errored")
    status, body = detail
    if (
        400 <= status < 500
        and status != 429
        and not _NOT_OVERFLOW_BODY.search(body)
        and _OVERFLOW_BODY.search(body)
    ):
        return Failure("overflow", f"HTTP {status}: {body[:160]}", status, body)
    return Failure("errored", None, status, body)
