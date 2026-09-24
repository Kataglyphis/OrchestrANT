"""OPS-7: a lanes report's runtimes are diffed lane by lane.

A `lanes` report drives several endpoints at once and puts each lane's own
runtime on its row -- build, serve flags, model files, drivers -- but the
provenance block, all provenance.compare() reads, is collected for ONE URL:
the first lane's, or the batching endpoint's under --batching. A second lane
rebuilt, relaunched with other flags or re-pulled behind the same id moved its
tok/s with nothing saying why.

Split from test_bench_compare.py, which is frozen at its size. The rows are
built by lanes.build_reports(), the producer, so the shape cannot drift.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bench_compare import compare, load, normalise  # noqa: E402
from compare_lanes import lane_runtimes  # noqa: E402

from orchestrant.benchmark.lanes import build_reports  # noqa: E402

NPU_URL, CPU_URL = "http://127.0.0.1:18181", "http://127.0.0.1:18184"
TRACKED = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "benchmark_results",
    "2026-09-23-geniex-upgrade",
)


def _geniex(compute, *, cli="v0.7.0", llama_cpp="4ff829e", nctx="16384", files=None):
    """A lane runtime as provenance.runtime_info() records a GenieX lane."""
    runtime = {
        "server": "geniex",
        "cli": cli,
        "qairt": "2.45",
        "llama_cpp": llama_cpp,
        "serve_args": ["serve", "--compute", compute, "--nctx", nctx],
        "verified": True,
    }
    if files is not None:
        runtime["model_files"] = {"model": f"{compute}-model", "files": files}
    return runtime


NPU, CPU = _geniex("npu"), _geniex("cpu")


def lanes_report(runtimes, prov=None, tok=10.0):
    """A lanes report over {name: runtime}; NPU_URL first, as the tool orders it."""
    urls = {"geniex-npu": NPU_URL, "geniex-cpu": CPU_URL, "geniex-gpu": "http://g:1"}
    lanes = {name: (urls[name], f"{name}-model") for name in runtimes}
    run = {
        "lanes": {name: {"decode_tok_per_sec": tok, "tokens": 100} for name in lanes},
        "baseline": {},
        "runtimes": runtimes,
        "aggregate_tok_per_sec": tok * len(lanes),
        "delivered_tok_per_sec": tok * len(lanes),
        "wall_s": 10.0,
    }
    return {
        "benchmark": "bench_lanes",
        "provenance": prov if prov is not None else {"base_url": NPU_URL},
        "config": {"max_tokens": 256},
        "reports": build_reports(lane_run=run, lanes=lanes),
    }


def _lane_lines(findings):
    """The per-lane lines; the envelope's own "! lane launched with ..." is not one."""
    return [f for f in findings if re.match(r"! lane \S+: ", f)]


class TestTheAggregateOfAnotherLaneSet:
    """The aggregate row sums whatever lanes ran: a lane dropped between the
    runs halves it, and that read as a 50 % SLOWER regression of the runtime.
    Each lane row is measured beside the others, so it moves with the set too."""

    def test_a_dropped_lane_leaves_the_aggregate_unjudged(self):
        old = normalise(lanes_report({"geniex-npu": NPU, "geniex-cpu": CPU}))
        new = normalise(lanes_report({"geniex-npu": NPU}))
        seen = {}
        findings, regressed = compare(old, new, seen=seen)
        (line,) = [f for f in findings if f.startswith("  aggregate:")]
        assert "NOT judged: the lane set changed" in line
        assert not regressed
        # Not a load verdict: it must not turn into CONDITIONS DIFFER either.
        assert seen["withheld"] == []

    def test_an_added_lane_leaves_the_lane_it_joined_unjudged(self):
        # A lane row is its rate beside every other lane: the NPU lane ran
        # 22.9 tok/s alone and 8.8 beside the CPU lane (v0.6.1, the
        # concurrency table in docs/geniex-v0.7.0-cpu-npu-2026-09-24.md). An
        # added lane read as the NPU runtime going 62 % SLOWER.
        old = normalise(lanes_report({"geniex-npu": NPU}, tok=22.9))
        new = normalise(lanes_report({"geniex-npu": NPU, "geniex-cpu": CPU}, tok=8.8))
        seen = {}
        findings, regressed = compare(old, new, seen=seen)
        (line,) = [f for f in findings if f.startswith("  geniex-npu:")]
        assert "NOT judged: the lane set changed" in line
        assert not regressed and seen["withheld"] == []

    def test_with_every_rate_unjudged_nothing_was_compared(self):
        # Exit 0 would read "compared, nothing regressed" with no tok/s judged.
        old = lanes_report({"geniex-npu": NPU, "geniex-cpu": CPU})
        new = lanes_report({"geniex-npu": NPU})
        seen = {}
        compare(normalise(old), normalise(new), seen=seen)
        assert seen["compared"] == 0
        # The batching verdict is judged whatever the lanes did.
        for report in (old, new):
            report["reports"].append({"label": "batching", "serialised": False})
        compare(normalise(old), normalise(new), seen=seen)
        assert seen["compared"] == 1

    def test_the_same_lanes_still_judge_the_aggregate(self):
        old = normalise(lanes_report({"geniex-npu": NPU, "geniex-cpu": CPU}))
        new = normalise(lanes_report({"geniex-npu": NPU, "geniex-cpu": CPU}, tok=5.0))
        findings, regressed = compare(old, new)
        lines = [f for f in findings if f.startswith(("  aggregate:", "  geniex-"))]
        assert len(lines) == 3 and all("SLOWER" in f for f in lines) and regressed


class TestEachLaneRuntimeIsDiffed:
    """The notes provenance.compare() prints for the envelope, per lane."""

    def test_a_rebuilt_lane_is_named_and_an_unchanged_one_says_nothing(self):
        old = normalise(lanes_report({"geniex-npu": NPU, "geniex-cpu": CPU}))
        rebuilt = _geniex("cpu", cli="v0.7.1", llama_cpp="5aa0001")
        new = normalise(lanes_report({"geniex-npu": NPU, "geniex-cpu": rebuilt}))
        findings, regressed = compare(old, new)
        (line,) = _lane_lines(findings)
        assert line.startswith("! lane geniex-cpu: SERVING RUNTIME CHANGED")
        # Old build first: read the other way round, an upgrade is a downgrade.
        before, after = line.split(" → ")
        assert "v0.7.0" in before and "v0.7.1" in after
        # Evidence about the measurement, not a verdict on the model.
        assert not regressed

    def test_other_serve_flags_on_one_lane_are_named(self):
        old = normalise(lanes_report({"geniex-npu": NPU, "geniex-cpu": CPU}))
        relaunched = _geniex("cpu", nctx="4096")
        new = normalise(lanes_report({"geniex-npu": NPU, "geniex-cpu": relaunched}))
        (line,) = _lane_lines(compare(old, new)[0])
        assert line.startswith("! lane geniex-cpu: ")
        assert "different serve flags" in line
        # The old run's flags first, as the envelope's own note prints them.
        assert line.index("'16384'") < line.index("'4096'")

    def test_other_weights_behind_the_same_id_are_named(self):
        def cpu(size):
            return _geniex("cpu", files=[{"name": "q4_0.gguf", "size": size}])

        old = normalise(lanes_report({"geniex-npu": NPU, "geniex-cpu": cpu(1)}))
        new = normalise(lanes_report({"geniex-npu": NPU, "geniex-cpu": cpu(2)}))
        (line,) = _lane_lines(compare(old, new)[0])
        assert line.startswith("! lane geniex-cpu: MODEL FILES CHANGED")
        assert "q4_0.gguf" in line

    def test_identical_lanes_say_nothing(self):
        both = normalise(lanes_report({"geniex-npu": NPU, "geniex-cpu": CPU}))
        assert _lane_lines(compare(both, both)[0]) == []

    def test_an_unreadable_runtime_is_no_evidence_of_a_rebuild(self):
        # lanes.py records {"error": ...} for a lane it could not read.
        # Compared as a build, it read as SERVING RUNTIME CHANGED -- "? ?".
        failed = {"error": "OSError: lane process not readable"}
        old = normalise(lanes_report({"geniex-npu": NPU, "geniex-cpu": failed}))
        new = normalise(lanes_report({"geniex-npu": NPU, "geniex-cpu": CPU}))
        (line,) = _lane_lines(compare(old, new)[0])
        assert line.startswith("! lane geniex-cpu: runtime recorded on one side only")


class TestALaneOnOneSideOnly:
    """The aggregate row then covers another set of lanes."""

    def test_a_lane_gone_from_the_new_run_is_a_finding(self):
        old = normalise(lanes_report({"geniex-npu": NPU, "geniex-cpu": CPU}))
        new = normalise(lanes_report({"geniex-npu": NPU}))
        (line,) = _lane_lines(compare(old, new)[0])
        assert line.startswith("! lane geniex-cpu: in the old run only")
        assert "aggregate" in line and "not like-for-like" in line

    def test_a_lane_new_in_this_run_is_a_finding(self):
        old = normalise(lanes_report({"geniex-npu": NPU}))
        new = normalise(lanes_report({"geniex-npu": NPU, "geniex-gpu": _geniex("gpu")}))
        (line,) = _lane_lines(compare(old, new)[0])
        assert line.startswith("! lane geniex-gpu: in the new run only")


class TestReportsOlderThanTheField:
    """The tracked lanes reports of 2026-09-23 carry no runtime on their rows;
    each recorded its FIRST lane's in the provenance block."""

    def test_the_first_lane_takes_the_provenance_runtime(self):
        # v070r3: the NPU lane relaunched with --log none, the CPU lane not
        # recorded at all. The flag change belongs to the NPU lane; the CPU
        # lane, unrecorded on both sides, has nothing to say.
        old = load(os.path.join(TRACKED, "v070r3-lanes-npuloginfo.json"))
        new = load(os.path.join(TRACKED, "v070r3-lanes-npulognone.json"))
        lines = _lane_lines(compare(old, new)[0])
        assert len(lines) == 1, lines
        assert lines[0].startswith("! lane geniex-npu: lane launched with different")
        # --log info was the old run: its flags come first.
        assert lines[0].index("'info'") < lines[0].index("'none'")

    def test_an_old_report_against_a_new_one_diffs_what_both_recorded(self):
        path = os.path.join(TRACKED, "v070r3-lanes-npulognone.json")
        with open(path) as f:
            raw = json.load(f)
        old = normalise(raw)
        npu = raw["provenance"]["runtime"]
        for row in raw["reports"]:
            if row["label"] == "geniex-npu":
                row["runtime"] = npu
            elif row["label"] == "geniex-cpu":
                row["runtime"] = CPU
        (line,) = _lane_lines(compare(old, normalise(raw))[0])
        assert line.startswith("! lane geniex-cpu: runtime recorded on one side only")

    def test_the_block_is_the_batching_endpoints_under_batching(self):
        # --batching --lanes collects the block for the batching endpoint
        # (lanes.main), not the first lane: a first-lane rule handed the NPU
        # lane the CPU lane's build.
        lanes = {"geniex-npu": (NPU_URL, "npu-model"), "geniex-cpu": (CPU_URL, "cpu")}
        run = {
            "lanes": {name: {"decode_tok_per_sec": 1.0} for name in lanes},
            "baseline": {},
            "aggregate_tok_per_sec": 2.0,
            "wall_s": 1.0,
        }
        rows = build_reports({"serialised": False}, (CPU_URL, "cpu"), run, lanes)
        for row in rows:
            row.pop("runtime", None)  # written before the field existed
        prov = {"base_url": CPU_URL, "runtime": CPU}
        assert lane_runtimes(rows, prov) == {"geniex-npu": None, "geniex-cpu": CPU}

    def test_a_lane_nobody_attributed_is_not_given_the_envelopes_runtime(self):
        # runtime None on a row is the tool's answer, not a missing field:
        # the provenance block describes that URL at another time.
        rows = lanes_report({"geniex-npu": None})["reports"]
        prov = {"base_url": NPU_URL, "runtime": NPU}
        assert lane_runtimes(rows, prov) == {"geniex-npu": None}


class TestOnlyLaneRowsAreLanes:
    def test_the_aggregate_and_batching_rows_are_no_lane(self):
        report = lanes_report({"geniex-npu": NPU})
        report["reports"].append({"label": "batching", "serialised": False})
        assert normalise(report)["lane_runtimes"] == {"geniex-npu": NPU}

    def test_other_benchmarks_have_no_lane_runtimes(self):
        tools = {
            "benchmark": "bench_tools",
            "provenance": {"runtime": NPU},
            "config": {},
            "reports": [{"label": "m", "passed": 1, "total": 1}],
        }
        assert normalise(tools)["lane_runtimes"] == {}
        assert _lane_lines(compare(normalise(tools), normalise(tools))[0]) == []
