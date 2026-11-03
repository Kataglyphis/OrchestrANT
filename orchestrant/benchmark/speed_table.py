"""The speed lane's console table and its summary block."""

from typing import Any

from orchestrant.benchmark import answers, speed_summary
from orchestrant.benchmark.hostload import summary_lines


def print_separator(char="━", width=80):
    print(char * width)


def _spread_line(label: str, values: list[float], unit: str, digits: int) -> str:
    """One `min / avg / max` summary row."""
    lo, avg, hi = min(values), sum(values) / len(values), max(values)
    return f"    {label:<16}{lo:.{digits}f}{unit}  /  {avg:.{digits}f}{unit} avg  /  {hi:.{digits}f}{unit} max"


def _table_row(r: dict[str, Any], *, has_gpu: bool) -> list[object]:
    """The cells of one successful result."""
    ttft = r.get("ttft_s")
    values = [
        r["prompt_index"],
        r["prompt_preview"][:38],
        r.get("prompt_tokens", "-"),
        r.get("completion_tokens", "-"),
        r.get("tokens_per_sec", "-"),
        f"{ttft:.2f}" if ttft is not None else "-",
        answers.answer_cell(r),
        r.get("cpu_percent", "-"),
        r.get("ram_used_gb", "-"),
    ]
    if has_gpu:
        values.append(r.get("gpu_utilization_percent", "-"))
    return values


def _print_rows(results: list[dict[str, Any]], *, has_gpu: bool) -> None:
    """The per-request table between its separators."""
    headers = ["#", "Prompt", "PT", "CT", "T/s", "TTFT", "Ans(s)", "CPU%", "RAM(GB)"]
    col_widths = [3, 38, 5, 5, 7, 7, 8, 7, 8]
    if has_gpu:
        headers.append("GPU%")
        col_widths.append(6)

    def fmt_row(values: list[object] | list[str]) -> str:
        return "  ".join(f"{str(v)[:w]:{w}}{'':>1}" for v, w in zip(values, col_widths))

    print_separator("─")
    print(fmt_row(headers))
    print_separator("─")

    for r in results:
        if "error" in r:
            print(
                f"  {r['prompt_index']:<3}  {r['prompt_preview'][:38]:<38}  ERROR: {r['error'][:40]}"
            )
            continue
        print(fmt_row(_table_row(r, has_gpu=has_gpu)))

    print_separator("─")
    print()


def _print_summary(results: list[dict[str, Any]], latencies: list[float]) -> None:
    """The spread rows and the attributed summaries under the table."""
    ok = [r for r in results if "error" not in r]
    speed = speed_summary.summarise(results)
    errored = f", {speed['errored']} errored" if speed["errored"] else ""
    print(f"  Summary ({speed['requests']} requests{errored}):")
    print(_spread_line("Latency:", latencies, "s", 2))
    cpu_vals = [r["cpu_percent"] for r in ok if "cpu_percent" in r]
    ram_vals = [r["ram_used_gb"] for r in ok if "ram_used_gb" in r]
    print(_spread_line("CPU:", cpu_vals, "%", 1))
    print(_spread_line("RAM used:", ram_vals, "GB", 2))
    for key, label, unit in (
        ("gpu_utilization_percent", "GPU util:", "%"),
        ("gpu_power_watts", "GPU power:", "W"),
    ):
        vals = [r[key] for r in results if r.get(key) is not None]
        if vals:
            print(_spread_line(label, vals, unit, 1))

    # speed_summary's figures, the ones `report` and the viewer print too.
    for line in speed_summary.summary_lines(speed):
        print(line)

    # Who burned the CPU, measured and attributed.
    for line in summary_lines(results):
        print(line)

    # Rank by time to a FINISHED answer, not by tok/s.
    for line in answers.summary_lines(results):
        print(line)


def print_table(results):
    """Print benchmark results as a formatted table."""
    if not results:
        print("No results to display.")
        return

    has_gpu = any(
        r.get("gpu_utilization_percent") is not None
        for r in results
        if "error" not in r
    )
    _print_rows(results, has_gpu=has_gpu)
    latencies = [
        r["latency_s"] for r in results if "latency_s" in r and "error" not in r
    ]
    if latencies:
        _print_summary(results, latencies)
