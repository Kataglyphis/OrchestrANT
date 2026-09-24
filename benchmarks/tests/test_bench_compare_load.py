"""P1.5: a verdict load can move is withheld across runs started under unlike load.

Every report records its host load at the start (provenance.host_load), and
compare() has named a busy start or a load difference since 2026-09-24 -- but
the exit status never moved. A CPU lane measured at 14.1 tok/s beside 0.93
cores of other load could still be called SLOWER against its quiet 30, and a
slowdown hidden by a busy baseline could still pass as "no regression". These
pin which verdicts are withheld, which stay judged, and the order of the exit
codes, for one pair and for --dir.
"""

import argparse
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_compare as bcmp  # noqa: E402
from compare_verdict import CONDITIONS_DIFFER, NOT_COMPARED, exit_code  # noqa: E402

QUIET, BUSY = 0.2, 1.3


def prov(other_cores):
    """A run-start load record; None is a report that predates the record."""
    return {} if other_cores is None else {"host_load": {"other_cores": other_cores}}


def tools(other_cores, wall=2.0, passing=12):
    """A deterministic 12-case bench_tools report, every case timed at `wall` s."""
    rows = [{"case": f"c{i}", "passed": i < passing, "wall_s": wall} for i in range(12)]
    entry = {"label": "m", "model": "m", "passed": passing, "total": 12}
    entry |= {"effective_n": 12, "deterministic": True, "results": rows}
    report = {"benchmark": "bench_tools", "provenance": prov(other_cores)}
    return {**report, "reports": [entry]}


def speed(other_cores, decode, lane_cores=None):
    """A speed-runner report: nine prompts at `decode` tok/s, nothing scored."""
    share = {} if lane_cores is None else {"lane_cores": lane_cores}
    rows = [
        {"prompt_index": i, "decode_tok_per_sec": decode, "completion_tokens": 100}
        | share
        for i in range(9)
    ]
    report = {"model": "m", "results": rows, "config": {}, "correctness": None}
    return {**report, "provenance": prov(other_cores)}


def lanes(other_cores, tok, serialised=None):
    """A bench_lanes report: throughput per lane, and the batching verdict."""
    reports = [{"label": k, "model": "m", "tok_per_sec": v} for k, v in tok.items()]
    if serialised is not None:
        reports.append({"label": "batching", "model": "m", "serialised": serialised})
    report = {"benchmark": "bench_lanes", "provenance": prov(other_cores)}
    return {**report, "reports": reports}


def pair(old, new, **kwargs):
    """compare() on two raw reports -> (findings, regressed, seen)."""
    seen = {}
    findings, regressed = bcmp.compare(
        bcmp.normalise(old), bcmp.normalise(new), seen=seen, **kwargs
    )
    return findings, regressed, seen


class TestTheGateIsTheLoadNote:
    """The refusal fires exactly where provenance.compare() prints a load note:
    over 1.0 other cores on either side, or both recorded and > 0.3 apart."""

    def test_a_busy_new_run_withholds_the_timing_verdict(self):
        findings, regressed, seen = pair(tools(QUIET, 2.0), tools(BUSY, 3.0))
        assert not regressed
        assert seen["withheld"] == ["m per-attempt time"]
        line = next(f for f in findings if "per attempt" in f)
        assert "2.00s -> 3.00s" in line and "WITHHELD" in line
        assert "SLOWER" not in line

    def test_the_note_that_decided_it_is_still_printed(self):
        findings, _, _ = pair(tools(QUIET, 2.0), tools(BUSY, 3.0))
        assert any(f.startswith("! HOST WAS BUSY") for f in findings)

    def test_runs_under_different_load_withhold_it(self):
        _, regressed, seen = pair(tools(0.13, 2.0), tools(0.86, 3.0))
        assert not regressed and seen["withheld"] == ["m per-attempt time"]

    def test_the_spread_of_two_quiet_runs_is_judged(self):
        # 0.13 vs 0.42: what two quiet runs of the CPU lane already showed.
        findings, regressed, seen = pair(tools(0.13, 2.0), tools(0.42, 3.0))
        assert regressed and seen["withheld"] == []
        assert any("*** SLOWER ***" in f for f in findings)

    def test_a_baseline_that_predates_the_record_is_not_refused_for_it(self):
        # Every baseline saved before 2026-09-24 has no host_load at all.
        _, regressed, seen = pair(tools(None, 2.0), tools(QUIET, 3.0))
        assert regressed and seen["withheld"] == []
        _, regressed, seen = pair(tools(None, 2.0), tools(None, 3.0))
        assert regressed and seen["withheld"] == []

    def test_but_a_busy_run_is_refused_against_one(self):
        _, regressed, seen = pair(tools(None, 2.0), tools(1.5, 3.0))
        assert not regressed and seen["withheld"] == ["m per-attempt time"]


class TestWhatIsWithheld:
    """Load slows an answer; it does not change it. Rates and times are
    withheld, scores, per-case flips and batching stay judged."""

    def test_an_unchanged_time_is_withheld_too(self):
        # "No change" beside a busy run is the comfort load can fake: the
        # busy side ran slow, so a real slowdown on the other one looks flat.
        findings, _, seen = pair(tools(BUSY, 2.0), tools(QUIET, 2.0))
        assert seen["withheld"] == ["m per-attempt time"]
        assert any("2.00s -> 2.00s" in f and "WITHHELD" in f for f in findings)

    def test_faster_against_a_busy_baseline_is_not_called_faster(self):
        findings, _, _ = pair(tools(BUSY, 3.0), tools(QUIET, 2.0))
        line = next(f for f in findings if "per attempt" in f)
        assert "WITHHELD" in line and "faster" not in line

    def test_a_score_regression_is_still_judged(self):
        findings, regressed, seen = pair(tools(QUIET), tools(BUSY, passing=6))
        assert regressed
        assert any("*** REGRESSION ***" in f for f in findings)
        assert seen["withheld"] == ["m per-attempt time"]

    def test_lane_throughput_is_withheld(self):
        old = lanes(QUIET, {"aggregate": 31.0})
        findings, regressed, seen = pair(old, lanes(BUSY, {"aggregate": 20.0}))
        assert not regressed and seen["withheld"] == ["aggregate tok/s"]
        assert any("31.0 -> 20.0 tok/s" in f and "WITHHELD" in f for f in findings)

    def test_losing_batching_is_still_judged(self):
        # A ratio inside one run (the second request's TTFT against the first
        # one's wall), so load both requests share does not decide it.
        old = lanes(QUIET, {}, serialised=False)
        _, regressed, seen = pair(old, lanes(BUSY, {}, serialised=True))
        assert regressed and seen["withheld"] == []

    def test_the_speed_tripwire_is_withheld_on_a_cpu_lane(self):
        # The measured case: 30 tok/s quiet, 14.1 at 0.93 other cores.
        old, new = speed(0.13, 30.0, lane_cores=7.4), speed(0.93, 14.1, lane_cores=7.4)
        findings, regressed, seen = pair(old, new)
        assert not regressed
        assert "m decode tok/s" in seen["withheld"]
        line = next(f for f in findings if "decode tok/s" in f)
        assert "WITHHELD" in line and "SLOWER" not in line

    def test_and_where_the_lane_share_is_unknown(self):
        _, regressed, seen = pair(speed(QUIET, 30.0), speed(BUSY, 14.1))
        assert not regressed and "m decode tok/s" in seen["withheld"]

    def test_a_withheld_better_is_not_called_better(self):
        findings, _, _ = pair(speed(BUSY, 14.1, 7.4), speed(QUIET, 30.0, 7.4))
        line = next(f for f in findings if "decode tok/s" in f)
        assert "WITHHELD" in line and "better" not in line

    def test_the_npu_lane_is_still_judged(self):
        # The NPU lane (~1.7 cores) did not move from 0.1 to 2.0 other cores:
        # a busy start says nothing about its rate, and GenieX v0.7.0's 13 %
        # NPU decode loss must not hide behind one.
        old, new = speed(0.1, 22.8, lane_cores=1.7), speed(2.0, 19.8, lane_cores=1.7)
        findings, regressed, seen = pair(old, new)
        assert regressed and seen["withheld"] == []
        assert any("decode" in f and "*** SLOWER ***" in f for f in findings)

    def test_one_side_of_unknown_share_spares_nothing(self):
        _, regressed, seen = pair(speed(0.1, 22.8, 1.7), speed(2.0, 19.8))
        assert not regressed and "m decode tok/s" in seen["withheld"]


class TestAllowLoadDifference:
    def test_it_judges_as_before_and_still_prints_the_note(self):
        findings, regressed, seen = pair(
            tools(QUIET, 2.0), tools(BUSY, 3.0), allow_load_difference=True
        )
        assert regressed and seen["withheld"] == []
        assert any("*** SLOWER ***" in f for f in findings)
        assert any(f.startswith("! HOST WAS BUSY") for f in findings)


class TestExitPrecedence:
    """REGRESSION (1) > CONDITIONS DIFFER (4) > NOTHING COMPARED (3) > 0."""

    @pytest.mark.parametrize(
        ("regressed", "withheld", "compared", "code"),
        [
            (True, ["m per-attempt time"], 1, 1),  # a score fell: load cannot do that
            (True, [], 0, 1),  # different benchmarks: nothing compared, refused
            (False, ["m per-attempt time"], 1, CONDITIONS_DIFFER),
            (False, [], 0, NOT_COMPARED),
            (False, [], 2, 0),
        ],
    )
    def test_the_order(self, regressed, withheld, compared, code):
        assert exit_code(regressed, withheld, compared) == code

    def _main(self, monkeypatch, tmp_path, old, new, *flags):
        paths = []
        for name, report in (("old.json", old), ("new.json", new)):
            (tmp_path / name).write_text(json.dumps(report))
            paths.append(str(tmp_path / name))
        monkeypatch.setattr(sys, "argv", ["bench_compare.py", *flags, *paths])
        with pytest.raises(SystemExit) as e:
            bcmp.main()
        return e.value.code

    def test_a_withheld_verdict_alone_is_conditions_differ(
        self, monkeypatch, tmp_path, capsys
    ):
        code = self._main(monkeypatch, tmp_path, tools(QUIET, 2.0), tools(BUSY, 3.0))
        out = capsys.readouterr().out
        assert code == CONDITIONS_DIFFER
        assert "  CONDITIONS DIFFER" in out
        assert "WITHHELD for load: m per-attempt time" in out
        assert "--allow-load-difference" in out
        assert "no regression detected" not in out

    def test_a_regression_beside_it_still_exits_1_and_names_both(
        self, monkeypatch, tmp_path, capsys
    ):
        new = tools(BUSY, 3.0, passing=6)
        code = self._main(monkeypatch, tmp_path, tools(QUIET, 2.0), new)
        out = capsys.readouterr().out
        assert code == 1
        assert "  REGRESSION" in out and "WITHHELD for load" in out
        assert "CONDITIONS DIFFER" not in out

    def test_nothing_compared_on_a_busy_host_stays_nothing_compared(
        self, monkeypatch, tmp_path, capsys
    ):
        # Nothing comparable means nothing to withhold: the remedy is other
        # reports, not a quieter host.
        blind = {"model": "m", "results": [], "config": {}, "provenance": prov(1.5)}
        code = self._main(monkeypatch, tmp_path, blind, blind)
        out = capsys.readouterr().out
        assert code == NOT_COMPARED
        assert "HOST WAS BUSY" in out and "WITHHELD" not in out

    def test_the_flag_restores_todays_verdict(self, monkeypatch, tmp_path):
        old, new = tools(QUIET, 2.0), tools(BUSY, 3.0)
        flag = "--allow-load-difference"
        assert self._main(monkeypatch, tmp_path, old, new, flag) == 1

    def test_quiet_runs_that_agree_exit_0(self, monkeypatch, tmp_path):
        assert self._main(monkeypatch, tmp_path, tools(0.1), tools(0.3)) == 0


class TestDirectories:
    """--dir: one exit code over several pairings, the same order as one pair."""

    @staticmethod
    def _dirs(tmp_path, pairs):
        for side in (0, 1):
            d = tmp_path / ("old", "new")[side]
            d.mkdir()
            for name, reports in pairs.items():
                (d / name).write_text(json.dumps(reports[side]))
        return [str(tmp_path / "old"), str(tmp_path / "new")]

    def _run(self, tmp_path, pairs, allow=False):
        dirs = self._dirs(tmp_path, pairs)
        args = argparse.Namespace(
            reports=dirs, time_tolerance=0.25, allow_load_difference=allow
        )
        return bcmp._compare_directories(args)

    WITHHELD = (tools(QUIET, 2.0), tools(BUSY, 3.0))
    FINE = (tools(0.1), tools(0.3))
    FELL = (tools(0.1), tools(0.3, passing=6))
    BLIND = ({"model": "m", "results": []}, {"model": "m", "results": []})

    def test_a_regression_anywhere_wins(self, tmp_path, capsys):
        assert self._run(tmp_path, {"a.json": self.FELL, "b.json": self.WITHHELD}) == 1
        out = capsys.readouterr().out
        assert "0 with nothing to compare, 1 with a verdict withheld for load" in out

    def test_a_withheld_pairing_beats_a_passing_one(self, tmp_path, capsys):
        code = self._run(tmp_path, {"a.json": self.WITHHELD, "b.json": self.FINE})
        out = capsys.readouterr().out
        assert code == CONDITIONS_DIFFER
        assert "    CONDITIONS DIFFER" in out and "    no regression detected" in out
        assert "    WITHHELD for load: m per-attempt time" in out

    def test_and_a_blind_one(self, tmp_path):
        pairs = {"a.json": self.WITHHELD, "b.json": self.BLIND}
        assert self._run(tmp_path, pairs) == CONDITIONS_DIFFER

    def test_all_blind_is_still_nothing_compared(self, tmp_path):
        pairs = {"a.json": self.BLIND, "b.json": self.BLIND}
        assert self._run(tmp_path, pairs) == NOT_COMPARED

    def test_the_flag_judges_every_pairing(self, tmp_path):
        pairs = {"a.json": self.WITHHELD, "b.json": self.FINE}
        assert self._run(tmp_path, pairs, allow=True) == 1

    def test_a_caller_that_predates_the_flag_is_gated(self, tmp_path):
        # The Namespace is built by hand in tests and scripts; no attribute
        # means no override.
        dirs = self._dirs(tmp_path, {"a.json": self.WITHHELD})
        args = argparse.Namespace(reports=dirs, time_tolerance=0.25)
        assert bcmp._compare_directories(args) == CONDITIONS_DIFFER
