"""Pure shaping of what the lab records since 2026-09-24, for the viewer.

Whether an answer arrived, the load and CPU-rail energy a run cost, the server
build that served it, and the contract probe as a check x run grid -- the
fields `benchmarks/docs/geniex-v0.7.0-cpu-npu-2026-09-24.md` is measured with.
Like ``benchmark_data`` it imports no Reflex, so it is tested without the
frontend extra; ``lab_cards`` renders what it returns.
"""

from __future__ import annotations

from typing import Any

# Relative: the Reflex app imports this as `frontend.lab_data` (from
# frontend/), the tests as `frontend.frontend.lab_data` (from the repo root).
from .benchmark_data import _fmt, _mean, _other_cores, _result_rows, _think_column

# A run gets a row in the lab table when its rows carry any of these. Each
# helper below mirrors the runner's own summary line, so the viewer and
# `orchestrant-bench speed` cannot disagree about one report. Not
# thinking_char_share: coding rows carry it too and would add a row of dashes;
# the comparison table already shows it for every speed run.
_LAB_KEYS = ("answered", "ttfa_s", "lane_cores", "other_cores", "cpu_rail_energy_j")


def _answer_fields(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Answered k/n, mean time to the first answer token, and the thinking share.

    The first-answer mean covers the ANSWERED rows only, as the runner's
    "First answer" line does; a cut reply's first answer token, if any, is not
    the start of an answer anyone received.
    """
    flagged = [r for r in rows if "answered" in r]
    done = [r for r in flagged if r["answered"]]
    ttfa = _mean([r["ttfa_s"] for r in done if r.get("ttfa_s") is not None])
    return {
        "answered": f"{len(done)}/{len(flagged)}" if flagged else "-",
        "cut": len(done) < len(flagged),
        "ttfa": _fmt(ttfa, 2),
        "think": _think_column(rows),
    }


def _load_fields(rows: list[dict[str, Any]], ncpu: Any) -> dict[str, str]:
    """Mean lane cores, and other load as mean (max) -- a CPU lane's rate is
    conditional on it: 0.9 other cores took one from ~30 to 14 tok/s."""
    lane = _mean([r["lane_cores"] for r in rows if r.get("lane_cores") is not None])
    pairs = [p for p in (_other_cores(r, ncpu) for r in rows) if p[0] is not None]
    others = [value for value, _ in pairs]
    other = "-"
    if others:
        other = f"{sum(others) / len(others):.2f} (max {max(others):.2f})"
        other += "*" if any(derived for _, derived in pairs) else ""
    return {"lane_cores": _fmt(lane, 2), "other_cores": other}


def _energy_fields(rows: list[dict[str, Any]]) -> dict[str, str]:
    """CPU-rail J/token gross and net as a RATIO OF SUMS, as the runner prints.

    A mean of per-request ratios would let a half-second answer's meter noise
    over 8 tokens outweigh an 11 s answer's 256 (hostload.summary_lines).
    """
    metered = [
        r
        for r in rows
        if r.get("cpu_rail_energy_j") is not None and r.get("completion_tokens")
    ]
    if not metered:
        return {"j_gross": "-", "j_net": "-", "watts": "-"}
    tokens = sum(r["completion_tokens"] for r in metered)
    joules = sum(r["cpu_rail_energy_j"] for r in metered)
    seconds = sum(
        r.get("cpu_rail_window_s") or r.get("latency_s") or 0 for r in metered
    )
    net = None
    if all(r.get("cpu_rail_net_energy_j") is not None for r in metered):
        net = sum(r["cpu_rail_net_energy_j"] for r in metered) / tokens
    return {
        "j_gross": f"{joules / tokens:.3f}",
        "j_net": _fmt(net, 3),
        "watts": f"{joules / seconds:.1f}" if seconds else "-",
    }


def net_reliability(
    energy: dict[str, Any] | None, *, netted: bool = True
) -> dict[str, str]:
    """Can the run's NET joules be read? The report's `energy` block says.

    On 2026-09-24 a single 5-s idle baseline read 1.26 W in one NPU run and
    1.84 W in the next, turning +21 % gross into a published "+70 % net"; the
    runner now takes one before and one after and sets `net_reliable`.
    `netted` is whether the rows carry net joules at all.
    """
    if not energy:
        return _net("unknown", "-", "no energy block: the report predates the meter")
    if not energy.get("available"):
        reason = str(energy.get("reason") or "unavailable")
        return _net("none", "no meter", reason)
    if "net_reliable" not in energy:
        return _unflagged(netted=netted)
    drift = energy.get("idle_drift_w") or 0.0
    if energy["net_reliable"] is None:
        return _net("unknown", "unknown", "one idle baseline: its drift is unknown")
    if energy["net_reliable"]:
        return _net("reliable", "steady", f"idle baseline drifted {drift:.2f} W")
    why = f"idle baseline drifted {drift:.2f} W between before and after: read gross"
    return _net("drifted", f"DRIFTED {drift:.2f} W", why)


def _unflagged(*, netted: bool) -> dict[str, str]:
    """A metered run without `net_reliable`: an older report whose rows were
    netted against one baseline, or a run that took none (`--idle-seconds 0`)
    and netted nothing -- which is not "unknown", there is no net to read."""
    if not netted:
        return _net("none", "gross only", "no idle baseline, so nothing was netted")
    why = "older report: net was taken against one idle baseline"
    return _net("unknown", "unknown", why + ", so its drift was never measured")


def _net(state: str, text: str, note: str) -> dict[str, str]:
    return {"net_state": state, "net_text": text, "net_note": note}


def lab_rows(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per run: answers, thinking, lane and other load, CPU-rail energy.

    Only runs whose rows carry at least one of those fields: a scored report's
    flattened cases would otherwise fill a row with dashes.
    """
    rows = []
    for config in configs:
        ok = _result_rows(config)
        if not any(key in row for row in ok for key in _LAB_KEYS):
            continue
        energy = _energy_fields(ok)
        rows.append(
            {
                "label": str(config.get("label", "")),
                **_answer_fields(ok),
                **_load_fields(ok, config.get("cpu_threads")),
                **energy,
                **net_reliability(config.get("energy"), netted=energy["j_net"] != "-"),
            }
        )
    return rows


def runtime_label(runtime: dict[str, Any] | None) -> str:
    """'geniex v0.7.0 (QAIRT 2.45, llama.cpp 4ff829e)' -- mirrors
    provenance.runtime_label, which the viewer cannot import."""
    if not runtime:
        return "not recorded"
    if runtime.get("server") == "geniex":
        return (
            f"geniex {runtime.get('cli', '?')} (QAIRT {runtime.get('qairt', '?')}, "
            f"llama.cpp {runtime.get('llama_cpp', '?')})"
        )
    return f"{runtime.get('server', '?')} {runtime.get('version', '?')}"


def serve_flags(runtime: dict[str, Any] | None) -> str:
    """The lane's serve flags, minus the subcommand and --host: a port is not a
    config (provenance._serve_flags). '-' when the lane process was not seen."""
    args = [str(a) for a in (runtime or {}).get("serve_args") or []]
    if args[:1] == ["serve"]:
        args = args[1:]
    if "--host" in args:
        index = args.index("--host")
        del args[index : index + 2]
    return " ".join(args) or "-"


def _seen(runtime: dict[str, Any]) -> str:
    """How the build was identified: from the lane process itself, or only
    from what is installed (a WSL2 client cannot see a Windows lane)."""
    verified = runtime.get("verified")
    if verified is None:
        return "-"
    return "lane process" if verified else "installed binary"


def _reports(config: dict[str, Any]) -> list[dict[str, Any]]:
    return list(config.get("scored") or []) + list(config.get("unscored") or [])


def _joined(values: list[Any]) -> str:
    return ", ".join(dict.fromkeys(str(v) for v in values if v)) or "-"


def _endpoint(url: Any) -> str:
    """host:port of the endpoint whose runtime a report recorded.

    A report records ONE runtime, of the URL it was written against: a lanes
    report spans two lanes and names only its first one's build and flags.
    """
    if not url:
        return "-"
    return str(url).split("://", 1)[-1].split("/", 1)[0]


def _lane(config: dict[str, Any]) -> str:
    """The speed runner's backend name, else the envelope reports' labels.

    Only reports that name a model: a lanes report's third row, `aggregate`,
    sums the other two and is not a lane.
    """
    if config.get("backend"):
        return str(config["backend"])
    return _joined([r.get("label") for r in _reports(config) if r.get("model")])


def runtime_rows(configs: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Per run: which server build served it, and the flags its lane ran with.

    Every GenieX number carried its version in prose until provenance.runtime;
    and on v0.7.0 `--log info` alone cost the NPU lane 13 % of its decode, so
    two runs of one lane are only comparable with their flags in view.
    """
    rows = []
    for config in configs:
        runtime = config.get("runtime") or {}
        models = [config.get("model")] + [r.get("model") for r in _reports(config)]
        rows.append(
            {
                "label": str(config.get("label", "")),
                "kind": str(config.get("kind", "")).replace("bench_", ""),
                "lane": _lane(config),
                "model": _joined(models),
                "endpoint": _endpoint(config.get("base_url")),
                "runtime": runtime_label(runtime),
                "flags": serve_flags(runtime),
                "seen": _seen(runtime),
                "source": str(runtime.get("source") or ""),
            }
        )
    return rows


def _contract_columns(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One column per `bench_contract` report, grouped by lane, oldest first.

    Within a lane the columns read as the lane's history, so a neighbour must
    be the run before: ordered by when each report was written, not by file
    name -- `after-upgrade` sorts before `before-upgrade` and would mark every
    change backwards. The name only breaks ties, and orders a report too old
    to carry a timestamp.
    """
    columns = []
    for config in configs:
        if config.get("kind") != "bench_contract":
            continue
        for report in config.get("unscored") or []:
            checks = [
                c
                for c in report.get("checks") or []
                if isinstance(c, dict) and c.get("id")
            ]
            if checks:
                lane = report.get("label") or report.get("model") or "?"
                columns.append(
                    {
                        "lane": str(lane),
                        "file": str(config.get("label", "")),
                        "when": str(config.get("timestamp") or ""),
                        "runtime": config.get("runtime") or {},
                        "checks": {str(c["id"]): c for c in checks},
                    }
                )
    columns.sort(key=lambda col: (col["lane"], col["when"], col["file"]))
    return columns


def _merged_order(sequences: list[list[str]]) -> list[str]:
    """Check ids of every report, merged in the reports' own order.

    Newer contract runs add checks mid-list (the r2 determinism checks), and
    first appearance alone would push them to the bottom, away from the rows
    they refine; each new id goes in right after the one it followed.
    """
    order: list[str] = []
    for ids in sequences:
        previous = None
        for check_id in ids:
            if check_id not in order:
                at = order.index(previous) + 1 if previous is not None else 0
                order.insert(at, check_id)
            previous = check_id
    return order


def _contract_cell(check: dict[str, Any] | None, before: str | None) -> dict[str, str]:
    """One answer; `moved` when it differs from the lane's previous answer."""
    if check is None:
        return {"text": "-", "tip": "not asked in this run", "state": "", "moved": ""}
    answer = str(check.get("answer", "?"))
    tip = str(check.get("evidence") or "")[:300]
    if check.get("seconds") is not None:
        tip += f" ({check['seconds']} s)"
    moved = before is not None and answer != before
    return {
        "text": answer,
        "tip": tip,
        "state": answer,
        "moved": "yes" if moved else "",
    }


def _version(runtime: dict[str, Any]) -> str:
    return str(runtime.get("cli") or runtime.get("version") or "(runtime not recorded)")


def contract_table(configs: list[dict[str, Any]]) -> dict[str, Any]:
    """`orchestrant-bench contract` reports as one check x run grid.

    What each lane does, asked of the lane itself -- the table the GenieX page
    keeps by hand. A cell whose answer differs from the same lane's previous
    answer to that check is marked `moved`, as `contract --diff` would call it
    CHANGED; a check one run never asked is '-' and moves nothing.
    """
    columns = _contract_columns(configs)
    order = _merged_order([list(col["checks"]) for col in columns])
    rows = []
    for check_id in order:
        asked = [
            col["checks"][check_id] for col in columns if check_id in col["checks"]
        ]
        question = str(asked[0].get("question") or "")
        row = [{"text": check_id, "tip": question, "state": "check", "moved": ""}]
        last: dict[str, str] = {}
        for col in columns:
            item = _contract_cell(col["checks"].get(check_id), last.get(col["lane"]))
            if check_id in col["checks"]:
                last[col["lane"]] = item["text"]
            row.append(item)
        rows.append(row)
    header = [
        {
            "title": f"{col['lane']} {_version(col['runtime'])}",
            "file": col["file"],
            "tip": f"{runtime_label(col['runtime'])}; {serve_flags(col['runtime'])}",
        }
        for col in columns
    ]
    return {"columns": header, "rows": rows}
