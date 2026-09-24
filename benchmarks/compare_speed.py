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
    rate is the machine's. The NPU lane (~1.7 cores) is immune and passes."""
    rows = list(rows)
    lane = _median_of(rows, "lane_cores") or 0
    others = [v for v in (_other_cores(r, ncpu) for r in rows) if v is not None]
    other = statistics.median(others) if others else 0
    return lane >= 4 and other > OTHER_LOAD_LIMIT


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


def _energy_line(label, a_rows, b_rows, shared):
    """CPU-rail J/token, gross, as a ratio of sums: net depends on each run's
    idle baseline. Reported, never alarmed."""
    rails = [
        (a_rows[i], b_rows[i])
        for i in shared
        if all(
            side.get("cpu_rail_energy_j") is not None and side.get("completion_tokens")
            for side in (a_rows[i], b_rows[i])
        )
    ]
    if not rails:
        return None
    per_tok = [
        sum(pair[k]["cpu_rail_energy_j"] for pair in rails)
        / sum(pair[k]["completion_tokens"] for pair in rails)
        for k in (0, 1)
    ]
    return (
        f"  {label}: CPU-rail energy {per_tok[0]:.3f} -> {per_tok[1]:.3f} "
        f"J/token gross ({per_tok[1] / per_tok[0] - 1:+.0%}; reported, not alarmed)"
    )


def speed_findings(label, a, b):
    """(lines, regressed) for two normalised speed entries; see the module doc."""
    a_rows, b_rows = a.get("speed") or {}, b.get("speed") or {}
    shared = sorted(set(a_rows) & set(b_rows))
    # Other load only LOWERS a CPU lane's rate: a slower new run proves
    # nothing if the new run was loaded, a faster one nothing if the old was.
    old_loaded = loaded_cpu_lane(a_rows.values(), a.get("ncpu"))
    new_loaded = loaded_cpu_lane(b_rows.values(), b.get("ncpu"))
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
        if line.endswith("   better") and old_loaded:
            line = line[: -len("   better")] + (
                f"   faster, NOT judged: the old run had over {OTHER_LOAD_LIMIT} "
                "cores of other load on a CPU lane"
            )
        if worse and new_loaded:
            line += (
                f"   slower, NOT judged: the new run had over {OTHER_LOAD_LIMIT} "
                "cores of other load on a CPU lane"
            )
        elif worse and alarms:
            line += "   *** SLOWER ***"
            regressed = True
        elif worse:
            line += "   worse (reported, not alarmed)"
        lines.append(line)
    energy = _energy_line(label, a_rows, b_rows, shared)
    return [*lines, *([energy] if energy else [])], regressed
