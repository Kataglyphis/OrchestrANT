"""The wiring between the tools and the shared plumbing.

Two guarantees live only at the seams, so neither module's own tests can see
them:

1. bench_coding.ask() and bench_tools.call()/call_multi() go through
   bench_cli.post_json. Before that, six sites built their own Request with
   nothing but Content-Type: a hosted endpoint needing an Authorization header
   could not be measured at all, and a per-lane knob like Ollama's num_ctx had
   to be typed into every tool separately. The report has to record what the
   entry added -- and the header NAMES only, never a key value, because reports
   are committed.

2. A case the CONTROL endpoint also fails is evidence about the CASE, not about
   the candidates. Scoring a candidate on a contradictory prompt charges the
   model for a broken task.

Nothing here opens a socket: urlopen is monkeypatched at the urllib level, so
the real post_json body -- merge order, auth, deadline -- is what runs.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrant.benchmark import client as bench_cli
import bench_coding as bc  # noqa: E402
import bench_tools as bt  # noqa: E402
from bench_compare import mark_suspect_cases  # noqa: E402


class _Captured:
    """One recorded request, with the body parsed back out."""

    def __init__(self, req):
        self.url = req.full_url
        self.headers = dict(req.headers)
        self.body = json.loads(req.data.decode())


class _Resp:
    """Stands in for urlopen's return. Iterable (SSE) and readable (JSON)."""

    def __init__(self, lines=(), payload=None):
        self._lines = [ln if isinstance(ln, bytes) else ln.encode() for ln in lines]
        self._payload = payload
        self.status = 200
        self.headers = {}

    def __iter__(self):
        return iter(self._lines)

    def read(self, *a):
        return json.dumps(self._payload).encode()

    def close(self):
        pass


@pytest.fixture
def captured(monkeypatch):
    """Capture every request; hand back an SSE stream and a JSON body."""
    seen = []
    sse = [
        b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}]}\n',
        b"data: [DONE]\n",
    ]
    reply = {
        "choices": [
            {"message": {"content": "hi", "tool_calls": []}, "finish_reason": "stop"}
        ]
    }

    def fake_urlopen(req, timeout=None):
        seen.append(_Captured(req))
        return _Resp(sse, reply)

    monkeypatch.setattr(bench_cli.urllib.request, "urlopen", fake_urlopen)
    return seen


ENTRY = {
    "api_key_env": "INTEGRATION_TEST_KEY",
    "headers": {"X-Lane": "npu"},
    "request_extra": {"num_ctx": 8192, "max_tokens": 99},
}


class TestEveryRequestGoesThroughPostJson:
    def test_coding_ask_sends_the_entry_auth_headers_and_extras(
        self, captured, monkeypatch
    ):
        monkeypatch.setenv("INTEGRATION_TEST_KEY", "s3cret")
        bc.ask("http://lane", "m", "prompt", 4096, entry=ENTRY)
        req = captured[0]
        assert req.headers["Authorization"] == "Bearer s3cret"
        assert req.headers["X-lane"] == "npu"
        assert req.body["num_ctx"] == 8192
        # The measured budget is the one the caller asked for: a registry
        # default must never be able to change what the benchmark measures.
        assert req.body["max_tokens"] == 4096

    def test_tools_call_sends_the_entry_auth_headers_and_extras(
        self, captured, monkeypatch
    ):
        monkeypatch.setenv("INTEGRATION_TEST_KEY", "s3cret")
        bt.call("http://lane", "m", "prompt", entry=ENTRY)
        req = captured[0]
        assert req.headers["Authorization"] == "Bearer s3cret"
        assert req.headers["X-lane"] == "npu"
        assert req.body["num_ctx"] == 8192
        assert req.body["max_tokens"] == 600

    def test_tools_call_multi_sends_the_entry_auth(self, captured, monkeypatch):
        monkeypatch.setenv("INTEGRATION_TEST_KEY", "s3cret")
        bt.call_multi(
            "http://lane", "m", [{"role": "user", "content": "x"}], entry=ENTRY
        )
        assert captured[0].headers["Authorization"] == "Bearer s3cret"

    def test_no_entry_still_works_and_sends_no_auth(self, captured):
        bc.ask("http://lane", "m", "prompt", 16)
        bt.call("http://lane", "m", "prompt")
        assert all("Authorization" not in r.headers for r in captured)

    @pytest.mark.parametrize(
        "run",
        [
            lambda: bc.ask("http://lane", "m", "p", 16, entry=ENTRY),
            lambda: bt.call("http://lane", "m", "p", entry=ENTRY),
        ],
    )
    def test_an_unset_api_key_variable_is_loud_before_the_request(
        self, captured, monkeypatch, run
    ):
        monkeypatch.delenv("INTEGRATION_TEST_KEY", raising=False)
        with pytest.raises(SystemExit) as e:
            run()
        # Names the variable, never a value, and nothing was sent.
        assert "INTEGRATION_TEST_KEY" in str(e.value)
        assert captured == []

    def test_the_coding_deadline_still_abandons_the_stream(self, monkeypatch):
        """ask() no longer owns the deadline; it must still honour one."""

        class Endless:
            status = 200
            headers = {}

            def __iter__(self):
                while True:
                    yield b'data: {"choices":[{"delta":{"content":"x"}}]}\n'

            def close(self):
                pass

        monkeypatch.setattr(
            bench_cli.urllib.request, "urlopen", lambda req, timeout=None: Endless()
        )
        out = bc.ask("http://lane", "m", "p", 4096, deadline=0.05)
        assert out[8] is True, "the deadline must stop an endless stream"


class TestReportsRecordTheEntryNeverTheKey:
    def test_entry_config_carries_names_and_extras_only(self, monkeypatch):
        monkeypatch.setenv("INTEGRATION_TEST_KEY", "s3cret")
        cfg = bench_cli.entry_config(ENTRY)
        assert cfg["request_extra"] == {"num_ctx": 8192, "max_tokens": 99}
        assert cfg["headers"] == ["X-Lane"]
        assert cfg["api_key_env"] == "INTEGRATION_TEST_KEY"
        assert "s3cret" not in json.dumps(cfg)


# ── suspect cases ────────────────────────────────────────────────────────────


def _row(label, backend, outcomes, deterministic=False):
    """One report row: outcomes maps task name -> pass/fail (or a flag dict)."""
    results = []
    for name, ok in outcomes.items():
        row = {"task": name, "attempt": 0, "output_sha256": name}
        row.update(ok if isinstance(ok, dict) else {"passed": ok})
        results.append(row)
    measured = [
        r
        for r in results
        if not (r.get("errored") or r.get("truncated") or r.get("skipped"))
    ]
    passed = sum(1 for r in measured if r.get("passed"))
    return {
        "label": label,
        "backend": backend,
        "passed": passed,
        "total": len(measured),
        "wrong": len(measured) - passed,
        "deterministic": deterministic,
        "effective_n": len(measured),
        "effective_k": passed,
        "results": results,
    }


class TestSuspectCasesLeaveTheRanking:
    def test_a_case_the_control_fails_is_excluded_from_every_candidate(self):
        reports = [
            _row("control", "control", {"a": True, "broken": False}),
            _row("lane-a", "geniex", {"a": True, "broken": False}),
            _row("lane-b", "geniex", {"a": False, "broken": False}),
        ]
        suspect = mark_suspect_cases(reports)
        assert suspect == ["broken"]
        control, a, b = reports
        # The control keeps its own score: it is the calibration, not a rival.
        assert (control["passed"], control["total"]) == (1, 2)
        # 1/2 was the model being charged for a task nobody can pass.
        assert (a["passed"], a["total"], a["wrong"]) == (1, 1, 0)
        assert (b["passed"], b["total"], b["wrong"]) == (0, 1, 1)
        assert (a["effective_n"], a["effective_k"]) == (1, 1)
        assert a["suspect_excluded"] == 1 and control["suspect_excluded"] == 0

    def test_the_suspect_cases_are_listed_on_every_row(self):
        reports = [
            _row("control", "control", {"broken": False}),
            _row("lane", "geniex", {"broken": True}),
        ]
        mark_suspect_cases(reports)
        assert all(r["suspect_cases"] == ["broken"] for r in reports)
        assert all(item.get("suspect") for r in reports for item in r["results"])

    def test_a_case_the_control_only_errored_on_is_not_suspect(self):
        """The control never saw it, so it is evidence about nothing."""
        reports = [
            _row("control", "control", {"x": {"passed": False, "errored": True}}),
            _row("lane", "geniex", {"x": False}),
        ]
        assert mark_suspect_cases(reports) == []
        assert reports[1]["total"] == 1

    def test_an_overflow_or_skipped_row_is_not_measured(self):
        """A prompt that never fit, and a task whose grader is not installed,
        were never attempted: counting them charges the model for neither."""
        from bench_compare import measured

        assert not measured({"task": "x", "passed": False, "overflow": True})
        assert not measured({"task": "x", "passed": False, "skipped": "no cmake"})
        assert measured({"task": "x", "passed": False})
        reports = [
            _row("control", "control", {"broken": False}),
            _row(
                "lane",
                "geniex",
                {
                    "broken": False,
                    "ok": True,
                    "big": {"passed": False, "overflow": True},
                },
            ),
        ]
        mark_suspect_cases(reports)
        # 1/1, not 1/2: the overflowed task is out of the denominator.
        assert (reports[1]["passed"], reports[1]["total"]) == (1, 1)

    def test_without_a_control_nothing_is_excluded(self):
        reports = [
            _row("lane-a", "geniex", {"a": False}),
            _row("lane-b", "ollama", {"a": False}),
        ]
        assert mark_suspect_cases(reports) is None
        assert all(r["total"] == 1 for r in reports)
        assert all("suspect_cases" not in r for r in reports)

    def test_a_control_is_recognised_by_its_label_too(self):
        """backends.json is not the only way in: --compare files carry labels."""
        reports = [
            _row("control (gpt-x)", None, {"broken": False}),
            _row("lane", "geniex", {"broken": False, "ok": True}),
        ]
        assert mark_suspect_cases(reports) == ["broken"]
        assert (reports[1]["passed"], reports[1]["total"]) == (1, 1)

    def test_a_deterministic_row_keeps_counting_tasks_not_attempts(self):
        row = _row("lane", "geniex", {}, deterministic=True)
        row["results"] = [
            {"task": t, "attempt": i, "passed": p, "output_sha256": t}
            for t, p in (("a", True), ("broken", False))
            for i in range(2)
        ]
        reports = [_row("control", "control", {"broken": False, "a": True}), row]
        mark_suspect_cases(reports)
        # Two attempts on one surviving task is one observation, not two.
        assert (row["effective_n"], row["effective_k"]) == (1, 1)
        assert (row["passed"], row["total"]) == (2, 2)

    def test_bench_tools_rows_key_on_case_and_variant(self):
        reports = [
            {
                "label": "control",
                "backend": "control",
                "passed": 1,
                "total": 2,
                "deterministic": False,
                "effective_n": 2,
                "effective_k": 1,
                "results": [
                    {"case": "ok", "variant": 0, "passed": True},
                    {"case": "broken", "variant": 0, "passed": False},
                ],
            },
            {
                "label": "lane",
                "backend": "geniex",
                "passed": 1,
                "total": 3,
                "deterministic": False,
                "effective_n": 3,
                "effective_k": 1,
                "results": [
                    {"case": "ok", "variant": 0, "passed": True},
                    {"case": "broken", "variant": 0, "passed": False},
                    {"case": "broken", "variant": 1, "passed": False},
                ],
            },
        ]
        assert mark_suspect_cases(reports) == ["broken"]
        lane = reports[1]
        # Both phrasings of the suspect case go, not just the first.
        assert (lane["passed"], lane["total"]) == (1, 1)
        assert lane["suspect_excluded"] == 2


# ── the wiring in main() ─────────────────────────────────────────────────────

CANDIDATES = [
    # Deliberately NOT labelled "control": the registry name carries the
    # calibration, and a label spelling it would hide a dropped `backend`.
    {
        "label": "calibration",
        "explicit_label": True,
        "backend": "control",
        "base_url": "http://control",
        "raw_base_url": None,
        "model": "gpt-x",
        "entry": {
            "api_key_env": "INTEGRATION_TEST_KEY",
            "headers": {"X-Lane": "npu"},
            "request_extra": {"num_ctx": 8192},
        },
    },
    {
        "label": "lane",
        "explicit_label": True,
        "backend": "geniex",
        "base_url": "http://lane",
        "raw_base_url": None,
        "model": "q4",
        "entry": {},
    },
]


def _drive(monkeypatch, tmp_path, module, argv, outcomes):
    """Run a tool's main() with the network and the grader stubbed out."""
    out = tmp_path / "report.json"
    monkeypatch.setattr(
        bench_cli, "candidate_rows", lambda *a, **k: [dict(c) for c in CANDIDATES]
    )
    seen = {}

    def fake_evaluate(base_url, model, label, *a, **kw):
        seen[label] = kw
        return dict(
            outcomes[label], label=label, model=model, backend=kw.get("backend")
        )

    monkeypatch.setattr(module, "evaluate", fake_evaluate)
    monkeypatch.setattr(sys, "argv", list(argv) + ["--output", str(out)])
    module.main()
    with open(out) as f:
        return json.load(f), seen


def _outcome(rows):
    measured = [r for r in rows if not r.get("errored")]
    passed = sum(1 for r in measured if r["passed"])
    return {
        "passed": passed,
        "total": len(measured),
        "wrong": len(measured) - passed,
        "deterministic": False,
        "effective_n": len(measured),
        "effective_k": passed,
        "total_wall_s": 1.0,
        "avg_wall_s": 1.0,
        "results": rows,
    }


class TestMainWiring:
    def test_bench_coding_main_excludes_control_failures_and_records_the_entry(
        self, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.setattr(
            bc,
            "grader_selfcheck",
            lambda tasks: {
                "tasks": 0,
                "checked": 0,
                "passed": True,
                "netns": False,
                "seconds": 0.0,
                "tools": {},
                "skipped": {},
            },
        )
        rows = {
            "calibration": [
                {"task": "ok", "passed": True, "output_sha256": "a"},
                {"task": "broken", "passed": False, "output_sha256": "b"},
            ],
            "lane": [
                {"task": "ok", "passed": True, "output_sha256": "c"},
                {"task": "broken", "passed": False, "output_sha256": "d"},
            ],
        }
        report, seen = _drive(
            monkeypatch,
            tmp_path,
            bc,
            ["bench_coding.py", "--task-set", "classic"],
            {k: _outcome(v) for k, v in rows.items()},
        )
        lane = next(r for r in report["reports"] if r["label"] == "lane")
        assert lane["suspect_cases"] == ["broken"]
        assert (lane["passed"], lane["total"]) == (1, 1)
        # The entry reached evaluate(), and the report says what it added.
        assert seen["calibration"]["entry"]["request_extra"] == {"num_ctx": 8192}
        assert seen["calibration"]["backend"] == "control"
        cfg = report["config"]["backend_entry"]
        assert cfg["calibration"]["api_key_env"] == "INTEGRATION_TEST_KEY"
        assert cfg["calibration"]["headers"] == ["X-Lane"]
        assert "SUSPECT" in capsys.readouterr().out

    def test_bench_tools_main_excludes_control_failures_and_records_the_entry(
        self, monkeypatch, tmp_path, capsys
    ):
        rows = {
            "calibration": [
                {"case": "ok", "variant": 0, "passed": True},
                {"case": "broken", "variant": 0, "passed": False},
            ],
            "lane": [
                {"case": "ok", "variant": 0, "passed": True},
                {"case": "broken", "variant": 0, "passed": False},
            ],
        }
        report, seen = _drive(
            monkeypatch,
            tmp_path,
            bt,
            ["bench_tools.py"],
            {k: _outcome(v) for k, v in rows.items()},
        )
        lane = next(r for r in report["reports"] if r["label"] == "lane")
        assert lane["suspect_cases"] == ["broken"]
        assert (lane["passed"], lane["total"]) == (1, 1)
        assert seen["lane"]["entry"] == {} and seen["lane"]["backend"] == "geniex"
        assert report["config"]["backend_entry"]["calibration"]["headers"] == ["X-Lane"]
        assert "SUSPECT" in capsys.readouterr().out


class TestTheSuiteStaysOffline:
    """The guard in conftest.py. Renaming one seam un-patched three tests and
    the suite went to localhost:11434 and hung instead of failing."""

    def test_a_socket_connect_inside_a_test_is_refused(self):
        import socket

        sock = socket.socket()
        # A timeout so that with the guard removed this fails in a fifth of a
        # second instead of sitting through TCP's two minutes of SYN retries.
        sock.settimeout(0.2)
        with pytest.raises(Exception) as e:
            sock.connect(("127.0.0.1", 11434))
        assert "run offline" in str(e.value)

    def test_an_unpatched_request_fails_loudly_rather_than_hanging(self):
        with pytest.raises(Exception) as e:
            bc.ask("http://127.0.0.1:11434", "m", "p", 16, timeout=1)
        assert "run offline" in str(e.value)


class TestTheRowNamesItsBackend:
    """`backend` on the report row is what `is_control` keys on. The main()
    tests above stub evaluate(), so the returned row needs its own check."""

    def test_bench_coding_evaluate_records_the_backend(self, monkeypatch):
        monkeypatch.setattr(bc, "TASKS", [bc.TASKS[0]])
        monkeypatch.setattr(
            bc,
            "ask",
            lambda *a, **k: (
                "```python\n" + bc.TASKS[0]["reference"] + "\n```",
                0.1,
                0.2,
                3,
                10,
                "",
                "stop",
                5,
                False,
            ),
        )
        row = bc.evaluate("http://x", "m", "lbl", 100, warmup=False, backend="control")
        assert row["backend"] == "control"

    def test_bench_tools_evaluate_records_the_backend(self, monkeypatch):
        monkeypatch.setattr(bt, "CASES", [])
        monkeypatch.setattr(bt, "MULTI_CASES", [])
        row = bt.evaluate("http://x", "m", "lbl", warmup=False, backend="control")
        assert row["backend"] == "control"
