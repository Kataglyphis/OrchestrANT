"""The PowerShell runner (roadmap P7.6): the sandbox pwsh starts in, the
statement-at-a-time harness, PSScriptAnalyzer's note, and the six tasks.

Two halves, so a host without pwsh still proves the plumbing: a stub `pwsh`
answers the marker protocol and reports the ceilings it was started under,
and the real-pwsh tests skip visibly where it is absent. The reference-passes
and wrong-fails contract for the six tasks lives with every other task's, in
test_bench_coding_tasks.py.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_coding as bc  # noqa: E402
from bench_coding import LANGUAGE_TASKS, extract_code, run_candidate  # noqa: E402

needs_pwsh = pytest.mark.skipif(
    not bc.tool_available("pwsh"), reason="pwsh is not on PATH"
)

FUNC = "function Get-Answer { return 42 }"
TESTS = "assert_eq 42 (Get-Answer) 'the answer'\nassert_ok 'it runs' { Get-Answer }\n"
ANSWER_TASK = {
    "name": "ps_answer",
    "kind": "spec-transcription",
    "lang": "powershell",
    "function": "Get-Answer",
    "prompt": "Write Get-Answer.",
    "tests": TESTS,
    "reference": FUNC,
}

PS_TASKS = [t for t in LANGUAGE_TASKS if t["lang"] == "powershell"]
WRONG_VARIANTS = [
    pytest.param(t, v, id=f"{t['name']}-{i}")
    for t in PS_TASKS
    for i, v in enumerate(t.get("wrong_variants", []))
]


def _grade(task, code):
    """The real path: extract_code on a fenced reply, then the runner."""
    extracted = extract_code(
        "```powershell\n" + code + "\n```", want=task["function"], lang="powershell"
    )
    return run_candidate(extracted, task["tests"], lang="powershell")


class _Proc:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class TestTheSixTasks:
    def test_one_task_per_trap_the_repository_hit(self):
        assert {t["name"] for t in PS_TASKS} == {
            "powershell_requires_version",
            "powershell_nested_module_import",
            "powershell_pipeline_output",
            "powershell_single_element_array",
            "powershell_null_comparison",
            "powershell_error_action_stop",
        }

    @needs_pwsh
    @pytest.mark.parametrize("task,variant", WRONG_VARIANTS)
    def test_every_plausible_half_fix_is_rejected(self, task, variant):
        # The canonical wrong answer is the original bug; these are the fixes a
        # model most plausibly stops at, and each must still fail.
        ok, detail, credit = _grade(task, variant)
        assert not credit.get("skipped"), detail
        assert not ok, f"{task['name']}: a known-wrong variant PASSED ({detail})"


class TestExtraction:
    def test_the_defining_fence_wins_over_a_longer_demo(self):
        reply = (
            "```powershell\n"
            "$lanes = Get-LaneNames 'cpu, npu'\n"
            'Write-Host "a demo block that is much longer than the answer itself"\n'
            'Write-Host "padding padding padding padding padding padding"\n'
            "```\n"
            "```powershell\nfunction Get-LaneNames { param([string] $Spec) }\n```\n"
        )
        code = extract_code(reply, want="Get-LaneNames", lang="powershell")
        assert code.startswith("function Get-LaneNames"), code

    def test_a_prefix_name_does_not_claim_a_longer_function(self):
        # Hyphens are part of the name: `Get-Lane` is not `Get-LaneNames`.
        assert not bc._defines("function Get-LaneNames { }", "Get-Lane", "powershell")
        assert bc._defines("function get-lanenames { }", "Get-LaneNames", "powershell")
        assert bc._defines("function global:Get-Lane { }", "Get-Lane", "powershell")

    def test_bare_code_starts_at_the_requires_line(self):
        text = "Here it is:\n#requires -Version 7.0\nfunction Get-X { 1 }\n"
        assert extract_code(text, want="Get-X", lang="powershell").startswith(
            "#requires -Version 7.0"
        )


@needs_pwsh
class TestHarness:
    """The real pwsh, in the real sandbox."""

    def test_a_correct_function_passes_and_names_the_analyzer(self):
        ok, detail, credit = run_candidate(FUNC, TESTS, lang="powershell")
        assert ok, detail
        assert credit["passed"] == credit["total"] == 2
        # Never silent: the row says whether the analyzer ran.
        assert "PSScriptAnalyzer" in detail and credit["psscriptanalyzer"]

    def test_a_wrong_value_fails_with_partial_credit(self):
        ok, detail, credit = run_candidate(
            "function Get-Answer { return 41 }", TESTS, lang="powershell"
        )
        assert not ok
        assert credit["passed"] == 1 and credit["total"] == 2
        assert "expected [42] got [41]" in detail, detail

    def test_assert_eq_tells_an_array_of_one_from_its_element(self):
        # The helper must not fall into the traps the tasks test for.
        tests = (
            "$one = , 'cpu'\n"
            "assert_eq 'cpu' $one 'an array of one is not its element'\n"
            "assert_eq 1 '1' 'an int is not a string'\n"
            "assert_eq $null @() 'an empty array is not $null'\n"
            "assert_eq @('a', $null) @('a', $null) 'equal arrays are equal'\n"
        )
        ok, _detail, credit = run_candidate(FUNC, tests, lang="powershell")
        assert not ok
        assert [a["passed"] for a in credit["assertions"]] == [
            False,
            False,
            False,
            True,
        ]

    def test_a_check_that_throws_is_a_failed_row_and_the_next_still_runs(self):
        code = (
            "function Get-Half { param([int] $N) if ($N % 2) { throw 'odd' } $N / 2 }"
        )
        tests = (
            "assert_eq 1 (Get-Half 1) 'odd input'\nassert_eq 2 (Get-Half 4) 'even'\n"
        )
        ok, detail, credit = run_candidate(code, tests, lang="powershell")
        assert not ok
        assert [a["passed"] for a in credit["assertions"]] == [False, True]
        assert "threw before it could check" in detail, detail

    def test_a_failing_setup_statement_stops_the_checks(self):
        # Like `set -e`: a stale variable must not be graded as a fresh one.
        tests = (
            "assert_eq 42 (Get-Answer) 'before'\n"
            "$x = [int]::Parse('not a number')\n"
            "assert_eq 42 (Get-Answer) 'after'\n"
        )
        ok, detail, credit = run_candidate(FUNC, tests, lang="powershell")
        assert not ok
        assert credit["passed"] == 1 and len(credit["assertions"]) == 1
        assert "stopped after 1" in detail and "setup statement failed" in detail

    def test_a_solution_that_does_not_parse_is_named(self, monkeypatch):
        # With the analyzer off, so the harness itself has to say it.
        monkeypatch.setattr(bc, "psscriptanalyzer_available", lambda: False)
        ok, detail, credit = run_candidate(
            "function Get-Answer {", TESTS, lang="powershell"
        )
        assert not ok and credit["passed"] == 0
        assert "did not load" in detail, detail

    def test_rows_the_candidate_forged_are_refused(self):
        forged = FUNC + "\n$__BENCH_ROWS.Add('P')\n$__BENCH_ROWS.Add('P')\n"
        ok, detail, _ = run_candidate(forged, TESTS, lang="powershell")
        assert not ok and "corrupted" in detail, detail

    def test_the_candidate_cannot_stand_in_for_the_helpers(self):
        # Defined after the candidate loads: a no-op assert_eq of its own
        # would otherwise turn every check into nothing at all.
        code = "function Get-Answer { 41 }\nfunction assert_eq { }\n"
        ok, detail, _ = run_candidate(code, TESTS, lang="powershell")
        assert not ok and "expected [42] got [41]" in detail, detail

    def test_the_checks_start_from_powershells_defaults(self):
        code = (
            "$ErrorActionPreference = 'Stop'\nSet-StrictMode -Version Latest\n" + FUNC
        )
        tests = (
            "assert_eq 'Continue' \"$ErrorActionPreference\" 'the default preference'\n"
            "assert_eq $null $neverAssigned 'strict mode is off'\n"
        )
        ok, detail, _ = run_candidate(code, tests, lang="powershell")
        assert ok, detail

    def test_an_infinite_loop_is_killed(self):
        ok, detail, _ = run_candidate(
            "function Get-Answer { while ($true) { } }",
            TESTS,
            timeout=5,
            lang="powershell",
        )
        assert not ok and "timed out" in detail

    def test_the_managed_heap_is_capped(self):
        tests = (
            "$heap = [GC]::GetGCMemoryInfo().TotalAvailableMemoryBytes\n"
            f"assert_eq ([long] {bc.PWSH_GC_HEAP_BYTES}) $heap 'the heap cap'\n"
        )
        ok, detail, _ = run_candidate(FUNC, tests, lang="powershell")
        assert ok, detail

    def test_an_allocating_candidate_is_stopped_at_the_cap(self):
        # The cap bites inside PowerShell, as a catchable error, and the harness
        # outlives it: not the kernel, not the timeout, not the host.
        code = (
            "function Get-Answer {\n"
            "    $held = [System.Collections.Generic.List[byte[]]]::new()\n"
            "    while ($true) { $held.Add([byte[]]::new(64MB)) }\n"
            "}\n"
        )
        tests = (
            "assert_fail 'allocating past the cap throws' { Get-Answer }\n"
            "assert_eq 1 1 'the harness outlives it'\n"
        )
        ok, detail, _ = run_candidate(code, tests, timeout=60, lang="powershell")
        assert ok, detail

    @pytest.mark.skipif(not bc._netns_available(), reason="no user namespaces here")
    def test_the_candidate_has_no_network(self):
        # Asked of the interfaces, never of the wire: nothing is sent anywhere.
        tests = (
            "$up = @([System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()"
            " | Where-Object { $_.OperationalStatus -eq 'Up' })\n"
            "assert_eq 0 $up.Count 'no interface is up'\n"
        )
        ok, detail, _ = run_candidate(FUNC, tests, lang="powershell")
        assert ok, detail


class TestAnalyzer:
    PLAINTEXT = (
        "function Get-Answer {\n"
        "    $s = ConvertTo-SecureString 'p' -AsPlainText -Force\n"
        "    return 42\n"
        "}\n"
    )

    @needs_pwsh
    def test_an_error_severity_finding_fails_the_task(self):
        if not bc.psscriptanalyzer_available():
            pytest.skip("PSScriptAnalyzer is not installed")
        ok, detail, credit = run_candidate(self.PLAINTEXT, TESTS, lang="powershell")
        assert not ok
        assert "PSAvoidUsingConvertToSecureStringWithPlainText" in detail, detail
        assert credit["psscriptanalyzer"] == "[PSScriptAnalyzer FAILED]"

    @needs_pwsh
    def test_an_absent_analyzer_is_named_on_passing_and_failing_rows(self, monkeypatch):
        monkeypatch.setattr(bc, "psscriptanalyzer_available", lambda: False)
        skip = "[PSScriptAnalyzer SKIPPED: module not installed]"
        ok, detail, credit = run_candidate(FUNC, TESTS, lang="powershell")
        assert ok and skip in detail and credit["psscriptanalyzer"] == skip
        ok, detail, _ = run_candidate(
            "function Get-Answer { 41 }", TESTS, lang="powershell"
        )
        assert not ok and skip in detail

    def test_a_finding_stops_the_task_before_anything_runs(self, monkeypatch):
        monkeypatch.setattr(bc, "tool_available", lambda n: True)
        monkeypatch.setattr(bc, "psscriptanalyzer_available", lambda: True)
        monkeypatch.setattr(
            bc.subprocess,
            "run",
            lambda *a, **k: _Proc(
                3, "PSAvoidUsingComputerNameHardcoded line 1: srv01\n"
            ),
        )
        monkeypatch.setattr(
            bc.subprocess, "Popen", lambda *a, **k: pytest.fail("the candidate ran")
        )
        ok, detail, credit = run_candidate(FUNC, TESTS, lang="powershell")
        assert not ok
        assert detail.startswith("PSScriptAnalyzer: PSAvoidUsingComputerNameHardcoded")
        assert credit["psscriptanalyzer"] == "[PSScriptAnalyzer FAILED]"

    def test_no_pwsh_means_no_analyzer_and_no_probe(self, monkeypatch):
        monkeypatch.setattr(bc, "tool_available", lambda n: False)
        monkeypatch.setattr(
            bc.subprocess, "run", lambda *a, **k: pytest.fail("probed anyway")
        )
        assert bc.psscriptanalyzer_available() is False

    def test_the_probe_runs_in_the_callers_environment(self, monkeypatch):
        # In the sandbox's HOME-less env the user module path moves under /tmp
        # and an installed analyzer reads as absent.
        seen = {}

        def run(cmd, **kw):
            seen.update(kw["env"])
            return _Proc(0)

        monkeypatch.setattr(bc, "tool_available", lambda n: True)
        monkeypatch.setattr(bc, "_PSSA", None)
        monkeypatch.setattr(bc.subprocess, "run", run)
        monkeypatch.setenv("HOME", "/home/somebody")
        assert bc.psscriptanalyzer_available() is True
        assert seen["HOME"] == "/home/somebody"
        assert seen["POWERSHELL_TELEMETRY_OPTOUT"] == "1"


class TestSkips:
    def test_pwsh_absent_is_a_skip_not_a_pass_and_not_a_fail(self, monkeypatch):
        monkeypatch.setattr(bc.shutil, "which", lambda n: None)
        ok, detail, credit = run_candidate(FUNC, TESTS, lang="powershell")
        assert ok is False
        assert credit["skipped"] == "pwsh not on PATH"
        assert detail.startswith("SKIPPED")

    def test_the_self_check_records_pwsh_and_the_analyzer(self, monkeypatch):
        monkeypatch.setattr(bc, "tool_available", lambda n: n != "pwsh")
        rec = bc.grader_selfcheck([ANSWER_TASK])
        assert rec["checked"] == 0
        assert rec["skipped"]["ps_answer"] == "pwsh not on PATH"
        assert rec["tools"]["pwsh"] is False
        assert rec["tools"]["PSScriptAnalyzer"] is False
        assert rec["rlimits"]["pwsh_as_bytes"] == bc.PWSH_AS_BYTES
        assert rec["rlimits"]["pwsh_gc_heap_bytes"] == bc.PWSH_GC_HEAP_BYTES


# A stand-in `pwsh` that answers the marker protocol, so the runner's plumbing
# is proven on a host with no PowerShell. The lint call is `-Command` in $3;
# the run is `-File <harness>` in $3/$4, and the marker is the harness's line 1.
_STUB_HEAD = r"""#!/usr/bin/env bash
if [ "${3-}" = "-Command" ]; then
    printf '%s\n' "${BENCH_STUB_LINT_OUT-}"
    exit "${BENCH_STUB_LINT_RC:-0}"
fi
m=$(sed -n -e "1s/^[$]__BENCH_MARKER = '\(.*\)'$/\1/p" "$4")
"""

# One row per sandbox property: "P" when the stub saw what the runner promised.
_STUB_SANDBOX = r"""
limit() { awk -v k="$1" 'index($0, k) == 1 { print $(NF-2) }' /proc/self/limits; }
row() { if [ "$2" = "$3" ]; then echo P; else echo "F $1: [$3]"; fi; }
{
    echo "$m"
    row argv "-NoProfile -NonInteractive -File" "$1 $2 $3"
    row harness "$PWD/harness.ps1" "$4"
    row files "yes" "$([ -f solution.ps1 ] && [ -f checks.ps1 ] && echo yes)"
    row heap "0x40000000" "${DOTNET_GCHeapHardLimit-}"
    row wx "0" "${DOTNET_EnableWriteXorExecute-}"
    row home "$PWD" "${HOME-}"
    row telemetry "1" "${POWERSHELL_TELEMETRY_OPTOUT-}"
    row as "8589934592" "$(limit 'Max address space')"
    row fsize "8388608" "$(limit 'Max file size')"
    echo "$m"
}
"""


@pytest.fixture
def stub_pwsh(monkeypatch, tmp_path):
    """Install a stub `pwsh` whose run prints `body`; the analyzer is off."""

    def install(body):
        stub = tmp_path / "pwsh"
        stub.write_text(_STUB_HEAD + body)
        stub.chmod(0o755)
        monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    monkeypatch.setattr(bc, "psscriptanalyzer_available", lambda: False)
    return install


class TestPlumbingWithAStub:
    def test_two_passing_rows_are_a_pass(self, stub_pwsh):
        stub_pwsh('printf \'%s\\nP\\nP\\n%s\\n\' "$m" "$m"\n')
        ok, detail, credit = run_candidate(FUNC, TESTS, lang="powershell")
        assert ok, detail
        assert credit["passed"] == credit["total"] == 2

    def test_more_rows_than_checks_is_refused(self, stub_pwsh):
        stub_pwsh('printf \'%s\\nP\\nP\\nP\\n%s\\n\' "$m" "$m"\n')
        ok, detail, _ = run_candidate(FUNC, TESTS, lang="powershell")
        assert not ok and "corrupted" in detail

    def test_the_sandbox_is_the_one_the_runner_promises(self, stub_pwsh):
        # argv, files, the heap cap, W^X off, HOME in the temp dir, telemetry
        # off, the WIDER address space and the UNCHANGED file-size ceiling.
        stub_pwsh(_STUB_SANDBOX)
        checks = "".join(f"assert_eq {i} {i} 'row {i}'\n" for i in range(9))
        ok, detail, credit = run_candidate(FUNC, checks, lang="powershell")
        assert ok, detail
        assert credit["passed"] == 9

    def test_a_pwsh_that_cannot_start_is_reported_not_scored(self, stub_pwsh):
        # The failure this runner met first: .NET refusing the address space.
        stub_pwsh(
            "echo 'Failed to create CoreCLR, HRESULT: 0x8007000E' >&2\nexit 134\n"
        )
        ok, detail, credit = run_candidate(FUNC, TESTS, lang="powershell")
        assert not ok and credit["total"] == 0
        assert "never printed" in detail and "CoreCLR" in detail, detail

    def test_an_analyzer_that_cannot_run_is_a_visible_skip(
        self, stub_pwsh, monkeypatch
    ):
        stub_pwsh('printf \'%s\\nP\\nP\\n%s\\n\' "$m" "$m"\n')
        monkeypatch.setattr(bc, "psscriptanalyzer_available", lambda: True)
        monkeypatch.setenv("BENCH_STUB_LINT_RC", "1")
        ok, detail, credit = run_candidate(FUNC, TESTS, lang="powershell")
        assert ok, detail
        assert credit["psscriptanalyzer"] == "[PSScriptAnalyzer SKIPPED: exit 1]"

    def test_an_analyzer_finding_fails_the_task(self, stub_pwsh, monkeypatch):
        stub_pwsh('printf \'%s\\nP\\nP\\n%s\\n\' "$m" "$m"\n')
        monkeypatch.setattr(bc, "psscriptanalyzer_available", lambda: True)
        monkeypatch.setenv("BENCH_STUB_LINT_RC", "3")
        monkeypatch.setenv("BENCH_STUB_LINT_OUT", "PSReservedParams line 2: Verbose")
        ok, detail, _ = run_candidate(FUNC, TESTS, lang="powershell")
        assert not ok
        assert detail == "PSScriptAnalyzer: PSReservedParams line 2: Verbose"

    def test_the_report_row_carries_the_analyzer_note(self, stub_pwsh, monkeypatch):
        stub_pwsh('printf \'%s\\nP\\nP\\n%s\\n\' "$m" "$m"\n')
        monkeypatch.setattr(bc, "TASKS", [ANSWER_TASK])

        def fake_ask(base_url, model, prompt, max_tokens, **kw):
            return (
                "```powershell\n" + FUNC + "\n```",
                0.1,
                1.0,
                9,
                5,
                "",
                "stop",
                9,
                False,
            )

        monkeypatch.setattr(bc, "ask", fake_ask)
        rep = bc.evaluate("http://x", "m", "lbl", 3000, warmup=False)
        row = rep["results"][0]
        assert row["passed"] is True and row["lang"] == "powershell"
        assert row["linter"] == "[PSScriptAnalyzer SKIPPED: module not installed]"
        assert rep["by_lang"]["powershell"]["passed"] == 1
