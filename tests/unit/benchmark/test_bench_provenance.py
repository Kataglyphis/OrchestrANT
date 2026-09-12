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


class TestDeterminismProbe:
    @staticmethod
    def _post(replies):
        calls = []

        def post(url, payload):
            calls.append((url, payload))
            return {"choices": [{"message": {"content": replies[len(calls) - 1]}}]}

        return post, calls

    def test_two_identical_answers_are_deterministic(self):
        post, calls = self._post(["ready", "ready"])
        r = determinism_probe("http://h:1", "m", post)
        assert r["deterministic"] is True and r["error"] is None
        assert len(calls) == 2 and calls[0] == calls[1]
        assert calls[0][0] == "http://h:1/v1/chat/completions"
        assert calls[0][1]["temperature"] == 0 and calls[0][1]["model"] == "m"

    def test_two_different_answers_are_not(self):
        post, _ = self._post(["ready", "Ready!"])
        r = determinism_probe("http://h:1", "m", post)
        assert r["deterministic"] is False
        assert r["output_sha256"][0] != r["output_sha256"][1]

    def test_a_failed_probe_is_null_not_an_exception(self):
        def post(url, payload):
            raise OSError("connection refused")

        r = determinism_probe("http://h:1", "m", post)
        assert r["deterministic"] is None and "OSError" in r["error"]

    def test_a_reply_without_content_is_a_failed_probe(self):
        r = determinism_probe("http://h:1", "m", lambda u, p: {"choices": []})
        assert r["deterministic"] is None

    def test_known_deterministic_needs_a_positive_probe(self):
        assert known_deterministic({"determinism_probe": {"deterministic": True}})
        assert not known_deterministic({"determinism_probe": {"deterministic": False}})
        assert not known_deterministic({"determinism_probe": {"deterministic": None}})
        assert not known_deterministic({}) and not known_deterministic(None)
