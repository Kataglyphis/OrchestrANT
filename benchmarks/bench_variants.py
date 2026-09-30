#!/usr/bin/env python3
"""Prompt variants: how much of a score is the wording; imports no other lab module (cycles)."""

# What variant_spread() adds to a report row; effective_n/k are set apart.
VARIANT_FIELDS = (
    "variant_case_count",
    "variant_spread",
    "variant_spread_rate",
    "variant_spread_cases",
    "by_variant",
    "variant_outcomes",
)


def phrasing_agreement(rows, key, repeats, hash_field):
    """(deterministic, repeats_agreed), voted per (case, phrasing): paraphrases always differ."""
    hashes, outcomes = {}, {}
    for r in rows:
        k = (r[key], r.get("variant", 0))
        hashes.setdefault(k, set()).add(r.get(hash_field))
        outcomes.setdefault(k, set()).add(bool(r.get("passed")))
    voted = repeats > 1 and bool(outcomes)
    return (
        voted and all(len(v) == 1 for v in hashes.values()),
        voted and all(len(v) == 1 for v in outcomes.values()),
    )


def _phrasing_cells(rows, key):
    """{case: {variant: [passes, attempts]}} over the given rows."""
    cells = {}
    for r in rows:
        cell = cells.setdefault(r[key], {}).setdefault(r.get("variant", 0), [0, 0])
        cell[0] += int(bool(r.get("passed")))
        cell[1] += 1
    return cells


def _by_variant(outcomes):
    """The score of each phrasing index over only the cases asked more than one way."""
    width = max((len(o["phrasings"]) for o in outcomes.values()), default=0)
    out = []
    for i in range(width):
        cells = [
            o["phrasings"][i]
            for o in outcomes.values()
            if i < len(o["phrasings"]) and o["phrasings"][i][1]
        ]
        out.append(
            {
                "variant": i,
                "passed": sum(c[0] for c in cells),
                "total": sum(c[1] for c in cells),
                "cases": len(cells),
            }
        )
    return out


def _as_written_sample(rows, key, collapse):
    """(effective_n, effective_k), each case counted once by its prompt AS WRITTEN."""
    units = {}
    for r in rows:
        unit = r[key] if collapse else (r[key], r.get("attempt", 0))
        units.setdefault(unit, {}).setdefault(r.get("variant", 0), []).append(
            bool(r.get("passed"))
        )
    observed = [phrasings[min(phrasings)] for phrasings in units.values()]
    return len(observed), sum(1 for v in observed if all(v))


def variant_spread(rows, key, collapse):
    """Where a case's phrasings disagreed, and the sample they make (paraphrases add none)."""
    outcomes = {}
    for case, cells in sorted(_phrasing_cells(rows, key).items()):
        if len(cells) < 2:
            continue
        passes = [p for p, _ in cells.values()]
        outcomes[case] = {
            # [passes, attempts] per phrasing; [0, 0] = never measured.
            "phrasings": [cells.get(i, [0, 0]) for i in range(max(cells) + 1)],
            "disagreed": any(passes) and not all(passes),
        }
    effective_n, effective_k = _as_written_sample(rows, key, collapse)
    spread = [c for c, o in outcomes.items() if o["disagreed"]]
    return {
        "effective_n": effective_n,
        "effective_k": effective_k,
        "variant_case_count": len(outcomes),
        "variant_spread": len(spread),
        "variant_spread_rate": (
            round(len(spread) / len(outcomes), 3) if outcomes else None
        ),
        "variant_spread_cases": spread,
        "by_variant": _by_variant(outcomes),
        "variant_outcomes": outcomes,
    }


def variant_report_fields(summary):
    """The report-row half of variant_spread(); {} when no variants ran."""
    return {k: summary[k] for k in VARIANT_FIELDS} if summary else {}


def variant_spread_lines(summary, total, unit="case", repeats=1):
    """What a run prints about its paraphrases; [] without the flag (`summary` None)."""
    if not summary:
        return []
    asked = summary["variant_case_count"]
    if not asked:
        return [f"       prompt variants: no {unit} was measured in two phrasings"]
    names = ", ".join(summary["variant_spread_cases"])
    per = ", ".join(
        f"v{b['variant']} {b['passed']}/{b['total']}" for b in summary["by_variant"]
    )
    lines = [
        f"       prompt variants: {summary['variant_spread']}/{asked} {unit}s passed "
        f"in one phrasing and failed in another "
        f"({100 * summary['variant_spread_rate']:.0f}%)"
        + (f": {names}" if names else ""),
        f"       per phrasing (v0 = as written), over those {asked}: {per}",
        f"       paraphrases are draws of the same {unit}, not new ones: effective "
        f"sample {summary['effective_n']}, not {total} attempts "
        f"({summary['effective_k']} passed as written)",
    ]
    if repeats < 2 and summary["variant_spread"]:
        # One draw per phrasing cannot tell wording from a sampler's draw.
        lines.append(
            "       one draw per phrasing: on a lane that samples, an unlucky "
            "draw reads as spread too -- --repeats shrinks that share"
        )
    return lines
