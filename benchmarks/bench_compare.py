#!/usr/bin/env python3
"""Diff two benchmark reports and fail on a regression.

Scores are judged by a paired sign test, a changed grader is named before any
score, and a load difference withholds speed verdicts (exit 4, CONDITIONS DIFFER).

Usage:
    python3 bench_compare.py old.json new.json
    python3 bench_compare.py --baseline geniex-npu new.json      # vs stored
    python3 bench_compare.py --save-baseline geniex-npu new.json
    python3 bench_compare.py --allow-load-difference old.json new.json
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# Standalone runs (not a package) need the repo root on sys.path for orchestrant.benchmark.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Re-exported: the tools and tests import the split-out helpers from this module.
from compare_dirs import BASELINE_DIR, baseline_path, compare_directories  # noqa: E402
from compare_dirs import pair_directories  # noqa: E402
from compare_lanes import lane_findings, lane_runtimes, lane_set_changed  # noqa: E402
from compare_speed import probe_fields, speed_findings  # noqa: E402
from compare_suspect import (  # noqa: E402
    _case_key,
    _recount_groups,
    _recount_sample,
    _recount_walls,
    is_control,
    mark_suspect_cases,
    measured,
    suspect_cases,
)
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

# A timing change under this is treated as noise rather than a regression.
DEFAULT_TIME_TOLERANCE = 0.25


def _legacy_provenance(report):
    """A speed report's provenance block, else its hardware dict (older reports)."""
    # Older reports have only the hardware dict, which names no runtime.
    return report.get("provenance") or report.get("hardware", {})


def normalise(report):
    """Bring either envelope (the shared one, or the speed runner's legacy one) into one form."""
    if not isinstance(report, dict) or not ("reports" in report or "results" in report):
        raise ValueError(
            "not a benchmark report: neither 'reports' (shared envelope) "
            "nor 'results' (orchestrant.benchmark.openai_api) is present"
        )
    if "reports" in report:
        prov = report.get("provenance") or {}
        entries = []
        for r in report["reports"]:
            # Per-case outcomes: a flipped case is attributable where the proportion may not move.
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
                    # Measured attempts only if recorded; else from the rows; else None.
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
                    # Counts, not all(): a bool alarms on one flaky draw and hides a collapse.
                    "cases": {k: (sum(v), len(v)) for k, v in cases.items()},
                    # bench_lanes throughput; an older aggregate row is a per-lane sum.
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
    return {
        "benchmark": "orchestrant.benchmark.openai_api",
        "provenance": _legacy_provenance(report),
        "config": report.get("config", {}),
        "entries": [
            {
                "label": report.get("model"),
                "model": report.get("model"),
                # Only the probe's integrity items are a score; throughput is not pass/fail.
                **probe_fields(report.get("correctness")),
                "wall_s": round(sum(walls), 2) if walls else None,
                "median_wall_s": None,
                # No latency verdict: wall moves with max_tokens; compare_speed judges rates.
                "timing": False,
                "deterministic": None,
                # prompt_index -> row: decode, prefill, TTFT, load, energy.
                "speed": speed,
                "ncpu": (report.get("hardware") or {}).get("cpu_total_threads"),
            }
        ],
    }


def load(path):
    with open(path) as f:
        try:
            return normalise(json.load(f))
        except ValueError as e:
            raise SystemExit(f"{path}: {e}") from e


def _per_attempt(entry):
    """Seconds per MEASURED attempt and its source; attempts cut at the deadline stay out."""
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
    """The config minus the self-check's run time and the host's process ceiling."""
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
    """Lane throughput measured alike on both sides: delivered, else the old per-lane sum."""
    if a.get("delivered") != b.get("delivered"):
        return a.get("summed_tok_per_sec"), b.get("summed_tok_per_sec")
    return a.get("tok_per_sec"), b.get("tok_per_sec")


def _throughput_line(label, a, b, gate, tolerance, lanes_moved):
    """(line, slower) for bench_lanes throughput; only reported when the lane set moved."""
    a_tps, b_tps = _tps_pair(a, b)
    if not a_tps or b_tps is None:
        return None, False
    delta = (b_tps - a_tps) / a_tps
    head = f"  {label}: {a_tps:.1f} -> {b_tps:.1f} tok/s ({delta:+.0%})"
    if lanes_moved:
        return f"{head}   NOT judged: the lane set changed (! lane lines above)", False
    mark, slower = gate.judge(label, "tok/s", -delta, tolerance)
    return head + mark, slower


def _comparable(a, b, lanes_moved=False):
    """Is there any score, timing, throughput, speed or batching verdict both sides have?"""
    throughput = a.get("tok_per_sec") and b.get("tok_per_sec") is not None
    return bool(
        (a.get("total") and b.get("total"))
        or (_per_attempt(a)[0] and _per_attempt(b)[0])
        or (throughput and not lanes_moved)
        or set(a.get("speed") or {}) & set(b.get("speed") or {})
        or None not in (a.get("serialised"), b.get("serialised"))
    )


def _compared(old, new, old_by, new_by):
    """How many shared labels have something comparable (_comparable)."""
    moved = lane_set_changed(old, new)
    return sum(_comparable(old_by[k], new_by[k], moved) for k in old_by.keys() & new_by)


def compare(
    old,
    new,
    time_tolerance=DEFAULT_TIME_TOLERANCE,
    seen=None,
    allow_load_difference=False,
):
    """Returns (findings, regressed); `findings` is a list of printable lines.

    `seen`, when a dict, receives "compared", "paired" and "withheld".
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
        # Contract answers are neither scores nor timings.
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

    # A changed --system or --repeats changes what the numbers MEAN.
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
    # `repeats` scales total_wall_s linearly: more work is no slowdown.
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
        seen["compared"] = _compared(old, new, old_by, new_by)
    for label in sorted(set(old_by) & set(new_by)):
        a, b = old_by[label], new_by[label]
        speed_lines, speed_regressed = speed_findings(label, a, b, gate)
        findings += speed_lines
        regressed = regressed or speed_regressed

        # --- per-case diff, which needs no statistics to be meaningful
        a_cases, b_cases = a.get("cases") or {}, b.get("cases") or {}
        shared = set(a_cases) & set(b_cases)
        # Strict flip on a deterministic endpoint; "passed before, never passes now" otherwise.
        strict = _known_deterministic(a) and _known_deterministic(b)
        # One attempt on a lane nobody showed deterministic is a coin toss: named, not alarmed.
        single_draw = (
            not strict
            and shared
            and all(a_cases[k][1] == 1 and b_cases[k][1] == 1 for k in shared)
        )

        def _rate(pair):
            passes, attempts = pair
            return (passes / attempts) if attempts else 0.0

        def _broke(k):
            # On a sampling lane, the only per-case claim one unlucky draw cannot fake.
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
                f"{b.get('rerun', 'rerun with --repeats 3')}): {', '.join(broke)}"
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
            # Reported, not alarmed: a lower pass RATE on a sampling lane is no proof alone.
            findings.append(
                f"  {label}: {len(degraded)} case(s) pass less often "
                f"(sampling lane, not treated as a regression): "
                f"{', '.join(degraded)}"
            )

        if a.get("total") and b.get("total"):
            a_rate = a["passed"] / a["total"]
            b_rate = b["passed"] / b["total"]
            # Intervals on the EFFECTIVE sample: deterministic repeats are not independent.
            a_n = a.get("effective_n") or a["total"]
            b_n = b.get("effective_n") or b["total"]
            # Observed counts, never a rounded ratio, unless an old report has only that.
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
                # Paired: only the cases that disagree carry information.
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

        # Per-attempt time, not the sum: errored-out requests would read as FASTER.
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
    """{case: (passes, attempts)} over a RAW report row's measured, non-suspect cases."""
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
    """The cases behind the headline score: suspect ones leave a candidate's, not the control's."""
    cases = entry.get("cases") or {}
    kept = {k: v for k, v in cases.items() if k not in suspect}
    if sum(m for _, m in kept.values()) == entry.get("total"):
        return kept
    return cases if sum(m for _, m in cases.values()) == entry.get("total") else kept


def _score(k, n, entry, suspect):
    """format_score, plus the case-clustered interval when repeats disagree."""
    return format_score(k, n) + clustered_note(_scored_cases(entry, suspect))


def _diff_text(a_cases, b_cases, counts, rate_diff):
    """Paired over the shared cases when both sides carry them, else Newcombe."""
    lo, hi = diff_interval(*counts)
    return paired_diff_note(a_cases, b_cases) or (
        f"diff {100 * rate_diff:+.0f}pt [{100 * lo:+.0f}, {100 * hi:+.0f}]"
    )


def _note_pairing(seen, label, n_cases, back_flips):
    """Record a paired comparison for the closing minimum-detectable-drop line."""
    if seen is not None and n_cases:
        seen.setdefault("paired", []).append((label, n_cases, back_flips))


def _mde_lines(seen):
    """The weakest pairing's minimum detectable drop, by the drop and not the case count."""
    pairs = (seen or {}).get("paired") or []
    if len(pairs) < 2:
        return [paired_mde_note(n_cases, flips) for _, n_cases, flips in pairs]

    def missed(pair):
        mde = paired_mde(pair[1], back_flip_estimate(pair[1], pair[2])[0])
        return 2.0 if mde is None else mde

    label, n_cases, back_flips = max(pairs, key=missed)
    return [f"{label} (weakest pairing): {paired_mde_note(n_cases, back_flips)}"]


def _pass_k_lines(label, a, b, suspect):
    """pass^k at the smaller side's `repeats` (not attempts) when a sampling side repeated."""
    a_cases, b_cases = _scored_cases(a, suspect), _scored_cases(b, suspect)
    draws = [
        entry.get("repeats") or max(m for _, m in cases.values())
        for entry, cases in ((a, a_cases), (b, b_cases))
        if cases and not _known_deterministic(entry)
    ]
    k = min((d for d in draws if d > 1), default=None)
    return [f"  {label}: {pass_k_note(a_cases, b_cases, k)}"] if k else []


def _compare_directories(args):
    """--dir: compare_dirs walks them; names resolve here at call time, so patches apply."""
    return compare_directories(args, compare, load, _mde_lines)


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
        help="Judge speed and timing that a busy or unlike-load start withheld, and "
        "let a rate NOT judged for its requests' load pass (default: exit 4)",
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
        print("  NOTHING COMPARED — no score, timing or speed metric was like-for-like")
    else:
        # "No regression" is not "nothing changed": power on the EFFECTIVE sample.
        sizes = [
            e.get("effective_n") or e["total"] for e in new["entries"] if e.get("total")
        ]
        has_cases = any(e.get("cases") for e in new["entries"])
        print("  no regression detected")
        if has_cases:
            # How few flips could ever be seen, and how large a real drop slips through.
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
