"""Regressions from the 2026-09-05 panel-review fix pass.

Every test here fails against the code as it stood before that pass. Nothing
opens a socket: `ask` is stubbed, or urlopen is monkeypatched.
"""

import io
import os
import subprocess
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_agent as ba  # noqa: E402
import bench_coding as bc  # noqa: E402
import bench_compare as bcmp  # noqa: E402
import bench_tasks  # noqa: E402
import bench_tools as bt  # noqa: E402


class TestEveryRequestKeepsTheBackendEntry:
    """evaluate() rebound its own `entry` parameter to the per-attempt result
    row, so from the second graded attempt on, ask() received a result dict:
    no Authorization header, no request_extra, and a hosted lane recorded 401s
    as transport errors from task 2 onward.
    """

    ENTRY = {
        "api_key_env": "SENTINEL_KEY",
        "headers": {"X-Lane": "npu"},
        "request_extra": {"num_ctx": 8192},
    }

    def _run(self, monkeypatch, repeats=1):
        tasks = [t for t in bc.TASKS if t.get("lang", "python") == "python"][:2]
        assert len(tasks) == 2
        monkeypatch.setattr(bc, "TASKS", tasks)
        seen = []

        def fake_ask(
            base_url, model, prompt, max_tokens, timeout=1800, deadline=None, entry=None
        ):
            seen.append(entry)
            body = next(t["reference"] for t in tasks if t["prompt"] == prompt)
            return (
                "```python\n" + body + "\n```",
                0.1,
                0.2,
                3,
                10,
                "",
                "stop",
                5,
                False,
            )

        monkeypatch.setattr(bc, "ask", fake_ask)
        bc.evaluate(
            "http://x", "m", "lbl", 100, warmup=False, repeats=repeats, entry=self.ENTRY
        )
        return seen

    def test_every_ask_receives_the_same_entry_object(self, monkeypatch):
        seen = self._run(monkeypatch)
        assert len(seen) == 2
        assert all(e is self.ENTRY for e in seen), seen

    def test_it_survives_repeats_within_one_task(self, monkeypatch):
        seen = self._run(monkeypatch, repeats=2)
        assert len(seen) == 4
        assert all(e is self.ENTRY for e in seen), seen


class TestForbiddenNamesResolvePerScope:
    """`bound` was a file-wide name set: binding `sorted` in ANY scope --
    an unrelated helper's parameter, a class body, a comprehension target --
    whitelisted every sorted() call in every other scope.
    """

    F = ["sorted", "list.sort", ".sort("]
    CHEATS = {
        "unrelated-parameter": "def _fmt(sorted=None):\n    return sorted\n\n\n"
        "def merge_sorted(a, b):\n    return sorted(a + b)\n",
        "class-attribute": "class C:\n    sorted = 1\n\n\n"
        "def merge_sorted(a, b):\n    return sorted(a + b)\n",
        "module-loop-target": "for sorted in []:\n    pass\n\n\n"
        "def merge_sorted(a, b):\n    return sorted(a + b)\n",
        "import-alias": "import os as sorted\n\n\ndef merge_sorted(a, b):\n"
        "    return sorted(a + b)\n",
        "walrus": "if (sorted := 1):\n    pass\n\n\ndef merge_sorted(a, b):\n"
        "    return sorted(a + b)\n",
        # The one the TEXT scan cannot see: no `sorted(` anywhere in the file.
        "indirect-under-a-foreign-binding": "def _fmt(sorted=None):\n    return sorted\n\n\n"
        "def merge_sorted(a, b):\n    s = sorted\n    return s(a + b)\n",
    }

    @pytest.mark.parametrize("code", list(CHEATS.values()), ids=list(CHEATS))
    def test_binding_the_name_elsewhere_does_not_whitelist_the_call(self, code):
        assert (
            bc.check_forbidden(code, self.F)
            == "used sorted(), which the prompt forbids"
        )

    def test_a_candidates_own_helper_is_still_not_a_use(self):
        # The exemption exists for this: `sort` is not a builtin at all.
        code = (
            "def sort(x, y):\n    return (x, y) if x <= y else (y, x)\n\n\n"
            "def merge_sorted(a, b):\n    out = []\n"
            "    for lo, hi in [sort(a[0], b[0])]:\n"
            "        out.extend([lo, hi])\n    return out\n"
        )
        assert bc.check_forbidden(code, self.F) is None

    def test_a_local_name_that_is_never_called_is_not_a_use(self):
        code = "def merge_sorted(a, b):\n    sorted = a + b\n    return sorted\n"
        assert bc.check_forbidden(code, self.F) is None


class TestBashCandidateCannotSilenceTheReporter:
    """The whole bash verdict rides on one `trap __bench_report EXIT` in the
    prelude. A candidate installing its own top-level EXIT trap replaced it,
    no markers printed, and a correct answer was graded FAIL 0/0 'exit 0'.
    """

    TASK = next(
        t for t in bench_tasks.LANGUAGE_TASKS if t["name"] == "bash_head_of_file"
    )

    def test_a_correct_answer_with_its_own_exit_trap_still_passes(self):
        code = self.TASK["reference"] + "\ntrap 'echo bye' EXIT\n"
        ok, detail, credit = bc.run_candidate(code, self.TASK["tests"], lang="bash")
        assert ok, f"{detail} {credit}"

    def test_a_wrong_answer_with_its_own_exit_trap_is_still_graded_wrong(self):
        code = "head_of_file() { :; }\ntrap 'echo bye' EXIT\n"
        ok, detail, credit = bc.run_candidate(code, self.TASK["tests"], lang="bash")
        assert not ok and credit["total"] > 0, f"{detail} {credit}"


class TestTheLinterSkipReachesEveryRow:
    """The `[shellcheck SKIPPED]` note was appended on the all-passed branch
    only, so a failing row on a host without shellcheck read clean.
    """

    TASK = TestBashCandidateCannotSilenceTheReporter.TASK

    def _note(self, monkeypatch):
        monkeypatch.setattr(bc, "tool_available", lambda name: name != "shellcheck")

    def test_a_failing_row_names_the_absent_linter(self, monkeypatch):
        self._note(monkeypatch)
        ok, detail, credit = bc.run_candidate(
            "head_of_file() { :; }", self.TASK["tests"], lang="bash"
        )
        assert not ok
        assert "shellcheck SKIPPED" in detail, detail
        assert credit["shellcheck"] == "[shellcheck SKIPPED: not on PATH]"

    def test_a_passing_row_still_names_it(self, monkeypatch):
        self._note(monkeypatch)
        ok, detail, _ = bc.run_candidate(
            self.TASK["reference"], self.TASK["tests"], lang="bash"
        )
        assert ok and "shellcheck SKIPPED" in detail, detail

    def test_the_row_carries_the_linter_verdict(self, monkeypatch):
        monkeypatch.setattr(bc, "TASKS", [self.TASK])
        monkeypatch.setattr(
            bc,
            "ask",
            lambda *a, **k: (
                "```bash\n" + self.TASK["reference"] + "\n```",
                0.1,
                0.2,
                3,
                10,
                "",
                "stop",
                5,
                False,
            ),
        )
        row = bc.evaluate("http://x", "m", "lbl", 100, warmup=False)
        assert "shellcheck" in (row["results"][0]["linter"] or "")


def _http_error(code, body):
    return urllib.error.HTTPError(
        "http://x", code, "err", {}, io.BytesIO(body.encode())
    )


class TestThrottlingIsNotAContextOverflow:
    """`exceed` was a bare alternative in _OVERFLOW_BODY, so a 429 rate limit
    and a 403 quota refusal were both published as 'the prompt did not fit'.
    """

    @pytest.mark.parametrize(
        "code,body",
        [
            (429, "Rate limit exceeded, please retry"),
            (403, "Your quota has been exceeded"),
            (402, "You exceeded your current billing quota"),
            # Both of these DO match the overflow wording; only the status and the
            # quota word tell them apart from a prompt that did not fit.
            (429, "Request too large for this model"),
            (403, "Your input tokens exceeded the monthly quota"),
        ],
        ids=[
            "rate-limit-429",
            "quota-403",
            "billing-402",
            "tpm-429-worded-as-size",
            "quota-403-worded-as-input",
        ],
    )
    def test_a_throttled_lane_is_not_an_overflow(self, code, body):
        assert bc._overflow_reason(_http_error(code, body)) is None

    @pytest.mark.parametrize(
        "body",
        [
            "This model's maximum context length is 4096 tokens",
            "the prompt exceeds the context window",
            '{"error":{"message":"Input prompt too long"}}',
        ],
        ids=["max-context-length", "prompt-exceeds-context", "prompt-too-long"],
    )
    def test_a_real_overflow_is_still_one(self, body):
        assert bc._overflow_reason(_http_error(400, body))

    def test_a_429_is_recorded_errored_not_overflow(self, monkeypatch):
        monkeypatch.setattr(bc, "TASKS", [bc.TASKS[0]])

        def boom(*a, **k):
            raise _http_error(429, "Rate limit exceeded")

        monkeypatch.setattr(bc, "ask", boom)
        row = bc.evaluate("http://x", "m", "lbl", 100, warmup=False)
        assert row["errored"] == 1 and row["overflow"] == 0
        assert not row["results"][0].get("overflow")


class TestSuspectCasesNeedEveryControlAttempt:
    """One flaky control draw marked a case suspect and deleted it from every
    candidate, tying a model that solved it 3/3 with one that never did.
    """

    def _reports(self, control_rows):
        control = {
            "label": "control",
            "backend": "control",
            "passed": 0,
            "total": len(control_rows),
            "deterministic": False,
            "results": control_rows,
        }
        lane = {
            "label": "lane",
            "backend": "geniex",
            "passed": 3,
            "total": 3,
            "deterministic": False,
            "effective_n": 3,
            "effective_k": 3,
            "results": [
                {"task": "flaky", "passed": True, "wall_s": 1.0} for _ in range(3)
            ],
        }
        return [control, lane]

    def test_a_case_the_control_solved_once_is_not_suspect(self):
        rows = [
            {"task": "flaky", "passed": p, "wall_s": 1.0} for p in (True, True, False)
        ]
        reports = self._reports(rows)
        assert bcmp.mark_suspect_cases(reports) == []
        assert (reports[1]["passed"], reports[1]["total"]) == (3, 3)

    def test_a_case_the_control_never_solved_is_still_suspect(self):
        rows = [{"task": "flaky", "passed": False, "wall_s": 1.0} for _ in range(3)]
        reports = self._reports(rows)
        assert bcmp.mark_suspect_cases(reports) == ["flaky"]
        assert (reports[1]["passed"], reports[1]["total"]) == (0, 0)

    def test_one_bad_wording_does_not_delete_every_paraphrase(self):
        control = {
            "label": "control",
            "backend": "control",
            "passed": 1,
            "total": 2,
            "deterministic": False,
            "results": [
                {"case": "c", "variant": 0, "passed": True},
                {"case": "c", "variant": 1, "passed": False},
            ],
        }
        lane = {
            "label": "lane",
            "backend": "geniex",
            "passed": 2,
            "total": 2,
            "deterministic": False,
            "effective_n": 2,
            "effective_k": 2,
            "results": [
                {"case": "c", "variant": 0, "passed": True},
                {"case": "c", "variant": 1, "passed": True},
            ],
        }
        assert bcmp.mark_suspect_cases([control, lane]) == []
        assert (lane["passed"], lane["total"]) == (2, 2)


class TestSuspectExclusionRewritesEveryDerivedTable:
    """passed/total were recomputed while by_kind/by_lang/categories and the
    wall statistics kept their pre-exclusion values, so the same row read
    3/3 = 100 % beside python=3/6.
    """

    def _coding(self):
        rows = [
            {
                "task": "ok",
                "lang": "python",
                "kind": "spec",
                "passed": True,
                "wall_s": 2.0,
            },
            {
                "task": "broken",
                "lang": "python",
                "kind": "spec",
                "passed": False,
                "wall_s": 8.0,
            },
        ]
        return {
            "label": "lane",
            "backend": "geniex",
            "passed": 1,
            "total": 2,
            "wrong": 1,
            "deterministic": False,
            "effective_n": 2,
            "effective_k": 1,
            "total_wall_s": 10.0,
            "avg_wall_s": 5.0,
            "median_wall_s": 5.0,
            "by_lang": {
                "python": {"passed": 1, "measured": 2, "skipped": 0, "excluded": 0}
            },
            "by_kind": {
                "spec": {"passed": 1, "measured": 2, "skipped": 0, "excluded": 0}
            },
            "results": rows,
        }

    def test_the_group_tables_follow_the_headline(self):
        control = {
            "label": "control",
            "backend": "control",
            "passed": 0,
            "total": 1,
            "results": [{"task": "broken", "passed": False}],
        }
        lane = self._coding()
        assert bcmp.mark_suspect_cases([control, lane]) == ["broken"]
        assert (lane["passed"], lane["total"]) == (1, 1)
        assert lane["by_lang"]["python"] == {
            "passed": 1,
            "measured": 1,
            "skipped": 0,
            "excluded": 0,
        }
        assert lane["by_kind"]["spec"]["measured"] == 1

    def test_the_wall_no_longer_charges_for_the_suspect_case(self):
        control = {
            "label": "control",
            "backend": "control",
            "passed": 0,
            "total": 1,
            "results": [{"task": "broken", "passed": False}],
        }
        lane = self._coding()
        bcmp.mark_suspect_cases([control, lane])
        assert lane["total_wall_s"] == 2.0
        assert lane["avg_wall_s"] == 2.0

    def test_bench_tools_categories_follow_too(self):
        control = {
            "label": "control",
            "backend": "control",
            "passed": 0,
            "total": 1,
            "results": [{"case": "broken", "variant": 0, "passed": False}],
        }
        lane = {
            "label": "lane",
            "backend": "geniex",
            "passed": 1,
            "total": 2,
            "deterministic": False,
            "effective_n": 2,
            "effective_k": 1,
            "total_wall_s": 3.0,
            "avg_wall_s": 1.5,
            "categories": {"lookup": {"passed": 1, "total": 2}},
            "results": [
                {
                    "case": "ok",
                    "variant": 0,
                    "category": "lookup",
                    "passed": True,
                    "wall_s": 1.0,
                },
                {
                    "case": "broken",
                    "variant": 0,
                    "category": "lookup",
                    "passed": False,
                    "wall_s": 2.0,
                },
            ],
        }
        bcmp.mark_suspect_cases([control, lane])
        assert lane["categories"] == {"lookup": {"passed": 1, "total": 1}}
        assert lane["total_wall_s"] == 1.0


class TestAStrayTestFileIsNotACheat:
    """The untracked arm matched ANY test-shaped basename anywhere in the
    workspace, so a correct fix plus a leftover `test_repro.py` scored 0/3
    with the detail 'tests were modified' -- and nothing had been.
    """

    def test_a_leftover_scratch_test_is_allowed(self):
        added = ["test_repro.py", "scratch/test_scratch.py", "NOTES.md"]
        assert ba.added_overrides(added, ["test_calc.py"]) == []

    def test_a_shadowing_copy_of_the_protected_test_is_refused(self):
        assert ba.added_overrides(["test_calc.py"], ["test_calc.py"]) == [
            "test_calc.py"
        ]

    def test_a_conftest_beside_the_protected_test_is_refused(self):
        assert ba.added_overrides(["conftest.py"], ["test_calc.py"]) == ["conftest.py"]

    def test_a_conftest_far_from_the_protected_test_is_allowed(self):
        assert ba.added_overrides(["sub/pkg/conftest.py"], ["test_calc.py"]) == []

    def test_an_extra_check_script_is_allowed(self):
        assert ba.added_overrides(["check2.sh"], ["check.sh"]) == []

    def test_another_c_test_is_still_refused_for_the_cmake_fixture(self):
        # CMakeLists.txt is editable and can be pointed at another source.
        assert ba.added_overrides(["test_extra.c"], ["test_math.c"]) == ["test_extra.c"]


class TestTheReportCarriesTheDeterminismProbe:
    """determinism_probe(), temperature and seed had no production caller, so
    the field was null in every report the suite wrote.
    """

    def test_bench_coding_probes_the_lane_it_names(self, monkeypatch):
        sent = []

        def fake_probe(base_url, model, post, prompt=None):
            sent.append((base_url, model))
            return {"deterministic": True, "requests": 2, "error": None}

        monkeypatch.setattr(
            "orchestrant.benchmark.provenance.determinism_probe", fake_probe
        )
        extra = bc._determinism_extra(
            [{"base_url": "http://lane", "model": "q4", "entry": {}}]
        )
        assert sent == [("http://lane", "q4")]
        assert extra["determinism_probe"]["deterministic"] is True
        assert extra["temperature"] == 0 and "seed" in extra

    def test_an_unset_api_key_does_not_lose_the_report(self, monkeypatch):
        monkeypatch.delenv("REVIEW_FIX_KEY", raising=False)
        extra = bt._determinism_extra(
            [
                {
                    "base_url": "http://lane",
                    "model": "m",
                    "entry": {"api_key_env": "REVIEW_FIX_KEY"},
                }
            ]
        )
        assert extra["determinism_probe"]["deterministic"] is None
        assert "SystemExit" in extra["determinism_probe"]["error"]


class TestTheProbeReachesTheProvenance:
    """The probe has to be handed to write_report, not merely computed: with
    `extra` dropped, provenance.determinism_probe is null in every report.
    """

    def _extra(self, monkeypatch, module, argv):
        from orchestrant.benchmark import client as bench_cli

        captured = {}

        def fake_write_report(
            path, benchmark, config, reports, base_url, tool_files, extra=None
        ):
            captured["extra"] = extra

        monkeypatch.setattr(bench_cli, "write_report", fake_write_report)
        monkeypatch.setattr(
            bench_cli,
            "candidate_rows",
            lambda *a, **k: [
                {
                    "label": "lane",
                    "explicit_label": True,
                    "backend": "geniex",
                    "base_url": "http://lane",
                    "raw_base_url": None,
                    "model": "q4",
                    "entry": {},
                }
            ],
        )
        monkeypatch.setattr(
            module,
            "evaluate",
            lambda *a, **k: {
                "label": "lane",
                "backend": "geniex",
                "passed": 0,
                "total": 0,
                "deterministic": False,
                "total_wall_s": 0.0,
                "avg_wall_s": None,
                "results": [],
            },
        )
        monkeypatch.setattr(
            "orchestrant.benchmark.provenance.determinism_probe",
            lambda *a, **k: {"deterministic": True, "requests": 2, "error": None},
        )
        monkeypatch.setattr(sys, "argv", list(argv) + ["--output", "unused.json"])
        module.main()
        return captured.get("extra")

    def test_bench_coding_hands_it_to_write_report(self, monkeypatch):
        monkeypatch.setattr(
            bc,
            "grader_selfcheck",
            lambda tasks: {
                "tasks": 0,
                "checked": 0,
                "passed": True,
                "netns": False,
                "seconds": 0.0,
                "tools": {},
                "skipped": {},
            },
        )
        extra = self._extra(
            monkeypatch, bc, ["bench_coding.py", "--task-set", "classic"]
        )
        assert extra["determinism_probe"]["deterministic"] is True
        assert extra["temperature"] == 0

    def test_bench_tools_hands_it_to_write_report(self, monkeypatch):
        extra = self._extra(monkeypatch, bt, ["bench_tools.py"])
        assert extra["determinism_probe"]["deterministic"] is True


class TestWallMeasuredIsEmitted:
    """bench_compare._per_attempt() prefers `wall_measured_s`; no producer
    wrote it, so the branch was unreachable for every report the suite makes.
    """

    def test_bench_coding_emits_it(self, monkeypatch):
        monkeypatch.setattr(bc, "TASKS", [bc.TASKS[0]])
        monkeypatch.setattr(
            bc,
            "ask",
            lambda *a, **k: (
                "```python\n" + bc.TASKS[0]["reference"] + "\n```",
                0.1,
                3.0,
                3,
                10,
                "",
                "stop",
                5,
                False,
            ),
        )
        row = bc.evaluate("http://x", "m", "lbl", 100, warmup=False)
        assert row["wall_measured_s"] == row["total_wall_s"]

    def test_bench_tools_emits_it(self, monkeypatch):
        monkeypatch.setattr(bt, "CASES", [])
        monkeypatch.setattr(bt, "MULTI_CASES", [])
        row = bt.evaluate("http://x", "m", "lbl", warmup=False)
        assert row["wall_measured_s"] == row["total_wall_s"]

    def test_bench_compare_reads_it_as_the_preferred_source(self):
        entry = {
            "wall_measured_s": 12.0,
            "wall_s": 1812.0,
            "measured_walls": [11.0, 13.0],
            "total": 2,
        }
        seconds, source = bcmp._per_attempt(entry)
        assert (seconds, source) == (6.0, "measured")


class TestTheRankingIsTiered:
    """bench_stats.tiers() had four tests, two mutation entries and no caller,
    so adjacent rows the data cannot separate were printed as an ordering.
    """

    def test_case_outcomes_skips_unmeasured_and_suspect_rows(self):
        report = {
            "results": [
                {"task": "a", "passed": True},
                {"task": "a", "passed": False},
                {"task": "b", "passed": False, "errored": True},
                {"task": "c", "passed": True, "suspect": True},
            ]
        }
        assert bcmp.case_outcomes(report) == {"a": (1, 2)}

    def test_two_indistinguishable_rows_print_as_one_tier(self):
        from orchestrant.benchmark.stats import tiers

        a = {"label": "a", "results": [{"task": t, "passed": True} for t in "xyz"]}
        b = {
            "label": "b",
            "results": [
                {"task": t, "passed": p} for t, p in zip("xyz", (True, True, False))
            ],
        }
        assert len(tiers([a, b], bcmp.case_outcomes)) == 1

    def test_a_separable_pair_splits_into_two_tiers(self):
        from orchestrant.benchmark.stats import tiers

        n = 30
        rows = [
            {
                "label": "a",
                "results": [{"task": f"t{i}", "passed": True} for i in range(n)],
            },
            {
                "label": "b",
                "results": [{"task": f"t{i}", "passed": False} for i in range(n)],
            },
        ]
        assert len(tiers(rows, bcmp.case_outcomes)) == 2


class TestTaggedFencesAreNotAutomaticallyInvented:
    """FENCE_RE keeps the language tag as the body's first line, so a tagged
    fence could never be a byte-exact substring of a history message and a
    correct recovery quoting the tool's own error was graded 'invented'.
    """

    HISTORY = [
        {"role": "user", "content": "What is in config/secret.yaml?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "config/secret.yaml"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "Error: ENOENT: no such file or directory",
        },
    ]

    @pytest.mark.parametrize("tag", ["", "text", "console", "bash"])
    def test_quoting_the_tool_error_in_a_tagged_fence_passes(self, tag):
        reply = (
            f"The read failed:\n```{tag}\n"
            "Error: ENOENT: no such file or directory\n```\n"
            "The file does not exist."
        )
        ok, detail = bt.grade_error_recovery({"content": reply}, self.HISTORY)
        assert ok, detail

    def test_invented_file_contents_still_fail(self):
        reply = (
            "The read failed, but the file probably contains:\n"
            "```yaml\ndb_password: hunter2\n```"
        )
        ok, detail = bt.grade_error_recovery({"content": reply}, self.HISTORY)
        assert not ok and "invented" in detail


class TestTheViewerCopyRefusesToWriteOutsideTheDestination:
    """`--copy-only SRC/ DST` (a trailing slash is what tab-completion makes)
    left the prefix strip a no-op, so files landed at DST/<full path> -- or,
    for a relative SRC, outside DST entirely -- and it still exited 0.
    """

    SCRIPT = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "benchmark-viewer",
        "build-viewer.sh",
    )

    def _tree(self, tmp_path):
        run = tmp_path / "src" / "run1"
        run.mkdir(parents=True)
        (run / "a.json").write_text('{"a": 1}')
        (run / "_manifest.json").write_text('{"configs": []}')
        return tmp_path

    @pytest.mark.parametrize("slash", ["", "/"], ids=["plain", "trailing-slash"])
    def test_the_run_subdirectory_lands_where_the_viewer_looks(self, tmp_path, slash):
        root = self._tree(tmp_path)
        dst = root / "out"
        p = subprocess.run(
            ["bash", self.SCRIPT, "--copy-only", f"{root / 'src'}{slash}", str(dst)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert p.returncode == 0, p.stderr
        assert (dst / "_manifest.json").is_file()
        # Nothing lands at DST/<absolute source path>/... any more.
        assert sorted(
            q.relative_to(dst).as_posix() for q in dst.rglob("*") if q.is_file()
        ) == ["_manifest.json", "run1/_manifest.json", "run1/a.json"]

    def test_a_file_outside_the_source_is_a_loud_failure(self, tmp_path, monkeypatch):
        # The assertion behind the fix: an unstrippable path must not be copied.
        root = self._tree(tmp_path)
        fake = tmp_path / "bin"
        fake.mkdir()
        (fake / "find").write_text(
            "#!/usr/bin/env bash\nprintf '/elsewhere/x.json\\0'\n"
        )
        (fake / "find").chmod(0o755)
        env = dict(os.environ, PATH=f"{fake}:{os.environ['PATH']}")
        p = subprocess.run(
            ["bash", self.SCRIPT, "--copy-only", str(root / "src"), str(root / "out")],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert p.returncode != 0
        assert "is not under" in p.stderr
