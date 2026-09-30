"""Pure shaping of the lab's answer, load, energy, runtime and contract fields; no Reflex."""

from __future__ import annotations

from typing import Any

# Relative: the app imports this as frontend.lab_data, the tests as frontend.frontend.lab_data.
from .benchmark_data import _fmt, _mean, _other_cores, _result_rows, _think_column

# A run gets a lab row when its rows carry any of these; coding rows carry thinking_char_share too.
_LAB_KEYS = ("answered", "ttfa_s", "lane_cores", "other_cores", "cpu_rail_energy_j")


def _answer_fields(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Answered k/n, mean time to the first answer token (answered rows only), thinking share."""
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
    """Mean lane cores, and other load as mean (max): a CPU lane's rate depends on it."""
    lane = _mean([r["lane_cores"] for r in rows if r.get("lane_cores") is not None])
    pairs = [p for p in (_other_cores(r, ncpu) for r in rows) if p[0] is not None]
    others = [value for value, _ in pairs]
    other = "-"
    if others:
        other = f"{sum(others) / len(others):.2f} (max {max(others):.2f})"
        other += "*" if any(derived for _, derived in pairs) else ""
    return {"lane_cores": _fmt(lane, 2), "other_cores": other}


def _energy_fields(rows: list[dict[str, Any]]) -> dict[str, str]:
    """CPU-rail J/token gross and net as a RATIO OF SUMS, as the runner prints."""
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
    """Can the run's NET joules be read? The report's `energy` block says."""
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
    """A metered run without `net_reliable`: netted against one baseline, or not at all."""
    if not netted:
        return _net("none", "gross only", "no idle baseline, so nothing was netted")
    why = "older report: net was taken against one idle baseline"
    return _net("unknown", "unknown", why + ", so its drift was never measured")


def _net(state: str, text: str, note: str) -> dict[str, str]:
    return {"net_state": state, "net_text": text, "net_note": note}


def lab_rows(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per run carrying any of them: answers, thinking, lane and other load, CPU-rail energy."""
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
    """'geniex v0.7.0 (QAIRT 2.45, llama.cpp 4ff829e)', mirroring provenance.runtime_label."""
    if not runtime:
        return "not recorded"
    if runtime.get("server") == "geniex":
        return (
            f"geniex {runtime.get('cli', '?')} (QAIRT {runtime.get('qairt', '?')}, "
            f"llama.cpp {runtime.get('llama_cpp', '?')})"
        )
    return f"{runtime.get('server', '?')} {runtime.get('version', '?')}"


def serve_flags(runtime: dict[str, Any] | None) -> str:
    """The lane's serve flags minus the subcommand and --host; '-' when the lane was not seen."""
    args = [str(a) for a in (runtime or {}).get("serve_args") or []]
    if args[:1] == ["serve"]:
        args = args[1:]
    if "--host" in args:
        index = args.index("--host")
        del args[index : index + 2]
    return " ".join(args) or "-"


def _seen(runtime: dict[str, Any]) -> str:
    """How the build was identified: from the lane process, or only from what is installed."""
    verified = runtime.get("verified")
    if verified is None:
        return "-"
    return "lane process" if verified else "installed binary"


def _reports(config: dict[str, Any]) -> list[dict[str, Any]]:
    return list(config.get("scored") or []) + list(config.get("unscored") or [])


def _joined(values: list[Any]) -> str:
    return ", ".join(dict.fromkeys(str(v) for v in values if v)) or "-"


def _endpoint(url: Any) -> str:
    """host:port of the ONE endpoint whose runtime a report recorded."""
    if not url:
        return "-"
    return str(url).split("://", 1)[-1].split("/", 1)[0]


def _lane(config: dict[str, Any]) -> str:
    """The speed runner's backend name, else the labels of report rows that name a model."""
    if config.get("backend"):
        return str(config["backend"])
    return _joined([r.get("label") for r in _reports(config) if r.get("model")])


def runtime_rows(configs: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Per run: which server build served it, and the flags its lane ran with."""
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
    """One column per contract report, grouped by lane, ordered by write time, not file name."""
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
    """Check ids of every report, each new id placed right after the one it followed."""
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
    """`orchestrant-bench contract` reports as one check x run grid; changed cells are `moved`."""
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
