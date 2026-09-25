"""Pure shaping of a benchmark manifest for the viewer.

No Reflex import here on purpose: every table and every interval is a plain
function over the manifest dict, so it is testable without the frontend extra
and without a browser. The viewer module only renders what these return.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_Z = 1.96
_DETAIL_SKIP = ("extra_params", "prompts_requested", "prompts_completed")


def manifest_location(value: str, cwd: Path, root: Path) -> Path:
    """The manifest file a path names: relative to the repository root.

    `reflex run` runs from frontend/, so a relative path read against the
    working directory never found the default -- frontend/benchmarks/ does
    not exist -- and every documented `ORCHESTRANT_BENCHMARK_MANIFEST=
    benchmarks/...` pointed there too. A relative path that does exist from
    the working directory still wins, so `../benchmarks/...` keeps working.
    """
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    here = cwd / path
    return here if here.exists() else root / path


def hardware_rows(hw: dict[str, Any] | None) -> list[dict[str, str]]:
    """The HardwareCard's key/value rows. Missing fields render as '?', not ''."""
    if not hw:
        return []
    rows = [
        {
            "label": "OS",
            "value": f"{hw.get('os', '?')} {hw.get('os_release', '')}".strip(),
        },
        {"label": "Architecture", "value": str(hw.get("architecture", "?"))},
        {"label": "CPU", "value": str(hw.get("cpu_model") or "unknown")},
        {
            "label": "Cores / Threads",
            "value": f"{hw.get('cpu_physical_cores', '?')} cores / "
            f"{hw.get('cpu_total_threads', '?')} threads",
        },
        {"label": "RAM", "value": f"{hw.get('ram_total_gb', '?')} GB"},
    ]
    gpu = hw.get("gpu") or {}
    if gpu:
        rows.append({"label": "GPU", "value": _gpu_label(gpu)})
    rows += [
        {"label": "Container", "value": "Yes" if hw.get("in_container") else "No"},
        {"label": "Ollama Host", "value": str(hw.get("ollama_host", "?"))},
    ]
    return rows


def _gpu_label(gpu: dict[str, Any]) -> str:
    """'AMD Radeon RX 9070 XT (AMD, 16.0 GB)', degrading field by field."""
    name = str(gpu.get("name") or "unknown")
    vendor = str(gpu.get("vendor") or "").upper()
    details = [vendor] if vendor and vendor != "NONE" else []
    total_mb = gpu.get("memory_total_mb")
    if isinstance(total_mb, (int, float)) and total_mb > 0:
        details.append(f"{total_mb / 1024:.1f} GB")
    return f"{name} ({', '.join(details)})" if details else name


def missing_hardware(hw: dict[str, Any] | None) -> list[str]:
    """The incomplete record's field names."""
    return list((hw or {}).get("incomplete") or [])


def correctness_summary(configs: list[dict[str, Any]]) -> dict[str, Any]:
    """The banner: is the model WORKING, not just fast?

    A broken model emits fluent nonsense at excellent tokens/sec, so this
    outranks every speed number on the page. Only the probe's integrity items
    decide the state -- the manifest records their counts, for older reports
    too -- and capability misses (strawberry, Canberra) get a line of their
    own: on 2026-09-24 Llama-3.2-3B read Degraded at 3/6 on a healthy lane.
    A block with no `integrity` count (an older manifest) is judged whole.
    """
    scored = [c for c in configs if c.get("correctness")]
    if not scored:
        return {
            "checked": False,
            "state": "unknown",
            "headline": "not checked",
            "score": 0,
            "total": 0,
            "capability": "",
            "rows": [],
        }
    gates = [c["correctness"].get("integrity") or c["correctness"] for c in scored]
    total = sum(g.get("total", 0) for g in gates)
    score = sum(g.get("score", 0) for g in gates)
    skills = [c["correctness"].get("capability") or {} for c in scored]
    skill_total = sum(s.get("total", 0) for s in skills)
    skill_score = sum(s.get("score", 0) for s in skills)
    ratio = score / total if total else 0.0
    state = "ok" if ratio == 1 else "degraded" if ratio >= 0.5 else "broken"
    headline = {"ok": "Correct", "degraded": "Degraded", "broken": "BROKEN"}[state]
    rows: list[dict[str, Any]] = []
    for config in scored:
        items = config["correctness"].get("items", [])
        for index, item in enumerate(items):
            rows.append(
                {
                    "config": config.get("label", "") if index == 0 else "",
                    "ok": bool(item.get("correct")),
                    "kind": str(item.get("kind", "")),
                    "expected": str(item.get("expected", "")),
                    "answer": str(
                        item.get("answer_preview") or item.get("error") or ""
                    ),
                }
            )
    return {
        "checked": True,
        "state": state,
        "headline": headline,
        "score": score,
        "total": total,
        "capability": (
            f"capability {skill_score}/{skill_total} -- not a kernel verdict"
            if skill_total
            else ""
        ),
        "rows": rows,
    }


def wilson(passed: int, total: int) -> tuple[float, float]:
    """95% Wilson interval, mirroring bench_stats: a bare fraction invites a
    conclusion the sample cannot support, and 25/27 vs 27/27 do not differ."""
    if not total:
        return 0.0, 1.0
    p = passed / total
    denominator = 1 + _Z * _Z / total
    centre = (p + _Z * _Z / (2 * total)) / denominator
    spread = (
        _Z
        * ((p * (1 - p) / total + _Z * _Z / (4 * total * total)) ** 0.5)
        / denominator
    )
    return max(0.0, centre - spread), min(1.0, centre + spread)


def scored_rows(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coding and tool-calling runs, each with its interval and its caveats."""
    rows: list[dict[str, Any]] = []
    for config in configs:
        for row in config.get("scored") or []:
            total = row.get("effective_n") or row.get("total") or 0
            if row.get("effective_k") is not None:
                passed = min(total, row["effective_k"])
            else:  # older manifests carry only the ratio
                passed = (
                    round(row.get("passed", 0) * total / row["total"])
                    if row.get("total")
                    else 0
                )
            low, high = wilson(passed, total)
            rows.append(
                {
                    "kind": str(config.get("kind", "")).replace("bench_", ""),
                    "label": str(
                        row.get("label") or row.get("model") or config.get("label", "")
                    ),
                    "passed": row.get("passed"),
                    "total": row.get("total"),
                    "pct": round(100 * passed / total) if total else 0,
                    "low": round(100 * low),
                    "high": round(100 * high),
                    "truncated": row.get("truncated") or 0,
                    "errored": row.get("errored") or 0,
                    "wall": row.get("total_wall_s"),
                    "deterministic": bool(row.get("deterministic")),
                }
            )
    rows.sort(key=lambda r: (r["passed"] or 0) / (r["total"] or 1), reverse=True)
    return rows


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _answer_s(row: dict[str, Any]) -> float | None:
    """Seconds to a FINISHED answer; None for a row cut at max_tokens.

    Mirrors orchestrant.benchmark.answers.row_answer_s -- the viewer does not
    import the package. Reports older than the `answered` field fall back to
    the old reading, which counted time to the cap as time to an answer.
    """
    if "answered" in row:
        return row.get("wall_s_to_answer") if row["answered"] else None
    return row.get("wall_s_to_answer", row.get("latency_s"))


def _think(row: dict[str, Any]) -> float | None:
    """The thinking share, reading an old report's never-closed <think> as 1.0.

    Before `answered` existed such a row scored 0.0; averaged in, it made a
    run that was ~95 % thinking read ~30 %. Mirrors answers.row_thinking_share.
    """
    share = row.get("thinking_char_share")
    if (
        share == 0.0
        and "answered" not in row
        and str(row.get("content_preview") or "").lstrip().startswith("<think>")
    ):
        return 1.0
    return share


def _answer_column(rows: list[dict[str, Any]]) -> str:
    """Mean seconds to an answer over the rows that have one, and how many.

    A run that cut 5 of 9 replies averages its 4 shortest; without "(4/9)" it
    would rank as the fastest configuration.
    """
    mean = _mean([s for s in map(_answer_s, rows) if s is not None])
    flagged = [r for r in rows if "answered" in r]
    done = sum(1 for r in flagged if r["answered"])
    cut = f" ({done}/{len(flagged)})" if flagged and done < len(flagged) else ""
    return _fmt(mean, 1) + cut


def _fmt(value: float | None, digits: int) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _result_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    """The requests that returned a reply: no `error` KEY, as speed_summary.

    The runner writes str(e), "" for an exception raised without a message.
    Dropping only a truthy `error` served such a row: its latency entered
    "Answer" as a time to an answer, and the card counted no error where the
    runner's summary and the `speed` block counted one.
    """
    return [r for r in config.get("results", []) if "error" not in r]


# A row field -> the run's headline figure for it in the manifest's `speed`
# block, which `report manifest` computes with the speed runner's own
# summariser (orchestrant.benchmark.speed_summary). The viewer used to average
# the rows itself, and charted 18.3 tok/s as "overall" for a run whose own
# table printed 25.4 under that name.
_SPEED = {
    "ttft_s": "ttft_s",
    "decode_tok_per_sec": "decode_tok_s",
    "tokens_per_sec": "overall_tok_s",
}


def _speed(config: dict[str, Any], key: str) -> float | None:
    """One headline figure; None for a manifest written before the block."""
    value = (config.get("speed") or {}).get(key)
    return value if isinstance(value, (int, float)) else None


def comparison_rows(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per config; the speed figures are the manifest's, not re-averaged."""
    rows = []
    for config in configs:
        ok = _result_rows(config)
        think = _mean([s for s in map(_think, ok) if s is not None])
        label = str(config.get("label", ""))
        ctx = label.split("ctx", 1)[1].split("_", 1)[0] if "ctx" in label else "?"
        tok = label.split("tok", 1)[1].split("_", 1)[0] if "tok" in label else "?"
        rows.append(
            {
                "label": label,
                "ctx": ctx,
                "tok": tok,
                "tps": _fmt(_speed(config, "overall_tok_s"), 1),
                "ttft": _fmt(_speed(config, "ttft_s"), 2),
                "decode": _fmt(_speed(config, "decode_tok_s"), 1),
                "think": f"{100 * think:.0f}%" if think is not None else "-",
                "answer": _answer_column(ok),
                "cpu": _fmt(
                    _mean([r["cpu_percent"] for r in ok if "cpu_percent" in r]), 1
                ),
                "ram": _fmt(
                    _mean([r["ram_used_gb"] for r in ok if "ram_used_gb" in r]), 1
                ),
                "gpu": _fmt(
                    _mean(
                        [
                            r["gpu_utilization_percent"]
                            for r in ok
                            if r.get("gpu_utilization_percent") is not None
                        ]
                    ),
                    1,
                ),
                "completion": sum(r.get("completion_tokens", 0) for r in ok),
                "prompt": sum(r.get("prompt_tokens", 0) for r in ok),
                "ok": len(ok),
            }
        )
    return rows


def chart_series(
    configs: list[dict[str, Any]], data_key: str, digits: int = 1
) -> list[dict[str, Any]]:
    """Bars for one metric; a config with no result carrying it is dropped.

    Treating a missing value as 0 would draw a bar claiming an instant first
    token, which is worse than drawing nothing. A speed field charts the
    run's headline figure from the manifest, as the comparison table shows it.
    """
    series = []
    for config in configs:
        if data_key in _SPEED:
            value = _speed(config, _SPEED[data_key])
        else:
            values = [
                r[data_key]
                for r in _result_rows(config)
                if isinstance(r.get(data_key), (int, float))
            ]
            value = _mean(values)
        if value is not None:
            series.append(
                {"name": str(config.get("label", "")), "value": round(value, digits)}
            )
    return series


def summary_stats(configs: list[dict[str, Any]]) -> dict[str, Any]:
    """The ModelCard's six numbers; `avg_tps` is the runs' overall tok/s, averaged."""
    requests = sum(len(_result_rows(c)) for c in configs)
    errors = sum(len(c.get("results", [])) - len(_result_rows(c)) for c in configs)
    per_config = [_speed(c, "overall_tok_s") for c in configs]
    return {
        "configs": len(configs),
        "requests": requests,
        "errors": errors,
        "avg_tps": _fmt(_mean([v for v in per_config if v is not None]), 1),
    }


def detail_rows(config: dict[str, Any]) -> list[dict[str, str]]:
    """ConfigDetail's key/value table: extra_params first, then the rest."""
    import json

    extra = (config.get("config") or {}).get("extra_params") or {}
    rows = [{"label": str(k), "value": json.dumps(v)} for k, v in extra.items()]
    rows += [
        {"label": str(k), "value": json.dumps(v)}
        for k, v in (config.get("config") or {}).items()
        if k not in _DETAIL_SKIP
    ]
    return rows


def per_prompt_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    """The per-prompt table. Missing metrics render '-', never a fake 0.

    A reply cut at max_tokens reads "cut", as in the runner's own table: it
    is a known outcome, where '-' means the report never measured the field.
    """
    rows = []
    for result in _result_rows(config):
        busiest = (result.get("top_processes") or [{}])[0]
        think = _think(result)
        rows.append(
            {
                "index": result.get("prompt_index", 0),
                "prompt": str(result.get("prompt_preview") or ""),
                "pt": result.get("prompt_tokens"),
                "ct": result.get("completion_tokens"),
                "estimated": bool(result.get("tokens_estimated")),
                "answer": (
                    "cut"
                    if result.get("answered") is False
                    else _fmt(_answer_s(result), 1)
                ),
                **_prompt_lab_fields(result, config.get("cpu_threads")),
                "ttft": _fmt(result.get("ttft_s"), 2),
                "decode": _fmt(result.get("decode_tok_per_sec"), 1),
                "prefill": (
                    f"{result['prefill_tok_per_sec']:.0f}"
                    if result.get("prefill_tok_per_sec") is not None
                    else "-"
                ),
                "think": "-" if think is None else f"{100 * think:.0f}%",
                "tps": _fmt(result.get("tokens_per_sec"), 1),
                "cpu": _fmt(result.get("cpu_percent"), 1),
                "ram": _fmt(result.get("ram_used_gb"), 2),
                "gpu": _fmt(result.get("gpu_utilization_percent"), 1),
                "busiest": f"{busiest.get('name', '-')} {busiest.get('cpu_percent', '')}".strip(),
            }
        )
    return rows


def prompt_errors(config: dict[str, Any]) -> list[dict[str, Any]]:
    """The Errors list under the per-prompt table."""
    return [
        {"index": r.get("prompt_index"), "error": str(r.get("error", ""))}
        for r in config.get("results", [])
        if "error" in r  # the rows _result_rows leaves out, empty message or not
    ]


def scored_table_exists(configs: list[dict[str, Any]]) -> bool:
    return any(config.get("scored") for config in configs)


def _other_cores(row: dict[str, Any], ncpu: Any) -> tuple[float | None, bool]:
    """(other load in cores, derived?) -- mirrors benchmarks/compare_speed.py.

    The field arrived after the first v0.7.0 runs; for an older row it is
    derived as the runner computes it (cpu_percent x threads - lane_cores,
    window rows only), and flagged so the cell can say it was.
    """
    if row.get("other_cores") is not None:
        return row["other_cores"], False
    window = row.get("cpu_percent_method") == "window"
    lane, cpu = row.get("lane_cores"), row.get("cpu_percent")
    if ncpu and window and lane is not None and cpu is not None:
        return max(0.0, cpu / 100.0 * ncpu - lane), True
    return None, False


def _prompt_lab_fields(result: dict[str, Any], ncpu: Any) -> dict[str, str]:
    """One request's first-answer time, load and CPU-rail joules per token."""
    other, derived = _other_cores(result, ncpu)
    return {
        "ttfa": _fmt(result.get("ttfa_s"), 2),
        "lane": _fmt(result.get("lane_cores"), 2),
        "other": _fmt(other, 2) + ("*" if derived else ""),
        "jtok": _fmt(result.get("cpu_rail_j_per_token"), 3),
        "jnet": _fmt(result.get("cpu_rail_net_j_per_token"), 3),
    }
