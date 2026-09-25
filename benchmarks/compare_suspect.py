"""A control's suspect cases, and every score recounted without them.

A case the CONTROL endpoint (backend "control" in backends.json) also fails is
evidence about the case, not the candidates. suspect_cases() names those
cases; mark_suspect_cases() takes them out of every other row's score before a
producer -- bench_tools, bench_coding, bench_chat -- writes its report, and
re-derives each number the rows decide. bench_compare.normalise() reads the
same list for its "the CONTROL also fails" line.

Split from bench_compare.py at 1011 lines, the split its file-size.allow row
named. measured() came along because the recount keeps only measured rows:
the rule for which rows count sits beside the count, and bench_compare imports
it for normalise() and case_outcomes(). Nothing here imports bench_compare,
which imports this module -- a reach back would close a cycle (bench_variants.py
keeps the same rule for the same reason).

Being its own file is also what lets a producer hash it (suspect_tool_files):
bench_compare as a whole is the comparison, which decides no report's numbers.
"""

import os
import statistics

from bench_variants import variant_report_fields, variant_spread


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


def is_control(report):
    """The calibration entry: backend 'control' in backends.json, or a label
    starting with 'control' (the label falls back to the model id otherwise)."""
    return report.get("backend") == "control" or str(
        report.get("label") or ""
    ).lower().startswith("control")


def suspect_tool_files(candidates):
    """This file, for a producer's tool_files, when a control is a candidate.

    A control's failures leave every other row's passed, total and effective
    sample (mark_suspect_cases), so an edit here moves those scores as a
    grader edit would -- and tool_sha256 is what says "the grader moved".
    Without a control nothing here touches a number, and hashing it would call
    an edit a grader change in reports it never decided (OPS-9: no plumbing).
    A candidate row carries the label and backend its report will, so
    is_control() gives the same answer before the run as after it.
    """
    if any(is_control(c) for c in candidates):
        return (os.path.abspath(__file__),)
    return ()


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
