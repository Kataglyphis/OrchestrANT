"""Tests for the shared CLI front end.

This code had NO coverage before it was extracted: nothing in tests/ imports
either tool's main(), so candidate resolution — the code deciding WHICH
endpoint gets measured — was untested in both copies. That is how a None label
survived long enough to crash a ranking print after a full run and before the
report was written.
"""

import json
import os
import types

import pytest

from orchestrant.benchmark import client as bench_cli
from orchestrant.benchmark.client import resolve_candidates, write_report


def stub_backend(pinned_model=None):
    """Stands in for resolve_backend without needing a server."""

    def _resolve(backend, base_url):
        if base_url:
            return base_url.rstrip("/"), None, "--base-url"
        return f"http://{backend or 'default'}:1", pinned_model, f"backend {backend!r}"

    return _resolve


def args(**kw):
    base = {
        "compare": None,
        "backend": None,
        "base_url": None,
        "model": None,
        "label": None,
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


class TestSingleRun:
    def test_explicit_label_wins(self):
        c = resolve_candidates(
            args(backend="npu", model="org/M", label="mine"), stub_backend()
        )
        assert c == [("mine", "http://npu:1", "org/M")]

    def test_model_is_the_label_when_none_given(self):
        c = resolve_candidates(args(backend="npu", model="org/M"), stub_backend())
        assert c[0][0] == "org/M"

    def test_a_backend_pinned_model_is_used(self):
        c = resolve_candidates(args(backend="npu"), stub_backend("pinned/M"))
        assert c[0] == ("pinned/M", "http://npu:1", "pinned/M")

    def test_label_never_ends_up_none(self):
        # The defect this whole module exists for: with no label, no --model and
        # a backend that pins nothing, the label used to be None and the ranking
        # print crashed AFTER the run and BEFORE the report was written.
        c = resolve_candidates(args(backend="ollama"), stub_backend(None))
        assert c[0][0] is not None and c[0][0] != ""

    def test_label_never_none_even_with_no_backend_at_all(self):
        c = resolve_candidates(args(), stub_backend(None))
        assert c[0][0]


class TestCompareFile:
    def _file(self, tmp_path, entries):
        p = tmp_path / "cands.json"
        p.write_text(json.dumps(entries))
        return str(p)

    def test_reads_every_entry(self, tmp_path):
        f = self._file(
            tmp_path, [{"backend": "a", "model": "m1"}, {"backend": "b", "model": "m2"}]
        )
        c = resolve_candidates(args(compare=f), stub_backend())
        assert [x[2] for x in c] == ["m1", "m2"]

    def test_entries_without_label_or_model_still_get_a_label(self, tmp_path):
        # Exactly the reproduction from the audit.
        f = self._file(tmp_path, [{"backend": "geniex-npu"}, {"backend": "geniex-cpu"}])
        c = resolve_candidates(args(compare=f), stub_backend(None))
        assert all(x[0] for x in c), c

    def test_explicit_base_url_overrides_the_backend(self, tmp_path):
        f = self._file(tmp_path, [{"base_url": "http://elsewhere:9/", "model": "m"}])
        c = resolve_candidates(args(compare=f), stub_backend())
        assert c[0][1] == "http://elsewhere:9"

    def test_a_non_list_file_fails_loudly(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text('{"backend": "a"}')
        with pytest.raises(SystemExit):
            resolve_candidates(args(compare=str(p)), stub_backend())


class TestWriteReport:
    def test_writes_the_shared_envelope(self, tmp_path):
        out = str(tmp_path / "r.json")
        write_report(
            out, "bench_tools", {"repeats": 1}, [{"label": "m"}], None, ("client.py",)
        )
        d = json.load(open(out))
        assert d["benchmark"] == "bench_tools"
        assert d["config"] == {"repeats": 1}
        assert d["reports"] == [{"label": "m"}]
        assert "provenance" in d

    def test_the_write_is_atomic(self, tmp_path):
        # A Ctrl-C mid-write used to be able to leave a truncated JSON that a
        # later comparison would silently misread.
        out = str(tmp_path / "r.json")
        write_report(out, "b", {}, [], None, ("client.py",))
        assert not os.path.exists(out + ".tmp")
        json.load(open(out))  # parses

    def test_this_module_is_not_in_the_fingerprint(self):
        # tool_sha256 means "the GRADER moved". Folding plumbing into it would
        # fire that alarm on every --compare-schema edit while the grader is
        # provably unchanged.
        lab = os.path.join(
            os.path.dirname(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
            ),
            "third_party",
            "ANTfrastructure",
            "linux",
            "llm-stack",
        )
        for tool in ("bench_coding.py", "bench_tools.py"):
            path = os.path.join(lab, tool)
            if not os.path.isfile(path):
                pytest.skip(f"{tool} not present (the lab moves in a later phase)")
            with open(path) as handle:
                src = handle.read()
            i = src.index("write_report(")
            call = src[i : i + 400]
            assert "client.py" not in call, f"{tool} hashes the plumbing"


# ── the shared request path ──────────────────────────────────────────────────


class FakeResponse:
    """Stands in for urlopen's response. Never touches a socket."""

    def __init__(self, body=b"", lines=(), delay=0.0):
        self._body = body
        self._lines = list(lines)
        self._delay = delay
        self.closed = False
        self.status = 200
        self.headers = {}

    def read(self, *a):
        return self._body

    def __iter__(self):
        for line in self._lines:
            if self._delay:
                _fake_clock[0] += self._delay
            yield line

    def close(self):
        self.closed = True


_fake_clock = [0.0]


@pytest.fixture
def captured(monkeypatch):
    """Capture the Request bench_cli builds instead of sending it."""
    seen = {}
    response = FakeResponse(body=b'{"ok": true}')

    def fake_urlopen(req, timeout=None):
        seen["request"] = req
        seen["timeout"] = timeout
        return seen.get("response", response)

    monkeypatch.setattr(bench_cli.urllib.request, "urlopen", fake_urlopen)
    seen["response"] = response
    return seen


class TestRequestHeaders:
    def test_content_type_by_default(self):
        assert bench_cli.request_headers(None) == {"Content-Type": "application/json"}

    def test_api_key_env_becomes_a_bearer_header(self, monkeypatch):
        monkeypatch.setenv("BENCH_TEST_KEY", "s3cret")
        h = bench_cli.request_headers({"api_key_env": "BENCH_TEST_KEY"})
        assert h["Authorization"] == "Bearer s3cret"

    def test_a_missing_variable_fails_loudly_and_names_it(self, monkeypatch):
        # An anonymous 401 an hour into a sweep is the failure this prevents.
        monkeypatch.delenv("BENCH_TEST_KEY", raising=False)
        with pytest.raises(SystemExit) as e:
            bench_cli.request_headers({"api_key_env": "BENCH_TEST_KEY"})
        assert "BENCH_TEST_KEY" in str(e.value)

    def test_an_empty_variable_is_treated_as_missing(self, monkeypatch):
        monkeypatch.setenv("BENCH_TEST_KEY", "")
        with pytest.raises(SystemExit):
            bench_cli.request_headers({"api_key_env": "BENCH_TEST_KEY"})

    def test_building_the_header_does_not_stash_the_key_in_the_entry(self, monkeypatch):
        # entry_config() reports the entry into a committed report file; a key
        # cached back into the entry would ride along.
        monkeypatch.setenv("BENCH_TEST_KEY", "s3cret")
        entry = {"api_key_env": "BENCH_TEST_KEY"}
        bench_cli.request_headers(entry)
        assert "s3cret" not in json.dumps(entry)
        assert "s3cret" not in json.dumps(bench_cli.entry_config(entry))

    def test_entry_headers_are_merged(self):
        h = bench_cli.request_headers({"headers": {"X-Lane": "npu"}})
        assert h["X-Lane"] == "npu" and h["Content-Type"] == "application/json"

    def test_api_key_env_wins_over_a_literal_authorization_header(self, monkeypatch):
        monkeypatch.setenv("BENCH_TEST_KEY", "fromenv")
        h = bench_cli.request_headers(
            {
                "headers": {"Authorization": "Bearer stale"},
                "api_key_env": "BENCH_TEST_KEY",
            }
        )
        assert h["Authorization"] == "Bearer fromenv"


class TestEntryConfig:
    def test_reports_the_extras(self):
        cfg = bench_cli.entry_config({"request_extra": {"num_ctx": 16384}})
        assert cfg["request_extra"] == {"num_ctx": 16384}

    def test_header_values_never_reach_a_report(self):
        # Reports are committed; a header value can be a key.
        cfg = bench_cli.entry_config({"headers": {"X-Api-Key": "s3cret"}})
        assert cfg["headers"] == ["X-Api-Key"]
        assert "s3cret" not in json.dumps(cfg)

    def test_the_key_variable_name_is_kept_but_not_read(self, monkeypatch):
        monkeypatch.setenv("BENCH_TEST_KEY", "s3cret")
        cfg = bench_cli.entry_config({"api_key_env": "BENCH_TEST_KEY"})
        assert cfg["api_key_env"] == "BENCH_TEST_KEY"
        assert "s3cret" not in json.dumps(cfg)

    def test_probe_defaults_to_true(self):
        assert bench_cli.entry_config({})["probe"] is True
        assert bench_cli.entry_config({"probe": False})["probe"] is False


class TestPostJson:
    def _body(self, captured):
        return json.loads(captured["request"].data.decode())

    def test_the_body_is_sent_as_json(self, captured):
        bench_cli.post_json("http://h/v1/chat/completions", {"model": "m"})
        assert self._body(captured) == {"model": "m"}

    def test_request_extra_is_merged_in(self, captured):
        bench_cli.post_json(
            "http://h/x", {"model": "m"}, entry={"request_extra": {"num_ctx": 16384}}
        )
        assert self._body(captured) == {"model": "m", "num_ctx": 16384}

    def test_an_explicit_key_wins_over_request_extra(self, captured):
        # A per-backend default must never silently change the budget a
        # benchmark is measuring.
        bench_cli.post_json(
            "http://h/x",
            {"max_tokens": 3000},
            entry={"request_extra": {"max_tokens": 256}},
        )
        assert self._body(captured)["max_tokens"] == 3000

    def test_auth_travels_with_the_request(self, captured, monkeypatch):
        monkeypatch.setenv("BENCH_TEST_KEY", "s3cret")
        bench_cli.post_json("http://h/x", {}, entry={"api_key_env": "BENCH_TEST_KEY"})
        assert captured["request"].get_header("Authorization") == "Bearer s3cret"

    def test_stream_sets_the_body_flag(self, captured):
        bench_cli.post_json("http://h/x", {"model": "m"}, stream=True)
        assert self._body(captured)["stream"] is True

    def test_a_caller_can_still_turn_streaming_off_in_the_body(self, captured):
        bench_cli.post_json("http://h/x", {"stream": False}, stream=True)
        assert self._body(captured)["stream"] is False

    def test_json_parses_the_response(self, captured):
        assert bench_cli.post_json("http://h/x", {}).json() == {"ok": True}

    def test_lines_decodes_and_strips(self, captured):
        captured["response"] = FakeResponse(lines=[b"data: {}\n", b"\n"])
        got = list(bench_cli.post_json("http://h/x", {}, stream=True).lines())
        assert got == ["data: {}", ""]

    def test_the_deadline_abandons_the_stream_and_says_so(self, captured, monkeypatch):
        # urlopen's timeout is PER SOCKET READ, so a server that keeps emitting
        # deltas never trips it. The deadline is the total-duration cap.
        _fake_clock[0] = 0.0
        monkeypatch.setattr(bench_cli.time, "monotonic", lambda: _fake_clock[0])
        captured["response"] = FakeResponse(lines=[b"a", b"b", b"c", b"d"], delay=10.0)
        resp = bench_cli.post_json("http://h/x", {}, stream=True, deadline=25)
        got = list(resp.lines())
        assert resp.gave_up is True
        assert len(got) < 4

    def test_no_deadline_reads_the_whole_stream(self, captured):
        captured["response"] = FakeResponse(lines=[b"a", b"b"])
        resp = bench_cli.post_json("http://h/x", {}, stream=True)
        assert list(resp.lines()) == ["a", "b"] and resp.gave_up is False

    def test_the_context_manager_closes_the_socket(self, captured):
        with bench_cli.post_json("http://h/x", {}) as r:
            pass
        assert r.raw.closed is True

    def test_the_timeout_reaches_urlopen(self, captured):
        bench_cli.post_json("http://h/x", {}, timeout=42)
        assert captured["timeout"] == 42


class TestLabelCollisions:
    """Two lanes serving the same GGUF resolved to one label: the ranking
    showed one row and bench_compare read a 3/3 -> 0/3 collapse as unchanged.
    """

    def _file(self, tmp_path, entries):
        p = tmp_path / "cands.json"
        p.write_text(json.dumps(entries))
        return str(p)

    def test_colliding_derived_labels_get_the_backend_appended(self, tmp_path):
        f = self._file(
            tmp_path,
            [
                {"backend": "gpu", "model": "org/M"},
                {"backend": "cpu", "model": "org/M"},
            ],
        )
        labels = [c[0] for c in resolve_candidates(args(compare=f), stub_backend())]
        assert len(set(labels)) == 2
        assert all("org/M" in lbl for lbl in labels)
        assert any("gpu" in lbl for lbl in labels) and any(
            "cpu" in lbl for lbl in labels
        )

    def test_a_unique_label_keeps_the_bare_model_id(self, tmp_path):
        # A stored baseline is keyed on the label; renaming a lone candidate
        # would stop the shipped baseline comparing.
        f = self._file(
            tmp_path,
            [
                {"backend": "gpu", "model": "org/M"},
                {"backend": "cpu", "model": "org/Other"},
            ],
        )
        labels = [c[0] for c in resolve_candidates(args(compare=f), stub_backend())]
        assert labels == ["org/M", "org/Other"]

    def test_colliding_explicit_labels_are_refused(self, tmp_path):
        # Only the author knows which is which; renaming silently would put the
        # wrong name on a published number.
        f = self._file(
            tmp_path,
            [{"backend": "gpu", "label": "same"}, {"backend": "cpu", "label": "same"}],
        )
        with pytest.raises(SystemExit) as e:
            resolve_candidates(args(compare=f), stub_backend())
        assert "same" in str(e.value)

    def test_the_same_backend_twice_is_refused_rather_than_silently_merged(
        self, tmp_path
    ):
        f = self._file(
            tmp_path,
            [
                {"backend": "gpu", "model": "org/M"},
                {"backend": "gpu", "model": "org/M"},
            ],
        )
        with pytest.raises(SystemExit):
            resolve_candidates(args(compare=f), stub_backend())


class TestCandidateEntries:
    def _file(self, tmp_path, entries):
        p = tmp_path / "cands.json"
        p.write_text(json.dumps(entries))
        return str(p)

    def stub_entry(self, backend, base_url):
        return {"api_key_env": "K"} if backend == "paid" else {}

    def test_three_tuples_stay_the_default(self, tmp_path):
        # Every existing caller unpacks three.
        f = self._file(tmp_path, [{"backend": "a", "model": "m"}])
        assert len(resolve_candidates(args(compare=f), stub_backend())[0]) == 3

    def test_asking_for_entries_yields_four_tuples(self, tmp_path):
        f = self._file(tmp_path, [{"backend": "paid", "model": "m"}])
        c = resolve_candidates(args(compare=f), stub_backend(), self.stub_entry)
        assert c[0][3] == {"api_key_env": "K"}

    def test_the_single_run_path_carries_an_entry_too(self):
        c = resolve_candidates(
            args(backend="paid", model="m"), stub_backend(), self.stub_entry
        )
        assert c[0][3] == {"api_key_env": "K"}

    def test_a_comment_only_element_is_not_a_candidate(self, tmp_path):
        # candidates.example.json has to be able to explain its own fields.
        f = self._file(
            tmp_path,
            [{"_comment": ["how to use this file"]}, {"backend": "a", "model": "m"}],
        )
        c = resolve_candidates(args(compare=f), stub_backend())
        assert [x[2] for x in c] == ["m"]

    def test_a_commented_candidate_is_still_a_candidate(self, tmp_path):
        f = self._file(
            tmp_path, [{"_comment": "the CPU lane", "backend": "a", "model": "m"}]
        )
        c = resolve_candidates(args(compare=f), stub_backend())
        assert [x[2] for x in c] == ["m"]


class TestShippedExampleCandidates:
    def test_the_example_file_resolves(self):
        # The example is lab data (it documents --compare input), so it
        # stays in the hub until the lab moves; skip where it is absent.
        hub = os.path.join(
            os.path.dirname(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
            ),
            "third_party",
            "ANTfrastructure",
            "linux",
            "llm-stack",
        )
        path = os.path.join(hub, "candidates.example.json")
        if not os.path.isfile(path):
            pytest.skip("candidates.example.json not present (lab data)")
        rows = bench_cli.load_candidates(path, stub_backend())
        assert len(rows) >= 4
        labels = [r["label"] for r in rows]
        assert len(set(labels)) == len(labels), f"example labels collide: {labels}"
