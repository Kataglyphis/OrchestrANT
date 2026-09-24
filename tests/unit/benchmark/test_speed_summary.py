"""One speed summary per run, and every printer reading it.

On 2026-09-24 the tracked `v070-npu-speed.json` printed three headline rates:
the runner's table said `Tokens/sec 18.3 avg`, `Overall ... 25.4 tok/s` and
`Decode only 19.7 tok/s avg`, `report table` said `T/s: 18.3`, and the viewer
charted 18.3 under the runner's name for 25.4. These pin one definition per
figure and that the runner's table, `report summary`, `report table` and the
viewer's comparison row print the same number under the same name.
"""

import json
import re
from pathlib import Path

import pytest

from frontend.frontend import benchmark_data
from orchestrant.benchmark import report, speed_summary
from orchestrant.benchmark.openai_api import print_table


def row(completion, seconds, *, ttft=0.2, prompt=20, index=0, **extra):
    """A speed row as benchmark_chat writes one, its rates derived from its times."""
    decode = None
    if ttft is not None and completion > 1 and seconds > ttft:
        decode = round((completion - 1) / (seconds - ttft), 2)
    out = {
        "prompt_index": index,
        "prompt_preview": f"prompt {index}",
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "tokens_per_sec": round(completion / seconds, 2),
        "latency_s": seconds,
        "ttft_s": ttft,
        "decode_tok_per_sec": decode,
        "prefill_tok_per_sec": round(prompt / ttft, 1) if ttft and prompt else None,
        "cpu_percent": 20.0,
        "ram_used_gb": 4.0,
    }
    out.update(extra)
    return out


# An 8-token reply decoding at 10 tok/s over 0.7 s, and a 257-token one at 20
# tok/s over 12.8 s: the shape of the speed runner's one-liners beside its
# 256-token replies.
SHORT = row(8, 0.9, index=0)
LONG = row(257, 13.2, prompt=160, ttft=0.4, index=1)


class TestRatesArePooled:
    """A rate is the run's tokens over the seconds they took, not a mean of
    per-request rates: an 8-token reply must not weigh as much as a 256-token one.
    """

    def test_decode_is_tokens_after_the_first_over_their_seconds(self):
        # 7 + 256 tokens over 0.7 + 12.8 s: 19.5, where the mean of the rates is 15.
        assert (SHORT["decode_tok_per_sec"], LONG["decode_tok_per_sec"]) == (10.0, 20.0)
        assert speed_summary.decode_tok_s([SHORT, LONG]) == pytest.approx(263 / 13.5)

    def test_overall_counts_completion_tokens_only(self):
        # The runner's old "Overall" added the 180 prompt tokens: 31.6 here.
        assert speed_summary.overall_tok_s([SHORT, LONG]) == pytest.approx(265 / 14.1)

    def test_prefill_is_prompt_tokens_over_their_ttfts(self):
        # 20 at 100 tok/s and 160 at 400: 180 over 0.6 s, where the mean says 250.
        assert speed_summary.prefill_tok_s([SHORT, LONG]) == pytest.approx(300.0)

    def test_ttft_is_a_plain_mean_of_times(self):
        s = speed_summary.summarise([SHORT, LONG])
        assert s["ttft_s"] == pytest.approx(0.3)
        assert s["per_request"]["ttft_s"] == [0.2, 0.4]


class TestErroredRows:
    """An errored request has no tokens and no decode: it leaves every figure
    and is counted apart -- even though its row carries a latency.
    """

    def test_an_errored_row_is_counted_not_averaged(self):
        failed = {
            "prompt_index": 2,
            "prompt_preview": "p",
            "error": "boom",
            "latency_s": 30.0,
        }
        s = speed_summary.summarise([SHORT, LONG, failed])
        assert (s["requests"], s["errored"]) == (2, 1)
        assert s["overall_tok_s"] == pytest.approx(265 / 14.1)
        assert s["wall_s"] == pytest.approx(14.1)

    def test_an_error_with_an_empty_message_is_still_an_error(self):
        # str(e) of an exception raised without a message is "".
        failed = {
            "prompt_index": 2,
            "prompt_preview": "p",
            "error": "",
            "latency_s": 30.0,
        }
        s = speed_summary.summarise([LONG, failed])
        assert (s["requests"], s["errored"]) == (1, 1)

    def test_a_run_that_only_errored_has_no_figures(self):
        s = speed_summary.summarise([{"prompt_index": 0, "error": "refused"}])
        assert s["requests"] == 0
        assert (s["overall_tok_s"], s["decode_tok_s"], s["ttft_s"]) == (None,) * 3


class TestCutAndThinkingRows:
    """A reply cut at max_tokens, or one that never left <think>, still produced
    and timed every token: it counts in every rate. Only the time to an ANSWER
    leaves it out, and answers.py owns that.
    """

    def test_a_cut_row_counts_in_every_rate(self):
        cut = row(257, 13.0, index=2, finish_reason="length", answered=False)
        with_cut = speed_summary.summarise([SHORT, LONG, cut])
        assert with_cut["decode_tok_s"] == pytest.approx((263 + 256) / (13.5 + 12.8))
        assert with_cut["completion_tokens"] == 8 + 257 + 257

    def test_a_thinking_only_row_counts_like_any_other(self):
        thinking = row(
            257,
            13.0,
            index=2,
            answered=False,
            thinking_char_share=1.0,
            content_preview="<think>\nOkay, the user",
        )
        plain = row(257, 13.0, index=2)
        assert speed_summary.summarise([SHORT, thinking]) == speed_summary.summarise(
            [SHORT, plain]
        )


class TestZeroAndOneTokenRows:
    """A request that returned nothing still cost the caller its wall time;
    it has no decode window, and neither has a one-token reply, whose only
    token the prefill produced.
    """

    def test_a_zero_token_row_slows_overall_and_leaves_decode_alone(self):
        empty = row(0, 2.0, ttft=None, index=2)
        s = speed_summary.summarise([LONG, empty])
        assert s["requests"] == 2
        assert s["overall_tok_s"] == pytest.approx(257 / 15.2)
        assert s["decode_tok_s"] == pytest.approx(20.0)

    def test_a_one_token_row_has_no_decode(self):
        one = row(1, 0.3, index=2)
        assert one["decode_tok_per_sec"] is None
        assert speed_summary.decode_tok_s([one]) is None
        assert speed_summary.overall_tok_s([one]) == pytest.approx(1 / 0.3)


class TestUnstreamedAndEstimatedRows:
    def test_a_non_streamed_run_has_no_ttft_decode_or_prefill(self):
        # No first-token moment without --stream; 0.00 s would claim an instant one.
        s = speed_summary.summarise([row(100, 5.0, ttft=None)])
        assert (s["ttft_s"], s["decode_tok_s"], s["prefill_tok_s"]) == (None,) * 3
        assert s["overall_tok_s"] == pytest.approx(20.0)
        assert "not measured" in "\n".join(speed_summary.summary_lines(s))

    def test_chunk_counted_rows_are_counted_and_said(self):
        s = speed_summary.summarise([row(100, 5.0, tokens_estimated=True), LONG])
        assert s["estimated"] == 1
        assert "1 of 2 token counts" in "\n".join(speed_summary.summary_lines(s))


def _figure(name, text):
    match = re.search(rf"{name}:\s+([\d.]+)", text)
    return match.group(1) if match else None


def _viewer_row(tmp_path, rows):
    (tmp_path / "run.json").write_text(json.dumps({"results": rows}))
    configs = report.build_manifest(str(tmp_path), "T", "m", "now")["configs"]
    return benchmark_data.comparison_rows(configs)[0]


class TestEveryPrinterPrintsOneHeadline:
    """The runner's table, `report summary`, `report table` and the viewer's
    comparison row: the same rows give the same number under the same name.
    """

    ROWS = (
        SHORT,
        LONG,
        row(257, 14.0, index=2, finish_reason="length", answered=False),
        row(0, 2.0, ttft=None, index=3),
        {"prompt_index": 4, "prompt_preview": "p", "error": "boom", "latency_s": 9.0},
    )

    def test_decode_overall_and_ttft_agree(self, tmp_path, capsys):
        rows = list(self.ROWS)
        print_table(rows)
        table = capsys.readouterr().out
        s = report.summarise({"results": rows})
        one_line, table_row = report.summary_line(s), report.table_line("run", s)
        viewer = _viewer_row(tmp_path, rows)
        decode = speed_summary.decode_tok_s(speed_summary.completed(rows))
        overall = speed_summary.overall_tok_s(speed_summary.completed(rows))
        for text in (table, one_line, table_row):
            assert _figure("Decode", text) == f"{decode:.1f}"
            assert _figure("Overall", text) == f"{overall:.1f}"
        assert (viewer["decode"], viewer["tps"]) == (f"{decode:.1f}", f"{overall:.1f}")
        ttft = f"{(0.2 + 0.4 + 0.2) / 3:.2f}"
        assert re.search(rf"TTFT:\s+[\d.]+s\s+/\s+{ttft}s avg", table)
        assert f"TTFT: {ttft}s" in one_line and f"TTFT:  {ttft}s" in table_row
        assert viewer["ttft"] == ttft

    def test_the_old_names_are_gone(self, capsys):
        # "Tokens/sec ... avg" and "T/s:" were the mean of per-request rates,
        # "Decode only" its decode twin: three names, two for one quantity.
        print_table(list(self.ROWS))
        s = report.summarise({"results": list(self.ROWS)})
        text = (
            capsys.readouterr().out + report.summary_line(s) + report.table_line("r", s)
        )
        assert "Tokens/sec" not in text and "Decode only" not in text
        assert "T/s:" not in text

    def test_the_summary_header_counts_the_errored_request(self, capsys):
        print_table(list(self.ROWS))
        assert "Summary (4 requests, 1 errored):" in capsys.readouterr().out

    def test_an_error_without_a_message_is_an_error_to_every_printer(
        self, tmp_path, capsys
    ):
        # The runner writes str(e), "" for an exception raised without a
        # message. The viewer dropped only a truthy `error`, so it served this
        # request, averaged its 30 s into "Answer" (15.4 s against the 0.9 s
        # `report table` printed) and counted no error the runner's header did.
        rows = [
            row(8, 0.9, index=0, answered=True, wall_s_to_answer=0.9),
            {"prompt_index": 1, "prompt_preview": "p", "error": "", "latency_s": 30.0},
        ]
        print_table(rows)
        assert "Summary (1 requests, 1 errored):" in capsys.readouterr().out
        s = report.summarise({"results": rows})
        (tmp_path / "run.json").write_text(json.dumps({"results": rows}))
        configs = report.build_manifest(str(tmp_path), "T", "m", "now")["configs"]
        viewer = benchmark_data.comparison_rows(configs)[0]
        card = benchmark_data.summary_stats(configs)
        assert (s["requests"], s["errored"]) == (1, 1)
        assert (viewer["ok"], card["requests"], card["errors"]) == (1, 1, 1)
        assert viewer["answer"] == f"{s['answer_s']:.1f}" == "0.9"
        assert [r["index"] for r in benchmark_data.per_prompt_rows(configs[0])] == [0]
        assert benchmark_data.prompt_errors(configs[0]) == [{"index": 1, "error": ""}]


TRACKED_RUN = (
    Path(__file__).resolve().parents[3]
    / "benchmarks"
    / "benchmark_results"
    / "2026-09-23-geniex-upgrade"
)


def tracked(name):
    with open(TRACKED_RUN / f"{name}.json", encoding="utf-8") as f:
        return speed_summary.summarise(json.load(f)["results"])


class TestTheTrackedRunReproducesThePage:
    """benchmarks/docs/geniex-v0.7.0-cpu-npu-2026-09-24.md's speed tables, from
    the tracked reports. The decode column was a mean of per-request rates
    until OPS-6 (22.7, 19.7 -13 %, 18.4, 19.4 +5 %; `--log none` 22.5); pooled
    it moves by at most 1.3 %, and the v0.6.1 -> v0.7.0 NPU loss moves to the
    per-prompt median `bench_compare` prints. TTFT did not move.
    """

    @pytest.mark.parametrize(
        ("name", "decode", "ttft"),
        [
            ("v061-npu-speed", "23.0", "0.16"),
            ("v070-npu-speed", "19.6", "0.16"),
            ("v061-cpu-speed", "18.2", "0.29"),
            ("v070-cpu-speed", "19.2", "0.30"),
            ("v070-npu-speed-lognone", "22.4", "0.15"),
        ],
    )
    def test_decode_and_ttft(self, name, decode, ttft):
        s = tracked(name)
        assert (f"{s['decode_tok_s']:.1f}", f"{s['ttft_s']:.2f}") == (decode, ttft)

    def test_the_upgrade_changes(self):
        def change(a, b):
            return round(
                100 * (tracked(b)["decode_tok_s"] / tracked(a)["decode_tok_s"] - 1)
            )

        assert change("v061-npu-speed", "v070-npu-speed") == -15
        assert change("v061-cpu-speed", "v070-cpu-speed") == 5
        # --log info against none on the same build: the page's -13 % holds.
        assert change("v070-npu-speed-lognone", "v070-npu-speed") == -13

    def test_overall_is_what_the_lane_generated(self):
        # Not 25.4 (prompt tokens counted) nor 18.3 (mean of per-request rates).
        s = tracked("v070-npu-speed")
        assert (s["requests"], s["completion_tokens"]) == (9, 1336)
        assert f"{s['overall_tok_s']:.1f}" == "19.3"

    def test_a_long_reply_run_reads_its_long_replies(self):
        # The mean of per-request rates said 19.2; most of the decoding ran at 12-13.
        assert f"{tracked('v070r2-cpu-speed-answer')['decode_tok_s']:.1f}" == "13.9"


class TestThePrintedLinesCarryTheSummary:
    """What the lines print beside the headline: each range, the prefill and
    the error count. The tests above read only the headline number, so a
    Decode line showing Overall's range, a Prefill line off by 10 % or a
    `report table` row that dropped ERRORS all passed them.
    """

    def test_the_tracked_run_prints_its_own_figures(self):
        # v070-npu-speed.log printed `Tokens/sec 16.2 / 18.3 avg / 19.5`,
        # `Decode only 19.7` and `Prefill 273 tok/s avg` (means of rates).
        lines = speed_summary.summary_lines(tracked("v070-npu-speed"))
        by_name = {line.split(":", 1)[0].strip(): line for line in lines}
        assert "1336 total  /  148.4 avg per req" in by_name["Completion tok"]
        assert by_name["Overall"].startswith("    Overall:        19.3 tok/s")
        assert by_name["Overall"].endswith("(per request 16.2-19.5)")
        assert by_name["Decode"].startswith("    Decode:         19.6 tok/s")
        assert by_name["Decode"].endswith("(per request 19.4-19.9)")
        assert "0.14s  /  0.16s avg  /  0.25s max" in by_name["TTFT"]
        assert by_name["Prefill"].startswith("    Prefill:        295 tok/s")

    def test_the_average_reply_and_the_report_lines_leave_the_error_out(self, capsys):
        rows = list(TestEveryPrinterPrintsOneHeadline.ROWS)
        print_table(rows)
        # 8 + 257 + 257 + 0 tokens over the four requests that returned.
        assert "522 total  /  130.5 avg per req" in capsys.readouterr().out
        s = report.summarise({"results": rows})
        assert report.summary_line(s).endswith("  ERRORS: 1")
        assert report.table_line("run", s).endswith("  ERRORS: 1")
