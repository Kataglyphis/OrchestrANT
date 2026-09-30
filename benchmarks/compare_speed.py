"""The speed runner's tripwire: decode, prefill and TTFT paired by prompt, plus its probe."""

import statistics

from orchestrant.benchmark import correctness
from orchestrant.benchmark.answers import row_decode_rate


# A probe flip is one answer at temperature 0; the speed runner has no --repeats.
PROBE_RERUN = "re-ask: orchestrant-bench speed --correctness-only"

# A decode drop beyond this, or beyond the paired prompts' own scatter, is SLOWER.
SPEED_TOLERANCE = 0.05
# Above this much other load a CPU lane's rate describes the machine, not the runtime.
OTHER_LOAD_LIMIT = 0.3
# A lane using this many cores or more is a CPU lane; the NPU lane uses about one.
CPU_LANE_CORES = 4
# The NPU lane held up to this much other load, yet lost rate beside a busy CPU lane.
NPU_UNMOVED_CORES = 2.0

# key, name, higher is better, may alarm; on short prompts prefill and TTFT measure overhead.
_SPEED_METRICS = (
    ("decode_tok_per_sec", "decode tok/s", True, True),
    ("prefill_tok_per_sec", "prefill tok/s", True, False),
    ("ttft_s", "TTFT s", False, False),
)


def _median_of(rows, key):
    values = [r[key] for r in rows if r.get(key) is not None]
    return statistics.median(values) if values else None


def _other_cores(row, ncpu):
    """The row's other load, derived as the runner does for reports predating the field."""
    if row.get("other_cores") is not None:
        return row["other_cores"]
    if ncpu and row.get("cpu_percent_method") == "window" and "lane_cores" in row:
        return max(0.0, row["cpu_percent"] / 100.0 * ncpu - row["lane_cores"])
    return None


def loaded_cpu_lane(rows, ncpu=None):
    """A lane using most of the cores, measured under other load? Then its rate is the machine's."""
    rows = list(rows)
    lane = _median_of(rows, "lane_cores") or 0
    others = [v for v in (_other_cores(r, ncpu) for r in rows) if v is not None]
    other = statistics.median(others) if others else 0
    return lane >= CPU_LANE_CORES and other > OTHER_LOAD_LIMIT


def load_spares(rows):
    """Do the rows show a lane other load does not move (the NPU lane)? Unrecorded is not."""
    lane = _median_of(list(rows), "lane_cores")
    return lane is not None and lane < CPU_LANE_CORES


def _spared(gate, a_rows, b_rows):
    """Does a shut gate still judge this pairing: an unmoved lane, and no busier start?"""
    return (
        gate is not None
        and gate.shut
        and (gate.busiest or 0.0) <= NPU_UNMOVED_CORES
        and load_spares(a_rows.values())
        and load_spares(b_rows.values())
    )


def _withholding(gate, a_rows, b_rows):
    """`gate` when it withholds this pairing's speed verdicts (shut, not spared), else None."""
    if gate is None or not gate.shut or _spared(gate, a_rows, b_rows):
        return None
    return gate


def _spared_lines(label, gate, a_rows, b_rows, judged):
    """Why speed verdicts were `judged` under a shut gate; [] when not."""
    if not (judged and _spared(gate, a_rows, b_rows)):
        return []
    return [
        f"  {label}: speed judged despite the load note -- both runs' lane used "
        f"under {CPU_LANE_CORES} cores (the NPU lane), which did not move from "
        f"0.1 to {NPU_UNMOVED_CORES} other cores"
    ]


def _pairs(a_rows, b_rows, shared, key):
    """(old, new) of one metric per shared prompt; decode via row_decode_rate, not burst rates."""
    read = row_decode_rate if key == "decode_tok_per_sec" else lambda r: r.get(key)
    pairs = [(read(a_rows[i]), read(b_rows[i])) for i in shared]
    return [(a, b) for a, b in pairs if a and b]


def _speed_line(label, name, higher, pairs):
    """(line, worse) for one metric paired by prompt, or None if too few."""
    if len(pairs) < 3:
        return None
    ratios = [b / a for a, b in pairs]
    med = statistics.median(ratios)
    q1, _, q3 = statistics.quantiles(ratios, n=4, method="inclusive")
    noise = max(SPEED_TOLERANCE, (q3 - q1) / 2)
    worse = med < 1 - noise if higher else med > 1 + noise
    better = med > 1 + noise if higher else med < 1 - noise
    line = (
        f"  {label}: {name} {statistics.median(a for a, _ in pairs):.2f} -> "
        f"{statistics.median(b for _, b in pairs):.2f} (median), {med - 1:+.1%} "
        f"paired over {len(pairs)} prompts, noise +/-{noise:.0%}"
    )
    return (line + ("   better" if better else ""), worse)


def _energy_lines(label, a_rows, b_rows, shared):
    """CPU-rail J/token, gross (net depends on each baseline); reported, never alarmed."""
    rails = [
        (a_rows[i], b_rows[i])
        for i in shared
        if all(
            side.get("cpu_rail_energy_j") is not None and side.get("completion_tokens")
            for side in (a_rows[i], b_rows[i])
        )
    ]
    if not rails:
        return []
    per_tok = [
        sum(pair[k]["cpu_rail_energy_j"] for pair in rails)
        / sum(pair[k]["completion_tokens"] for pair in rails)
        for k in (0, 1)
    ]
    return [
        f"  {label}: CPU-rail energy {per_tok[0]:.3f} -> {per_tok[1]:.3f} "
        f"J/token gross ({per_tok[1] / per_tok[0] - 1:+.0%}; reported, not alarmed)"
    ]


def _load_not_judged(line, worse, old_loaded, new_loaded):
    """`line` marked NOT judged when its own requests' load decides it, else None."""
    # Load only LOWERS a CPU lane's rate: slower against a busy baseline is still judged.
    if worse and new_loaded:
        side, verdict = "new", "slower"
    elif old_loaded and not worse:
        side = "old"
        verdict = "faster" if line.endswith("   better") else "unchanged"
    else:
        return None
    return (
        f"{line.removesuffix('   better')}   {verdict}, NOT judged: the {side} run "
        f"had over {OTHER_LOAD_LIMIT} cores of other load on a CPU lane"
    )


def speed_findings(label, a, b, gate=None):
    """(lines, regressed) for two normalised speed entries, withheld as `gate` decides."""
    a_rows, b_rows = a.get("speed") or {}, b.get("speed") or {}
    shared = sorted(set(a_rows) & set(b_rows))
    old_loaded = loaded_cpu_lane(a_rows.values(), a.get("ncpu"))
    new_loaded = loaded_cpu_lane(b_rows.values(), b.get("ncpu"))
    withholding = _withholding(gate, a_rows, b_rows)
    lines, regressed = [], False
    for key, name, higher, alarms in _SPEED_METRICS:
        judged = _speed_line(label, name, higher, _pairs(a_rows, b_rows, shared, key))
        if judged is None:
            continue
        line, worse = judged
        if withholding is not None:
            mark = withholding.withhold(label, name)
            lines.append(line.removesuffix("   better") + mark)
            continue
        marked = _load_not_judged(line, worse, old_loaded, new_loaded)
        if marked is not None:
            lines.append(marked)
            if alarms and gate is not None:
                gate.withhold_row(label, name)
            continue
        if worse and alarms:
            line += "   *** SLOWER ***"
            regressed = True
        elif worse:
            line += "   worse (reported, not alarmed)"
        lines.append(line)
    spared = _spared_lines(label, gate, a_rows, b_rows, lines)
    energy = _energy_lines(label, a_rows, b_rows, shared)
    probe, broke = probe_lines(label, a, b)
    return [*lines, *spared, *energy, *probe], regressed or broke


def probe_fields(block):
    """A speed report's correctness block as bench_compare scores it: integrity items only."""
    gate = correctness.integrity(block)
    if gate is None:
        return {"passed": None, "total": None, "effective_n": None}
    measured = gate["total"] - gate["truncated"] - gate["errors"]
    fields = {
        "passed": gate["score"],
        "total": measured,
        "effective_n": measured,
        "rerun": PROBE_RERUN,
        "probe_verdict": correctness.verdict(gate),
        "capability": correctness.outcomes(block, correctness.CAPABILITY),
    }
    cases = correctness.outcomes(block, correctness.INTEGRITY)
    if cases:
        fields["cases"] = {prompt: (int(ok), 1) for prompt, ok in cases.items()}
    return fields


def _collapse_lines(label, a, b):
    """The probe's integrity verdict became BROKEN: an absolute REGRESSION; [] otherwise."""
    before, after = a.get("probe_verdict"), b.get("probe_verdict")
    if after != "BROKEN" or before in ("BROKEN", correctness.NO_RESULT, None):
        return []
    return [
        f"  {label}: probe integrity {before} -> BROKEN, half or more of its "
        f"integrity answers wrong -- a working model gets them   *** REGRESSION ***"
    ]


def probe_lines(label, a, b):
    """(lines, regressed): a collapse, what the pairing left out, capability answers (unjudged)."""
    if "capability" not in a or "capability" not in b:
        return [], False  # not two probed speed reports: a lane has its own cases
    lines = _collapse_lines(label, a, b)
    broke = bool(lines)
    a_cases, b_cases = set(a.get("cases") or {}), set(b.get("cases") or {})
    if a_cases and b_cases and a_cases != b_cases:
        lines.append(
            f"  {label}: {len(a_cases ^ b_cases)} integrity item(s) measured by one "
            f"report only -- the score is paired over the {len(a_cases & b_cases)} "
            f"both measured"
        )
    a_cap, b_cap = a.get("capability") or {}, b.get("capability") or {}
    shared = sorted(set(a_cap) & set(b_cap))
    if not shared:
        return lines, broke
    before, after = (sum(side[k] for k in shared) for side in (a_cap, b_cap))
    lines.append(
        f"  {label}: capability {before}/{len(shared)} -> {after}/{len(shared)} "
        f"-- not a kernel verdict, reported, not judged"
    )
    for prompt in shared:
        if a_cap[prompt] != b_cap[prompt]:
            lines.append(f"      now {'right' if b_cap[prompt] else 'wrong'}: {prompt}")
    return lines, broke
