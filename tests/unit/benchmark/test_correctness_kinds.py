"""The correctness probe's two kinds of item, and a verdict over one of them.

The probe printed OK/DEGRADED/BROKEN with "wrong answers here usually mean
broken kernels or an over-aggressive quant". The 2026-09-24 campaign showed
that false for small non-thinking models on healthy lanes: the Qwen3 instruct
4B and Coder-7B miss only the strawberry count, Llama-3.2-3B and Phi-4-mini
three items each -- and upgrade_check failed a speed step on any of them.
These pin the split (integrity: what a broken kernel loses; capability: what
the model cannot do), a verdict and exit code over integrity alone, and that
reports written before kinds existed still read.
"""

import json
from pathlib import Path

import pytest

from orchestrant.benchmark import correctness, openai_api
from orchestrant.benchmark.correctness import (
    CAPABILITY,
    CORRECTNESS_PROBES,
    INTEGRITY,
    by_kind,
    exit_code,
    graded_item,
    summarise,
    verdict,
)


CAMPAIGN = (
    Path(__file__).resolve().parents[3]
    / "benchmarks"
    / "benchmark_results"
    / "2026-09-24-roadmap"
)

# The six prompts every tracked speed report asked, as its items record them.
HISTORIC = {
    "What is 23 * 17? Reply with only the number.": INTEGRITY,
    "What is 17 squared? Reply with only the number.": INTEGRITY,
    "What is the capital of Australia? Reply with only the city n": CAPABILITY,
    "How many times does the letter 'r' appear in the word strawb": CAPABILITY,
    "If 5 machines make 5 widgets in 5 minutes, how many minutes ": CAPABILITY,
    "Which number is larger, 9.11 or 9.9? Reply with only the num": CAPABILITY,
}


def answered(wrong=(), cut=()):
    """Every probe answered with its first accepted form, except `wrong`.

    `wrong` items answer "0" and `cut` ones never leave <think>; both are
    matched by the probe's expected value.
    """
    items = []
    for probe in CORRECTNESS_PROBES:
        expected = probe.accepted[0]
        content = probe.accepted[0]
        if expected in wrong:
            content = "0"
        if expected in cut:
            content = "<think>still counting"
        items.append(graded_item(probe, content))
    return summarise(items)


def campaign_blocks():
    """(file name, correctness block) for every 2026-09-24 speed-answer report."""
    if not CAMPAIGN.is_dir():
        pytest.skip("benchmarks/benchmark_results not present")
    blocks = []
    for path in sorted(CAMPAIGN.glob("*speed-answer.json")):
        block = json.loads(path.read_text(encoding="utf-8")).get("correctness")
        if block:
            blocks.append((path.name, block))
    return blocks


class TestTheTable:
    """Every item has a kind, and the prompt preview an item records names it."""

    def test_every_probe_has_a_known_kind(self):
        assert {p.kind for p in CORRECTNESS_PROBES} == {INTEGRITY, CAPABILITY}

    def test_the_historic_prompts_are_unchanged_and_keep_the_evidence_kinds(self):
        # Old reports are split by looking their prompt previews up here, and
        # bench_compare pairs by them: an edited prompt would orphan both.
        table = {p.prompt[:60]: p.kind for p in CORRECTNESS_PROBES}
        for preview, kind in HISTORIC.items():
            assert table.get(preview) == kind, preview

    def test_previews_are_unique_so_they_can_key_a_case(self):
        previews = [p.prompt[:60] for p in CORRECTNESS_PROBES]
        assert len(previews) == len(set(previews))

    def test_the_verdict_no_longer_rests_on_two_multiplications(self):
        integrity = [p for p in CORRECTNESS_PROBES if p.kind == INTEGRITY]
        assert len(integrity) >= 5

    def test_the_table_is_hashed_into_the_speed_report(self):
        # It decides the correctness block, so an edit to it is "the grader
        # moved" to bench_compare -- and a name the hash cannot find nulls it.
        from orchestrant.benchmark.provenance import tool_fingerprint

        assert "correctness.py" in openai_api.SPEED_TOOL_FILES
        assert tool_fingerprint(*openai_api.SPEED_TOOL_FILES) is not None

    def test_every_integrity_item_asks_for_a_bare_answer(self):
        # Answerable in a handful of tokens: a non-thinking model's reply is
        # the answer itself, so a miss cannot be a budget artefact.
        for probe in CORRECTNESS_PROBES:
            if probe.kind == INTEGRITY:
                assert "Reply with only" in probe.prompt, probe.prompt


class TestTheEvidence:
    """The split is read off the 2026-09-24 reports.

    Every model on every lane answered every integrity item those reports
    asked, so an integrity miss there would have been a lane fault, not a
    model one.
    """

    def test_every_campaign_model_passes_every_integrity_item_it_was_asked(self):
        for name, block in campaign_blocks():
            for item in block["items"]:
                if HISTORIC.get(item["prompt"]) == INTEGRITY:
                    assert item["correct"], (name, item)

    def test_every_campaign_report_reads_ok_on_integrity(self):
        verdicts = {
            name: verdict(by_kind(b)[INTEGRITY]) for name, b in campaign_blocks()
        }
        assert verdicts
        assert set(verdicts.values()) == {"OK"}, verdicts

    def test_the_small_instruct_models_miss_only_capability_items(self):
        # The reason for the change: 3/6 read BROKEN on Llama-3.2-3B and
        # Phi-4-mini, 5/6 DEGRADED on the instruct 4B and Coder-7B.
        misses = {
            name: {item["prompt"] for item in block["items"] if not item["correct"]}
            for name, block in campaign_blocks()
        }
        assert any(misses.values())
        for name, missed in misses.items():
            assert {HISTORIC[p] for p in missed} <= {CAPABILITY}, name


class TestTheVerdict:
    """OK/DEGRADED/BROKEN is taken over the integrity items only."""

    def test_capability_misses_alone_are_ok(self):
        block = answered(wrong={"3", "5", "9.9", "canberra"})
        assert block["verdict"] == "OK"
        assert block[CAPABILITY]["wrong"] == 4

    def test_one_wrong_integrity_answer_is_degraded(self):
        assert answered(wrong={"391"})["verdict"] == "DEGRADED"

    def test_half_the_integrity_answers_wrong_is_broken(self):
        total = answered()[INTEGRITY]["total"]
        wrong = [p.accepted[0] for p in CORRECTNESS_PROBES if p.kind == INTEGRITY]
        assert answered(wrong=set(wrong[: (total + 1) // 2]))["verdict"] == "BROKEN"

    def test_an_integrity_answer_cut_off_is_inconclusive(self):
        assert answered(cut={"289"})["verdict"] == "INCONCLUSIVE"

    def test_a_capability_answer_cut_off_is_not(self):
        assert answered(cut={"3"})["verdict"] == "OK"

    def test_garbage_is_broken(self):
        items = [graded_item(p, " majorityathersyre") for p in CORRECTNESS_PROBES]
        assert summarise(items)["verdict"] == "BROKEN"

    def test_no_scored_integrity_answer_is_no_result(self):
        assert verdict(None) == correctness.NO_RESULT
        errors = {"score": 0, "total": 6, "wrong": 0, "truncated": 0, "errors": 6}
        assert verdict(errors) == correctness.NO_RESULT


class TestTheExitCode:
    """--correctness-only: 1 act on it, 2 re-run with a bigger budget, else 0."""

    def test_capability_misses_exit_zero(self):
        assert exit_code(answered(wrong={"3", "5", "9.9"})) == 0

    def test_an_integrity_miss_exits_one(self):
        assert exit_code(answered(wrong={"289"})) == 1

    def test_an_integrity_cut_exits_two(self):
        assert exit_code(answered(cut={"391"})) == 2

    def test_an_unreachable_endpoint_exits_one(self):
        assert exit_code(None) == 1

    def test_main_exits_on_the_integrity_items(self, monkeypatch):
        block = answered(wrong={"3"})
        monkeypatch.setattr(openai_api, "run_correctness_probe", lambda *a, **k: block)
        # main() rebinds both globals; restore them for the tests after this.
        for name in ("LLM_BASE_URL", "OLLAMA_BASE_URL"):
            monkeypatch.setattr(openai_api, name, getattr(openai_api, name))
        argv = ["speed", "--base-url", "http://lane:1", "--model", "m"]
        monkeypatch.setattr("sys.argv", [*argv, "--correctness-only"])
        with pytest.raises(SystemExit) as stop:
            openai_api.main()
        assert stop.value.code == 0


class TestTheRecord:
    """The report's block stays a superset of the fields it always had."""

    def test_the_old_fields_still_count_every_item(self):
        block = answered(wrong={"3", "391"})
        assert block["total"] == len(CORRECTNESS_PROBES) == len(block["items"])
        assert block["score"] == len(CORRECTNESS_PROBES) - 2
        assert (block["wrong"], block["truncated"], block["errors"]) == (2, 0, 0)

    def test_each_kind_is_counted_apart_and_each_item_names_its_kind(self):
        block = answered(wrong={"3", "391"})
        assert block[INTEGRITY]["wrong"] == 1
        assert block[CAPABILITY]["wrong"] == 1
        assert {item["kind"] for item in block["items"]} == {INTEGRITY, CAPABILITY}
        integrity = block[INTEGRITY]["total"] + block[CAPABILITY]["total"]
        assert integrity == block["total"]

    def test_every_answer_errored_is_no_block(self):
        items = [
            correctness.errored_item(p, OSError("refused")) for p in CORRECTNESS_PROBES
        ]
        assert summarise(items) is None

    def test_the_runner_records_kinds(self, monkeypatch):
        class Reply:
            def __init__(self, content):
                self.content = content

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def json(self):
                return {"choices": [{"message": {"content": self.content}}]}

        answers = {p.prompt: p.accepted[0] for p in CORRECTNESS_PROBES}
        monkeypatch.setattr(
            openai_api,
            "post_json",
            lambda url, payload, **k: Reply(answers[payload["messages"][0]["content"]]),
        )
        block = openai_api.run_correctness_probe("m", base_url="http://lane:1")
        assert block["verdict"] == "OK"
        assert [item["kind"] for item in block["items"]] == [
            p.kind for p in CORRECTNESS_PROBES
        ]


class TestOldReports:
    """A block written before kinds existed is split by its prompts."""

    def test_an_old_block_takes_its_kinds_from_the_prompts(self):
        _, block = next(
            (n, b) for n, b in campaign_blocks() if n == "cpu-llama3b-speed-answer.json"
        )
        kinds = by_kind(block)
        assert (kinds[INTEGRITY]["score"], kinds[INTEGRITY]["total"]) == (2, 2)
        assert (kinds[CAPABILITY]["score"], kinds[CAPABILITY]["total"]) == (1, 4)

    def test_a_block_without_items_is_judged_whole_as_before(self):
        kinds = by_kind({"score": 4, "total": 6, "wrong": 2, "truncated": 0})
        assert kinds == {
            INTEGRITY: {"score": 4, "total": 6, "wrong": 2, "truncated": 0, "errors": 0}
        }

    def test_an_item_the_table_no_longer_has_counts_as_integrity(self):
        item = {"prompt": "What is 847 * 293?", "expected": "248171", "correct": False}
        assert by_kind({"items": [item]})[INTEGRITY]["wrong"] == 1

    def test_annotate_gives_an_old_block_what_a_new_one_records(self):
        _, block = campaign_blocks()[0]
        annotated = correctness.annotate(block)
        assert annotated["verdict"] == "OK"
        assert {item["kind"] for item in annotated["items"]} == {INTEGRITY, CAPABILITY}
        assert set(block) <= set(annotated)
        assert "kind" not in block["items"][0]  # the report itself is not touched

    def test_annotate_leaves_a_new_block_alone(self):
        block = answered()
        assert correctness.annotate(block) is block


class TestThePrintout:
    """Capability misses are printed apart and never blamed on the kernels."""

    def test_capability_is_its_own_line(self, capsys):
        correctness.print_correctness(answered(wrong={"3", "5"}))
        out = capsys.readouterr().out
        assert "[OK]" in out
        capability = sum(1 for p in CORRECTNESS_PROBES if p.kind == CAPABILITY)
        assert (
            f"capability: {capability - 2}/{capability} -- not a kernel verdict" in out
        )
        assert "kernels" not in out.split("not a kernel verdict")[1]

    def test_an_integrity_miss_names_the_kernels(self, capsys):
        correctness.print_correctness(answered(wrong={"391"}))
        out = capsys.readouterr().out
        assert "[DEGRADED]" in out
        assert "broken kernels" in out

    def test_no_block_is_no_result(self, capsys):
        correctness.print_correctness(None)
        assert "NO RESULT" in capsys.readouterr().out
