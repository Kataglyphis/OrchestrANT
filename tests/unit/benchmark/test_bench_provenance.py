"""Tests for provenance capture.

A result without provenance cannot be compared against a later one, which makes
regression detection impossible -- and an old number that looks authoritative
but cannot be reproduced is worse than no number.
"""

import pytest

from orchestrant.benchmark import provenance as bench_provenance
from orchestrant.benchmark.provenance import (
    collect,
    compare,
    determinism_probe,
    known_deterministic,
    tool_fingerprint,
)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    # collect() probes every registry endpoint for live_lanes; the tests must
    # never leave this machine, and the DNS timeouts made this file take 90 s.
    monkeypatch.setattr(bench_provenance, "busy_lanes", lambda *a, **k: [])
    # runtime_info() may run a locally installed `geniex --version`; a unit
    # test must not depend on what this machine has installed.
    monkeypatch.setattr(bench_provenance, "runtime_info", lambda *a, **k: None)


class TestCollect:
    def test_records_the_fields_a_rerun_needs(self):
        p = collect()
        for key in (
            "schema_version",
            "timestamp_utc",
            "host",
            "os",
            "architecture",
            "python",
            "git_sha",
            "git_dirty",
        ):
            assert key in p, key

    def test_names_missing_fields_instead_of_omitting_them(self):
        # LB9's lesson: a silent gap is worse than a visible one.
        p = collect()
        assert isinstance(p["incomplete"], list)

    def test_unreachable_server_is_recorded_as_null_not_an_exception(self):
        p = collect(base_url="http://127.0.0.1:1")
        assert p["server_models"] is None

    def test_extra_fields_are_merged(self):
        p = collect(extra={"lane": "npu"})
        assert p["lane"] == "npu"


class TestToolFingerprint:
    def test_hashes_the_benchmark_source(self):
        assert tool_fingerprint("provenance.py")

    def test_same_files_same_hash_regardless_of_order(self):
        a = tool_fingerprint("provenance.py", "openai_api.py")
        b = tool_fingerprint("openai_api.py", "provenance.py")
        assert a == b

    def test_different_files_differ(self):
        assert tool_fingerprint("client.py") != tool_fingerprint("openai_api.py")

    def test_missing_file_yields_none(self):
        assert tool_fingerprint("does-not-exist.py") is None


class TestCompare:
    def test_two_identical_clean_runs_report_nothing(self):
        p = collect(extra={"git_dirty": False})
        assert compare(p, p) == []

    def test_a_dirty_run_is_flagged_even_against_itself(self):
        # Deliberate, not a quirk: with a dirty tree the recorded SHA does not
        # describe what actually ran, so the caveat belongs on every comparison
        # the run takes part in -- including with itself.
        p = collect(extra={"git_dirty": True})
        assert any("dirty working tree" in n for n in compare(p, p))

    def test_a_changed_grader_is_called_out_first(self):
        # The trap this exists for: the ranking moved because the BENCHMARK
        # changed, which is indistinguishable from a model regression without it.
        old = collect(extra={"tool_sha256": "aaaa"})
        new = collect(extra={"tool_sha256": "bbbb"})
        notes = compare(old, new)
        assert notes and "BENCHMARK SOURCE CHANGED" in notes[0]

    def test_different_served_models_are_flagged(self):
        old = collect(extra={"server_models": ["a"]})
        new = collect(extra={"server_models": ["b"]})
        assert any("served models differ" in n for n in compare(old, new))

    def test_a_dirty_tree_is_flagged(self):
        old = collect(extra={"git_dirty": False})
        new = collect(extra={"git_dirty": True})
        assert any("dirty working tree" in n for n in compare(old, new))

    def test_a_moved_repository_is_flagged(self):
        old = collect(extra={"git_sha": "1" * 40, "git_dirty": False})
        new = collect(extra={"git_sha": "2" * 40, "git_dirty": False})
        assert any("repository moved" in n for n in compare(old, new))


V061 = """GenieX CLI Version:     v0.6.1
QAIRT Runtime Version:  2.45
LlamaCPP Runtime Hash:  0eadefe
"""


def _geniex(cli, llama, serve_args=None):
    return {
        "server": "geniex",
        "cli": cli,
        "qairt": "2.45",
        "llama_cpp": llama,
        "serve_args": serve_args,
        "verified": True,
    }


class TestRuntime:
    """Every GenieX release moved something a benchmark depended on.

    Until now no report said which release produced it.
    """

    def test_parses_all_three_version_lines(self):
        assert bench_provenance.parse_geniex_version(V061) == {
            "cli": "v0.6.1",
            "qairt": "2.45",
            "llama_cpp": "0eadefe",
        }

    def test_unrecognised_output_parses_to_nothing(self):
        assert bench_provenance.parse_geniex_version("usage: geniex ...") == {}
        assert bench_provenance.parse_geniex_version(None) == {}

    def test_a_runtime_upgrade_is_named_on_compare(self):
        old = collect(
            extra={"git_dirty": False, "runtime": _geniex("v0.6.1", "0eadefe")}
        )
        new = collect(
            extra={"git_dirty": False, "runtime": _geniex("v0.7.0", "4ff829")}
        )
        notes = compare(old, new)
        hit = [n for n in notes if "SERVING RUNTIME CHANGED" in n]
        assert hit and "v0.6.1" in hit[0] and "v0.7.0" in hit[0] and "4ff829" in hit[0]

    def test_an_identical_runtime_says_nothing(self):
        p = collect(extra={"git_dirty": False, "runtime": _geniex("v0.7.0", "4ff829")})
        assert compare(p, p) == []

    def test_a_runtime_on_one_side_only_is_flagged(self):
        old = collect(extra={"git_dirty": False})
        new = collect(
            extra={"git_dirty": False, "runtime": _geniex("v0.7.0", "4ff829")}
        )
        assert any("one side only" in n for n in compare(old, new))

    def test_changed_serve_flags_are_named_but_a_port_move_is_not(self):
        base = ["serve", "--compute", "npu", "--nctx", "16384"]
        a = _geniex("v0.7.0", "4ff829", [*base, "--host", "127.0.0.1:18181"])
        b = _geniex("v0.7.0", "4ff829", [*base, "--host", "127.0.0.1:18191"])
        c = _geniex("v0.7.0", "4ff829", [*base[:-1], "4096"])
        clean = {"git_dirty": False}
        assert (
            compare(
                collect(extra={**clean, "runtime": a}),
                collect(extra={**clean, "runtime": b}),
            )
            == []
        )
        notes = compare(
            collect(extra={**clean, "runtime": a}),
            collect(extra={**clean, "runtime": c}),
        )
        assert any("different serve flags" in n for n in notes)


class TestRuntimeInfo:
    def test_the_lane_process_is_the_best_evidence(self, monkeypatch):
        from orchestrant.benchmark import hostload

        class FakeLane:
            available = True
            reason = None

            def __init__(self, url):
                pass

            def info(self):
                return {
                    "pid": 42,
                    "exe": r"C:\Users\u\AppData\Local\GenieX CLI\geniex.exe",
                    "cmdline": ["geniex.exe", "serve", "--compute", "npu"],
                }

        monkeypatch.setattr(hostload, "LaneProcess", FakeLane)
        monkeypatch.setattr(
            bench_provenance,
            "_geniex_version",
            lambda exe, timeout=30: bench_provenance.parse_geniex_version(V061),
        )
        info = _REAL_RUNTIME_INFO("http://127.0.0.1:18181")
        assert info["server"] == "geniex" and info["cli"] == "v0.6.1"
        assert info["verified"] is True
        assert info["serve_args"] == ["serve", "--compute", "npu"]

    def test_an_unreachable_loopback_url_is_not_attributed_to_geniex(self, monkeypatch):
        monkeypatch.setattr(bench_provenance, "_installed_geniex", lambda: "geniex.exe")
        monkeypatch.setattr(bench_provenance, "_ollama_version", lambda *a, **k: None)
        assert _REAL_RUNTIME_INFO("http://127.0.0.1:1") is None


class TestBusyLanes:
    def test_a_probe_false_backend_is_never_asked(self, monkeypatch):
        # backends.json: probe:false marks a paid host, where a discovery
        # request costs money. busy_lanes() used to ask it on every report.
        from orchestrant.benchmark import openai_api

        registry = {
            "paid": {"base_url": "https://paid.example", "probe": False},
            "lane": {"base_url": "http://127.0.0.1:18181"},
        }
        monkeypatch.setattr(
            openai_api, "load_backends", lambda path=None: (registry, "t")
        )
        asked = []

        class Up:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(url, timeout=None):
            asked.append(url)
            return Up()

        monkeypatch.setattr(bench_provenance.urllib.request, "urlopen", fake_urlopen)
        assert _REAL_BUSY_LANES() == ["lane"]
        assert asked == ["http://127.0.0.1:18181/v1/models"]


# Captured at import, before the autouse fixture stubs them for every other test.
_REAL_RUNTIME_INFO = bench_provenance.runtime_info
_REAL_BUSY_LANES = bench_provenance.busy_lanes


class TestTemperatureAndSeed:
    """Whether a lane samples at T=0 decides how a --repeats 1 flip may be
    read. It is measured once and recorded, not rediscovered per run.
    """

    def test_defaults_are_explicit_nulls(self):
        p = collect()
        assert p["temperature"] is None and p["seed"] is None
        assert p["determinism_probe"] is None

    def test_values_are_recorded(self):
        probe = {"deterministic": True, "requests": 2}
        p = collect(temperature=0, seed=42, determinism=probe)
        assert (p["temperature"], p["seed"], p["determinism_probe"]) == (0, 42, probe)

    def test_positional_callers_still_work(self):
        p = collect(None, ("provenance.py",), {"lane": "npu"})
        assert p["lane"] == "npu" and p["tool_sha256"]

    def test_a_temperature_change_is_flagged_on_compare(self):
        old = collect(temperature=0, extra={"git_dirty": False})
        new = collect(temperature=0.7, extra={"git_dirty": False})
        assert any("temperature differs" in n for n in compare(old, new))
        assert compare(old, old) == []


class TestFingerprintLineEndings:
    def test_crlf_and_lf_checkouts_hash_the_same(self, tmp_path, monkeypatch):
        # core.autocrlf: the same commit is CRLF on the Windows host and LF in
        # WSL or CI, and every compare across them cried BENCHMARK SOURCE CHANGED.
        from orchestrant.benchmark import provenance

        (tmp_path / "a.py").write_bytes(b"x = 1\r\ny = 2\r\n")
        (tmp_path / "b.py").write_bytes(b"x = 1\ny = 2\n")
        monkeypatch.setattr(provenance, "__file__", str(tmp_path / "provenance.py"))
        assert provenance.tool_fingerprint("a.py") == provenance.tool_fingerprint(
            "b.py"
        )


class TestSourceChangedDuringRun:
    def test_a_mid_run_edit_is_recorded_and_named(self):
        from orchestrant.benchmark.provenance import collect, compare

        p = collect(tool_files=("stats.py",), tool_sha256_at_start="0" * 16)
        assert p["source_changed_during_run"] is True
        assert p["tool_sha256_at_start"] == "0" * 16
        notes = compare(p, {**p, "source_changed_during_run": False})
        assert any("WHILE it ran" in n for n in notes)

    def test_an_unchanged_source_adds_nothing(self):
        from orchestrant.benchmark.provenance import collect, tool_fingerprint

        sha = tool_fingerprint("stats.py")
        p = collect(tool_files=("stats.py",), tool_sha256_at_start=sha)
        assert "source_changed_during_run" not in p


class TestDeterminismProbe:
    @staticmethod
    def _post(replies):
        calls = []

        def post(url, payload):
            calls.append((url, payload))
            return {"choices": [{"message": {"content": replies[len(calls) - 1]}}]}

        return post, calls

    def test_two_identical_answers_are_deterministic(self):
        post, calls = self._post(["ready", "ok", "ready"])
        r = determinism_probe("http://h:1", "m", post)
        assert r["deterministic"] is True and r["error"] is None
        assert len(calls) == 3 and calls[0] == calls[2]
        assert calls[0][0] == "http://h:1/v1/chat/completions"
        assert calls[0][1]["temperature"] == 0 and calls[0][1]["model"] == "m"

    def test_the_draws_are_never_back_to_back(self):
        # GenieX answers an identical follow-up along a cache path that changes
        # the reply (llama.cpp: stale first token; QAIRT: a different sentence),
        # so two back-to-back draws measured that path, not the sampler.
        post, calls = self._post(["a", "ok", "a"])
        r = determinism_probe("http://h:1", "m", post)
        spacer = calls[1][1]
        assert (
            spacer["max_tokens"] == 1 and spacer["messages"] != calls[0][1]["messages"]
        )
        assert r["spacer"] is True

    def test_two_different_answers_are_not(self):
        post, _ = self._post(["ready", "ok", "Ready!"])
        r = determinism_probe("http://h:1", "m", post)
        assert r["deterministic"] is False
        assert r["output_sha256"][0] != r["output_sha256"][1]

    def test_a_failed_probe_is_null_not_an_exception(self):
        def post(url, payload):
            raise OSError("connection refused")

        r = determinism_probe("http://h:1", "m", post)
        assert r["deterministic"] is None and "OSError" in r["error"]

    def test_the_probe_leaves_the_model_real_choices(self):
        # "Reply with the single word: ready" has almost no entropy, so a
        # SAMPLING lane repeated it verbatim and was recorded deterministic
        # (GenieX v0.6.1 QAIRT lane, 2026-09-24). The probe must ask for enough
        # open-ended text that sampling shows.
        post, calls = self._post(["a", "ok", "a"])
        determinism_probe("http://h:1", "m", post)
        body = calls[0][1]
        assert body["max_tokens"] >= 32
        assert "single word" not in body["messages"][0]["content"]

    def test_a_reply_without_content_is_a_failed_probe(self):
        r = determinism_probe("http://h:1", "m", lambda u, p: {"choices": []})
        assert r["deterministic"] is None

    def test_known_deterministic_needs_a_positive_probe(self):
        assert known_deterministic({"determinism_probe": {"deterministic": True}})
        assert not known_deterministic({"determinism_probe": {"deterministic": False}})
        assert not known_deterministic({"determinism_probe": {"deterministic": None}})
        assert not known_deterministic({}) and not known_deterministic(None)
