#!/usr/bin/env python3
"""Diff two benchmark reports and fail on a regression.

Every tool here writes a report and, until this existed, nothing read two of
them. That made each measurement a one-off: a model swap, a runtime bump or an
edit to the grader could cost accuracy or speed and nobody would know.

Four things it refuses to do, because each is a way to be confidently wrong:

  * call a difference a regression when the sample cannot support it — both
    models answered the SAME cases, so the aggregate is judged by a paired
    sign test on per-case outcomes (interval overlap only for reports without
    per-case detail), and a single-draw flip on a sampling lane is named as
    such, not alarmed;
  * blame the model when the *grader* moved — provenance carries a hash of the
    benchmark's own source, and a mismatch is stated before any score;
  * judge a speed or timing change when a run started on a busy host or the
    two under different load — that verdict is withheld and the exit is 4,
    CONDITIONS DIFFER (compare_verdict.py), unless --allow-load-difference;
  * compare across hosts or architectures silently.

Usage:
    python3 bench_compare.py old.json new.json
    python3 bench_compare.py --baseline geniex-npu new.json      # vs stored
    python3 bench_compare.py --save-baseline geniex-npu new.json
    python3 bench_compare.py --allow-load-difference old.json new.json
"""

import argparse
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# Standalone runs of these scripts (they are not a package) need the repo
# root on sys.path; the runner lives in orchestrant.benchmark.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from bench_variants import variant_report_fields, variant_spread  # noqa: E402
from compare_lanes import lane_findings, lane_runtimes, lane_set_changed  # noqa: E402
from compare_speed import speed_findings  # noqa: E402
from compare_verdict import CONDITIONS_DIFFER, NOT_COMPARED, LoadGate  # noqa: E402
from compare_verdict import exit_code, withheld_lines  # noqa: E402

from orchestrant.benchmark.client import utf8_stdio  # noqa: E402
from orchestrant.benchmark.provenance import compare as compare_provenance  # noqa: E402
from orchestrant.benchmark.provenance import known_deterministic  # noqa: E402
from orchestrant.benchmark.stats import (  # noqa: E402
    ALPHA,
    back_flip_estimate,
    clustered_note,
    diff_interval,
    format_score,
    intervals_overlap,
    paired_diff_note,
    paired_mde,
    paired_mde_note,
    paired_outcomes,
    paired_power_note,
    paired_sign_test,
    pass_k_note,
    power_note,
)

BASELINE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baselines")

# A timing change under this is treated as noise rather than a regression.
DEFAULT_TIME_TOLERANCE = 0.25


def _legacy_provenance(report):
    """A speed report's provenance block, else its hardware dict (older reports)."""
    # The block exists since 2026-09-24; the hardware dict names no runtime.
    return report.get("provenance") or report.get("hardware", {})


def normalise(report):
    """Bring either report shape into one form.

    The suite emits two envelopes: the newer tools write
    {benchmark, provenance, config, reports:[...]} while
    orchestrant.benchmark.openai_api writes {timestamp, model, api_url, hardware, config, results:[...]}. Rather
    than break the viewer that reads the older one, adapt here — and record the
    divergence as a debt (roadmap P4b.2, still open for the legacy shape)
    instead of hiding it behind this function.
    """
    if not isinstance(report, dict) or not ("reports" in report or "results" in report):
        raise ValueError(
            "not a benchmark report: neither 'reports' (shared envelope) "
            "nor 'results' (orchestrant.benchmark.openai_api) is present"
        )
    if "reports" in report:
        prov = report.get("provenance") or {}
        entries = []
        for r in report["reports"]:
            # Per-case outcomes matter more than the aggregate on a
            # deterministic endpoint: a case that flipped is a concrete,
            # attributable change, where the proportion may not move enough to
            # clear a confidence interval.
            cases, walls = {}, []
            for item in r.get("results", []):
                key = item.get("case") or item.get("task")
                if key is None or not measured(item):
                    continue  # a transport failure says nothing about the model
                cases.setdefault(key, []).append(bool(item.get("passed")))
                if isinstance(item.get("wall_s"), (int, float)):
                    walls.append(item["wall_s"])
            label = r.get("label") or r.get("model")
            if any(e["label"] == label for e in entries):
                raise ValueError(
                    f"duplicate label {label!r}: two candidates resolved to the same "
                    f"name, so their scores cannot be told apart — give each entry in "
                    f"the --compare file a distinct 'label' (e.g. backend:model)"
                )
            probe = r.get("determinism_probe")
            if probe is None and r.get("base_url") in (None, prov.get("base_url")):
                probe = prov.get("determinism_probe")
            entries.append(
                {
                    "label": label,
                    "model": r.get("model"),
                    "passed": r.get("passed"),
                    "total": r.get("total"),
                    "wall_s": r.get("total_wall_s"),
                    "median_wall_s": r.get("median_wall_s"),
                    # Wall over measured attempts only, when the tool recorded it;
                    # else derived from the rows; else None (legacy report).
                    "wall_measured_s": r.get("wall_measured_s"),
                    "unmeasured_wall_s": r.get("unmeasured_wall_s"),
                    "measured_walls": walls,
                    "effective_n": r.get("effective_n"),
                    # A count of tasks observed to pass; absent in older reports.
                    "effective_k": r.get("effective_k"),
                    "deterministic": r.get("deterministic"),
                    "repeats": r.get(
                        "repeats", (report.get("config") or {}).get("repeats")
                    ),
                    "probe_deterministic": known_deterministic(
                        {"determinism_probe": probe}
                    ),
                    # Counts, not a bool. Collapsing repeats with all() was wrong in
                    # BOTH directions on a sampling lane: 3/3 -> 2/3 read as a hard
                    # regression off one flaky draw, while a real 2/3 -> 0/3 collapse
                    # produced nothing at all (False -> False is neither broke nor
                    # fixed) and the aggregate was too small to clear the interval.
                    "cases": {k: (sum(v), len(v)) for k, v in cases.items()},
                    # bench_lanes rows: throughput, not pass/fail. An aggregate
                    # row is delivered throughput since 2026-09-24, a sum before.
                    "tok_per_sec": r.get("tok_per_sec"),
                    "delivered": r.get("summed_tok_per_sec") is not None,
                    "summed_tok_per_sec": r.get(
                        "summed_tok_per_sec", r.get("tok_per_sec")
                    ),
                    "serialised": r.get("serialised"),
                }
            )
        return {
            "benchmark": report.get("benchmark", "unknown"),
            "provenance": prov,
            "config": report.get("config", {}),
            "suspect_cases": suspect_cases(report["reports"]),
            "lane_runtimes": lane_runtimes(report["reports"], prov),
            "entries": entries,
        }

    # older shape: one model, scores live in per-prompt results
    results = report.get("results", [])
    ok = [r for r in results if "error" not in r]
    walls = [r.get("latency_s") for r in ok if r.get("latency_s") is not None]
    speed = {r["prompt_index"]: r for r in ok if "prompt_index" in r}
    correctness = report.get("correctness") or {}
    return {
        "benchmark": "orchestrant.benchmark.openai_api",
        "provenance": _legacy_provenance(report),
        "config": report.get("config", {}),
        "entries": [
            {
                "label": report.get("model"),
                "model": report.get("model"),
                # Only the correctness probe is a score; throughput is not pass/fail.
                "passed": correctness.get("score"),
                "total": correctness.get("total"),
                "wall_s": round(sum(walls), 2) if walls else None,
                "median_wall_s": None,
                # No latency verdict: a speed report's wall moves with
                # max_tokens and includes replies cut at the cap, and
                # compare_speed judges its rates per prompt instead.
                "timing": False,
                "effective_n": correctness.get("total"),
                "deterministic": None,
                # prompt_index -> row: decode, prefill, TTFT, load, energy.
                "speed": speed,
                "ncpu": (report.get("hardware") or {}).get("cpu_total_threads"),
            }
        ],
    }


def measured(item):
    """Did this result row measure the model? Transport errors, cut outputs and
    context-blocked agent tasks (status CONTEXT: the prompt never fit) did not.

    Nor did an overflow (the 4xx that says the prompt did not fit) or a task
    skipped because the tool that grades its language is not on this host: the
    producers exclude both from their own rates, and counting them here made a
    row that nobody graded look like a row the model failed.
    """
    return not (
        item.get("errored")
        or item.get("truncated")
        or item.get("blocked")
        or item.get("overflow")
        or item.get("skipped")
        or item.get("status") == "CONTEXT"
    )


def load(path):
    with open(path) as f:
        try:
            return normalise(json.load(f))
        except ValueError as e:
            raise SystemExit(f"{path}: {e}") from e


def _per_attempt(entry):
    """Seconds per MEASURED attempt, and where the number came from.

    A cut or abandoned attempt can sit at the 1800 s deadline; letting it into
    the mean decided the SLOWER verdict for the wrong reason. Order: the tool's
    own wall_measured_s, then the per-row walls, then the legacy total (noted).
    """
    n = entry.get("total")
    if entry.get("timing") is False:
        return None, None
    if entry.get("wall_measured_s") is not None and n:
        return entry["wall_measured_s"] / n, "measured"
    walls = entry.get("measured_walls") or []
    if walls:
        return sum(walls) / len(walls), "measured"
    if entry.get("median_wall_s"):
        return entry["median_wall_s"], "legacy"
    if entry.get("wall_s") and n:
        return entry["wall_s"] / n, "legacy"
    return None, None


def _stable_config(cfg):
    """The config minus the self-check's run time and the host's process
    ceiling: every coding pair warned "not like-for-like" on those alone."""
    cfg = dict(cfg or {})
    check = cfg.get("grader_selfcheck")
    if isinstance(check, dict):
        check = {k: v for k, v in check.items() if k != "seconds"}
        if isinstance(check.get("rlimits"), dict):
            check["rlimits"] = {
                k: v for k, v in check["rlimits"].items() if k != "nproc_ceiling"
            }
        cfg["grader_selfcheck"] = check
    return cfg


def _tps_pair(a, b):
    """Lane throughput measured the same way on both sides: delivered where
    both reports carry it, the old sum of per-lane rates where one does not."""
    if a.get("delivered") != b.get("delivered"):
        return a.get("summed_tok_per_sec"), b.get("summed_tok_per_sec")
    return a.get("tok_per_sec"), b.get("tok_per_sec")


def _throughput_line(label, a, b, gate, tolerance, lanes_moved):
    """(line, slower) for bench_lanes throughput; (None, False) without it.

    A lane row is its rate beside every other lane (the NPU lane lost 46-87 %
    beside the CPU lane), the aggregate their sum: when the lane set changed no
    tok/s is like-for-like, and a SLOWER would blame the runtime for a lane
    that came or went. Reported, not judged; neither regresses nor withholds.
    """
    a_tps, b_tps = _tps_pair(a, b)
    if not a_tps or b_tps is None:
        return None, False
    delta = (b_tps - a_tps) / a_tps
    head = f"  {label}: {a_tps:.1f} -> {b_tps:.1f} tok/s ({delta:+.0%})"
    if lanes_moved:
        return f"{head}   NOT judged: the lane set changed (! lane lines above)", False
    mark, slower = gate.judge(label, "tok/s", -delta, tolerance)
    return head + mark, slower


def _comparable(a, b):
    """Is there any score, timing, throughput or speed metric both sides have?"""
    return bool(
        (a.get("total") and b.get("total"))
        or (_per_attempt(a)[0] and _per_attempt(b)[0])
        or (a.get("tok_per_sec") and b.get("tok_per_sec") is not None)
        or set(a.get("speed") or {}) & set(b.get("speed") or {})
    )


def compare(
    old,
    new,
    time_tolerance=DEFAULT_TIME_TOLERANCE,
    seen=None,
    allow_load_difference=False,
):
    """Returns (findings, regressed). `findings` is a list of printable lines.

    `seen`, when a dict, receives "compared": how many labels shared anything
    comparable -- zero means the verdict is "nothing compared", not "fine" --
    "paired": (label, cases, back-flips) per paired sign test, and "withheld":
    the verdicts the load gate held back, which `allow_load_difference` judges.
    """
    findings = []
    regressed = False
    if seen is not None:
        seen.update(compared=0, paired=[], withheld=[])

    if old["benchmark"] != new["benchmark"]:
        findings.append(
            f"! different benchmarks: {old['benchmark']} vs {new['benchmark']}"
        )
        return findings, True

    if new["benchmark"] == "bench_contract":
        # Its answers are neither scores nor timings: every pair used to print
        # "no regression detected" while `contract --diff` showed two moves.
        findings.append(
            "? contract reports are compared answer by answer: "
            "orchestrant-bench contract --diff OLD NEW"
        )
        return findings, False

    for note in compare_provenance(
        old.get("provenance", {}), new.get("provenance", {})
    ):
        findings.append(f"! {note}")
    findings += lane_findings(old, new)
    gate = LoadGate(old, new, allow_load_difference, seen)

    # The config was recorded and never read. Dropping --system, or changing
    # --repeats, changes what the numbers MEAN — and used to surface as the
    # model regressing.
    old_cfg, new_cfg = (
        _stable_config(old.get("config")),
        _stable_config(new.get("config")),
    )
    changed_cfg = sorted(
        k for k in set(old_cfg) | set(new_cfg) if old_cfg.get(k) != new_cfg.get(k)
    )
    for key in changed_cfg:
        findings.append(
            f"! config.{key} changed: {old_cfg.get(key)!r} -> "
            f"{new_cfg.get(key)!r} — the runs are not like-for-like"
        )
    # `repeats` scales total_wall_s linearly, so comparing raw totals across a
    # repeats change reports a slowdown for doing more work.
    timing_comparable = old_cfg.get("repeats") == new_cfg.get("repeats")

    suspect = set(new.get("suspect_cases") or old.get("suspect_cases") or ())
    if suspect:
        findings.append(
            f"! {len(suspect)} case(s) the CONTROL also fails — suspect "
            f"cases, evidence about the case not the candidates: "
            f"{', '.join(sorted(suspect))}"
        )

    old_by = {e["label"]: e for e in old["entries"]}
    new_by = {e["label"]: e for e in new["entries"]}

    for label in sorted(set(old_by) - set(new_by)):
        findings.append(f"- {label}: present in the old run, missing from the new one")
    for label in sorted(set(new_by) - set(old_by)):
        findings.append(f"+ {label}: new, no baseline to compare against")

    if seen is not None:
        seen["compared"] = sum(
            _comparable(old_by[k], new_by[k]) for k in set(old_by) & set(new_by)
        )
    for label in sorted(set(old_by) & set(new_by)):
        a, b = old_by[label], new_by[label]
        speed_lines, speed_regressed = speed_findings(label, a, b, gate)
        findings += speed_lines
        regressed = regressed or speed_regressed

        # --- per-case diff, which needs no statistics to be meaningful
        a_cases, b_cases = a.get("cases") or {}, b.get("cases") or {}
        shared = set(a_cases) & set(b_cases)
        # A single flaky draw must not fire the alarm on a sampling lane, and a
        # total collapse must not hide there either. Strict flip on a
        # deterministic endpoint; "passed before, never passes now" otherwise.
        strict = _known_deterministic(a) and _known_deterministic(b)
        # One attempt per case on a lane nobody has shown deterministic: a flip
        # is one coin toss. Named, not alarmed; the paired test judges the run.
        single_draw = (
            not strict
            and shared
            and all(a_cases[k][1] == 1 and b_cases[k][1] == 1 for k in shared)
        )

        def _rate(pair):
            passes, attempts = pair
            return (passes / attempts) if attempts else 0.0

        def _broke(k):
            # "Passed at least once before, never passes now." On a
            # deterministic lane (or repeats=1) that is exactly a flip; on a
            # sampling lane it is the only per-case claim the data supports,
            # since one unlucky draw is not evidence.
            ap, bp = a_cases[k], b_cases[k]
            return ap[0] > 0 and bp[0] == 0

        def _fixed(k):
            ap, bp = a_cases[k], b_cases[k]
            return ap[0] == 0 and bp[0] > 0

        broke = sorted(k for k in shared if _broke(k))
        fixed = sorted(k for k in shared if _fixed(k))
        degraded = sorted(
            k
            for k in shared
            if k not in broke and _rate(b_cases[k]) < _rate(a_cases[k])
        )
        if broke and single_draw:
            findings.append(
                f"  {label}: {len(broke)} case(s) flipped (single draw — "
                f"rerun with --repeats 3): {', '.join(broke)}"
            )
        elif broke:
            findings.append(
                f"  {label}: {len(broke)} case(s) that PASSED now fail — "
                f"*** REGRESSION ***"
            )
            for k in broke:
                findings.append(
                    f"      broke: {k}"
                    + (" (suspect: the control fails it too)" if k in suspect else "")
                )
            regressed = True
        if fixed:
            findings.append(
                f"  {label}: {len(fixed)} case(s) now fixed: {', '.join(fixed)}"
            )
        if degraded and not strict:
            # Reported, not alarmed: a lower pass RATE on a sampling lane is a
            # signal worth seeing but not proof on its own.
            findings.append(
                f"  {label}: {len(degraded)} case(s) pass less often "
                f"(sampling lane, not treated as a regression): "
                f"{', '.join(degraded)}"
            )

        if a.get("total") and b.get("total"):
            a_rate = a["passed"] / a["total"]
            b_rate = b["passed"] / b["total"]
            # Intervals on the EFFECTIVE sample. Repeats on a deterministic
            # endpoint return the identical answer, so counting them as
            # independent trials manufactures significance that is not there.
            a_n = a.get("effective_n") or a["total"]
            b_n = b.get("effective_n") or b["total"]
            # Counts of something observed, never a rounded ratio: rounding
            # printed 8/9 for seven passing tasks and "improved" for no change.
            # Reports written before effective_k existed carry only the ratio;
            # for those the rounding is the only option left, clamped to n.
            a_k = a.get("effective_k")
            if a_k is None:
                a_k = min(a_n, round(a_rate * a_n))
            b_k = b.get("effective_k")
            if b_k is None:
                b_k = min(b_n, round(b_rate * b_n))
            line = (
                f"  {label}: {_score(a_k, a_n, a, suspect)} -> "
                f"{_score(b_k, b_n, b, suspect)}   "
                + _diff_text(a_cases, b_cases, (a_k, a_n, b_k, b_n), b_rate - a_rate)
            )
            if shared:
                # Paired: only the cases that disagree carry information, and
                # 6-0 is p=0.031 where overlapping intervals say "cannot tell".
                worse, better, ties = paired_outcomes(a_cases, b_cases)
                _note_pairing(seen, label, worse + better + ties, better)
                p = paired_sign_test(worse, better)
                verdict = f"paired sign test {worse} worse / {better} better, p={p:.3f}"
                if worse > better and p < ALPHA:
                    findings.append(f"{line}   *** REGRESSION *** ({verdict})")
                    regressed = True
                elif worse > better:
                    findings.append(f"{line}   lower; not separable ({verdict})")
                elif better > worse:
                    sep = " (separable)" if p < ALPHA else ""
                    findings.append(f"{line}   improved{sep} ({verdict})")
                else:
                    mde = paired_mde_note(worse + better + ties, better)
                    findings += [f"{line}   unchanged ({verdict})", f"  {label}: {mde}"]
            elif b_rate < a_rate:
                if intervals_overlap(a_k, a_n, b_k, b_n):
                    findings.append(
                        line + "   lower; the AGGREGATE is not separable "
                        "at this sample size (unpaired — no per-case detail)"
                    )
                else:
                    findings.append(line + "   *** REGRESSION ***")
                    regressed = True
            elif b_rate > a_rate:
                sep = "" if intervals_overlap(a_k, a_n, b_k, b_n) else " (separable)"
                findings.append(line + f"   improved{sep}")
            else:
                findings.append(line + "   unchanged")
        findings += _pass_k_lines(label, a, b, suspect)

        # Per-attempt time, not the sum: a run that errored out half its
        # requests summed less wall time and was reported as FASTER.
        (a_time, a_src), (b_time, b_src) = _per_attempt(a), _per_attempt(b)
        if a_time and b_time and timing_comparable:
            delta = (b_time - a_time) / a_time
            mark, slower = gate.judge(label, "per-attempt time", delta, time_tolerance)
            regressed = regressed or slower
            if "legacy" in (a_src, b_src):
                mark += "   (legacy timing: may include cut or blocked attempts)"
            findings.append(
                f"  {label}: {a_time:.2f}s -> {b_time:.2f}s per attempt "
                f"({delta:+.0%}){mark}"
            )
        elif a_time and b_time:
            findings.append(f"  {label}: timing not compared — config differs")

        # --- bench_lanes: throughput and the batching verdict, no pass/fail
        line, slower = _throughput_line(
            label, a, b, gate, time_tolerance, lane_set_changed(old, new)
        )
        if line:
            findings.append(line)
            regressed = regressed or slower
        if a.get("serialised") is False and b.get("serialised") is True:
            findings.append(
                f"  {label}: overlapped concurrent requests before, now "
                f"SERIALISES them — *** REGRESSION ***"
            )
            regressed = True
        elif a.get("serialised") is True and b.get("serialised") is False:
            findings.append(f"  {label}: now overlaps concurrent requests (batching)")

    return findings, regressed


def case_outcomes(report):
    """{case: (passes, attempts)} over the measured, non-suspect rows.

    The shape `bench_stats.tiers`/`paired_outcomes` want, built from a RAW
    report row the way normalise() builds it from a stored one.
    """
    cases = {}
    for item in report.get("results", []):
        key = _case_key(item)
        if key is None or not measured(item) or item.get("suspect"):
            continue
        passes, attempts = cases.get(key, (0, 0))
        cases[key] = (passes + int(bool(item.get("passed"))), attempts + 1)
    return cases


def _known_deterministic(entry):
    return bool(entry.get("deterministic")) or bool(entry.get("probe_deterministic"))


def _scored_cases(entry, suspect):
    """The cases behind the headline score: suspect ones leave a candidate's
    score (mark_suspect_cases) but not the control's, whose total keeps them."""
    cases = entry.get("cases") or {}
    kept = {k: v for k, v in cases.items() if k not in suspect}
    if sum(m for _, m in kept.values()) == entry.get("total"):
        return kept
    return cases if sum(m for _, m in cases.values()) == entry.get("total") else kept


def _score(k, n, entry, suspect):
    """format_score, plus the case-clustered interval when repeats disagree."""
    return format_score(k, n) + clustered_note(_scored_cases(entry, suspect))


def _diff_text(a_cases, b_cases, counts, rate_diff):
    """Paired over the shared cases when both sides carry them (two identical
    runs of 98/124 read +/-10 pt unpaired, [+0, +0] paired), else Newcombe."""
    lo, hi = diff_interval(*counts)
    return paired_diff_note(a_cases, b_cases) or (
        f"diff {100 * rate_diff:+.0f}pt [{100 * lo:+.0f}, {100 * hi:+.0f}]"
    )


def _note_pairing(seen, label, n_cases, back_flips):
    """Record a paired comparison for the closing minimum-detectable-drop line."""
    if seen is not None and n_cases:
        seen.setdefault("paired", []).append((label, n_cases, back_flips))


def _mde_lines(seen):
    """The weakest pairing's minimum detectable drop, after "no regression".

    Weakest by the drop itself, not by the case count: 42 cases with 6 flipping
    back miss 35 pt where 31 cases with none miss 33 pt. None -- no drop is
    caught at all -- is the weakest there is.
    """
    pairs = (seen or {}).get("paired") or []
    if len(pairs) < 2:
        return [paired_mde_note(n_cases, flips) for _, n_cases, flips in pairs]

    def missed(pair):
        mde = paired_mde(pair[1], back_flip_estimate(pair[1], pair[2])[0])
        return 2.0 if mde is None else mde

    label, n_cases, back_flips = max(pairs, key=missed)
    return [f"{label} (weakest pairing): {paired_mde_note(n_cases, back_flips)}"]


def _pass_k_lines(label, a, b, suspect):
    """pass^k when a sampling side drew one prompt more than once, at the
    smaller side's draws; a deterministic side repeats one answer.

    The draws are the producer's `repeats`, not a case's attempts: under
    --prompt-variants a case holds a row per paraphrase, and one draw of three
    paraphrases printed "pass^3" for a run that repeated nothing. The most
    attempts of any case stand in only for a report that records no repeats.
    """
    a_cases, b_cases = _scored_cases(a, suspect), _scored_cases(b, suspect)
    draws = [
        entry.get("repeats") or max(m for _, m in cases.values())
        for entry, cases in ((a, a_cases), (b, b_cases))
        if cases and not _known_deterministic(entry)
    ]
    k = min((d for d in draws if d > 1), default=None)
    return [f"  {label}: {pass_k_note(a_cases, b_cases, k)}"] if k else []


def is_control(report):
    """The calibration entry: backend 'control' in backends.json, or a label
    starting with 'control' (the label falls back to the model id otherwise)."""
    return report.get("backend") == "control" or str(
        report.get("label") or ""
    ).lower().startswith("control")


def suspect_cases(reports):
    """Cases the CONTROL endpoint also failed.

    Without a calibration point, "every model failed this" reads as a hard case
    when it may be a broken one — a contradictory assertion, an ambiguous
    prompt, a tool description nobody could disambiguate. A case the control
    fails is evidence about the CASE, not about the candidates.

    Contract: `reports` is the envelope's reports[] list (raw rows, as the
    tools emit and as a ranking holds them); each row may carry `backend`,
    `label` and `results` [{case|task, passed, errored, truncated, status}].
    Returns the sorted case keys the control measured and failed, or None when
    no row is a control. Rankings call this to mark those cases.
    """
    control = next((r for r in reports if is_control(r)), None)
    if not control:
        return None
    # Aggregated over the control's own attempts: one flaky draw out of three
    # is not evidence that the case is broken, and used to delete it anyway.
    outcomes = {}
    for item in control.get("results", []):
        key = item.get("case") or item.get("task")
        if key is not None and measured(item):
            outcomes.setdefault(key, []).append(bool(item.get("passed")))
    return sorted(k for k, v in outcomes.items() if v and not any(v))


def _case_key(item):
    """The case identity in a result row: bench_tools writes `case`, bench_coding
    and bench_agent write `task`."""
    return item.get("case") if item.get("case") is not None else item.get("task")


def mark_suspect_cases(reports):
    """Exclude the cases the control also fails from every OTHER row's score.

    Mutates `reports` in place and returns the sorted suspect keys, or None
    when no control ran. A case the control fails says the case is broken, so
    scoring a candidate on it charges the candidate for a bad prompt: the rows
    stay in the report, flagged `suspect`, and leave the rate, the interval and
    the rank. The control's own row keeps its full score -- it is the
    calibration, not a competitor.

    Determinism is not re-derived: `deterministic` was decided from the output
    hashes over every attempt, and dropping a case cannot make a sampling lane
    deterministic. Everything else derived from the rows IS re-derived, so no
    table in the same object can disagree with the headline -- the paraphrase
    spread of a --prompt-variants run included (_recount_sample).
    """
    suspect = suspect_cases(reports)
    if not suspect:
        # None (no control ran) and [] (a control that failed nothing) are both
        # "nothing to exclude"; the caller tells them apart by the return value.
        return suspect
    dropped = set(suspect)
    for report in reports:
        report["suspect_cases"] = list(suspect)
        for item in report.get("results", []):
            if _case_key(item) in dropped:
                item["suspect"] = True
        if is_control(report):
            report["suspect_excluded"] = 0
            continue
        rows = report.get("results") or []
        kept = [r for r in rows if measured(r) and _case_key(r) not in dropped]
        report["suspect_excluded"] = sum(
            1 for r in rows if measured(r) and _case_key(r) in dropped
        )
        passed = sum(1 for r in kept if r.get("passed"))
        report["passed"], report["total"] = passed, len(kept)
        if "wrong" in report:
            report["wrong"] = len(kept) - passed
        report["effective_n"], report["effective_k"] = _recount_sample(report, kept)
        _recount_groups(report, rows, kept, dropped)
    return suspect


def _recount_sample(report, kept):
    """(effective_n, effective_k) over the KEPT rows, by the producers' rule.

    Identical replies, or repeats that agree on pass/fail, make the case the
    unit -- and dropping cases cannot make agreeing repeats disagree. Under
    --prompt-variants (`variant_case_count` set) a case's paraphrases are ONE
    observation, seen through the prompt as written, and the spread loses the
    suspect cases as the rate does. That rule is bench_variants'; this used to
    recount per (case, variant) instead, and bench_tools and bench_coding each
    repaired the number with a second call straight after this one.
    """
    collapse = bool(report.get("deterministic") or report.get("repeats_agreed"))
    if report.get("variant_case_count"):
        # One key for variant_spread: bench_tools rows carry `case`, the
        # others `task` (_case_key).
        rows = [{**r, "case": _case_key(r)} for r in kept]
        summary = variant_spread(rows, "case", collapse)
        report.update(variant_report_fields(summary))
        return summary["effective_n"], summary["effective_k"]
    if not collapse:
        return len(kept), sum(1 for r in kept if r.get("passed"))
    outcomes = {}
    for r in kept:
        outcomes.setdefault((_case_key(r), r.get("variant")), set()).add(
            bool(r.get("passed"))
        )
    return len(outcomes), sum(1 for v in outcomes.values() if v == {True})


def _recount_groups(report, rows, kept, dropped):
    """Re-derive the group tables and the wall statistics from the KEPT rows.

    Left stale, `by_kind`/`by_lang`/`categories` contradicted the headline in
    the same object (3/3 = 100% beside python=3/6) and suspect seconds still
    decided the rank tiebreak.
    """
    for field, group_key in (("by_kind", "kind"), ("by_lang", "lang")):
        if field not in report:
            continue
        groups = {}
        for r in rows:
            if _case_key(r) in dropped:
                continue
            g = groups.setdefault(
                r.get(group_key) or "unknown",
                {"passed": 0, "measured": 0, "skipped": 0, "excluded": 0},
            )
            if r.get("skipped"):
                g["skipped"] += 1
            elif not measured(r):
                g["excluded"] += 1
            else:
                g["measured"] += 1
                g["passed"] += int(bool(r.get("passed")))
        report[field] = dict(sorted(groups.items()))
    if "categories" in report:
        cats = {}
        for r in kept:
            c = cats.setdefault(r.get("category", "unknown"), {"passed": 0, "total": 0})
            c["total"] += 1
            c["passed"] += int(bool(r.get("passed")))
        report["categories"] = cats
    _recount_walls(report, kept)


def _recount_walls(report, kept):
    """Every wall statistic the producer wrote, over the KEPT rows only.

    wall_measured_s was left at its pre-exclusion value while total fell, so
    the timing verdict divided a suspect case's seconds by fewer attempts;
    median_wall_s was only redone when total_wall_s was present. Fields the
    producer did not write stay absent.
    """
    walls = [r["wall_s"] for r in kept if isinstance(r.get("wall_s"), (int, float))]
    derived = {
        "total_wall_s": round(sum(walls), 2),
        "wall_measured_s": round(sum(walls), 2),
        "avg_wall_s": round(sum(walls) / len(walls), 2) if walls else None,
        "median_wall_s": round(statistics.median(walls), 2) if walls else None,
        "stdev_wall_s": round(statistics.stdev(walls), 2) if len(walls) > 1 else None,
    }
    for field, value in derived.items():
        if field in report:
            report[field] = value


def pair_directories(old_dir, new_dir):
    """Match reports between two run directories by file name.

    Returns (pairs, only_new, only_old). `_manifest.json` is the viewer's index,
    not a report, so it never pairs.
    """

    def reports(d):
        return {
            f for f in os.listdir(d) if f.endswith(".json") and f != "_manifest.json"
        }

    o, n = reports(old_dir), reports(new_dir)
    pairs = sorted(
        (f, os.path.join(old_dir, f), os.path.join(new_dir, f)) for f in (o & n)
    )
    return pairs, sorted(n - o), sorted(o - n)


def baseline_path(name):
    return os.path.join(BASELINE_DIR, f"{name}.json")


def _compare_directories(args):
    """Compare two run directories report-by-report. Returns an exit code."""
    if len(args.reports) != 2:
        raise SystemExit("--dir takes exactly two directories: OLD NEW")
    old_dir, new_dir = args.reports
    for d in (old_dir, new_dir):
        if not os.path.isdir(d):
            raise SystemExit(f"not a directory: {d}")
    pairs, only_new, only_old = pair_directories(old_dir, new_dir)

    # A config that appeared or vanished between runs IS a change; staying quiet
    # about it would let the sweep shrink without the comparison noticing.
    for f in only_old:
        print(f"  ! {f}: in {old_dir} but not in {new_dir}")
    for f in only_new:
        print(f"  ! {f}: new in {new_dir}, nothing to compare against")
    if not pairs:
        raise SystemExit(f"no report names in common between {old_dir} and {new_dir}")

    # A Namespace built by hand (the tests, a script) may predate the flag.
    allow = getattr(args, "allow_load_difference", False)
    regressed_any, blind, differ = False, 0, 0
    for name, old_path, new_path in pairs:
        print(f"\n  {name}")
        seen = {}
        findings, regressed = compare(
            load(old_path), load(new_path), args.time_tolerance, seen, allow
        )
        for line in findings:
            print(f"    {line}")
        if regressed:
            print("    REGRESSION")
        elif seen["withheld"]:
            print("    CONDITIONS DIFFER -- nothing judged regressed")
        elif not seen.get("compared"):
            print("    NOTHING COMPARED -- no verdict")
            blind += 1
        else:
            print("    no regression detected")
            for note in _mde_lines(seen):
                print(f"    {note}")
        for line in withheld_lines(seen["withheld"]):
            print(f"    {line}")
        regressed_any = regressed_any or regressed
        differ += bool(seen["withheld"])

    print(
        f"\n  {len(pairs)} report(s) paired, {blind} with nothing to compare, "
        f"{differ} with a verdict withheld for load, "
        f"{len(only_old)} gone, {len(only_new)} new"
    )
    return exit_code(regressed_any, differ, blind < len(pairs))


def main():
    utf8_stdio()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "reports", nargs="+", help="old.json new.json, or just new.json with --baseline"
    )
    ap.add_argument(
        "--baseline", default=None, help="Compare against a stored baseline by name"
    )
    ap.add_argument(
        "--save-baseline",
        default=None,
        help="Store this report as the accepted baseline under that name",
    )
    ap.add_argument(
        "--time-tolerance",
        type=float,
        default=DEFAULT_TIME_TOLERANCE,
        help="Relative slowdown treated as noise (default 0.25)",
    )
    ap.add_argument(
        "--dir",
        action="store_true",
        help="Treat the two arguments as run DIRECTORIES and compare "
        "every report they share by name",
    )
    ap.add_argument(
        "--allow-load-difference",
        action="store_true",
        help="Judge speed and timing even when a run started on a busy host or "
        "the two under different load (default: withhold them and exit 4)",
    )
    args = ap.parse_args()

    if args.dir:
        sys.exit(_compare_directories(args))

    if args.save_baseline:
        os.makedirs(BASELINE_DIR, exist_ok=True)
        with (
            open(args.reports[-1]) as src,
            open(baseline_path(args.save_baseline), "w") as dst,
        ):
            dst.write(src.read())
        print(f"  Baseline '{args.save_baseline}' saved from {args.reports[-1]}")
        return

    if args.baseline:
        path = baseline_path(args.baseline)
        if not os.path.exists(path):
            raise SystemExit(
                f"no baseline named {args.baseline!r} in {BASELINE_DIR}. "
                f"Record one with --save-baseline {args.baseline} <report>"
            )
        old, new = load(path), load(args.reports[-1])
        old_name, new_name = f"baseline:{args.baseline}", args.reports[-1]
    else:
        if len(args.reports) < 2:
            ap.error("give two reports, or one report with --baseline")
        old, new = load(args.reports[0]), load(args.reports[1])
        old_name, new_name = args.reports[0], args.reports[1]

    print(f"\n  {old_name}\n  -> {new_name}\n")
    seen = {}
    findings, regressed = compare(
        old, new, args.time_tolerance, seen, args.allow_load_difference
    )
    for line in findings:
        print(f"  {line}")
    print()
    sys.exit(_verdict(new, regressed, seen))


def _verdict(new, regressed, seen):
    """Print the closing verdict; return the exit code (compare_verdict's order)."""
    withheld = seen.get("withheld")
    code = exit_code(regressed, withheld, seen.get("compared"))
    if regressed:
        print("  REGRESSION — see the lines marked ***")
    elif code == CONDITIONS_DIFFER:
        print(
            "  CONDITIONS DIFFER — nothing judged regressed, but the verdicts "
            "load can move were withheld"
        )
    elif code == NOT_COMPARED:
        # Exit 0 here read as "checked, fine" to every script that called it.
        print("  NOTHING COMPARED — the reports share no score, timing or speed metric")
    else:
        # "No regression" must not be mistaken for "nothing changed" when the
        # suite is too small to tell the difference.
        # Power on the EFFECTIVE sample, as the interval five lines up: on a
        # deterministic lane 9 tasks x 3 repeats is n=9, and "n=27 cannot
        # see a drop below 74%" was printed right after missing a 67% drop.
        sizes = [
            e.get("effective_n") or e["total"] for e in new["entries"] if e.get("total")
        ]
        has_cases = any(e.get("cases") for e in new["entries"])
        print("  no regression detected")
        if has_cases:
            # The floor says how few flips could ever be seen; the drop says
            # how large a real one slips through at this case count.
            for note in [paired_power_note(), *_mde_lines(seen)]:
                print(f"  {note}")
        elif sizes:
            print(f"  {power_note(min(sizes))}")
        if not has_cases:
            print(
                "  (no per-case detail in these reports — only the aggregate "
                "could be checked, which is the weaker test)"
            )
    for line in withheld_lines(withheld):
        print(f"  {line}")
    return code


if __name__ == "__main__":
    main()
