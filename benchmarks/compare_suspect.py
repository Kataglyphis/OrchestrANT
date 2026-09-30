"""A control's suspect cases, and every score recounted without them; never imports bench_compare."""

import os
import statistics

from bench_variants import variant_report_fields, variant_spread


def measured(item):
    """Did this row measure the model? Not errors, cuts, overflows, CONTEXT blocks or skips."""
    return not (
        item.get("errored")
        or item.get("truncated")
        or item.get("blocked")
        or item.get("overflow")
        or item.get("skipped")
        or item.get("status") == "CONTEXT"
    )


def is_control(report):
    """The calibration entry: backend 'control', or a label starting with 'control'."""
    return report.get("backend") == "control" or str(
        report.get("label") or ""
    ).lower().startswith("control")


def suspect_tool_files(candidates):
    """This file for a producer's tool_files, only when a control runs: then it moves scores."""
    if any(is_control(c) for c in candidates):
        return (os.path.abspath(__file__),)
    return ()


def suspect_cases(reports):
    """Cases the CONTROL endpoint also failed: evidence about the case, not the candidates.

    Returns the sorted case keys the control measured and failed, or None without a control.
    """
    control = next((r for r in reports if is_control(r)), None)
    if not control:
        return None
    # Over the control's attempts: one flaky draw of three is no evidence of a broken case.
    outcomes = {}
    for item in control.get("results", []):
        key = item.get("case") or item.get("task")
        if key is not None and measured(item):
            outcomes.setdefault(key, []).append(bool(item.get("passed")))
    return sorted(k for k, v in outcomes.items() if v and not any(v))


def _case_key(item):
    """The case identity in a result row: `case` (bench_tools) or `task` (coding, agent)."""
    return item.get("case") if item.get("case") is not None else item.get("task")


def mark_suspect_cases(reports):
    """Exclude the cases the control also fails from every OTHER row's score, in place.

    Returns the sorted suspect keys, or None when no control ran.
    """
    suspect = suspect_cases(reports)
    if not suspect:
        # None (no control) and [] (nothing failed) both mean nothing to exclude.
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
    """(effective_n, effective_k) over the KEPT rows, by the producers' (bench_variants') rule."""
    collapse = bool(report.get("deterministic") or report.get("repeats_agreed"))
    if report.get("variant_case_count"):
        # One key for variant_spread: rows carry `case` or `task`.
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
    """Re-derive the group tables and wall statistics from the KEPT rows, so none go stale."""
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
    """Every wall statistic the producer wrote, over the KEPT rows only; absent stays absent."""
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
