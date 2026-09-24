#!/usr/bin/env python3
"""The speed runner's tripwire: decode, prefill and TTFT paired by prompt.

bench_compare used to reduce a speed report to its correctness score plus the
summed latency, judged against a 25 % tolerance. On 2026-09-24 that passed
GenieX v0.7.0's 13 % NPU decode loss (`--log info`) as "no regression
detected", and compared nothing at all between two CPU-lane runs. Pairing each
prompt with itself removes the prompts' own differences, so the noise left is
what the threshold is taken from.
"""

import statistics

# A decode rate falling by more than this -- or by more than the paired
# prompts' own scatter, if larger -- is SLOWER. Within one NPU run nine
# prompts' decode rates spread ~1 %.
SPEED_TOLERANCE = 0.05
# Above this many cores of other load a CPU lane's rate describes the machine,
# not the runtime (measured: 0.5-1.0 other cores cost it 25-55 % of decode).
OTHER_LOAD_LIMIT = 0.3
# A lane using this many cores or more is a CPU lane: every tracked speed
# report shows the CPU lane at 7.2-7.5 of the host's 8, the NPU lane at ~1.0.
CPU_LANE_CORES = 4
# The NPU lane did not move from 0.1 to 2.0 other cores (hostload.py), but
# beside the CPU lane's 7.2 busy cores it lost 46-87 % of its rate (the
# concurrency table, docs/geniex-v0.7.0-cpu-npu-2026-09-24.md): a busier start
# is evidence against it too.
NPU_UNMOVED_CORES = 2.0

# key, name, higher is better, may fire the alarm. Prefill and TTFT are
# reported only: on the runner's short prompts they measure request overhead.
_SPEED_METRICS = (
    ("decode_tok_per_sec", "decode tok/s", True, True),
    ("prefill_tok_per_sec", "prefill tok/s", True, False),
    ("ttft_s", "TTFT s", False, False),
)


def _median_of(rows, key):
    values = [r[key] for r in rows if r.get(key) is not None]
    return statistics.median(values) if values else None


def _other_cores(row, ncpu):
    """The row's other load; derived as the runner does for reports that
    predate the field (cpu_percent x cores - lane_cores, window rows only)."""
    if row.get("other_cores") is not None:
        return row["other_cores"]
    if ncpu and row.get("cpu_percent_method") == "window" and "lane_cores" in row:
        return max(0.0, row["cpu_percent"] / 100.0 * ncpu - row["lane_cores"])
    return None


def loaded_cpu_lane(rows, ncpu=None):
    """A lane using most of the cores, measured under other load? Then its
    rate is the machine's. The NPU lane (~1.0 cores) passes."""
    rows = list(rows)
    lane = _median_of(rows, "lane_cores") or 0
    others = [v for v in (_other_cores(r, ncpu) for r in rows) if v is not None]
    other = statistics.median(others) if others else 0
    return lane >= CPU_LANE_CORES and other > OTHER_LOAD_LIMIT


def load_spares(rows):
    """Do the rows show a lane other load does not move (under
    CPU_LANE_CORES: the NPU lane)? An unrecorded share is not spared."""
    lane = _median_of(list(rows), "lane_cores")
    return lane is not None and lane < CPU_LANE_CORES


def _spared(gate, a_rows, b_rows):
    """Does a shut gate still judge this pairing? Both runs' rows show a lane
    load does not move, and no recorded start was busier than the load that
    lane was measured unmoved at (NPU_UNMOVED_CORES)."""
    return (
        gate is not None
        and gate.shut
        and (gate.busiest or 0.0) <= NPU_UNMOVED_CORES
        and load_spares(a_rows.values())
        and load_spares(b_rows.values())
    )


def _withholding(gate, a_rows, b_rows):
    """`gate` when it withholds this pairing's speed verdicts, else None:
    shut (compare_verdict.LoadGate), and not _spared."""
    if gate is None or not gate.shut or _spared(gate, a_rows, b_rows):
        return None
    return gate


def _spared_lines(label, gate, a_rows, b_rows, judged):
    """Why speed verdicts were `judged` under a shut gate; [] when not. The
    "! HOST WAS BUSY ... not evidence" note stands above them, and a SLOWER
    with nothing between read as a gate that failed to shut."""
    if not (judged and _spared(gate, a_rows, b_rows)):
        return []
    return [
        f"  {label}: speed judged despite the load note -- both runs' lane used "
        f"under {CPU_LANE_CORES} cores (the NPU lane), which did not move from "
        f"0.1 to {NPU_UNMOVED_CORES} other cores"
    ]


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
    """CPU-rail J/token, gross, as a ratio of sums: net depends on each run's
    idle baseline. Reported, never alarmed; [] when no prompt has it."""
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
    """`line` marked NOT judged when its own requests' load decides it, else None.

    Other load only LOWERS a CPU lane's rate: a slower new run proves nothing
    if the new run was loaded, and a flat or faster one nothing if the old run
    was -- a busy baseline understates the old rate, so it can hide a real
    slowdown. A slower new run against a busy baseline is still judged: the
    real drop is only larger.
    """
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
    """(lines, regressed) for two normalised speed entries; see the module doc.

    `gate` is the pairing's compare_verdict.LoadGate: shut, it withholds every
    verdict here unless _spared, and a spared pairing says so. Open, it still
    records an alarming metric its own requests' load left NOT judged
    (LoadGate.withhold_row, which the flag skips): exit 0 would say it passed.
    """
    a_rows, b_rows = a.get("speed") or {}, b.get("speed") or {}
    shared = sorted(set(a_rows) & set(b_rows))
    old_loaded = loaded_cpu_lane(a_rows.values(), a.get("ncpu"))
    new_loaded = loaded_cpu_lane(b_rows.values(), b.get("ncpu"))
    withholding = _withholding(gate, a_rows, b_rows)
    lines, regressed = [], False
    for key, name, higher, alarms in _SPEED_METRICS:
        pairs = [
            (a_rows[i][key], b_rows[i][key])
            for i in shared
            if a_rows[i].get(key) and b_rows[i].get(key)
        ]
        judged = _speed_line(label, name, higher, pairs)
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
    return [*lines, *spared, *_energy_lines(label, a_rows, b_rows, shared)], regressed
