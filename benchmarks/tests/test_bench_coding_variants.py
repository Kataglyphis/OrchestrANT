"""P1.2 for code: every paraphrase is the SAME task, and the spread is reported.

A paraphrase that dropped a rule, renamed the function or lost a worked example
would make a model look wording-sensitive when it was really answering a
different task. So every phrasing is held to the prompt as written,
mechanically: the signature line; every backticked, quoted or single-quoted
literal; every number outside a list marker; every exception name, forbidden
call and fence tag; and every worked example -- which must also hold for the
task's own reference solution. The reference/known-wrong contract of
test_bench_coding_tasks.py is then re-run under every phrasing.

Nothing here opens a socket: `ask` is stubbed.
"""

import ast
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_coding as bc  # noqa: E402
import bench_tools as bt  # noqa: E402
from bench_coding import (  # noqa: E402
    EXTENDED_TASKS,
    LANGUAGE_TASKS,
    NOVEL_TASKS,
    TASKS,
    extract_code,
    run_candidate,
)

from orchestrant.benchmark import client as bench_cli  # noqa: E402
from orchestrant.benchmark import provenance  # noqa: E402


def _listed(task, field):
    """A task's list field, or [] -- the task dicts mix value types."""
    return list(task.get(field) or [])


ALL_TASKS = TASKS + NOVEL_TASKS + EXTENDED_TASKS + LANGUAGE_TASKS
WITH_VARIANTS = [t for t in ALL_TASKS if t.get("variants")]
PHRASINGS = [
    pytest.param(t, i, v, id=f"{t['name']}-v{i}")
    for t in WITH_VARIANTS
    for i, v in enumerate(_listed(t, "variants"), start=1)
]
EXAMPLES = [
    pytest.param(t, e, id=f"{t['name']}-{n}")
    for t in WITH_VARIANTS
    for n, e in enumerate(_listed(t, "examples"))
]

# Backtick spans, double-quoted and whitespace-free single-quoted literals.
_QUOTED = re.compile(r"`[^`\n]+`|\"[^\"\n]*\"|'[^'\s]+'")
_FENCE_TAG = re.compile(r"```\w+")
_EXCEPTION = re.compile(r"\b[A-Z]\w*Error\b")
_LIST_MARKER = re.compile(r"(?m)^[ \t]*\d+\.[ \t]")
_NUMBER = re.compile(r"\b\d+\b")


def _signature(prompt):
    m = re.search(r"exact signature:[ \t]*\n(.+)", prompt)
    return m.group(1).strip() if m else None


def _literals(prompt):
    """The tokens of a prompt a paraphrase may not reword.

    Numbers are taken outside list markers only: "1." numbers a rule, while
    "less than 1" or "age_days > 30" IS the rule.
    """
    found = set(_QUOTED.findall(prompt))
    found |= set(_FENCE_TAG.findall(prompt)) | set(_EXCEPTION.findall(prompt))
    found |= set(_NUMBER.findall(_LIST_MARKER.sub("", prompt)))
    return found


def _example_fragments(example):
    """The source text of every argument and of the expected value."""
    tree = ast.parse(example, mode="eval").body
    assert isinstance(tree, ast.Compare), example
    assert isinstance(tree.left, ast.Call), example
    parts = [ast.get_source_segment(example, a) for a in tree.left.args]
    parts.append(ast.get_source_segment(example, tree.comparators[0]))
    return parts


def _grade_under(task, phrasing, code):
    """Grade `code` the way evaluate() would after asking `phrasing`."""
    lang = task.get("lang", "python")
    want = task.get("function") or bc._want_from_prompt({"prompt": phrasing})
    extracted = extract_code("```\n" + code + "\n```", want=want, lang=lang)
    ok, detail, credit = run_candidate(
        extracted,
        task["tests"],
        forbidden=task.get("forbidden"),
        stdlib_only=task.get("stdlib_only", False),
        lang=lang,
    )
    if credit.get("skipped"):
        pytest.skip(f"{task['name']}: {credit['skipped']}")
    return ok, detail


class TestWhichTasksCarryParaphrases:
    def test_every_classic_and_every_novel_task_has_them(self):
        # Classic against novel is the recall-vs-reasoning comparison; the
        # spread has to be measurable on both halves of it.
        for task in TASKS + NOVEL_TASKS:
            assert task.get("variants"), f"{task['name']} has no paraphrase"

    def test_at_least_four_more_across_the_other_sets(self):
        others = [t for t in EXTENDED_TASKS + LANGUAGE_TASKS if t.get("variants")]
        assert len(others) >= 4, [t["name"] for t in others]

    @pytest.mark.parametrize("task", WITH_VARIANTS, ids=lambda t: t["name"])
    def test_each_paraphrase_is_a_distinct_rewording(self, task):
        def squash(s):
            return " ".join(s.split())

        seen = {squash(task["prompt"])}
        for v in task["variants"]:
            assert squash(v) not in seen, f"{task['name']}: a repeated phrasing"
            seen.add(squash(v))


class TestEveryParaphraseIsTheSameTask:
    @pytest.mark.parametrize(("task", "vi", "phrasing"), PHRASINGS)
    def test_the_signature_survives_verbatim(self, task, vi, phrasing):
        assert "exact signature" in phrasing
        assert _signature(phrasing) == _signature(task["prompt"]) is not None
        # The grader takes the name from the prompt as written; a paraphrase
        # that renamed the function would grade a correct answer as missing.
        assert bc._want_from_prompt({"prompt": phrasing}) == bc._want_from_prompt(task)
        if task.get("function"):
            assert task["function"] in _signature(phrasing)

    @pytest.mark.parametrize(("task", "vi", "phrasing"), PHRASINGS)
    def test_every_literal_survives(self, task, vi, phrasing):
        missing = sorted(t for t in _literals(task["prompt"]) if t not in phrasing)
        assert not missing, f"{task['name']} v{vi} lost {missing}"

    @pytest.mark.parametrize(("task", "vi", "phrasing"), PHRASINGS)
    def test_every_stated_constraint_word_survives(self, task, vi, phrasing):
        # `forbidden` is enforced on the syntax tree: a phrasing that no longer
        # forbids sorted() would fail a model for obeying the prompt it saw.
        for token in task.get("forbidden", []):
            assert token.strip(".(") in phrasing, token
        if "standard library" in task["prompt"]:
            assert "standard library" in phrasing

    @pytest.mark.parametrize(("task", "vi", "phrasing"), PHRASINGS)
    def test_every_worked_example_is_stated(self, task, vi, phrasing):
        for example in task.get("examples", []):
            for fragment in _example_fragments(example):
                assert fragment in phrasing, f"{example}: {fragment!r}"

    @pytest.mark.parametrize("task", WITH_VARIANTS, ids=lambda t: t["name"])
    def test_the_examples_are_the_prompts_own(self, task):
        for example in task.get("examples", []):
            for fragment in _example_fragments(example):
                assert fragment in task["prompt"], f"{example}: {fragment!r}"

    @pytest.mark.parametrize("task", WITH_VARIANTS, ids=lambda t: t["name"])
    def test_a_prompt_with_a_worked_example_declares_it(self, task):
        if re.search(r"(?i)\bexamples?:|' gives \(", task["prompt"]):
            assert task.get("examples"), f"{task['name']} states examples"


class TestTheContractHoldsUnderEveryPhrasing:
    @pytest.mark.parametrize(("task", "example"), EXAMPLES)
    def test_the_worked_example_holds_for_the_reference(self, task, example):
        # A worked example the reference contradicts is a prompt that lies to
        # the model, in every phrasing at once.
        want = task.get("function") or bc._want_from_prompt(task)
        code = extract_code("```\n" + task["reference"] + "\n```", want=want)
        ok, detail, _ = run_candidate(
            code, f"assert {example}\n", stdlib_only=task.get("stdlib_only", False)
        )
        assert ok, f"{task['name']}: {example} -- {detail}"

    @pytest.mark.parametrize(("task", "vi", "phrasing"), PHRASINGS)
    def test_the_reference_passes(self, task, vi, phrasing):
        ok, detail = _grade_under(task, phrasing, task["reference"])
        assert ok, f"{task['name']} v{vi}: reference rejected -- {detail}"

    @pytest.mark.parametrize(("task", "vi", "phrasing"), PHRASINGS)
    def test_the_known_wrong_solution_fails(self, task, vi, phrasing):
        ok, _ = _grade_under(task, phrasing, task["wrong"])
        assert not ok, f"{task['name']} v{vi}: a known-wrong solution PASSED"


# ── evaluate() and main(), with the request path stubbed ─────────────────────

GOOD = "def g():\n    return 1\n"
BAD = "def g():\n    return 2\n"


def _task(name, paraphrases=0):
    return {
        "name": name,
        "kind": "spec-transcription",
        "lang": "python",
        "function": "g",
        "prompt": f"{name}: def g(",
        "variants": [f"{name} v{i}: def g(" for i in range(1, paraphrases + 1)],
        "tests": "assert g() == 1\n",
    }


def _stub_ask(monkeypatch, verdict, draw=lambda prompt, n: ""):
    """verdict(prompt, n) -> passes? ; draw(prompt, n) varies the output text."""
    seen, sent = {}, []

    def fake_ask(
        base_url, model, prompt, max_tokens, timeout=1800, deadline=None, entry=None
    ):
        seen[prompt] = seen.get(prompt, 0) + 1
        sent.append(prompt)
        n = seen[prompt]
        body = (GOOD if verdict(prompt, n) else BAD) + draw(prompt, n)
        return "```python\n" + body + "```", 0.1, 1.0, 10, 5, "", "stop", 10, False

    monkeypatch.setattr(bc, "ask", fake_ask)
    return sent


class TestEvaluateAsksEveryPhrasing:
    def _run(self, monkeypatch, tasks, **kw):
        monkeypatch.setattr(bc, "TASKS", tasks)
        return bc.evaluate("http://x", "m", "lbl", 3000, warmup=False, **kw)

    def test_the_spread_and_the_score_per_phrasing_are_reported(
        self, monkeypatch, capsys
    ):
        _stub_ask(monkeypatch, lambda prompt, n: not prompt.startswith("t1 v1"))
        rep = self._run(
            monkeypatch, [_task("t1", 1), _task("t2")], prompt_variants=True
        )
        assert [(r["task"], r["variant"]) for r in rep["results"]] == [
            ("t1", 0),
            ("t1", 1),
            ("t2", 0),
        ]
        assert (rep["passed"], rep["total"], rep["wrong"]) == (2, 3, 1)
        assert rep["prompt_variants"] is True
        assert rep["variant_spread"] == 1 and rep["variant_spread_cases"] == ["t1"]
        assert rep["variant_spread_rate"] == 1.0
        assert rep["by_variant"][1] == {
            "variant": 1,
            "passed": 0,
            "total": 1,
            "cases": 1,
        }
        out = capsys.readouterr().out
        assert re.search(r"t1 +v1 FAIL", out), out
        assert "1/1 tasks passed in one phrasing and failed in another" in out
        assert "2/3 attempts pass" in out

    def test_paraphrases_do_not_inflate_effective_n(self, monkeypatch):
        _stub_ask(monkeypatch, lambda prompt, n: True)
        rep = self._run(
            monkeypatch, [_task("t1", 2), _task("t2", 1)], prompt_variants=True
        )
        assert rep["total"] == 5
        assert (rep["effective_n"], rep["effective_k"]) == (2, 2)

    def test_a_task_passes_the_effective_count_only_in_every_phrasing(
        self, monkeypatch
    ):
        _stub_ask(monkeypatch, lambda prompt, n: not prompt.startswith("t1 v2"))
        rep = self._run(
            monkeypatch, [_task("t1", 2), _task("t2", 1)], prompt_variants=True
        )
        assert (rep["effective_n"], rep["effective_k"]) == (2, 1)

    def test_determinism_is_voted_per_phrasing(self, monkeypatch):
        # Byte-identical output per prompt, different output per phrasing: the
        # per-task vote called this a sampling lane.
        _stub_ask(
            monkeypatch,
            lambda prompt, n: True,
            draw=lambda prompt, n: f"# {prompt.split(':')[0]}\n",
        )
        rep = self._run(
            monkeypatch, [_task("t1", 1), _task("t2")], repeats=3, prompt_variants=True
        )
        assert rep["deterministic"] is True
        assert rep["total"] == 9
        assert (rep["effective_n"], rep["effective_k"]) == (2, 2)

    def test_sampling_repeats_count_rounds_not_phrasings(self, monkeypatch):
        _stub_ask(
            monkeypatch, lambda prompt, n: not (prompt.startswith("t1 v1") and n == 1)
        )
        rep = self._run(
            monkeypatch, [_task("t1", 1), _task("t2")], repeats=2, prompt_variants=True
        )
        assert rep["repeats_agreed"] is False
        assert rep["total"] == 6
        assert (rep["effective_n"], rep["effective_k"]) == (4, 3)

    def test_phrasings_are_never_identical_follow_ups(self, monkeypatch, spacers):
        sent = _stub_ask(monkeypatch, lambda prompt, n: True)
        self._run(
            monkeypatch, [_task("t1", 1), _task("t2")], repeats=2, prompt_variants=True
        )
        assert sent == [
            "t1: def g(",
            "t1 v1: def g(",
            "t1: def g(",
            "t1 v1: def g(",
            "t2: def g(",
            "t2: def g(",
        ]
        # t1 alternates its phrasings; only t2 would follow itself.
        assert len(spacers) == 1

    def test_without_the_flag_nothing_changes(self, monkeypatch, spacers):
        sent = _stub_ask(monkeypatch, lambda prompt, n: True)
        rep = self._run(monkeypatch, [_task("t1", 1), _task("t2")], repeats=2)
        assert sent == ["t1: def g("] * 2 + ["t2: def g("] * 2
        assert len(spacers) == 2
        assert rep["prompt_variants"] is False
        assert not set(bt.VARIANT_FIELDS) & set(rep)
        assert rep["total"] == 4 and rep["effective_n"] == 2

    def test_an_errored_phrasing_leaves_the_denominator(self, monkeypatch):
        def fake_ask(base_url, model, prompt, max_tokens, **kw):
            if prompt.startswith("t1 v1"):
                raise ConnectionError("dropped")
            return "```python\n" + GOOD + "```", 0.1, 1.0, 10, 5, "", "stop", 10, False

        monkeypatch.setattr(bc, "ask", fake_ask)
        rep = self._run(monkeypatch, [_task("t1", 1)], prompt_variants=True)
        assert rep["errored"] == 1 and rep["total"] == 1
        assert rep["results"][1]["variant"] == 1
        # One measured phrasing cannot disagree with itself.
        assert rep["variant_case_count"] == 0


class TestPhrasingSchedule:
    def test_every_phrasing_once_per_round(self):
        task = _task("t", 2)
        sched = bc.phrasing_schedule(task, 2, prompt_variants=True)
        assert [(a, v) for a, v, _, _ in sched] == [
            (0, 0),
            (0, 1),
            (0, 2),
            (1, 0),
            (1, 1),
            (1, 2),
        ]
        assert all(several for *_, several in sched)

    def test_the_flag_off_asks_the_prompt_as_written(self):
        sched = bc.phrasing_schedule(_task("t", 2), 2, prompt_variants=False)
        assert sched == [(0, 0, "t: def g(", False), (1, 0, "t: def g(", False)]


def _control_and_lane_ask(base_url, model, prompt, max_tokens, **kw):
    """The control fails 'broken' in both phrasings (a broken task); the lane
    passes it as written and fails its paraphrase."""
    if prompt.startswith("broken"):
        passing = model != "ctl" and not prompt.startswith("broken v1")
    else:
        passing = True
    body = GOOD if passing else BAD
    return "```python\n" + body + "```", 0.1, 1.0, 10, 5, "", "stop", 10, False


class TestMainWiring:
    CANDIDATES = [
        {
            "label": "control",
            "explicit_label": True,
            "backend": "control",
            "base_url": "http://control",
            "raw_base_url": None,
            "model": "ctl",
            "entry": {},
        },
        {
            "label": "lane",
            "explicit_label": True,
            "backend": "geniex",
            "base_url": "http://lane",
            "raw_base_url": None,
            "model": "q4",
            "entry": {},
        },
    ]

    def _main(self, monkeypatch, tmp_path, argv, fake_ask):
        monkeypatch.setattr(
            bench_cli,
            "candidate_rows",
            lambda *a, **k: [dict(c) for c in self.CANDIDATES],
        )
        monkeypatch.setattr(
            bc,
            "grader_selfcheck",
            lambda tasks: {
                "tasks": len(tasks),
                "checked": len(tasks),
                "passed": True,
                "netns": False,
                "seconds": 0.0,
                "tools": {},
                "skipped": {},
            },
        )
        # Provenance asks each base_url for its models; nothing is listening.
        monkeypatch.setattr(bc, "_determinism_extra", lambda candidates: {})
        monkeypatch.setattr(provenance, "collect", lambda *a, **k: {})
        monkeypatch.setattr(bc, "ask", fake_ask)
        out = tmp_path / "coding.json"
        monkeypatch.setattr(
            sys, "argv", ["bench_coding.py", *argv, "--no-warmup", "--output", str(out)]
        )
        # main() rebinds the global TASKS; monkeypatch restores it afterwards.
        monkeypatch.setattr(bc, "TASKS", [_task("ok", 1), _task("broken", 1)])
        monkeypatch.setattr(bc, "NOVEL_TASKS", [])
        monkeypatch.setattr(bc, "EXTENDED_TASKS", [])
        monkeypatch.setattr(bc, "LANGUAGE_TASKS", [])
        bc.main()
        return json.loads(out.read_text())

    def test_the_sample_is_net_of_the_suspect_task_and_counted_by_task(
        self, monkeypatch, tmp_path
    ):
        report = self._main(
            monkeypatch,
            tmp_path,
            ["--prompt-variants", "--task-set", "all"],
            _control_and_lane_ask,
        )
        assert report["config"]["prompt_variants"] is True
        lane = next(r for r in report["reports"] if r["label"] == "lane")
        assert lane["suspect_cases"] == ["broken"]
        # mark_suspect_cases alone left 2 here: both phrasings of 'ok'.
        assert (lane["effective_n"], lane["effective_k"]) == (1, 1)
        assert lane["variant_spread"] == 0 and lane["variant_case_count"] == 1

    def test_no_flag_leaves_the_config_as_it_was(self, monkeypatch, tmp_path):
        # A False here would read as "config changed" against every report
        # written before the flag existed.
        report = self._main(
            monkeypatch, tmp_path, ["--task-set", "all"], _control_and_lane_ask
        )
        assert "prompt_variants" not in report["config"]
