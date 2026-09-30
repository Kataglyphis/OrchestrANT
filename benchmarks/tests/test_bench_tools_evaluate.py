"""bench_tools.evaluate() on real case data, with `call` and `call_multi` stubbed."""

import io
import json
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_tools as bt  # noqa: E402


def _case(name, category="lookup"):
    return {
        "name": name,
        "category": category,
        "prompt": f"do {name}",
        "expect": {"name": "read_file", "args": {"path": f"{name}.txt"}},
        "variants": [],
    }


def _multi(name):
    return {
        "name": name,
        "category": "multi",
        "kind": "use_result",
        "must_contain": ["9.4.1"],
        "history": [
            {"role": "user", "content": "version?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "V"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "9.4.1"},
        ],
    }


def _call_for(case_name, passing=True):
    """A reply that grades PASS (or FAIL) for the stub case of that name."""
    path = f"{case_name}.txt" if passing else "wrong.txt"
    return {
        "content": None,
        "tool_calls": [
            {
                "id": "x",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": json.dumps({"path": path}),
                },
            }
        ],
    }


@pytest.fixture
def suite(monkeypatch):
    """Three single-turn cases and one multi-turn case, all stubbed."""
    cases = [_case("a"), _case("b"), _case("c")]
    monkeypatch.setattr(bt, "CASES", cases)
    monkeypatch.setattr(bt, "MULTI_CASES", [_multi("m")])
    return cases


def _stub_single(monkeypatch, fn):
    def call(base_url, model, prompt, system=None, tools=None, entry=None):
        return fn(prompt), "stop", 1.0

    monkeypatch.setattr(bt, "call", call)


def _stub_multi(monkeypatch, fn):
    def call_multi(base_url, model, history, system=None, tools=None, entry=None):
        return fn(history), "stop", 1.0

    monkeypatch.setattr(bt, "call_multi", call_multi)


class TestAMultiTurnTransportFailureLeavesTheDenominator:
    """A multi-turn transport failure leaves the denominator and never crashes evaluate()."""

    def test_a_raising_multi_turn_call_is_excluded_not_scored_wrong(
        self, monkeypatch, suite
    ):
        _stub_single(monkeypatch, lambda p: _call_for(p.split()[-1]))

        def boom(history):
            raise ConnectionResetError("peer went away")

        _stub_multi(monkeypatch, boom)
        row = bt.evaluate("http://x", "m", "lbl", warmup=False)
        assert row["total"] == len(bt.CASES)
        assert row["errored"] == len(bt.MULTI_CASES)
        assert row["passed"] == len(bt.CASES)
        assert row["total_wall_s"] > 0

    def test_the_errored_row_is_flagged_and_carries_no_wall(self, monkeypatch, suite):
        _stub_single(monkeypatch, lambda p: _call_for(p.split()[-1]))
        _stub_multi(monkeypatch, lambda h: (_ for _ in ()).throw(OSError("down")))
        row = bt.evaluate("http://x", "m", "lbl", warmup=False)
        errored = [r for r in row["results"] if r.get("errored")]
        assert len(errored) == 1
        assert errored[0]["wall_s"] is None and errored[0]["passed"] is False

    def test_a_single_turn_transport_failure_is_excluded_too(self, monkeypatch, suite):
        def call(prompt):
            if prompt.endswith("b"):
                raise ConnectionResetError("peer went away")
            return _call_for(prompt.split()[-1])

        _stub_single(monkeypatch, call)
        _stub_multi(monkeypatch, lambda h: {"content": "9.4.1", "tool_calls": []})
        row = bt.evaluate("http://x", "m", "lbl", warmup=False)
        assert row["errored"] == 1
        assert row["total"] == len(bt.CASES) - 1 + len(bt.MULTI_CASES)


# A stand-in: the NPU lane's real refusal body was never kept.
REFUSAL = b'{"error":{"message":"prompt too long","type":"invalid_request_error"}}'


def refused(body=REFUSAL, code=400):
    return urllib.error.HTTPError(
        "http://x/v1/chat/completions", code, "Bad Request", {}, io.BytesIO(body)
    )


def _raise(exc):
    def fail(_):
        raise exc

    return fail


def _errored_row(monkeypatch, multi_failure, single=None):
    """The one errored row of a run whose multi-turn case raises."""
    _stub_single(monkeypatch, single or (lambda p: _call_for(p.split()[-1])))
    _stub_multi(monkeypatch, _raise(multi_failure))
    report = bt.evaluate("http://x", "m", "lbl", warmup=False)
    return report, next(r for r in report["results"] if r.get("errored"))


def _error_line(capsys, name):
    out = capsys.readouterr().out
    return next(ln for ln in out.splitlines() if ln.startswith(f"    {name} "))


class TestAnErroredCaseSaysWhy:
    """An HTTP-errored row adds `http_status` and `response_body`, and changes nothing else."""

    def test_the_row_keeps_what_it_had_and_adds_status_and_body(
        self, monkeypatch, suite
    ):
        _, row = _errored_row(monkeypatch, refused())
        assert row == {
            "case": "m",
            "attempt": 0,
            "variant": 0,
            "category": "multi",
            "passed": False,
            "errored": True,
            "recovered": False,
            "detail": "request failed: HTTP Error 400: Bad Request",
            "wall_s": None,
            "finish_reason": None,
            "http_status": 400,
            "response_body": REFUSAL.decode(),
        }

    def test_the_printed_line_says_it_too(self, monkeypatch, suite, capsys):
        _errored_row(monkeypatch, refused())
        assert _error_line(capsys, "m") == (
            f"    {'m':22s} ERROR HTTPError 400: {REFUSAL.decode()}"
        )

    def test_a_single_turn_case_says_it_too(self, monkeypatch, suite, capsys):
        def call(prompt):
            if prompt.endswith("b"):
                raise refused()
            return _call_for(prompt.split()[-1])

        _stub_single(monkeypatch, call)
        _stub_multi(monkeypatch, lambda h: {"content": "9.4.1", "tool_calls": []})
        report = bt.evaluate("http://x", "m", "lbl", warmup=False)
        row = next(r for r in report["results"] if r.get("errored"))
        assert (row["case"], row["http_status"]) == ("b", 400)
        assert row["response_body"] == REFUSAL.decode()
        assert _error_line(capsys, "b").endswith(f"400: {REFUSAL.decode()}")

    def test_the_body_is_cut_at_500_characters_and_printed_on_one_line(
        self, monkeypatch, suite, capsys
    ):
        page = b"<html>\n  <body>" + b"x" * 900 + b"</body>\n</html>\n"
        _, row = _errored_row(monkeypatch, refused(page, code=502))
        assert row["response_body"] == page.decode()[:500]
        cut = " ".join(page.decode()[:500].split())
        assert _error_line(capsys, "m") == f"    {'m':22s} ERROR HTTPError 502: {cut}"

    def test_any_other_failure_records_what_it_always_did(
        self, monkeypatch, suite, capsys
    ):
        _, row = _errored_row(monkeypatch, ConnectionResetError("peer went away"))
        assert "http_status" not in row and "response_body" not in row
        assert row["detail"] == "request failed: peer went away"
        assert _error_line(capsys, "m") == f"    {'m':22s} ERROR ConnectionResetError"

    def test_the_score_is_what_any_other_failure_gives(self, monkeypatch, suite):
        # Same status line, so the only difference left is the two new fields.
        http, http_row = _errored_row(monkeypatch, refused())
        other, other_row = _errored_row(monkeypatch, OSError(str(refused())))
        assert {k: v for k, v in http.items() if k != "results"} == {
            k: v for k, v in other.items() if k != "results"
        }
        del http_row["http_status"], http_row["response_body"]
        assert http_row == other_row


class TestTheDeterminismBookkeeping:
    """Determinism votes on the OUTPUT hash, effective_k is a COUNT, errored attempts abstain."""

    def _pair(self, monkeypatch, replies, multi_reply=None):
        seen = {"n": 0}

        def call(prompt):
            seen["n"] += 1
            return replies(prompt, seen["n"])

        _stub_single(monkeypatch, call)
        _stub_multi(
            monkeypatch, lambda h: multi_reply or {"content": "9.4.1", "tool_calls": []}
        )

    def test_byte_identical_replies_are_deterministic(self, monkeypatch, suite):
        self._pair(monkeypatch, lambda p, n: _call_for(p.split()[-1]))
        row = bt.evaluate("http://x", "m", "lbl", repeats=2, warmup=False)
        assert row["deterministic"] is True
        assert row["effective_n"] == len(bt.CASES) + len(bt.MULTI_CASES)
        assert row["effective_k"] == row["effective_n"]

    def test_differing_text_with_the_same_verdict_is_not_deterministic(
        self, monkeypatch, suite
    ):
        # A sampling lane that passes every draw agrees on the verdict too.
        def replies(prompt, n):
            reply = _call_for(prompt.split()[-1])
            reply["content"] = f"draw {n}"
            return reply

        self._pair(monkeypatch, replies)
        row = bt.evaluate("http://x", "m", "lbl", repeats=2, warmup=False)
        assert row["deterministic"] is False
        assert row["repeats_agreed"] is True
        # Verdicts fixed per case: the case is the unit, however the text varied.
        assert row["effective_n"] == len(bt.CASES) + len(bt.MULTI_CASES)
        assert row["effective_n"] < row["total"]

    def test_effective_k_is_a_count_when_attempts_are_uneven(self, monkeypatch, suite):
        # Two errored draws on the failing case: a rounded ratio would say 3, not 2.
        state = {"c": 0}

        def call(prompt):
            name = prompt.split()[-1]
            if name == "c":
                state["c"] += 1
                if state["c"] <= 2:
                    raise ConnectionResetError("flaky")
                return _call_for(name, passing=False)
            return _call_for(name)

        _stub_single(monkeypatch, call)
        _stub_multi(monkeypatch, lambda h: {"content": "9.4.1", "tool_calls": []})
        row = bt.evaluate("http://x", "m", "lbl", repeats=3, warmup=False)
        assert row["deterministic"] is True
        assert row["errored"] == 2
        assert row["effective_n"] == 4  # a, b, c, m
        assert row["effective_k"] == 3  # a, b, m -- never 4
        assert row["effective_k"] != round(
            row["passed"] * row["effective_n"] / row["total"]
        )

    def test_a_single_run_is_never_called_deterministic(self, monkeypatch, suite):
        self._pair(monkeypatch, lambda p, n: _call_for(p.split()[-1]))
        row = bt.evaluate("http://x", "m", "lbl", repeats=1, warmup=False)
        assert row["deterministic"] is False
        assert (row["effective_n"], row["effective_k"]) == (row["total"], row["passed"])


class TestTheReportSaysWhatChangedTheScore:
    """The report config carries the flags that move the denominator."""

    def test_the_flags_that_change_the_score_are_on_the_row(self, monkeypatch, suite):
        _stub_single(monkeypatch, lambda p: _call_for(p.split()[-1]))
        _stub_multi(monkeypatch, lambda h: {"content": "9.4.1", "tool_calls": []})
        row = bt.evaluate(
            "http://x",
            "m",
            "lbl",
            warmup=False,
            accept_text_json=True,
            prompt_variants=True,
            context_tokens=0,
        )
        assert row["accept_text_json"] is True
        assert row["prompt_variants"] is True
        assert row["context_tokens"] == 0
        assert row["tool_set"] in bt.TOOL_SETS

    def test_the_per_case_key_is_case_and_variant(self, monkeypatch, suite):
        # A paraphrase makes this a --prompt-variants report.
        suite[0]["variants"] = ["another way to say a"]
        _stub_single(monkeypatch, lambda p: _call_for("a" if "a" in p else "z"))
        _stub_multi(monkeypatch, lambda h: {"content": "9.4.1", "tool_calls": []})
        row = bt.evaluate("http://x", "m", "lbl", warmup=False, prompt_variants=True)
        variants = {(r["case"], r["variant"]) for r in row["results"]}
        assert ("a", 0) in variants and ("a", 1) in variants


class TestNoRegressionAgainstACleanBaseline:
    """An errored attempt is neither a regression nor an improvement to bench_compare."""

    def _report(self, row):
        return {
            "benchmark": "bench_tools",
            "provenance": {},
            "config": {},
            "reports": [row],
        }

    def test_a_transport_error_is_not_a_regression(self, monkeypatch, suite):
        from bench_compare import compare, normalise

        _stub_single(monkeypatch, lambda p: _call_for(p.split()[-1]))
        _stub_multi(monkeypatch, lambda h: {"content": "9.4.1", "tool_calls": []})
        clean = bt.evaluate("http://x", "m", "lbl", warmup=False)

        _stub_multi(monkeypatch, lambda h: (_ for _ in ()).throw(OSError("down")))
        errored = bt.evaluate("http://x", "m", "lbl", warmup=False)

        findings, regressed = compare(
            normalise(self._report(clean)), normalise(self._report(errored))
        )
        assert not regressed, findings

    def test_a_case_that_stopped_passing_IS_a_regression(self, monkeypatch, suite):
        # The other direction, so "not regressed" above is not vacuous.
        from bench_compare import compare, normalise

        _stub_single(monkeypatch, lambda p: _call_for(p.split()[-1]))
        _stub_multi(monkeypatch, lambda h: {"content": "9.4.1", "tool_calls": []})
        clean = bt.evaluate("http://x", "m", "lbl", warmup=False)

        _stub_single(
            monkeypatch, lambda p: _call_for(p.split()[-1], passing="c" not in p)
        )
        broken = bt.evaluate("http://x", "m", "lbl", warmup=False)

        findings, _ = compare(
            normalise(self._report(clean)), normalise(self._report(broken))
        )
        assert any("c" in f for f in findings), findings


class TestRepeatsAreNeverIdenticalFollowUps:
    """A repeat never directly follows its own previous attempt (see client.spacer)."""

    def test_a_spacer_separates_the_repeats_of_one_case(
        self, monkeypatch, suite, spacers
    ):
        sent = []

        def call(prompt):
            sent.append(prompt)
            return _call_for(prompt.split()[-1])

        _stub_single(monkeypatch, call)
        _stub_multi(monkeypatch, lambda h: {"content": "9.4.1", "tool_calls": []})
        bt.evaluate("http://x", "m", "lbl", repeats=3, warmup=False)
        # 2 extra attempts for each of 3 single-turn cases and 1 multi-turn one
        assert len(spacers) == 2 * (len(bt.CASES) + len(bt.MULTI_CASES))

    def test_one_draw_needs_no_spacer(self, monkeypatch, suite, spacers):
        _stub_single(monkeypatch, lambda p: _call_for(p.split()[-1]))
        _stub_multi(monkeypatch, lambda h: {"content": "9.4.1", "tool_calls": []})
        bt.evaluate("http://x", "m", "lbl", repeats=1, warmup=False)
        assert spacers == []
