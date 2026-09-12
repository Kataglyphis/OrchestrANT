"""Pure shaping of a benchmark manifest for the viewer.

No Reflex import here on purpose: every table and every interval is a plain
function over the manifest dict, so it is testable without the frontend extra
and without a browser. The viewer module only renders what these return.
"""

from __future__ import annotations

from typing import Any

_Z = 1.96
_DETAIL_SKIP = ("extra_params", "prompts_requested", "prompts_completed")


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
    outranks every speed number on the page.
    """
    scored = [c for c in configs if c.get("correctness")]
    if not scored:
        return {
            "checked": False,
            "state": "unknown",
            "headline": "not checked",
            "score": 0,
            "total": 0,
            "rows": [],
        }
    total = sum(c["correctness"].get("total", 0) for c in scored)
    score = sum(c["correctness"].get("score", 0) for c in scored)
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


def _fmt(value: float | None, digits: int) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _result_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in config.get("results", []) if not r.get("error")]


def comparison_rows(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per config, averaging ONLY over results carrying each metric."""
    rows = []
    for config in configs:
        ok = _result_rows(config)
        ttft = _mean([r["ttft_s"] for r in ok if r.get("ttft_s") is not None])
        decode = _mean(
            [
                r["decode_tok_per_sec"]
                for r in ok
                if r.get("decode_tok_per_sec") is not None
            ]
        )
        think = _mean(
            [
                r["thinking_char_share"]
                for r in ok
                if r.get("thinking_char_share") is not None
            ]
        )
        label = str(config.get("label", ""))
        ctx = label.split("ctx", 1)[1].split("_", 1)[0] if "ctx" in label else "?"
        tok = label.split("tok", 1)[1].split("_", 1)[0] if "tok" in label else "?"
        rows.append(
            {
                "label": label,
                "ctx": ctx,
                "tok": tok,
                "tps": _fmt(
                    _mean([r["tokens_per_sec"] for r in ok if "tokens_per_sec" in r]), 1
                ),
                "ttft": _fmt(ttft, 2),
                "decode": _fmt(decode, 1),
                "think": f"{100 * think:.0f}%" if think is not None else "-",
                "answer": _fmt(
                    _mean(
                        [
                            r.get("wall_s_to_answer", r.get("latency_s"))
                            for r in ok
                            if r.get("wall_s_to_answer") or r.get("latency_s")
                        ]
                    ),
                    1,
                ),
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
    token, which is worse than drawing nothing.
    """
    series = []
    for config in configs:
        values = [
            r[data_key]
            for r in _result_rows(config)
            if isinstance(r.get(data_key), (int, float))
        ]
        if values:
            series.append(
                {
                    "name": str(config.get("label", "")),
                    "value": round(sum(values) / len(values), digits),
                }
            )
    return series


def summary_stats(configs: list[dict[str, Any]]) -> dict[str, Any]:
    """The ModelCard's six numbers."""
    requests = sum(len(_result_rows(c)) for c in configs)
    errors = sum(len(c.get("results", [])) - len(_result_rows(c)) for c in configs)
    per_config = []
    for config in configs:
        tps = [
            r["tokens_per_sec"]
            for r in _result_rows(config)
            if r.get("tokens_per_sec") is not None
        ]
        if tps:
            per_config.append(sum(tps) / len(tps))
    return {
        "configs": len(configs),
        "requests": requests,
        "errors": errors,
        "avg_tps": _fmt(_mean(per_config), 1),
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
    """The per-prompt table. Missing metrics render '-', never a fake 0."""
    rows = []
    for result in _result_rows(config):
        busiest = (result.get("top_processes") or [{}])[0]
        rows.append(
            {
                "index": result.get("prompt_index", 0),
                "prompt": str(result.get("prompt_preview") or ""),
                "pt": result.get("prompt_tokens"),
                "ct": result.get("completion_tokens"),
                "estimated": bool(result.get("tokens_estimated")),
                "answer": _fmt(
                    result.get("wall_s_to_answer", result.get("latency_s")), 1
                ),
                "ttft": _fmt(result.get("ttft_s"), 2),
                "decode": _fmt(result.get("decode_tok_per_sec"), 1),
                "prefill": (
                    f"{result['prefill_tok_per_sec']:.0f}"
                    if result.get("prefill_tok_per_sec") is not None
                    else "-"
                ),
                "think": (
                    f"{100 * result['thinking_char_share']:.0f}%"
                    if result.get("thinking_char_share") is not None
                    else "-"
                ),
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
        if r.get("error")
    ]


def scored_table_exists(configs: list[dict[str, Any]]) -> bool:
    return any(config.get("scored") for config in configs)
