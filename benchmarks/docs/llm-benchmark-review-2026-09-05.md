<!--
Copyright (c) 2025 Kataglyphis
SPDX-License-Identifier: MIT
-->

# LLM benchmark — the 2026-09-05 panel review

A structured review of [`linux/llm-stack/`](../linux/llm-stack/README.md), run
on 2026-09-05 against commit `b03ac235` with a clean tree. Seven reviewers each
read the suite through one lens (the grader, the tasks, the agent loop, tool
calling, the statistics, the plumbing, the documentation), two researchers
worked the web for the model-widening and multimodal questions, and every
concrete defect claim was then handed to an independent skeptic told to refute
it. Thirty-six defect claims went in; **thirty-six were confirmed, none
refuted**, most by a scratch script that imported the module and reproduced the
behaviour. Five were re-checked by hand afterwards and all five held.

**Nothing in the repository was changed by the review.** This page is its
output: the ranked backlog, the confirmed defects with a location each, the
answer to "can I add more models", and the design for a multimodal benchmark on
the Snapdragon lanes. [`llm-benchmark-roadmap.md`](llm-benchmark-roadmap.md)
remains the map of what the suite can claim; it is also one of the pages this
review found stale, and it says so at its top.

Line numbers below are as of `b03ac235`. They rot; the file and the quoted
mechanism do not.

## Outcome (added 2026-09-05, after the work)

**The backlog was worked the same day, and every `R` and `D` item below now
carries a Status line** — done, partly done, or deferred with the reason and,
for anything needing a lane, the exact command. Twelve of the fifteen `R` items
are done or done-but-for-a-live-run; all 32 defects are fixed in code. The three
that are only partly closed, and the reason each matters:

- **R3** — closed 2026-09-05 by `tests/test_bench_tools_evaluate.py`: D19,
  D22 and D24 are pinned by a direct test of `evaluate()` and by mutation
  entries.
- **R8** — closed 2026-09-05: `determinism_probe()` and `tiers()` are both
  called, and `bench_coding`/`bench_tools` emit `wall_measured_s`. A probe that
  could not run records its error; read that as *nobody could ask*.
- **R2 / R4 / R7 / R11** — the measurements themselves. The grader that
  produced every published coding number has since changed in ways that move
  numbers in **both** directions, so the § 1i and § 1n tables have to be
  re-derived rather than adjusted, and no raw report exists for any of them.
  The commands are in the CHANGELOG entry of 2026-09-05.

One thing the review did not predict, worth recording: fixing the code turned up
**two vacuous tests** — one that had been reading green with the rule it
documented deleted, and one passing for an entirely unrelated reason. Both were
found by a mutation entry surviving, and both were fixed by strengthening the
test rather than weakening the entry.

---

## Verdict

This is an unusually careful suite: seven adversarial lenses found zero refuted or unverified defects among 36 verified ones, and almost every defect is a residual edge the suite's own prior audits framed but did not fully close (CUT-vs-FAIL classification, "errored attempts do not vote", constraints on the syntax tree). The ranking is not undermined: no published number was shown to be wrong, and two headline results survive every finding (QAIRT 4B cannot run opencode; the 9B-Distill GGUF passes 3/3 on the CPU lane). What is undermined is the *auditability* of those numbers — no raw report JSON is stored for any published coding or agent result, bench_agent's three verdicts accept cheap cheats (deleted tests, no tests written) and record no opencode version/config, and bench_tools has zero mutation entries and no test that drives evaluate(). The two live-during-measurement bugs are a one-day-old bench_coding regression that grades any syntax-error reply ending in "```\n" as CUT (excluded, inflating the rate) and a partial-credit harness that counts none of the 41 should-raise checks. For the owner's actual goal — choosing a model for a coding agent on a shell/CMake/PowerShell/Dockerfile repository — the biggest gap is construct validity, not measurement: all 27 coding tasks and all 3 agent fixtures are pure-Python spec transcription.

**What is already strong**, so it does not get re-invented:

- Every defect claimed by the seven lenses survived adversarial verification (36 confirmed, 0 refuted, 0 unverified) — a sign the code is transparent enough to reason about and honest about its own limits.
- Measurement-validity fundamentals are already in place and mostly correct: warm-up, PASS/FAIL/CUT with cuts excluded, transport errors excluded, Wilson intervals with effective_n, determinism decided on output hashes (bench_coding), per-case diffing in bench_compare, tool_sha256 fingerprint separating grader drift from model drift, provenance with dirty-tree flag.
- Executable verification everywhere: code is run in a sandboxed subprocess with a process-group kill; agent fixtures are verified by the repository's own tests and --self-test proves red-then-green; constraints are enforced on the AST, not by text scan.
- The task contract (reference passes, known-wrong fails) is enforced mechanically for all 27 tasks, and every worked example in every prompt holds under its reference (47 checked, 0 mismatches).
- bench_agent is the rare end-to-end check that disagreed with every proxy and was believed — the suite found the opencode preamble (8,175 tokens) and the server dropping tool calls by reading the wire, exactly as the roadmap now recommends.
- The mutation gate (418 entries, 29+ targeting bench_coding) and the docs-follow-the-change rule are real and mostly honoured; the CHANGELOG and GenieX page date version-sensitive numbers rather than overwriting them.
- Repo hygiene is sound: tarballs, .env and caches are gitignored; CI runs the whole tests/ directory; 461 tests pass offline.
- GenieX v0.6.1 transport already carries image_url (base64) and input_audio content parts, so a multimodal benchmark is an addition to the suite, not a new harness.

---

## The backlog, in order

Effort uses the repo's legend: S = hours, M = a day, L = days or more. IDs are
`R1`–`R15` so the refactoring backlog and commit messages can point at them.
Each item names the lens that raised it and the roadmap item it touches.

### R1 [M] Close the three bench_agent verification holes before publishing another agent number

*Raised by:* agent-loop · roadmap P3.1

**Why.** bench_agent is the one benchmark that overturned the recommendation and produced the 3/3 headline, yet its three verdicts accept: deleting/skipping/inverting the red test in fix_failing_test ('2 passed' identical to a real fix), a correct clamp with no tests written in add_function_and_test, and a substring scan that fails a correct rename over a comment. The published 3/3 cannot be re-audited because no report or workspace was kept.

**Do.** fix_failing_test: `git diff --quiet HEAD -- test_calc.py` (fixture is already committed) and fail with 'tests were modified'. add_function_and_test: after functional asserts, swap clamp for an identity mutant and a never-raises mutant and require `pytest -q` to fail against each (REFERENCE tests kill both, fixture tests kill neither). multi_file_rename: decide on the AST (Name/Attribute/non-docstring Constant/ImportFrom alias) mirroring bench_coding.check_forbidden. One test + one mutations.json entry each. Keep the run's report JSON and `git diff` of each workspace under benchmark_results/.

**Status.** **DONE 2026-09-05.** All three verdicts hardened (D12/D13/D14), each with a test and a mutation entry, and `--keep-output` stores each workspace's `git diff` (first 20 kB). What is **not** done is the second half — no report or workspace exists for the already-published 3/3, so it still cannot be re-audited; see R4.

### R2 [S] Fix the looks_truncated regression (closing fence + newline graded CUT) and the valid-prefix gap; re-derive § 1n's three cuts

*Raised by:* coding-grader

**Why.** Commit 4d469a22 (2026-09-04) replaced backtick parity with a regex that cannot tell a closing fence from an opener: every syntax-error reply ending in "```\n" or with trailing prose is excluded from rate, interval and rank instead of counted wrong. It was live for the § 1n coding measurements published 2026-09-05. The mirror gap — a server cut landing on a compiling prefix is graded FAIL 'timed out (likely an infinite loop)' — contradicts the docstring's 'either signal is enough'.

**Do.** Decide open/closed by fence parity over `re.findall(r'^[ \t]*```', tail, re.M)` (or pair via CODE_FENCE); check the unclosed-final-fence condition independently of compile() when finish is None; fix the docstring. Regression tests for '```', '```\n', '```\n\nHope this helps' and the valid-prefix text; mutations entries for the trailing-newline case and the re-nesting under except SyntaxError. Re-run the § 1n table with --keep-output so the raw replies exist.

**Status.** **PARTLY DONE 2026-09-05.** The grader is fixed and pinned (D1, D2). **Deferred:** re-deriving § 1n's three cuts needs a live sweep against lanes on another host — the command is in the CHANGELOG entry of 2026-09-05, item 2.

### R3 [M] Give bench_tools the test and mutation coverage the other tools have — it currently has none for evaluate()

*Raised by:* tool-calling, statistics · roadmap P1.1

**Why.** Six of the eight confirmed bench_tools defects live in code no test touches: multi-turn transport errors scored as wrong (and named REGRESSION by bench_compare), restraint cases passing on an empty reply (a dead lane scores 3/27 'correctly answered without a tool'), --context-tokens padding the prompt with the CASES table itself, config omitting the three flags that change the score, effective_n keyed on case name, and pass/fail-based determinism with errored attempts voting. bench_tools.py has 0 entries in mutations.json against 29+ for bench_coding.

**Do.** Add tests/test_bench_tools_evaluate.py with monkeypatched call/call_multi covering total/errored/effective_n/effective_k/deterministic; fix all six in one unit (errored flag on multi-turn rows; non-empty content for expect=None; PAD_SOURCES excluding bench_tools.py as bench_coding does; record accept_text_json/prompt_variants/context_tokens in config and a per-result recovered flag; key per_case on (case, variant); hash the message for determinism and emit effective_k). Add mutation entries for the `len(calls) > 1`, `key not in args`, restraint-branch and errored-flag lines.

**Status.** **DONE 2026-09-05.** All six behaviours are fixed (D19–D26), and the second pass of the day wrote `tests/test_bench_tools_evaluate.py` — eleven tests over a stubbed `call`/`call_multi` — so D19, D22 and D24 are pinned by a direct test of `evaluate()` and by their own mutation entries.

### R4 [M] Store the raw report for every published number, and record opencode version/config in bench_agent provenance

*Raised by:* agent-loop, coding-grader, task-quality, tool-calling · roadmap P1.5

**Why.** A recurring verdict across lenses: the § 1i/1j/1k/1m/1n numbers have no stored JSON under benchmark_results/ or baselines/, results keep only output_sha256 (not the code or reply), and bench_agent's report has base_url=None, no opencode version, no config hash, no tool-trim list — so the § 1n 2118 s→657 s attribution to the GenieX version is unprovable and no past PASS can be checked for the cheats above.

**Do.** Commit the report JSON (and --keep-output replies for coding) for each published table into benchmark_results/<date>-<section>/ and link it from the GenieX page. In bench_agent: resolve the provider baseURL from opencode.jsonc and pass it as base_url; pass extra={'opencode_version', 'opencode_config_sha256', 'tools_disabled', 'instructions'} into collect(); point XDG_DATA_HOME/OPENCODE_CONFIG at a per-run scratch dir so runs stop leaking sessions into ~/.local/share/opencode (49 agentbench project rows today).

**Status.** **PARTLY DONE 2026-09-05.** The mechanism exists: `--keep-output` on both `bench_coding` and `bench_agent`, provenance carrying the resolved `base_url`, `opencode_version`, `opencode_config_path`/`_sha256`, `tools_disabled` and `instructions`, and a per-run `XDG_DATA_HOME` so runs stop leaking sessions. **Deferred:** not one raw report has been stored for a published table. The original bytes are gone, so this means re-running each with its output kept — command in the CHANGELOG entry of 2026-09-05, item 3.

### R5 [L] Add tasks and fixtures in the languages this repository is written in, and tag tasks by kind

*Raised by:* task-quality, agent-loop · roadmap P2.0

**Why.** All 27 coding tasks are single pure-Python functions (0 classes, 0 imports, 0 I/O; median 12-line reference behind a 171-word ordered-rule prompt) and all 3 agent fixtures are Python — the suite measures spec transcription under a token budget. The repo the agent will edit is 325 .sh / 243 PowerShell / 29 Dockerfile / 23 CMake against 69 .py, and small quantised models degrade unevenly across languages. A model can score 27/27 here and be unable to do anything the agent is asked.

**Do.** Add a `kind` tag (spec-transcription, from-examples, bug-fix, design) and a `lang` field with a run_candidate dispatch: bash via `bash -euo pipefail` + shellcheck -S error, CMake via `cmake -P`, Dockerfile via hadolint (visible skip when absent). Start with 3 bash tasks from the five bug classes in AGENTS.md and 2 bench_agent fixtures (a bash quoting bug with a bats check; a CMake missing link dependency failing ctest). Report pass rate per kind/lang. Change the default --task-set from 'classic' (which the README itself calls recall-prone and too small) to 'all'.

**Status.** **DONE 2026-09-05.** Required `kind`/`lang` on every task with no default, a `_RUNNERS` dispatch giving bash, CMake and Dockerfile the identical sandbox, six new tasks, two new agent fixtures, per-kind/per-lang reporting, and `--task-set` default `classic` → `all` with a new `languages` value. **Unverified here:** `cmake`, `ctest`, `hadolint` and `shellcheck` are absent on this host, so the CMake task's reference and wrong answer, the `fix_cmake_link` fixture's red-then-green and the linter-cleanliness of the bash and Dockerfile references have never been executed — they skip visibly, and the first run on a host that has those tools must check exactly those rows. `from-examples` is in the vocabulary and used by no task; PowerShell still has none.

### R6 [S] Make CONTEXT-blocked classification honest and consistent across bench_agent and bench_compare

*Raised by:* agent-loop · roadmap P3.3

**Why.** agent_errors() treats any error containing 'context' or 'too long' as 'never reached the model' and drops it from the denominator even after 12 tool calls — the exact P3.3 context-growth failure the roadmap names as the condition that would overturn the recommendation is currently excluded rather than scored. Meanwhile bench_compare counts those excluded rows as REGRESSION, and per-attempt timing sums blocked walls into a denominator that excludes them.

**Do.** Match explicit markers only (context_length_exceeded, 'prompt too long', 'maximum context length', 'Input prompt too long'); call it blocked only when tool_events == 0 and no step event was seen, else status CONTEXT_GROWTH as a real FAIL. Set errored: True on blocked rows so normalise() skips them; exclude blocked wall from total_wall_s. Use Popen(start_new_session=True)+killpg like bench_coding so a timed-out opencode's bash children die too. Give --task `choices=` so a typo cannot produce a 0/0 exit-0 report.

**Status.** **DONE 2026-09-05.** Explicit markers only, `blocked` only before any tool or step event, `CONTEXT_GROWTH` as a real FAIL, `errored: True` on blocked rows, blocked wall excluded, process-group kill, and `--task choices=` (D15–D18).

### R7 [S] Count should-raise checks in the partial-credit denominator and re-derive the § 1i/§ 1n near-miss tables

*Raised by:* task-quality · roadmap P1.4

**Why.** _assertion_harness counts only top-level ast.Assert, so the 41 `try: f(bad); raise AssertionError except ValueError: pass` checks in 16 of 27 tasks are classified SETUP: a candidate that misses only the ValueError rule is reported 'test setup raised' with credit N/N (12/12, 9/9). The published near-miss shape and the § 1i caveat ('setup lines and helper definitions') describe the wrong mechanism. PASS/FAIL and rankings are unaffected.

**Do.** Count an ast.Try whose body contains a Raise of AssertionError or `assert False` as one assertion; add the test the lens wrote (`_assertion_harness(try-block)[1] == 1`) and a mutations entry; rewrite the § 1i paragraph and mark § 1n fractions as derived under the intermediate accounting. Also fix parse_version's tests (add '1.2.3.4', '1.-2', '1_0.2' probes; register the naive int() variant as WRONG; state the whitespace rule) and align parsing_item_list's wording to 'ASCII digits 0-9'.

**Status.** **PARTLY DONE 2026-09-05.** The denominator is fixed and pinned (D10), `parse_version` and `parsing_item_list` are corrected (D11), and § 1i now carries a dated correction naming the real mechanism — the 2026-09-04 note had described it wrongly. **Deferred:** re-deriving the § 1i/§ 1n near-miss fractions needs a live re-run.

### R8 [M] Replace unpaired interval-overlap with a paired sign test and stop the tripwire crying wolf at --repeats 1

*Raised by:* statistics · roadmap P2.6

**Why.** Both models answer the identical cases, but separability is decided by overlap of two independent Wilson CIs — a ~0.6% test at n=27, so 24/27 vs 18/27 with 6-0 discordant cases (sign p=0.031) is called 'not separable' and the roadmap's '119 cases needed' inherits that. Conversely, per-case 'broke' at the default --repeats 1 fires REGRESSION on 92% of same-model re-runs on a sampling lane (Monte-Carlo through the real compare()), and the ranking orders strictly by point estimate ignoring the interval it prints.

**Do.** Add paired_sign_test() and a Newcombe diff_interval to bench_stats and use them in compare() and both rankings; at repeats==1 on a lane not known deterministic, report flips as 'flipped (single draw — rerun with --repeats 3)' without setting regressed; group adjacent ranking rows whose paired difference is not significant into tiers. Record temperature/seed and a two-request determinism probe in provenance so 'does this lane sample at T=0' is measured once, not rediscovered per run. Recompute the P2.6 power numbers.

**Status.** **PARTLY DONE 2026-09-05.** `bench_stats` gained `paired_sign_test`, `paired_outcomes`, `diff_interval`, `smallest_detectable_flips` and `tiers`; `bench_compare` uses the paired test with a Newcombe difference interval, reports a single-draw flip as such without alarming, and `bench_provenance` gained `temperature`/`seed`/`determinism_probe`. **Wired 2026-09-05:** `bench_coding` and `bench_tools` send a two-request `determinism_probe()` once per run and record it with `temperature`/`seed`; both rankings print `tiers()` groups with a rule between tiers; both emit `wall_measured_s`. A probe that could not run records its error and `deterministic: null` — *nobody could ask*, not *the lane samples*. Roadmap P2.6 is recomputed.

### R9 [S] Fix extract_code's remaining single-block and indentation misgrades

*Raised by:* coding-grader, task-quality

**Why.** Three correct answer shapes are graded FAIL/CUT: imports or a helper in a preceding fence (NameError), a longer demo block whose docstring quotes `def merge_sorted(` (regex on raw text + longest wins → NameError), and a fence indented inside a markdown list with two top-level statements (IndentationError → then CUT via the regression above). The module docstring rules out measuring formatting compliance, and the bare-code fallback already keeps preceding imports.

**Do.** Decide `defining` on the AST (top-level FunctionDef named want, regex fallback for unparseable); when want is found, prepend preceding blocks that define no `want` and call nothing top-level; textwrap.dedent(b) before strip(). Three tests, three mutation entries. Also route the task-contract tests' reference through extract_code and pass stdlib_only.

**Status.** **DONE 2026-09-05.** D4, D5 and D9, each with a test that grades through `run_candidate` rather than on the extracted text, and a mutation entry.

### R10 [L] Ship a sweep driver, an example candidates file, and API-key plumbing so adding a model is one command

*Raised by:* plumbing-extension, statistics · roadmap P1.3

**Why.** Ranking a new candidate today is five commands across two hosts with hand-invented --output paths (a second candidate written to coding.json silently overwrites the first; two lanes serving the same GGUF collapse to one label and a 3/3→0/3 collapse reads 'unchanged'); candidates.json is referenced by README and bench_coding but no example is checked in; six request sites send only Content-Type so the hosted control P1.3 names cannot be used; and the control mechanism suspect_cases() has no callers.

**Do.** bench_sweep.py --candidates --outdir --tools speed,coding,tools,agent,lanes deriving <tool>_<slug(label)>.json, refusing overwrites, running the correctness gate first, ending with bench_report manifest + bench_compare --dir. candidates.example.json in the repo. Optional api_key_env/headers/request_extra per backends.json entry honoured by one shared post_json() in bench_cli (never logged). Disambiguate colliding labels and raise in normalise() on duplicates. Call suspect_cases() from the rankings matching on backend == 'control'.

**Status.** **DONE 2026-09-05.** `bench_sweep.py`, `candidates.example.json`, one `bench_cli.post_json` honouring `api_key_env`/`headers`/`request_extra`/`probe` with the key read from the environment at request time and never printed, `disambiguate()` at the producing side, and `suspect_cases()` finally called from both rankings. A hosted control (`mistral-glm`) ships in `backends.json`.

### R11 [M] Test whether the 2048 output cap behind § 1j/§ 1k was a `geniex serve --max-tokens` default, then re-measure Qwen3-8B and the Coder

*Raised by:* model-widening, docs-consistency · roadmap P4.1

**Why.** The GenieX CLI reference documents `--max-tokens` default 2048 as the per-response generation limit. The roadmap's 'P4.1 tested — does not change the recommendation' and the § 1k Coder verdict were taken on v0.5.0 where the 8B lost 26/27 to CUT. If the cap was a launch default, those Phase 4 conclusions are conditioned on a configuration, not on the models — and the v0.6.1 re-run in § 1n only covered the 4B/9B.

**Do.** Start the CPU lane with --max-tokens 4096 (and --nctx 16384), confirm with a 3000-token request, and re-run bench_coding --task-set all for Qwen3-8B and Qwen2.5-Coder under the fixed grader. Record the serve flags in provenance (they are invisible today). Update § 1j/§ 1k and roadmap P4.1/P4.2 with the outcome.

**Status.** **PARTLY DONE 2026-09-05.** The lever exists and is recorded: `Start-GeniexServers.ps1` now reads the model ids from `backends.json`, passes `--nctx` **and** `--max-tokens` explicitly (default 4096), warms every lane **it starts** (a lane already busy is left alone, so its caps are unknown to that run — re-run with `-Restart` to guarantee the recorded caps), and takes `-Models`/`-Pull`. **Deferred and unverified:** the script has never been executed — there is no `pwsh` on this host — and the re-measure of Qwen3-8B and Qwen2.5-Coder is a live-lane run. Command in the CHANGELOG entry of 2026-09-05, item 1. Roadmap P4.1/P4.2 are marked CONDITIONAL until it happens.

### R12 [L] Add the tool-calling categories that decide agent usability and un-tell the restraint cases

*Raised by:* tool-calling · roadmap P3.2

**Why.** Every restraint prompt literally says 'do not use any tool' (instruction following, not BFCL irrelevance); the near-neighbour prompts are near-verbatim copies of the tool descriptions; there are no parallel, typed-argument (int/enum/array), long-result, deep-history or repeated-error cases, and grade() rejects two calls outright; TOOLS total ~574 tokens against opencode's 5,286. grade_error_recovery also passes an identical retry and hallucinated content containing 'error'. Argument grading is type-blind (case_sensitive=1 passes True) and the `./` leniency strips 'done.' to 'done'.

**Do.** Add 3-4 unprompted irrelevance cases and paraphrase variants sharing <2 content words with the description; a list-of-expects format with parallel and parallel-multiple cases; typed params on TOOLS with isinstance checks against the schema; a 2k-token long-result case; a 5-turn deep-history case; a repeated-ENOENT case; a `--tools opencode` option loading the real 10 schemas captured in § 1m. Pass history into grade_error_recovery and fail an identical retry; word-bound the admit list. Thread accept_text_json into the multi-turn graders and use the shim's per-block parser.

**Status.** **DONE 2026-09-05.** Typed arguments, parallel and parallel-multiple cases, four unprompted irrelevance cases, a long-result, a deep-history and a repeated-error multi-turn case, paraphrases sharing fewer than two content words with the tool description, history-aware error-recovery grading, a word-bounded admit list, and `--tools opencode` loading a ten-schema preamble from the new `tools_opencode.py` (an authored approximation, and it says so in the report). The stored `baselines/geniex-npu-tools.json` is **no longer comparable** — different denominator, different rules, and a config carrying keys it never had.

### R13 [M] Clean up the remaining bookkeeping: wall time of cut attempts, in-stream SSE errors, HTTP 4xx overflow, blocked-agent timing, control-row and lane-report envelopes

*Raised by:* coding-grader, plumbing-extension, statistics · roadmap P3.2

**Why.** Each is small but each can flip a verdict: cut/abandoned attempts' wall time (up to 1800 s) still decides the rank tiebreak and avg/attempt and bench_compare's SLOWER verdict; an in-stream `{"error":…}` or `error:` SSE line is graded FAIL 'no code found' rather than ERROR; the same 4096 ceiling lands in three buckets (excluded/FAIL/CUT) depending on how the server reports it; bench_lanes writes an envelope-less report that the manifest labels 'throughput' with zero results and bench_compare compares as 'no regression' for any pair; build_manifest emits scored rows for turn_growth/embeddings that the viewer renders as '/ = 0%'.

**Do.** Compute wall statistics over measured attempts only and record unmeasured_wall_s; raise in ask() on an error key or `error:` event line; classify 4xx-with-context-body as a non-passed OVERFLOW state; route bench_lanes through write_report; emit `scored` only when passed/total are integers; fix build-viewer.sh to copy run-scoped subdirectories (the viewer has been disconnected from run_benchmarks.sh since OUTDIR became run-scoped).

**Status.** **DONE 2026-09-05.** Measured-only wall statistics with `unmeasured_wall_s` (D8), in-stream SSE errors (D3), an `OVERFLOW` state for the 4xx that says the prompt did not fit, blocked-agent timing (D16), the lane-report envelope (D29), and `scored` only for integer counts (D31). D30's viewer copy landed with it.

### R14 [S] Harden the sandbox and add a grader self-check

*Raised by:* coding-grader · roadmap P1.3

**Why.** run_candidate has no RLIMITs (an allocating candidate can take down WSL2) and pins PYTHONHASHSEED=0 so set-order-dependent solutions pass here and flake in the agent; if `unshare -rn python -I` fails on a host, every model scores identically with the same stderr — indistinguishable from 'the models are bad', the lesson P3.1 already taught once. TASKS and NOVEL_TASKS carry no `reference`.

**Do.** preexec_fn setting RLIMIT_AS/FSIZE/NPROC and a bounded read of output; add `reference` to all 27 tasks and run each through run_candidate at the start of main(), aborting loudly on failure and recording grader_selfcheck in the envelope; run set-sensitive tasks under two hash seeds.

**Status.** **DONE 2026-09-05.** `RLIMIT_AS`/`FSIZE`/`NPROC` and a bounded read on every candidate in every language, and a grader self-check that runs every task's `reference` through the real grading path at the start of `main()` and aborts with `GRADER SELF-CHECK FAILED` before any endpoint is contacted, recording `grader_selfcheck` in the envelope. **Not** done: set-sensitive tasks are not run under two hash seeds. No test asserts an *allocation* is stopped — proving it would mean running the reverted code with a multi-gigabyte allocation on WSL2, the exact failure the limit exists to prevent; the child's own `getrlimit` reading and a 9 MiB write against the 8 MiB ceiling pin the same hunk safely.

### R15 [S] Refresh the docs to the code (one work unit)

*Raised by:* docs-consistency, coding-grader, task-quality, statistics, plumbing-extension

**Why.** The roadmap's opening inventory says six tools / 135 tests (then 222) and 3+3 coding tasks against 14 tools, 491 collected and 27 tasks; P1.2-P1.5, P4.1/4.2/4.4, P5.1/5.3 shipped (b2d7b0f3, 816d80c0, 51ad1f8d, 23676f5a, 96321e8e) but read as open; README still enumerates 'two of the eight' multi-turn cases with names that do not exist eight lines after promising not to enumerate; bench_compare, bench_embeddings, --deadline, --prompt-variants, --turn-growth, --keep-output are documented nowhere; bench_agent's --help recommends the model § 1m proves cannot run opencode and the reproduce command uses an undeclared `geniex-cpu` provider.

**Do.** Apply the stale_docs list below. Drop raw test totals from prose (doc-numbers derives only mutation counts, so they will rot again); keep additions to AGENTS.md one-line (doc-dupes scans it); do not renumber § 1h-1l (doc-links checks bare '§ 1x' references).

**Status.** **DONE 2026-09-05** — this pass. The suite README's § Benchmarking checked sentence by sentence against `--help` for every tool (counts replaced by the command that derives them; the 'two of the eight cases' paragraph naming cases that do not exist deleted; new sections for `bench_compare`, `bench_embeddings` and adding a model with `bench_sweep`); the roadmap's inventory, status marks and a dated Phase 6; dated corrections in § 1i and § 1n of the GenieX page plus the CPU-lane opencode provider its own reproduce commands had always assumed; `docs/INDEX.md`; three lines of `AGENTS.md`; and this page's status lines. The four doc gates pass.


---

## Confirmed defects

Every entry below survived an adversarial verifier that read the exact code path
and, wherever the function was pure, reproduced the behaviour from a scratch
script. **Material** means it can move a published score, ranking or verdict;
the rest are bookkeeping. None was already pinned by a test or named in the
docs. Grouped by file; IDs `D1`–`D32`.

### `bench_coding.py`

- **D1** · `bench_coding.py:380` · **material** — **Closing fence followed by a newline is read as an unclosed opener: syntax-error replies ending in "```\n" are graded CUT and excluded**  
  The regex `r"```[^\n]*\n(?:(?!```).)*\Z"` is not anchored to an opener, so any reply whose final ``` is followed by a newline or prose is treated as unclosed; when compile() also fails, evaluate() records CUT and drops the attempt from total/wrong, the interval, determinism and rank instead of counting it wrong. Regression from commit 4d469a22 (2026-09-04), live for the § 1n measurements published 2026-09-05. All existing tests end exactly at ``` with no newline.  
  *Fix:* Decide open/closed by fence parity over `re.findall(r'^[ \t]*```', tail, re.M)` or pair via CODE_FENCE; add tests for '```\n' and '```\n\nprose' tails and a mutations.json entry.
  *Status:* **FIXED 2026-09-05.** Open/closed is decided by fence parity. `TestTruncationTails` pins six tails (```` ``` ````, ```` ```\n ````, ```` ```\n\nHope this helps ````, under `finish=None` and `finish="stop"`) as NOT cut while an unclosed final fence, `finish_reason="length"` and the delta-count cap still are; mutation `coding.fence-parity`. **The § 1n cuts have not been re-classified** — that needs a live sweep (R2).
- **D2** · `bench_coding.py:360` · **material** — **A server cut landing on a syntactically valid prefix is graded FAIL ('timed out, likely an infinite loop')**  
  The unclosed-fence signal is consulted only inside `except SyntaxError`, so a stream that stops mid-body below the cap without finish_reason 'length' yields a compiling prefix graded FAIL and counted as measured. The docstring's 'either signal is enough' matches neither the implemented rule nor this case.  
  *Fix:* Check the unclosed-final-fence condition independently of compile() when finish is None; keep compile() as second signal; fix the docstring; add a valid-prefix test and a mutation re-nesting the check.
  *Status:* **FIXED 2026-09-05.** The unclosed-final-fence condition is checked independently of `compile()`; the docstring now matches the rule. `test_a_cut_landing_on_a_compiling_prefix_is_a_cut_not_a_failure` compiles the prefix first so it cannot pass for the wrong reason; mutation `coding.cut-on-valid-prefix`.
- **D3** · `bench_coding.py:689` · **material** — **In-stream SSE error payloads are graded FAIL 'no code found in reply', not ERROR**  
  ask() records only `data:` lines carrying `choices`; a fault delivered inside an already-200 stream — `data: {"error": …}` (OpenAI/vLLM style) or llama.cpp's `error:` event line — is silently dropped, ask() returns '' and evaluate() counts wrong=1 total=1 instead of errored=1. Reproduced; nothing pins it.  
  *Fix:* Raise RuntimeError in ask() on an `error` key in a data chunk AND on an `error:` event line so evaluate()'s existing except branch records errored; test both framings; mutations entry.
  *Status:* **FIXED 2026-09-05.** `ask()` raises on a `data: {"error": …}` payload and on a bare `error:` event line. `TestInStreamErrors` drives the whole urlopen→ask→evaluate path (monkeypatched, no socket) and asserts `errored=1, total=0`; mutations `coding.sse-data-error`, `coding.sse-event-error`.
- **D4** · `bench_coding.py:336` · **material** — **A correct answer split across two fences (imports/helper, then the function) is graded FAIL with NameError**  
  extract_code returns exactly one fenced block; helper-then-function reproduces 4/7 NameError '_take', import-then-function 0/7 NameError 'heapq', same code in one fence passes. Prompts do demand a single block, but the module docstring rules out measuring formatting compliance and the bare-code fallback already keeps preceding imports.  
  *Fix:* When `want` is found, prepend preceding blocks that contain no `def want` and no top-level call to `want`; regression test and mutation reverting to single-block return.
  *Status:* **FIXED 2026-09-05.** Preceding blocks that define no `want` and call nothing at top level are prepended. Graded through `run_candidate`, not on the extracted text; mutation `coding.multi-fence-preamble`.
- **D5** · `bench_coding.py:336` · **material** — **Defining block chosen by raw-text regex and longest-wins: a demo block whose docstring quotes the signature is extracted and the function is never defined**  
  `re.search(r"\bdef\s+want\s*\(", b)` over block text counts a docstring or comment quoting the signature as defining, and `max(defining, key=len)` then picks the longer demo → NameError, graded FAIL with 0 credit — the failure the longest-block rule was replaced to remove. The existing test's demo never contains `def merge_sorted(`.  
  *Fix:* Decide `defining` on the AST (top-level FunctionDef named want; regex fallback for unparseable blocks); test the docstring-quotes-signature case; mutation swapping ast back to re.search.
  *Status:* **FIXED 2026-09-05.** `defining` is decided on the AST (top-level `FunctionDef` named `want`), with the regex kept only as the unparseable fallback; the test asserts the demo block is the *longer* one before requiring the real definition to win. Mutation `coding.defines-on-the-tree`.
- **D6** · `bench_coding.py:385` · minor — **check_forbidden never inspects ast.alias, so `from builtins import sorted as s` passes merge_sorted**  
  The tree check tests Name/Attribute/Constant only; the import form of the aliasing evasion commit 4d469a22 set out to close passes the constraint and all seven assertions. heapq.merge/bisect.insort passing is NOT a defect (the prompt forbids only sorted()/list.sort(), and README says only stated constraints are enforced). Published results store no model code, so no recorded pass can be shown affected.  
  *Fix:* Add `if isinstance(node, ast.alias) and node.name in wanted` branch; TestForbiddenOnTheTree case; mutations entry. Do not add heapq/bisect to forbidden without amending the prompt.
  *Status:* **FIXED 2026-09-05.** `ast.alias` is covered; `from builtins import sorted as s` is caught. Mutation `coding.forbidden-import-alias`.
- **D7** · `bench_coding.py:426` · **material** — **check_forbidden false-positives on any string constant or bare Name equal to 'sort'/'sorted', not only lookups**  
  Every non-docstring str Constant in wanted (defaults, dict keys, f-string parts) and every Name including user-defined `def sort` or `sorted = []` is flagged; a correct merge with `mode='sort'` or a trivial helper named sort is FAIL with 0/0 credit. Docstring, commit and README all describe the narrower getattr rule. `sort` is not a builtin, so the bare-Name branch yields no true positive the others miss.  
  *Fix:* Restrict the Constant rule to arguments of getattr/__import__/import_module or Subscript on __builtins__/vars(); restrict the Name rule to names not bound in the tree; tests asserting PASS for both correct answers; mutations entry.
  *Status:* **FIXED 2026-09-05.** Only lookup strings count, and a name the candidate binds itself is excluded — a correct merge with a `mode='sort'` default, and one that defines its own `def sort(x, y)`, both PASS. Mutations `coding.forbidden-lookup-strings-only`, `coding.forbidden-bound-names`.
- **D8** · `bench_coding.py:806` · **material** — **Cut and abandoned attempts are excluded from the rate but their wall time still decides the rank tiebreak, avg/attempt and bench_compare's SLOWER verdict**  
  `done` filters only on `wall_s is not None`, and CUT/GAVE-UP attempts carry a real wall_s (up to 1800 s), so total/avg/median/stdev_wall_s include unmeasured attempts while README:276-277 says cuts are excluded 'exactly like a transport error'. Reproduced: 1 pass in 10 s + 1 abandoned 1800 s ranks below 1 pass in 50 s + 1 error. bench_compare's fallback wall_s/total has a cut-inclusive numerator over a cut-exclusive denominator. Unchanged since 1d3d77d8, predating the cut reclassification.  
  *Fix:* Compute wall statistics over attempts neither errored nor truncated; record unmeasured_wall_s separately; test a CUT wall flipping the tiebreak; mutations entry.
  *Status:* **FIXED 2026-09-05.** `total/avg/median/stdev_wall_s` cover measured attempts only; the rest is reported as `unmeasured_wall_s`, and the rank tiebreak test rebuilds this audit's fast-lane-vs-slow-lane case. Mutations `coding.measured-wall-only`, `coding.unmeasured-wall-reported`. **One residue:** excluding a *suspect* case does not recompute the wall, so its seconds still reach the tiebreak.
- **D9** · `bench_coding.py:328` · **material** — **Indented fences (markdown list items) with more than one top-level statement raise IndentationError and then read as CUT**  
  `b.strip()` removes only the first line's margin; the second statement is an unexpected indent, run_candidate fails, and looks_truncated then reports CUT when the reply ends with a fence and newline (FAIL otherwise). A single-statement indented block passes, hiding the case.  
  *Fix:* textwrap.dedent(b) before strip() (verified byte-identical for column-0 blocks); indented import+def test asserting PASS; mutations entry.
  *Status:* **FIXED 2026-09-05.** `textwrap.dedent` before `strip()`; an indented fence with two top-level statements compiles. Mutation `coding.dedent-indented-fence`.
- **D10** · `bench_coding.py:515` · minor — **Should-raise checks (try/except ValueError) are excluded from the partial-credit denominator and reported as harness failures**  
  _assertion_harness counts only top-level ast.Assert (the ast.Raise arm is dead for every shipped task). All 41 `try: f(bad); raise AssertionError… except ValueError: pass` checks in 16 of 27 tasks are classified SETUP, so a candidate missing only the ValueError rule scores N/N (12/12, 9/9) with 'test setup raised', and console/report fields carry that N/N beside FAIL. PASS/FAIL and rankings are unaffected; the published § 1i/§ 1n near-miss fractions and the caveat text are.  
  *Fix:* Count an ast.Try whose body contains a Raise of AssertionError or `assert False` as one assertion; add the harness test and a mutation targeting the new arm; re-derive the § 1i/§ 1n denominators.
  *Status:* **FIXED 2026-09-05.** An `ast.Try` whose body raises `AssertionError` counts as exactly one assertion. The candidate missing only the marker rule now scores 5/6 with no setup failure where it read 4/4 'test setup raised'; mutation `coding.should-raise-is-an-assertion`. § 1i of the GenieX page carries a dated correction naming the real mechanism; **the tables themselves are not re-derived** (R7).
- **D11** · `bench_coding.py:97` · **material** — **parse_version tests do not enforce 'Raise ValueError on anything else': a naive int() solution passes 5/5 while accepting 4-part, negative and underscore versions**  
  The only rejection probes are 'abc' and '', which int() refuses unaided; `[int(x) for x in v.lstrip('v').split('.')]` with zero-padding passes while returning (1,2,3,4), (1,-2,0), (10,2,0). The registered WRONG differs only by an `if x.isdigit()` filter — the sole reason it is rejected. The REFERENCE also accepts surrounding whitespace and 'V' that the prompt never grants.  
  *Fix:* Add '1.2.3.4', '1.-2', '1_0.2' (and ' 1.2' once the prompt states the whitespace rule) via the counted `_bad()` pattern; register the naive int() variant as WRONG; mutations entry deleting the '1.2.3.4' probe.
  *Status:* **FIXED 2026-09-05.** `parse_version`'s prompt and tests were rewritten with `_bad()` probes, registered `wrong_variants` and an explicit whitespace rule; `parsing_item_list` says 'ASCII digits 0-9'. No dedicated test was added beyond `TestAsciiDigits` — the grader self-check now runs every reference through the real path on **every** invocation, which is the stronger guarantee.

### `bench_agent.py`

- **D12** · `bench_agent.py:61` · **material** — **'Do not edit the tests' is unenforced: deleting, skipping or inverting the red test passes fix_failing_test**  
  verify runs only `pytest -q test_calc.py`; deleting the test ('1 passed'), @pytest.mark.skip ('1 passed, 1 skipped') or inverting the assertion ('2 passed' — indistinguishable from the genuine fix even in the saved detail) all score PASS. make_workspace() already commits the fixture, so `git diff --quiet HEAD -- test_calc.py` detects every variant. The published 3/3 includes this task and cannot be re-audited (no report or workspace kept).  
  *Fix:* Fail with 'tests were modified' when `git diff --quiet HEAD -- <test files>` is non-zero (or restore pristine test files before pytest); test_fix_task_rejects_a_deleted_test; mutations entry.
  *Status:* **FIXED 2026-09-05.** `protect_tests` runs `git diff --name-only HEAD` *and* `git ls-files --others`, so a modified protected test or a newly dropped-in test file fails the task; a failed git invocation is reported, never read as untouched. A latent empty-pathspec bug was found and fixed while testing it (an empty pathspec means *every* path, so a task whose patterns matched nothing would have rejected the correct fix).
- **D13** · `bench_agent.py:84` · **material** — **add_function_and_test passes with no tests added at all**  
  verify asserts clamp() itself then runs `pytest -q`; a correct clamp with test_utils.py untouched passes, as does clamp plus `assert True`. Only deleting/emptying test_utils.py fails, and only because pytest exits 5 on zero tests. The README statement that an `assert True` test is refused is true only when clamp is also wrong. Published 3/3 may have been earned on half the task.  
  *Fix:* After functional assertions, swap clamp for an identity mutant and a never-raises mutant and require `pytest -q` to fail against each (REFERENCE tests kill both; fixture tests kill neither); test with utils-only edits expecting passed=False; mutations entry.
  *Status:* **FIXED 2026-09-05.** The agent's own tests are run against four `CLAMP_MUTANTS` in a copied workspace and must fail against each; the detail names the mutant that survived. The reference tests kill all four, the fixture's kill none.
- **D14** · `bench_agent.py:125` · **material** — **Rename check is a substring scan: a comment or docstring mentioning fetch_data fails a correct rename with an empty detail**  
  `'fetch_data' in src` over concatenated *.py text fails REFERENCE plus `# renamed from fetch_data`, a docstring mention, or any identifier containing the substring (`prefetch_database`), while pytest is green; `sys.exit(1)` prints nothing so the row is a bare FAIL. Same defect class the 2026-09-04 audit fixed in bench_coding.check_forbidden.  
  *Fix:* Decide on the AST as check_forbidden does (Name, Attribute, non-docstring string Constant, ImportFrom alias) — a Name/Attribute-only walk would newly miss `globals()['fetch_data'] = …`; tests for comment/docstring passing; mutations entry.
  *Status:* **FIXED 2026-09-05.** Decided on the syntax tree (`Name`, `Attribute`, a def of that name, an `import … as` alias, a non-docstring `Constant`), text scan only for a file that does not parse. Its test was found **vacuous** during integration — the workspace also broke the reference tests, so `verify()` returned False before reaching the AST check — and now asserts `old_name_uses()` reports `(attribute)`.
- **D15** · `bench_agent.py:287` · **material** — **CONTEXT classification fires on any error containing 'context' — including mid-run context growth after tool calls — and removes the task from the denominator**  
  `"too long" in msg or "context" in msg` → CONTEXT with no check that tool_events == 0; Go/Ollama 'context canceled', 'context deadline exceeded', 'context 16384 too large for VRAM' and a genuine overflow after 12 tool calls are all reported 'never reached the model' and excluded (inflation-only bias). The harness --timeout is unaffected (TIMEOUT is classified first). The roadmap's stated overturning condition is exactly a mid-run overflow the harness would exclude rather than score.  
  *Fix:* Match explicit markers only; classify as blocked only when tool_events == 0 and no step event was seen, else CONTEXT_GROWTH as a real FAIL; add the four false-positive strings and the mid-run case to TestErrorClassification; extend the blocked-counts-as-failure mutation.
  *Status:* **FIXED 2026-09-05.** `CONTEXT_MARKERS` matches four explicit strings, and only before any tool or step event; the same marker afterwards is the new status `CONTEXT_GROWTH`, a real FAIL that is counted.
- **D17** · `bench_agent.py:261` · **material** — **Timeout kills only the opencode process; children of its bash tool survive in the harness's process group and the workspace is deleted under them**  
  subprocess.run without start_new_session kills the direct child only; a grandchild spawned by opencode's bash tool (pytest, pip, venv) keeps running (reproduced: /proc/<pid>/cwd reads '(deleted)' after rmtree). The harness does not hang (run() closes its pipe ends), but a CPU-bound straggler competes with the next serial task's inference and can push it over the 900 s ceiling. bench_coding already does this right with killpg.  
  *Fix:* Mirror bench_coding: Popen(start_new_session=True) + communicate(timeout) + os.killpg(SIGKILL) + proc.kill(), preserving the partial stdout/stderr; grandchild-pid test like tests/test_bench_coding_audit.py:152; mutation removing start_new_session.
  *Status:* **FIXED 2026-09-05.** `Popen(start_new_session=True)` and a `killpg` on timeout, so opencode's bash children die with it.
- **D18** · `bench_agent.py:393` · **material** — **A mistyped --task runs nothing, prints '0/0', exits 0 and writes a valid report that bench_compare passes**  
  `--task` has no choices= and nothing checks the filter matched; a typo prints the same '-> 0/0 attempted tasks completed' line an all-blocked run prints (minus its explanation), exits 0, writes {passed:0,total:0,tasks_run:0,results:[]}, and bench_compare against a 3/3 baseline says 'no regression detected' exit 0 — a vacuous PASS AGENTS.md forbids.  
  *Fix:* Give --task `choices=[t['name'] for t in TASKS]` or raise SystemExit when the filter selects nothing; subprocess CLI test asserting non-zero exit and no report.
  *Status:* **FIXED 2026-09-05.** `--task` has `choices=`, plus a `SystemExit` fallback if the filter ever empties. **No mutation entry:** the guarantee is doubly guarded, so no single find/replace can redden a test — over-covered rather than untested, and recorded here instead of pretending otherwise.

### `bench_compare.py`

- **D16** · `bench_compare.py:58` · **material** — **bench_compare counts context-blocked agent tasks as REGRESSIONs that bench_agent itself excluded, and inflates per-attempt timing with blocked walls**  
  normalise() skips only `errored`/`truncated`; bench_agent's blocked rows carry `status: "CONTEXT"`, `passed: False` with neither flag, so `_broke()` reports every previously-passing blocked task as REGRESSION (regressed=True). bench_agent sums total_wall_s over all results while `total` excludes blocked ones (10 s→15 s, SLOWER). Exposure today is the manual two-report path only (bench_agent is not in run_benchmarks.sh).  
  *Fix:* Set errored: True on CONTEXT rows (or honour a blocked flag / status in normalise() and suspect_cases()); exclude blocked wall from total_wall_s; test in test_bench_compare.py that a blocked task neither breaks nor fixes; mutations entry.
  *Status:* **FIXED 2026-09-05.** Blocked rows carry `errored: True` and their wall leaves `total_wall_s`; `bench_compare.measured()` is the single rule and now also excludes `overflow` and `skipped` rows. Mutation `compare.blocked-rows-are-failures`, re-anchored onto the widened function.
- **D27** · `bench_compare.py:138` · **material** — **Two lanes serving the same model collapse to one label in --compare and in bench_compare; a lane collapse reads 'unchanged' depending on candidate order**  
  resolve_candidates falls back label → model → backend, so the shipped geniex-gpu and geniex-cpu (both pinning unsloth/Qwen3-4B-GGUF:Q4_0) yield one label; compare() keys entries by label in a dict, so a 3/3→0/3 collapse of the first-listed lane reports 'unchanged' exit 0 while the same collapse listed last fires REGRESSION. Rankings print two identical rows and bench_tools' report carries no base_url. The existing GPU/CPU-pair test uses a stub with no pinned model so labels never collide.  
  *Fix:* Disambiguate (e.g. backend:model) or raise SystemExit only when resolved labels collide — not unconditionally, since the stored baseline's label is the bare model id — and raise in normalise() on duplicate labels; test with the shipped pair.
  *Status:* **FIXED 2026-09-05.** `bench_cli.disambiguate()` fixes it at the producing side (colliding derived labels gain the backend name; colliding explicit labels are refused) and `normalise()` raises on duplicates. **Behaviour change:** a `--compare` file with two identically-labelled explicit candidates now exits instead of silently merging them.
- **D28** · `bench_compare.py:249` · minor — **The control-row mechanism backends.json promises (suspect_cases) is dead code and would not match a control entry that names a model**  
  suspect_cases() has exactly one hit in the repo — its definition; no tool, script or test calls it, so backends.json:14's 'the benchmarks will flag a case the control also fails as suspect' has never been true. Its `control_label in label.lower()` heuristic is defeated whenever the control entry carries a model (label becomes the model id) unless an explicit 'label' containing 'control' is given; `{"backend":"control"}` alone sends `"model": null`. Not material to any published number (no control was reachable in published runs).  
  *Fix:* Call suspect_cases() from bench_compare and both rankings; identify the control by `entry['backend'] == 'control'` carried through resolve_candidates; test + mutation; soften the backends.json comment until then.
  *Status:* **FIXED 2026-09-05.** `is_control()` matches `backend == "control"` or a label starting with `control`, and `mark_suspect_cases()` is called from both rankings before the report is written, so the file and the printed table agree.

### `bench_tools.py`

- **D19** · `bench_tools.py:546` · **material** — **A multi-turn transport failure in bench_tools is scored as a wrong answer, not excluded, and bench_compare then names it as a REGRESSION**  
  The MULTI_CASES except branch records passed=None with no `errored: True`, so unlike the single-turn branch it is not subtracted from total, prints FAIL not ERROR, suppresses the EXCLUDED note, and bench_compare.normalise treats it as a genuine failure (regressed=True, 'broke: use_file_contents'). With --repeats > 1 the None votes in per_case, flipping deterministic to False and inflating effective_n. The committed geniex-npu-tools baseline includes all six multi-turn cases. No test drives bench_tools.evaluate(); no mutations.json entry targets bench_tools.py.  
  *Fix:* Append `"errored": True, "passed": False` exactly as the single-turn branch does; test monkeypatching call_multi to raise asserting total == 21 and errored == 6 and compare() does not regress; mutations entry deleting the key.
  *Status:* **FIXED AND PINNED 2026-09-05 (second pass).** Every transport failure including the multi-turn branch sets `errored: True`. `tests/test_bench_tools_evaluate.py` now drives `evaluate()` with a stubbed `call`/`call_multi`; mutations `tools.multi-transport-scored-as-wrong` and `tools.errored-attempts-do-not-vote`. Deleting the flag does not merely mis-score the row — `report_error` also records `wall_s=None`, so `sum(walls)` raises and `evaluate()` dies on any transport error.
- **D20** · `bench_tools.py:448` · **material** — **Restraint cases pass on an empty, None or whitespace reply — a dead lane scores 3/27 'correctly answered without a tool'**  
  grade(message, None) tests only `calls`; content is never read, evaluate() never consults finish_reason and bench_tools has no CUT state. An endpoint returning HTTP 200 with zero tokens (documented on this stack past the context limit) earns 3/27 with a misleading detail, and bench_compare's per-case diff cannot flag a restraint case that went from answered to silent. grade_followup and grade_error_recovery already implement the empty check and have tests.  
  *Fix:* `if not (message.get("content") or "").strip(): return False, "empty reply"` on the expect=None branch; test asserting an empty message fails; mutations entry.
  *Status:* **FIXED 2026-09-05.** `expect is None` also requires non-empty `content`. Mutation `tools.restraint-passes-on-silence`.
- **D21** · `bench_tools.py:241` · **material** — **--context-tokens padding for bench_tools contains the answer key: bench_tools.py itself is the first pad source**  
  context_padding() reads bench_tools.py first, so from N ≥ ~1271 the model sees part of the CASES table (prompt next to expected tool name and args, plus TOOLS schemas and grade() rules) and from N ≥ 2345 all of it, prepended to every single-turn request as 'Repository context, for reference only'. Multi-turn and --turn-growth are not affected. No published score used the flag, but the report omits context_tokens so a padded run is undetectable, and roadmap P3.2's open GGUF-lane experiment is exactly this run. bench_coding's PAD_SOURCES already excludes its own file.  
  *Fix:* Follow bench_coding's PAD_SOURCES pattern (exclude bench_tools.py); record context_tokens in config; shape test asserting no CASES prompt/expect literal appears in context_padding(N) up to the largest configured N.
  *Status:* **FIXED 2026-09-05.** `PAD_SOURCES` is now `bench_coding.py`, `bench_lanes.py`, `benchmark_openai_api.py` — this file and `tools_opencode.py` are excluded, as `bench_coding` already did.
- **D22** · `bench_tools.py:670` · **material** — **bench_tools report config omits accept_text_json, prompt_variants and context_tokens, so bench_compare's like-for-like guard is blind to them**  
  write_report receives only {repeats, warmup, system_prompt} while all three flags are passed to evaluate() and change the score or denominator (27→35 with variants; 3/27→18/27 with --accept-text-json per the published table). bench_compare derives changed_cfg only from recorded keys, so the flag difference is reported as the model 'improving' or as REGRESSION with no config warning. Recovery leaves only a detail-string prefix no consumer reads. bench_coding already records context_tokens and task_set. The fix belongs to the normal branch only — turn_growth() does not consume these flags.  
  *Fix:* Add the three keys to the config dict; record per-result `recovered` and an aggregate count; print 'X/27 (Y recovered from text)'; test that compare() names each key when it differs.
  *Status:* **FIXED AND PINNED 2026-09-05 (second pass).** The envelope records `accept_text_json`, `prompt_variants`, `context_tokens`, `tools`, `tools_source` and `system_prompt_sha256`; mutation `tools.config-records-the-scoring-flags`.
- **D23** · `bench_tools.py:360` · **material** — **grade_error_recovery passes an identical retry of the failed call and passes hallucinated content containing any of 12 unbounded admit-substrings**  
  The function receives only `message` (the caller never passes case history), so re-issuing the exact failed read_file is 'retried with another call'; and `w in content` over 'error', 'cannot', 'missing' etc. means invented YAML containing `error_reporting: true` or `missing_value_policy` is 'reported the failure' — the docstring/README promise that inventing file contents is unacceptable is enforced only when the text avoids all 12 words. Both recover cases are recorded PASS in the stored 25/27 baseline with no reply preserved.  
  *Fix:* Pass history and fail a retry whose (name, canonical args) equals the failed call; word-bound the admit list and fail a reply containing a fenced block or multiple key: value lines absent from history (avoid failing 'Status: file not found'); tests and mutation entries.
  *Status:* **FIXED 2026-09-05.** `grade_error_recovery` takes the history: a retry whose (name, canonical args) equals a call that already errored FAILS, the admit list is word-bounded, a fenced block absent from the history is invented content, and text quoted from the tool output is not the model's own admission.
- **D24** · `bench_tools.py:574` · **material** — **bench_tools determinism/effective_n bookkeeping: pass/fail-based vote with errored attempts voting, variants folded into case names, and a ratio rounded into the printed k**  
  per_case is keyed on case name from ALL rows and deterministic = all sets have one value: one dropped single-turn request at repeats=2 flips deterministic to False and effective_n from 27 to 53 (interval width halves); a sampling lane failing a case on every draw with different text is called deterministic; --prompt-variants either collapses 35 distinct phrasings to n=27 or flips determinism when a variant consistently disagrees with its base. No output hash, no repeats_agreed, no effective_k, so the ranking prints k = round(passed*n/total) — 25/27 for 26 passing cases — and bench_compare's 'legacy report' rounding fallback applies to every bench_tools report; README:442-456 claims the output-hash/no-vote/never-rounded rule suite-wide. The lone published baseline (repeats=1) is unaffected. Lines 562-563 are dead code.  
  *Fix:* Mirror bench_coding: hash json.dumps(message, sort_keys=True) per attempt, build per_case from non-errored rows keyed on (case, variant), emit repeats_agreed and effective_k (count of cases whose measured attempts all passed), use effective_k at line 694; delete the dead scalar; fake-endpoint tests asserting effective_n == 35 with variants and that an errored attempt does not flip determinism; mutations entries.
  *Status:* **FIXED AND PINNED 2026-09-05 (second pass).** Determinism is the identical `message_sha256` per `(case, variant)` over measured attempts; `repeats_agreed` is reported separately and `effective_k` is a count. Mutations `tools.determinism-from-the-message-hash` and `tools.effective-k-count-not-ratio` — the second is an EQUIVALENT mutant unless the per-key measured counts are uneven, so its test combines a deterministic lane with two errored draws.
- **D25** · `bench_tools.py:480` · **material** — **Argument grading is type-blind for booleans and the `./` leniency strips every leading/trailing '.' and '/' from string values; patch_not_overwrite accepts a missing diff**  
  `got != want` treats 1/1.0 as True and 0 as False, so case_sensitive=1 passes optional_boolean_true (and the text-recovery path coerces a digit-only <parameter> to int); `str(got).strip().strip("./").rstrip("/")` coerces any type and strips runs of './' at both ends, so content='done.', 'done/', '.done' pass overwrite_not_patch and '/__init__.py/' passes extract_query — wider than the README's 'a ./ prefix, a trailing slash'. patch_not_overwrite expects only {path} though the schema marks diff required. The published 25/27 passed all four affected cases with raw args unrecorded.  
  *Fix:* Check type(got) against the parameter's declared schema type before value comparison; apply normpath-style leniency only to path/directory params; require diff in patch_not_overwrite after whitespace normalisation; tests for 1-for-True and 'done.' failing.
  *Status:* **FIXED 2026-09-05.** Values are checked against the parameter's declared JSON type (`bool` is never an integer, `"40"` never `40`, an enum must hold a declared member), and leniency is exactly one leading `./` and one trailing `/` on path-like parameters only.
- **D26** · `bench_tools.py:541` · **material** — **--accept-text-json is not threaded to the multi-turn graders, and the Qwen-template parser merges two <tool_call> blocks into one chimera call**  
  Under the flag the 21 single-turn cases are judged on the recovered channel while the 6 multi-turn cases are judged on raw tool_calls: a follow-up call written as text is a false PASS in use_* cases when it contains the must_contain token, and a text-channel retry in recover_* is a false FAIL. Separately, _tool_call_from_text takes the FIRST block's name and dict(findall) of ALL parameters (last wins), so two read_file blocks recover as one read_file LICENSE that grade() accepts where the native channel says '2 tool calls, expected 1'. The published 'with fallback' column (Llama 18/27, Qwen3.8-2B 10/27, Phi 7/27) was produced under this asymmetry.  
  *Fix:* Pass accept_text_json into grade_followup/grade_error_recovery; reuse geniex_toolcall_shim.parse_tool_calls (per-block) returning a list; test that two blocks yield two calls.
  *Status:* **FIXED 2026-09-05.** `accept_text_json` is threaded into `grade_followup` and `grade_error_recovery`, and `_tool_calls_from_text` returns a **list** parsed per `<tool_call>` block, so two blocks are two calls rather than one merged chimera. The published 'with fallback' column was measured under the old asymmetry and is not comparable.

### `bench_lanes.py`

- **D29** · `bench_lanes.py:339` · minor — **bench_lanes writes an envelope-less report: manifest labels it 'throughput' with zero results, bench_compare normalises it as benchmark_openai_api and passes any pair**  
  The only --output tool bypassing bench_cli.write_report: raw json.dump of {prompt, max_tokens, batching?, lane_run?} with no benchmark/provenance/config/reports. build_manifest → kind='throughput', results=[], config={}; normalise → benchmark_openai_api, label None; `bench_compare --dir` reports 'no regression detected' for ANY two lane reports; every lane report lacks git_sha/tool_sha256/live_lanes. No shipped script places a lane report where these would read it, so this is latent.  
  *Fix:* Route through write_report(path, 'bench_lanes', {prompt, max_tokens}, [...], base_url, ('bench_lanes.py','bench_provenance.py')); have build_manifest/normalise warn on a JSON with neither benchmark nor results; test manifest kind == 'bench_lanes'.
  *Status:* **FIXED 2026-09-05.** `bench_lanes --output` writes the shared envelope through `bench_cli.write_report`; no row carries `passed`/`total`, and a failed batching probe is still a row.

### `benchmark-viewer/build-viewer.sh`

- **D30** · `benchmark-viewer/build-viewer.sh:59` · minor — **Viewer pipeline disconnected: run_benchmarks.sh writes to a run-scoped subdir, build-viewer.sh copies only top-level *.json, App.jsx reads a fixed path**  
  Since commit 2ec55274 made OUTDIR `./benchmark_results/<backend>-<model>`, the manifest lands where `cp -r ../benchmark_results/*.json` (non-recursive) never copies it and App.jsx/ssr-smoke fetch only `./benchmark_results/_manifest.json`; a sweep reaches the viewer only via the undocumented BENCH_OUTDIR=./benchmark_results. README:145-146/588-589 describe the pre-2ec55274 layout. Display-only; scores, bench_report table, bench_compare --dir and baselines are unaffected. The stale top-level _manifest.json ('T'/'m'/'now') is a gitignored local artefact.  
  *Fix:* Copy `../benchmark_results/**/` recursively and let App.jsx accept a run directory (`?run=` or a top-level _runs.json index); shell test that a run_benchmarks.sh OUTDIR manifest is found by the copy step.
  *Status:* **FIXED 2026-09-05.** `copy_results SRC DST` copies every `*.json` with its run subdirectory and promotes the newest `_manifest.json` found in the **source** tree (`cp` does not preserve mtimes); `--copy-only` drives it from a test without a container. `App.jsx` is unchanged. **Not shellchecked** — no shellcheck on this host.

### `bench_report.py`

- **D31** · `bench_report.py:92` · **material** — **Scored-run table renders bench_tools_turn_growth and bench_embeddings rows as '/ = 0% [0–100%]'**  
  build_manifest emits a `scored` entry for every element of reports[] without checking passed/total exist; turn_growth writes {label, model, results} and embeddings writes semantic_passed/semantic_total, so both yield (None, None). The viewer filters only on array length and renders a fabricated 0% ranked last (SSR-verified), with the embeddings row showing det.=yes while its real 3/3 semantic score is discarded. Triggered by the same hand-placed --output workflow that produced the manifest's existing coding/tools rows.  
  *Fix:* Emit `scored` only when passed and total are integers; render turn_growth as its own kind or omit; test that a turn_growth manifest has no scored; mutation removing the guard.
  *Status:* **FIXED 2026-09-05.** `is_scored(row)` requires `passed` **and** `total` to be integers (a bool is not a count); everything else goes to a new `unscored` list, and `report_kind` warns on an envelope it cannot name.

### `tests/test_backends_registry.py`

- **D32** · `tests/test_backends_registry.py:129` · minor — **Registry test hard-wires the shipped geniex-npu default model — editing that field for P4.1 breaks an unrelated test**  
  TestEnvironmentPrecedence._resolve passes no path, so it resolves against the real backends.json and asserts the literal 'qualcomm/Qwen3-4B-Instruct-2507:W4A16'; changing only that field yields 1 failed / 10 passed for a reason unrelated to the precedence under test — the anti-pattern P4b.5 removed from bench_lanes. Has not fired yet (field never changed); P4.1 does not strictly require editing it.  
  *Fix:* Pass a tmp registry path as resolve_backend's third positional (or the existing `registry` fixture) and assert `model == 'org/Model:W4A16'`; keep TestShippedRegistry for structural checks.
  *Status:* **FIXED 2026-09-05.** The precedence test takes the tmp registry fixture as `resolve_backend`'s third positional, so editing the shipped `geniex-npu` model for P4.1 no longer reddens it; four structural checks were added beside it.

---

## Documentation that contradicts the code

Found by the documentation lens and confirmed against `--help` output and the
source. Each line names both locations. This is backlog item R15.

- docs/llm-benchmark-roadmap.md:21-30 — 'Six tools, 135 unit tests' (and :194 '222 tests') vs 14 tool files and 491 collected tests; table omits bench_agent, bench_compare, bench_stats, bench_report, bench_embeddings, bench_cli, geniex_toolcall_shim. Drop raw totals from prose (doc-numbers derives only mutation counts).
- docs/llm-benchmark-roadmap.md:37-38 and :89-91 — 'n = 3 tasks / 8 cases' and 'bench_coding still has 3 classic + 3 novel tasks' vs 27 tasks / 27 cases; § 1i already publishes 17/27.
- docs/llm-benchmark-roadmap.md:50-64 — P1.2, P1.3, P1.4, P1.5 listed open; all shipped in b2d7b0f3 (2026-08-31). Mark P1.4 DONE (per-assertion credit, noting the try/except denominator fix and that nothing downstream aggregates it), P1.5 partly DONE (live_lanes recorded and warned on; 'refuse to compare' not enforced; liveness ≠ load), P1.2 partial (4 of 27 tool cases, opt-in, no spread, 0 coding tasks), P1.3 partial (control entry exists; suspect_cases() has no callers).
- docs/llm-benchmark-roadmap.md:144-150 — P4.1, P4.2, P4.4 still open though measured and written up 2026-09-04 (816d80c0, 51ad1f8d, 23676f5a; § 1j/1k/1l); :37 'cannot claim anything outside the Qwen3 family' refuted by § 1k/§ 1l; 'Suggested order' items 1-3 are done. Note P4.1/P4.2 were measured under the v0.5.0 2048-token cap and should be flagged as conditional until re-run.
- docs/llm-benchmark-roadmap.md:129 P3.2 'still open for GGUF lanes' — --context-tokens and --turn-growth exist (undocumented anywhere); :198-202 P5.1/P5.2/P5.3 — bench_embeddings.py shipped (96321e8e), energy recorded as energy_proxy, lane knobs swept in § 1h. P4.3 should be reworded: no further QAIRT bundle exists for Snapdragon X Elite (Gemma-4 is GENIEX_LLAMACPP-only on this chip, Ministral-3-3B is X2-only, Llama-3.1-8B has no downloadable bundle).
- linux/llm-stack/README.md:353-360 — 'Two of the eight cases are multi-turn' with `multiturn_use_result` and `error_recovery`, names that do not exist (27 cases, 6 multi-turn: use_*/recover_*), eight lines after the section promises not to enumerate cases.
- linux/llm-stack/README.md:253 — 'Ranking is by tasks passed, then by time' vs code: pass rate over measured attempts, then attempts measured, then time.
- linux/llm-stack/README.md:260 and bench_coding.py:712 — 'GenieX honours neither max_tokens nor temperature' contradicts README:291-293 and the CHANGELOG (v0.6.1 honours max_tokens, measured 2026-09-05); only the temperature half stands and it is un-re-measured on v0.6.1.
- linux/llm-stack/README.md:276-277 and :442-456 — 'cuts excluded from the rank exactly like a transport error' (cut wall time still ranks) and the suite-wide claim that determinism is decided on output, errored attempts do not vote and effective_k is never a rounded ratio (true for bench_coding only; bench_tools does none of it).
- linux/llm-stack/README.md:384-386 and docs/geniex-local-ai-setup.md:1556-1558 — '0/0 rendered n/a over a [0%,100%] interval' and interval-bearing agent scores; bench_agent never calls bench_stats and prints bare '3/3' (a [44%,100%] result).
- linux/llm-stack/README.md:349-351 — leniency described as 'a ./ prefix, a trailing slash'; code strips every leading/trailing '.' and '/' character from all string args.
- linux/llm-stack/README.md — bench_compare.py (--baseline/--save-baseline/--dir/--time-tolerance, baselines/), bench_embeddings.py, --deadline/GAVE UP, --keep-output, --prompt-variants, --turn-growth, --context-tokens for bench_tools, the control backend: documented nowhere. README:145-146/588-589 still describe the pre-run-scoped benchmark_results layout.
- linux/llm-stack/README.md:234 — 39.9 tok/s aggregate vs 39.7 on the GenieX page (:46, :1254) and AGENTS.md:436; README:228 batching example (74.27 s) vs bench_lanes.py:11 docstring (27.6 s) illustrate SERIALISED with two different runs.
- docs/geniex-local-ai-setup.md:1108-1116 and § 1n :1704-1706 — 'setup lines and helper definitions' no longer counted; the statements removed are the try/except ValueError assertions (41 in 16 tasks). § 1i heading carries no date/version although the CHANGELOG says it was dated and bannered like § 1d; its closing 'that cap remains the single biggest distortion' is refuted by § 1n.
- linux/llm-stack/bench_agent.py:17 and :369 — usage/--help recommend `geniex/qualcomm/Qwen3-4B-Instruct-2507:W4A16`, the configuration § 1m proves scores 0/0 with zero tool calls; README:373 and geniex:1735 reproduce with provider `geniex-cpu/...` which Step 3 (geniex:312-372) never declares.
- AGENTS.md:396-398 — '58 unit tests need no server; the rest of tests/ needs the stack up' (461 pass offline; only test_v1_api.py and test_harness_against_ollama.py skip); :379-386 lists 3 of 14 tools.
- docs/INDEX.md:134 — the llm-stack README row mentions speed/TTFT/lanes only; coding, tool-calling, agent and embeddings benchmarks are unfindable from the index.
- linux/llm-stack/backends.json:14 — 'the benchmarks will flag a case the control also fails as suspect' describes behaviour no tool performs; also 'a hosted model' is unusable without API-key plumbing.
- linux/llm-stack/bench_lanes.py:223 sweep_nctx and bench_compare.py normalise() docstring 'roadmap P2.3' — dead code with no CLI flag/tests, and a pointer to a roadmap item that does not exist.
- CHANGELOG.md 2026-09-05 — no mention of --deadline/GAVE UP (2e1f9eb8) or --keep-output; README bench_coding section likewise.

---

## Adding more models

### How it works today

GenieX lane (the realistic case): (1) on the Windows host, `geniex pull <repo>:<precision>` (add `--model-type vlm` for VLMs; Q4_0 for anything meant to land on the NPU); (2) run `Start-GeniexServers.ps1` — it takes ports/nctx/keepalive but no model, so if the NPU lane already holds a different bundle kind restart it by hand (backends.json: loading a GGUF after a QAIRT bundle crashes the server); ideally start with `--max-tokens 4096 --nctx 16384` on the GGUF lanes; (3) add or edit an entry in linux/llm-stack/backends.json (url, model, note); (4) hand-write a candidates.json — the format exists only in bench_coding/bench_tools --help, no example is checked in — with an explicit unique `label` per entry (two entries sharing a model otherwise collapse to one label); (5) run the correctness gate `python3 benchmark_openai_api.py --backend <name>` (3 trivia questions), then `bench_tools.py --compare candidates.json --repeats 2 --output tools_<label>.json`, `bench_coding.py --compare candidates.json --task-set all --repeats 3 --output coding_<label>.json`, `bench_lanes.py` as needed — each with a hand-chosen, collision-free --output path; (6) for bench_agent additionally add the model under `provider.<name>.models` in ~/.config/opencode/opencode.jsonc with the exact /v1/models id (and a `geniex-cpu` provider block for port 18184, which Step 3 never defines), then `bench_agent.py --model <provider>/<id> --timeout 1800 --output agent_<label>.json`; (7) `bench_report.py manifest <dir>` and `bench_compare.py --baseline geniex-npu-tools tools_<label>.json` for the tripwire. Ollama lane: `ollama pull`, then steps 3-7. Expect a day per model at this host's speeds (coding --task-set all --repeats 3 on a 9B is hours; agent tasks 3-11 min each).

### What blocks it

- No Authorization/API-key plumbing anywhere (six request sites send only Content-Type; backends.json has no key field) — a hosted control endpoint (P1.3 'a hosted model') returns 401 on every request.
- No sweep driver: run_benchmarks.sh drives only benchmark_openai_api.py; every other tool's --output defaults to None with no derived filename, so two candidates written to the same file silently overwrite and pair_directories only pairs identical filenames.
- candidates.json format has no checked-in example (README:246 and bench_coding.py:23 reference one); resolve_candidates' label fallback collapses two lanes serving the same GGUF (shipped geniex-gpu/geniex-cpu) and bench_compare then compares by order.
- bench_agent bypasses backends.json entirely (endpoint comes from ~/.config/opencode/opencode.jsonc, outside the repo) and records base_url=None — the model added there can silently differ from the one the other four tools measured.
- Start-GeniexServers.ps1 has no model parameter and prints hard-coded model names; no `geniex pull` in any windows/scripts file; the QAIRT/GGUF same-server crash forces separate ports per bundle kind.
- Per-backend request dialect (Ollama num_ctx/think, llama.cpp chat_template_kwargs enable_thinking:false, /no_think) is reachable only via --extra-params in benchmark_openai_api — bench_coding/bench_tools/bench_agent cannot switch the thinking tax off, and an Ollama candidate runs at the server default context (2048/4096) while GenieX runs at 16384.
- GenieX's server parses only some tool-call formats (brace-scanned JSON name+arguments, Gemma 4, Qwen3.5/Qwen3-Coder/Nemotron XML, GPT-OSS harmony, LFM2 python-call); Mistral [TOOL_CALLS] (Devstral, Ministral), GLM <arg_key> and Llama-3 {name, parameters} are not — those need geniex_toolcall_shim.py extended.
- The NPU/QAIRT catalogue for Snapdragon X Elite is exhausted at 4096 context (Qwen3 0.6B–8B, Qwen3-VL-4B, Qwen2.5-VL-7B; Llama-3.1-8B has no downloadable bundle); Gemma-4 E2B/E4B run on this chip via GENIEX_LLAMACPP q4_0 only, Ministral-3-3B is X2-Elite-only. New candidates live on geniex-cpu (~22-24 GB usable with other lanes stopped).
- tests/test_backends_registry.py:129 hard-wires the geniex-npu default model, so moving the registry default turns an unrelated test red.
- GenieX llama.cpp lanes ignore temperature (sampling at T=0), so --repeats ≥3 is mandatory for any GGUF candidate and doubles the cost; MXFP4 and exotic quants are untested on this build (IQ1-IQ3 are known-broken) — always run the 3-question trivia gate first.

### The shortlist

Assembled from the GenieX supported-model pages, Qualcomm AI Hub and the model
cards on 2026-09-05. Every row is a GGUF for the CPU lane unless stated: the
QAIRT catalogue for Snapdragon X Elite has no bundle above 4096 context, so the
NPU lane has nothing new to offer for agent use. Each row is tied to a question
the current ranking cannot answer.

| Model (pull id) | Lane | RAM | Confidence | Question it answers |
|---|---|---|---|---|
| unsloth/Qwen3.5-9B-GGUF:Q4_0 (5.38 GB) | geniex-cpu | ~6.5 GB incl. 4K KV | high — format parsed, size fits, direct control for the current winner | Does the empero-ai Qwen3.8-9B-Distill (a distillation into this exact architecture; published gains are MMLU/GSM8K) add anything on tool-calling and coding, or is the 9B row really a Qwen3.5-9B row? Base already posts BFCL-V4 66.1 / TAU2 79.1; enable_thinking toggle; Qwen3.5 XML tool format parsed natively by v0.6. |
| unsloth/Qwen3.5-4B-GGUF:Q4_0 (2.58 GB) | geniex-cpu | ~3.5 GB | high | Does a newer-generation 4B on the CPU lane (BFCL-V4 50.3, TAU2 79.9, 262K ctx) beat the QAIRT Qwen3-4B-Instruct-2507 incumbent at ~2x per-turn latency — and survive the agent preamble the QAIRT bundle cannot? |
| LiquidAI/LFM2.5-8B-A1B-GGUF:Q4_0 (4.84 GB, 1.5B active) | geniex-cpu | ~6 GB | medium — MoE on GenieX's CPU lane unconfirmed; parser argument-type inference is a documented limitation | Can a fast-decoding MoE deliver a full opencode turn well under the 9B's ~2 min with 8B-class tool calling (BFCLv3 64.8, 128K ctx)? First genuine MoE test on the CPU lane at low RAM risk; its python-style tool calls were added to GenieX's parser in v0.6.0 (#1417); emits CoT before answers (thinking tax to measure). |
| unsloth/gemma-4-E4B-it-GGUF:Q4_0 (4.84 GB) | geniex-cpu | ~6 GB | high | Cross-family sanity (P4.4): are the thinking tax, cut-off and prefill asymmetry properties of Qwen or of the hardware? Qualcomm publishes X Elite reference numbers for exactly this config (20.5 tok/s @512 ctx, 11.8 @4096) to check the lane measurement against. Gemma 4 tool syntax parsed server-side (#1345); thinking off unless <\|think\|> is in the system prompt. |
| unsloth/gpt-oss-20b-GGUF (MXFP4, 12.1 GB, 3.6B active) | geniex-cpu (other lanes stopped) | ~14 GB | medium — MXFP4 kernels untested on this llama.cpp build; run the trivia gate first | Does the strongest published agentic small model that fits (SWE-bench Verified 60.7, Aider 34.2, τ-retail 54.8) beat the 9B-Distill on bench_agent at comparable decode speed with reasoning_effort=low? Harmony tool format parsed since v0.6.0; use non-streaming (PR #1417 notes header noise in streams). |
| unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:Q4_0 (17.4 GB, 3.3B active) | geniex-cpu (all other lanes stopped) | ~20 GB — tight | medium — fits only with every other lane stopped; avoid Q3_K_S without inspect_gguf.py on its tensor mix | Does a code-specialised non-thinking MoE (SWE-bench V 51.6, 256K ctx, no think tax) beat the 9B-Distill on agent tasks, and is 30B-class prefill tolerable in a real loop — the roadmap's stated third overturning condition? XML tool format parsed natively. |
| bartowski/granite-4.2-8b-GGUF Q4_K_M (5.54 GB) | geniex-cpu | ~6.5 GB | medium-high | Does an enterprise tool-calling specialist at 8B (released 2026-08-25: SWE-bench V 47.7, LiveCodeBench 73.2, BFCL v4 52.4, thinking toggle + low_effort) match Qwen 9B on tool_calls while spending fewer tokens? Same RAM class as the 9B-Distill for an apples-to-apples swap; Qwen3.5-style <function=…> XML parsed by GenieX. |
| unsloth/Devstral-Small-2-24B-Instruct-2512-GGUF:Q4_0 (13.5 GB) | geniex-cpu behind geniex_toolcall_shim (Mistral format not parsed by GenieX) | ~16 GB | low-medium — requires extending the shim for Mistral [TOOL_CALLS]; llama.cpp needed a template fix for it | Is the best sub-30B agentic coder (SWE-bench V 68.0, no thinking mode, temp 0.15) usable at all at ~6 tok/s, or is correctness per task worth more than the 9B's speed for batch use (§ 1k's second use case)? Compare against the Qwen3.8-27B Q4_0 row, not the 9B. |
| Hosted control: Mistral La Plateforme (Bearer key, random_seed documented deterministic, tools/tool_choice, EU endpoint already configured for zai-glm-5-2) | control (new api_key_env entry) | none | high once auth plumbing exists | P1.3 calibration: which of the cases every local model fails are broken tasks rather than hard ones? Alternatives: Anthropic OpenAI-compat (seed ignored), OpenRouter (pin provider.order + allow_fallbacks:false), OpenAI (seed deprecated), Gemini compat (seed dropped silently). |
| Deferred: GLM-4.7-Flash Q4_0 17.2 GB (SWE-bench V 59.2, but <arg_key> format unparsed and thinking-only), Nemotron-3-Nano-30B-A3B Q4_0 18.2 GB (parseable but SWE-bench 38.8 at the same RAM as Qwen3-Coder), Qwen3.6-35B-A3B (≥20.9 GB at Q4, over budget with KV), Ministral-3-8B (Mistral format), Phi-4-mini/SmolLM3 (2025-era, below Qwen3.5-4B) | — | 17-23 GB | low | Revisit GLM-4.7-Flash only if the shim grows a GLM parser. |

### Plumbing that turns this into one command

- bench_sweep.py --candidates candidates.json --outdir benchmark_results/<stamp> --tools speed,coding,tools,agent,lanes: derives <tool>_<slug(label)>.json, refuses overwrites, runs the correctness gate first, ends with bench_report manifest + optional bench_compare --dir/--baseline. Ship candidates.example.json.
- backends.json per-entry optional fields `api_key_env`, `headers`, `request_extra` (e.g. {num_ctx: 16384} for Ollama, {chat_template_kwargs: {enable_thinking: false}} where supported), `probe: false` for remote/paid hosts; resolve_backend returns the entry; one shared post_json(url, body, entry, stream) in bench_cli sets Authorization: Bearer $ENV and merges request_extra, used by all six call sites; merged extras recorded in report config (never the key).
- resolve_candidates: disambiguate or refuse colliding labels; normalise() raises on duplicates; bench_tools report records base_url. Keep the bare-model-id fallback for the existing baseline's sake.
- bench_agent --backend: generate a scoped opencode config (OPENCODE_CONFIG / per-run XDG_DATA_HOME) from the backends.json entry so the agent hits the same lane as the other tools; pass base_url into write_report; add opencode_version, config sha256, tools_disabled, instructions to provenance.
- Start-GeniexServers.ps1: read model ids from backends.json (or -Models @{npu=…; gpu=…}), `geniex pull` missing models, pass --max-tokens/--nctx explicitly, warm each lane once, print actual ids, and hint -Restart when the requested NPU bundle kind differs from the loaded one. Record the serve flags in provenance.
- geniex_toolcall_shim.py: add Mistral [TOOL_CALLS] and GLM <arg_key> parsers (with recorded-stdout fixtures) if Devstral/GLM are pursued.
- Call suspect_cases() from both rankings and bench_compare, matching on backend == 'control'; write suspect_cases into the report and exclude them from every candidate's rate/interval.
- tests/test_backends_registry.py:129 → resolve against the tmp registry fixture so registry edits do not turn an unrelated test red.
- Roadmap P4.3 reworded to a GGUF/CPU-lane item; README gains 'How to add a model' pointing at bench_sweep.py.

---

## A multimodal benchmark for the Snapdragon

### What "the ARM SDK of my Snapdragon" is taken to mean

'The ARM SDK of my Snapdragon' most plausibly means the Qualcomm AI Engine Direct / QAIRT SDK and its GenieX front end — the repo already stages QAIRT 2.49 for Linux and runs GenieX v0.6.1 with QAIRT 2.45 bundled. Of the candidates (QAIRT direct via qnn-net-run, AI Hub, ONNX Runtime QNN EP / Windows ML, Arm KleidiAI, Windows on Arm SDK), only GenieX puts NPU-compiled VLMs behind the OpenAI-compatible HTTP API the suite already speaks, so bench_vision.py is an addition to the suite, not a new harness. QAIRT-direct gives op-level HTP traces but no chat template, tokenizer or image preprocessing (useful later for 'why is prefill slow', not for correctness); AI Hub is the model catalogue; ORT QNN EP / Windows ML would need a Windows-side Python 3.11 harness and there is no packaged VLM asset for X Elite; KleidiAI is a llama.cpp CPU kernel flag GenieX does not expose; the Linux QAIRT SDK cannot reach the Windows-side HTP from WSL2 anyway. State this interpretation at the top of the bench doc so the owner can redirect.

### What is verified possible, and what is not

Verified from GenieX docs: POST /v1/chat/completions accepts content parts of type image_url (base64 data URI, http(s) URL, or a Windows-side file path) and input_audio (llama.cpp GGUF VLMs with a conformer mmproj only; QAIRT bundles report audio: false). Ollama's OpenAI endpoint accepts base64 image_url data URIs but not http URLs — so base64 PNG is the one form every lane accepts (Windows file paths are invisible from WSL2). v0.6.0 pins the mtmd vision encoder to HTP for npu/hybrid and v0.6.1 fixed VLM chat-template application in accuracy mode. On AI Hub for qualcomm-snapdragon-x-elite the only QAIRT VLM bundles are Qwen3-VL-4B-Instruct (w4a16, QAIRT 2.45, context 4096) and Qwen2.5-VL-7B-Instruct (w4a16; HF card says 2048 context for GENIEX_QAIRT); Qwen3-VL-8B is mobile-only. Uncertain and to be probed on the live server: whether GET /v1/models/{model} exposes an image capability flag beside the documented `audio` field; the QAIRT Qwen3-VL-4B bundle's effective image-token budget and on-disk size; whether a 7B w4a16 VLM fits the 2.93 GiB HTP vmem at all; whether GGUF+mmproj on the NPU lane actually beats CPU on this driver; whether Qwen3-VL is in llama.cpp's multimodal support at the hash GenieX ships (inferred from GenieX's own Qwen3-VL-4B example and unsloth GGUFs, not confirmed). media_time (encoder-only time) is exposed by geniex-bench/SDK, not over HTTP. The QAIRT/GGUF same-server crash means the VLM needs its own port (e.g. 18185), not a model swap on 18181.

### Candidate models

- NPU/QAIRT: qualcomm/Qwen3-VL-4B-Instruct:w4a16 (primary; context 4096; Apache-2.0)
- NPU/QAIRT: qualcomm/Qwen2.5-VL-7B-Instruct:w4a16 (secondary; verify 2048 vs 4096 context via /v1/models and HTP vmem fit)
- GPU/CPU llama.cpp: unsloth/Qwen3-VL-4B-Instruct-GGUF:Q4_0 + mmproj (32x32 px/token, ~768 tokens for 1024x768)
- GPU/CPU llama.cpp: google/gemma-3-4b-it GGUF (fixed 256 tokens per image at 896x896 — cheapest on context)
- GPU/CPU llama.cpp: SmolVLM2-2.2B (in Qualcomm's own QDC benchmark matrix; 81 tokens per 512 tile)
- GPU/CPU llama.cpp: google/gemma-4-E2B-it-qat-q4_0-gguf (the only documented audio-capable conformer mmproj; text+image on X Elite via GENIEX_LLAMACPP)
- Optional: LFM2-VL (adjustable per-image token budget) if SmolVLM2 is too weak
- Control: Ollama qwen3-vl:8b or gemma4 on the LAN box over base64 image_url; a hosted VLM once API-key plumbing exists

### The design

bench_vision.py in the bench_coding mould: TASKS = [{name, family, render(seed) -> (PNG bytes, ground_truth), prompt, grade(reply, truth) -> (ok, detail, credit)}], every image generated at run time inside the repo (matplotlib + PIL, seeded) so ground truth is exact and licence-clean — the multimodal analogue of hidden asserts. Request body = the same ask() as bench_coding with messages[0].content = [{type: text}, {type: image_url, image_url: {url: 'data:image/png;base64,…'}}], stream on, max_tokens 64-256, temperature 0, --repeats N (GGUF lanes sample). Verdicts PASS/FAIL/CUT with CUT decided exactly as bench_coding does (finish_reason length → cap → unclosed structure); transport errors and cuts excluded from rate/interval/rank; determinism by output hash. Per-result fields: image_px, est_image_tokens (Qwen2.5-VL ≈ W·H/784, Qwen3-VL ≈ W·H/1024, Gemma 3 fixed 256, SmolVLM2 81/tile), prompt_tokens from usage and the QAIRT-padded ceil(n/128)·128 side by side, first-token latency, and a text-only twin prompt per task so image cost = TTFT(image) − TTFT(text) since media_time is not exposed over HTTP. Default long edge 448 px (~256 Qwen2.5-VL / ~196 Qwen3-VL tokens) with --long-edge to sweep 448/640/896/1280 on the GGUF lanes (--nctx 16384) for a resolution-vs-accuracy-vs-TTFT curve, clamped to 640 on the QAIRT lane so prompt+image+answer+system stays under 4096. Plumbing reuse: backends.json entries geniex-npu-vl (own port) / geniex-cpu-vl, bench_cli.resolve_candidates and write_report, bench_provenance (add vision_model_id, mmproj id/hash via inspect_gguf.py, image_px, est_image_tokens, and the server's /v1/models capability fields), bench_stats intervals, bench_report/bench_compare tables, the control backend for suspect renderings, a mutations.json block and tests/test_bench_vision.py (render determinism, grader edge cases — units, thousands separators, quoted JSON — and CUT detection). Public-dataset spot check only behind an opt-in --dataset flag (OCRBench v2 MIT, DocVQA val Apache-2.0 ANLS≥0.5, MathVista testmini CC BY-SA, MMMU val Apache-2.0; 50-200 seeded items downscaled to 640 px), never the default. Audio deferred: llama.cpp-only, no NPU comparison possible.

### Task families

Every image is rendered at run time from a seed, so the ground truth is exact
and the licence question does not arise. The text-only twin of each task is what
makes the image cost measurable over an API that does not expose encoder time.

| Family | Ground truth | Grading |
|---|---|---|
| chart → numbers | Bar/line chart of N seeded integers; asked for a named bar's value or max/argmax | Exact integer match (PASS); ChartQA-style relaxed 5% as a PARTIAL column |
| terminal screenshot → error string | Rendered fake traceback with a random exception class and file:line in monospace | Exact match on exception type and line number |
| UI screenshot → which button / where | 3-6 labelled buttons at random positions and colours; 'which button is red' and 'give x,y of Cancel' | Label exact-match; bbox hit-test on 0-1 normalised coordinates (ScreenSpot-style, resize-invariant) |
| code screenshot → transcribe and run | Rendered 6-10 line Python function image whose hidden tests already exist in bench_tasks.py | Reuse bench_coding.run_candidate() verbatim on the transcribed ```python block — the one family that reuses executable verification |
| diagram → structured JSON | Seeded 3-5 node box-and-arrow graph | json.loads then set-equality on edges |
| receipt/table → CSV | Rendered 4x5 table of random items/prices with a total | Parsed rows compared cell-wise plus the total; partial credit per row |
| text-only twin (per task) | Same question with the data given as text instead of an image | Same grader; exists to subtract text TTFT and to detect a model that fails the reading rather than the task |

### First steps, in order

1. Probe the live v0.6.1 server: GET /v1/models/{model} for capability fields; one base64-PNG chat completion against the CPU lane's current GGUF (expect a refusal/garbage) to confirm the transport path end to end before any pull.
2. On Windows: `geniex pull ai-hub-models/Qwen3-VL-4B-Instruct:w4a16 --model-type vlm`, serve it on its own port (18185) with --max-tokens 512, read metadata.json for the image-token budget/resolution and note on-disk size; pull `unsloth/Qwen3-VL-4B-Instruct-GGUF:Q4_0` (+mmproj) and google/gemma-3-4b-it for the CPU lane.
3. Write the render helpers and graders first with tests (no model needed): image determinism per seed, grader edge cases, est_image_tokens formulas; run the 3-question trivia gate on each VLM lane, then a 3-image smoke test at 448 px.
4. Add backends.json entries, wire write_report/provenance/bench_stats, run --repeats 3 on the two Qwen3-VL lanes (QAIRT vs GGUF) at 448 px and publish the first PASS/FAIL/CUT table plus TTFT(image) − TTFT(text) — that single table answers whether the NPU VLM is usable at 4096.
5. Sweep --long-edge 448/640/896/1280 on the GGUF lane for the resolution curve; add the geniex-bench.exe side channel (--plugin qairt/llama_cpp --image --mmproj-path, parse ttft_ms/prefill_tps/decode_tps/media_time) and a Windows NPU-utilisation sample (Task Manager counter; WPR Neural Processing profile — counter path unverified) as extra columns like the existing core-count column.

### Risks

- The 4096 QAIRT ceiling is the binding constraint: a 1024x768 screenshot is ~1,003 Qwen2.5-VL / ~768 Qwen3-VL tokens and a 2560x1440 frame (~4.7k/3.6k) does not fit at all; every image must be pre-resized, which forbids CC BY-ND datasets (RealWorldQA) and makes ScreenSpot-Pro unusable at native resolution.
- Qwen2.5-VL-7B w4a16 may not fit the 2.93 GiB HTP vmem or may be limited to 2048 context on QAIRT; the 7B row may be unmeasurable on the NPU.
- Loading a GGUF after a QAIRT bundle crashes the server — the VLM needs its own port; the start script cannot express that today.
- media_time/NPU utilisation/energy are not observable over /v1; the image-cost metric is a TTFT difference and must be labelled as such; energy is not measurable on first-gen Snapdragon X via HWiNFO at all.
- GGUF lanes sample at temperature 0 (repeats mandatory) and the QAIRT VLM's accuracy-mode chat-template bug was fixed only in v0.6.1 — pin the version in provenance and re-measure on any GenieX update.
- Synthetic renders can leak into 'reading' via font/colour regularities that real screenshots lack; keep a small opt-in public spot check to calibrate, and use the control VLM to mark renders every model fails as suspect rather than as capability results.
- Public datasets have licence traps (ChartQA HF copy GPL-3.0; SWE-bench Multimodal/MMBench licences unstated) and heavy harnesses (Docker JS tests, similarity metrics); keep them off the default path.
- The grader will inherit bench_coding's truncation-classification defects unless those are fixed first — fix looks_truncated before copying it.

---

## Sources the two researchers verified

Only claims read from the cited page are listed; anything the researchers rated
"likely" or "uncertain" stays out of this page and in the review's raw output.

### Multimodal on GenieX and Qualcomm AI Hub

- GenieX local server: POST /v1/chat/completions supports image_url whose url may be a local file path (optional file:// prefix), an http/https URL the server fetches, or a base64 data URI ("data:image/png;base64,..."). Verbatim example uses {"type":"image_url","image_url":{"url":"/full/path/to/landmark.jpg"}} and {"type":"input_audio","input_audio":{"data":"/full/path/to/jfk.wav"}} in the same message. Endpoints documented: POST /v1/chat/completions, POST /v1/completions, GET /v1/models, GET /v1/models/{model}; no /v1/embeddings or /v1/audio/transcriptions documented.  
  <https://geniex.aihub.qualcomm.com/en/run/cli/local-server.md>
- GenieX local server: "Audio input runs on the llama.cpp backend only. QAIRT models report audio: false." Server decoder handles WAV/MP3/FLAC by magic bytes, down-mixes to mono and resamples to the encoder rate (typically 16 kHz). Only google/gemma-4-E2B-it-qat-q4_0-gguf (~4.0 GiB) is named as shipping an audio-capable (conformer) mmproj. SoX is needed only for the CLI /mic recording command, not for file or server audio input.  
  <https://geniex.aihub.qualcomm.com/en/tutorials/audio-input.md>
- GenieX v0.6.0 (2026-09-03) release notes include: "pin mtmd vision encoder to HTP for npu/hybrid" (#1420), "apply chat template in geniex-bench --accuracy mode" (#1421), "reuse VLM KV via char-level prefix match" (#1363), SmolVLM-2B and SmolVLM2-2.2B added to the QDC benchmark matrix, audio-input docs across CLI/server/Python, bundled QAIRT runtime by default with --qairt-lib override. v0.6.1 (same day): "fix: vlm does not have chat template applied in accuracy mode" (#1428). No release after v0.6.1 as of 2026-09-05.  
  <https://github.com/qualcomm/GenieX/releases>
- GenieX README states "Both LLMs and VLMs are supported"; runtimes are llama_cpp (any GGUF, NPU/GPU/CPU) and qairt (pre-compiled per-chipset bundle, NPU only). Quickstart names Gemma 4, Qwen 2.5-VL-7B, Qwen3, Qwen3-4B.  
  <https://github.com/qualcomm/GenieX>
- GenieX models page: for llama.cpp, "Stick with Q4_0 if you want the model to land on the Hexagon NPU. Other precisions will work but typically run on GPU or CPU." Most AI Hub bundles are w4a16. GGUF VLMs with a conformer encoder in the mmproj handle audio; "QAIRT bundles do not support audio." Input modality table: text = LLM+VLM, image = VLM only.  
  <https://geniex.aihub.qualcomm.com/en/models/supported.md>
- GenieX platforms page: Snapdragon X Elite / X Plus / X (SoC ids X1E*, X1P*, X126100) are the Compute family on Windows ARM64; AI Hub asset id is "qualcomm-snapdragon-x-elite" (all X SKUs map to the same asset); llama.cpp supports npu/gpu/cpu/hybrid, qairt is NPU-only and gpu/cpu requests are coerced to npu with a warning.  
  <https://geniex.aihub.qualcomm.com/en/get-started/platforms.md>
- geniex pull has --model-type llm|vlm (auto-detected when omitted); geniex-bench flags: --plugin llama_cpp|qairt (required), --device cpu|gpu|npu|hybrid, -m model, -n tokens to generate, --temperature, --output-json. The CLI reference does not document --image/--mmproj for serve.  
  <https://geniex.aihub.qualcomm.com/en/run/cli/reference.md>
- geniex-bench (sdk/benchmark) measures TTFT, prefill_tps, decode_tps, gen_tokens, prompt_tokens with median/min/max/mean/stdev; --accuracy runs once and prints text; VLMs: llama_cpp uses model .gguf plus --mmproj-path, QAIRT bundles the vision encoder internally so only --vlm and --image are needed. For a VLM TTFT "includes the media encoder, so it is not directly comparable to a text-only TTFT". QAIRT metrics are reported over padded length ceil(n/128)*128 because the engine pads input ids to 128-token chunks. No memory, NPU-utilisation or power metrics.  
  <https://github.com/qualcomm/GenieX/blob/main/sdk/benchmark/README.md>
- GenieX notes/run.md defines media_time as "The vision/audio encoder only — turning pixels/audio into decoder-space embeddings. 0 on text-only runs" and says prefill for a VLM includes the media encoder. This split is exposed by the SDK/bench tool, not by the OpenAI HTTP server.  
  <https://github.com/qualcomm/GenieX/blob/main/notes/run.md>
- Qualcomm AI Hub Compute catalogue lists as multimodal/VLM: Gemma-4-E4B-it, Gemma-4-E2B-it (text+image), Qwen2.5-VL-7B-Instruct, Qwen3-VL-4B-Instruct, Qwen3-VL-8B-Instruct.  
  <https://aihub.qualcomm.com/compute/models>
- AI Hub Qwen3-VL-4B-Instruct page lists Snapdragon X Elite and X2 Elite among supported chipsets; model license Apache-2.0 plus Qualcomm Generative AI terms; run via "geniex infer ai-hub-models/Qwen3-VL-4B-Instruct". The HF card (huggingface.co/qualcomm/Qwen3-VL-4B-Instruct) lists precisions w4a16 (QAIRT) and q4_0 (llama.cpp), context length 4096 (512 also tested), QAIRT 2.45. Bundle size on disk and image-token count are not published.  
  <https://aihub.qualcomm.com/models/qwen3_vl_4b_instruct>
- AI Hub Qwen3-VL-2B-Instruct page also lists Snapdragon X Elite / X2 Elite, Apache-2.0 model license.  
  <https://aihub.qualcomm.com/models/qwen3_vl_2b_instruct>
- Qwen2.5-VL-7B-Instruct on AI Hub: fetch with "qai-hub-models fetch Qwen2.5-VL-7B-Instruct --runtime geniex_qairt --precision w4a16"; HF card lists context length 2048 for GENIE/GENIEX_QAIRT and 512/4096 for GENIEX_LLAMACPP, QAIRT 2.45, Apache-2.0. Bundle is compiled with QAIRT 2.45 and GenieX bundles its own QAIRT 2.45 so versions match.  
  <https://huggingface.co/qualcomm/Qwen2.5-VL-7B-Instruct>
- Qwen3-VL-8B-Instruct on AI Hub is listed only for Snapdragon 8 Elite / 8 Elite Gen 5 mobile (and flagged as not currently supported), not for Snapdragon X Elite.  
  <https://aihub.qualcomm.com/mobile/models/qwen3_vl_8b_instruct>
- Macnica field report (GenieX v0.3.13, Dragonwing IQ-9075, dual Hexagon V73): geniex pull ai-hub-models/Qwen2.5-VL-7B-Instruct:w4a16, geniex serve --host 0.0.0.0:18181, image sent as image_url base64 data URI; measured ~7.6 tok/s decode, ~1.1-1.2 s TTFT. Different chipset from Snapdragon X, so numbers are indicative only.  
  <https://www.macnica.co.jp/en/business/semiconductor/articles/qualcomm/150066/>
- llama.cpp docs/multimodal.md: vision models Gemma 3/4, SmolVLM/SmolVLM2, Qwen2/2.5-VL, Pixtral 12B, InternVL 2.5/3, Mistral Small 3.1, Llama 4 Scout, Moondream2; audio Ultravox v0.5, Voxtral Mini, Qwen3-ASR; mixed Qwen2.5/3 Omni and Gemma 4. Load with -m model.gguf --mmproj projector.gguf; "By default, multimodal projector will be offloaded to GPU" (--no-mmproj-offload); "some models may require large context window, for example: -c 8192"; llama-server takes images via the OpenAI-compatible /chat/completions API.  
  <https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md>
- Qwen2/2.5-VL: "a patch of 28 * 28 pixels is a token"; default visual tokens per image range 4-16384; a 1024x768 image is about 1,003 tokens; min_pixels/max_pixels control the count.  
  <https://huggingface.co/Qwen/Qwen2-VL-7B-Instruct/discussions/47>
- Gemma 3 model card: images normalized to 896x896 and "encoded to 256 tokens each"; 4B/12B/27B have 128K context. Gemma 3n also uses 256 tokens per image at 256/512/768 px.  
  <https://ai.google.dev/gemma/docs/core/model_card_3>
- Ollama OpenAI compatibility: /v1/chat/completions supports image content via image_url with a base64 data URI ("data:image/png;base64,..."); "Image URL" (http) is explicitly listed as unsupported; logprobs unsupported; example uses qwen3-vl:8b. Native /api/chat uses an images: [base64] array.  
  <https://docs.ollama.com/api/openai-compatibility>
- Ollama vision-tagged library (2026-09) includes qwen3-vl (2b/4b/8b/30b/32b/235b), gemma4 (12b/26b/31b), qwen3.5 (0.8b-122b), minicpm-v4.5/4.6, medgemma, glm-ocr, mistral-medium-3.5 and others.  
  <https://ollama.com/search?c=vision>
- ChartQA (HuggingFaceM4/ChartQA): license gpl-3.0; train 28.3k / val 1.92k / test 2.5k; single-string answers in a list; human_or_machine flag; image widths 184-1320 px. lmms-lab/ChartQA test is 2.5k rows, 72.6 MB, answers are numbers, yes/no, or category strings. Standard metric is relaxed accuracy (exact match for text, 5% tolerance for numbers).  
  <https://huggingface.co/datasets/HuggingFaceM4/ChartQA>
- OCRBench v2 (ling99/OCRBench_v2): MIT, 10,000 human-verified QA pairs, 989 MB, answers given as lists of acceptable strings (e.g. "enabled"/"on"), includes APP-agent UI screenshot tasks.  
  <https://huggingface.co/datasets/ling99/OCRBench_v2>
- DocVQA (lmms-lab/DocVQA): apache-2.0; validation 5.35k rows with public answers as list of strings (e.g. ["0.28"]), test 5.19k without public answers; metric ANLS.  
  <https://huggingface.co/datasets/lmms-lab/DocVQA>
- TextVQA (facebook/textvqa): CC BY 4.0; 34,602 train / 5,000 val / 5,734 test.  
  <https://huggingface.co/datasets/facebook/textvqa/blob/main/README.md>
- MMMU (MMMU/MMMU): apache-2.0; 150 dev / 900 validation / 10,500 test; "The answers and explanations for the test set samples are now released"; multiple-choice letters; up to 7 images per question (image_1..image_7); 3.66 GB.  
  <https://huggingface.co/datasets/MMMU/MMMU>
- MathVista (AI4Math/MathVista): cc-by-sa-4.0; testmini 1k rows with public answers, test 5.14k; answer types multi_choice, free_form integer, free_form float.  
  <https://huggingface.co/datasets/AI4Math/MathVista>
- RealWorldQA (xai-org/RealworldQA): cc-by-nd-4.0 (no derivatives), 765 test rows, mixed multiple-choice / short answer / yes-no, 678 MB.  
  <https://huggingface.co/datasets/xai-org/RealworldQA>
- ScreenSpot-Pro (likaixin/ScreenSpot-Pro): MIT, 1,581 high-resolution professional screenshots (most common 2560x1440), 3.38 GB, bboxes as relative [x, y, w, h] in [0,1], targets average 0.07% of screen area.  
  <https://huggingface.co/datasets/likaixin/ScreenSpot-Pro>
- SWE-bench Multimodal: 617 task instances (612 issue-PR pairs from 17 JS/TS repos; dev 102 / test 510), field image_assets is a JSON map of where images appear, graded by unit tests in Docker via python -m swebench.harness.run_evaluation; V2 retains 480 reproducible tasks. No license field on the HF card.  
  <https://huggingface.co/datasets/SWE-bench/SWE-bench_Multimodal/blob/main/README.md>
- Design2Code dataset is ODC-By (built on C4, also ODC-By), research-use intent; WebSight is CC BY 4.0 with 823k synthetic HTML/CSS + screenshot pairs. Both are graded by visual/structural similarity, not exact match.  
  <https://huggingface.co/datasets/HuggingFaceM4/WebSight>
- ONNX Runtime QNN EP: profiling_level basic|detailed|optrace (optrace needs QAIRT >= 2.39, emits _qnn.log for qnn-profile-viewer), ETW profiling on Windows, htp_performance_mode burst|balanced|default|high_performance|...|sustained_high_performance; no option reports NPU utilisation or power. Windows ARM64: pip install onnxruntime-qnn (Python 3.11.x).  
  <https://onnxruntime.ai/docs/execution-providers/QNN-ExecutionProvider.html>
- Windows: Task Manager Performance tab shows NPU utilisation %, memory, driver; recent Windows 11 builds add NPU / NPU Engine columns per process; Windows Performance Recorder ships a Neural Processing profile recording NPU/MCDM activity. HWiNFO cannot report package/NPU power on first-generation Snapdragon X (X1) — only on X2.  
  <https://learn.microsoft.com/en-us/windows/ai/npu-devices/>
- Repo conventions to fit: bench_coding.py uses TASKS with exact required signatures, hidden asserts, PASS/FAIL/CUT verdicts (CUT from finish_reason=length, then token cap, then unclosed fence), --backend from backends.json with a 'control' calibration backend, repeats, provenance block (UTC, host, OS, arch, endpoint, model, server version). backends.json geniex-npu note: 'QAIRT bundles only - loading a GGUF after a QAIRT bundle crashes the server'. geniex serve handles one request at a time (docs/geniex-local-ai-setup.md).  
  <file:///home/jonas/GitHub/ANTfrastructure/linux/llm-stack/bench_coding.py>

### Models and runtimes

- Qualcomm AI Hub (Compute) LLM list: Qwen3-0.6B, Qwen3-1.7B, Qwen3-4B, Qwen3-4B-Instruct-2507, Qwen3-8B, Qwen2.5-VL-7B-Instruct, Qwen3-VL-4B-Instruct, Qwen3-VL-8B-Instruct, Gemma-4-E4B-it, Gemma-4-E2B-it, Ministral-3-3B-Instruct-2512, GPT-OSS-20B; header lists Snapdragon X Elite / X Plus 8-Core / X2 Elite.  
  <https://aihub.qualcomm.com/compute/models>
- huggingface.co/qualcomm additionally lists Llama-v3.2-3B-Instruct-SSD, JAIS-6p7b-Chat and Llama-v3.1-8B-Instruct as text-generation repos (248 models total in the org).  
  <https://huggingface.co/qualcomm>
- Qwen3-8B QAIRT bundle (qualcomm/Qwen3-8B, ai-hub-models/Qwen3-8B): context length 4096, Snapdragon X Elite + X2 Elite; no context above 4096 offered.  
  <https://aihub.qualcomm.com/compute/models/qwen3_8b>
- Qwen3-4B-Instruct-2507: all GENIE / GENIEX_QAIRT builds are 4096-context w4a16; a GENIEX_LLAMACPP q4_0 variant is also listed; no configuration exceeds 4096.  
  <https://huggingface.co/qualcomm/Qwen3-4B-Instruct-2507>
- Gemma-4-E4B-it on Snapdragon X Elite is listed under GENIEX_LLAMACPP q4_0 (not QAIRT): ~20.5 tok/s, TTFT 0.54–2.2 s at 512 ctx; ~11.8 tok/s at 4096 ctx. The w4a16 QAIRT row is for Snapdragon 8 Elite Gen 5. Min QNN SDK 2.45.0. Model context up to 256K (128K for E-series).  
  <https://huggingface.co/qualcomm/Gemma-4-E4B-it>
- Gemma-4-E2B-it on X Elite: GENIEX_LLAMACPP q4_0, 32.89 tok/s at 512 ctx, TTFT 1.3–5.2 s; X2 Elite 54.08 tok/s.  
  <https://huggingface.co/qualcomm/Gemma-4-E2B-it>
- Ministral-3-3B-Instruct-2512 AI Hub entry lists Snapdragon X2 Elite (26–39 tok/s, GENIEX_LLAMACPP q4_0) and Snapdragon 8 Elite but NOT Snapdragon X Elite.  
  <https://huggingface.co/qualcomm/Ministral-3-3B-Instruct-2512>
- Llama-v3.1-8B-Instruct on Snapdragon X Elite: 4.31–10.73 tok/s, TTFT 0.23–7.39 s, 4096 ctx, w4a16; 'Due to licensing restrictions, we cannot distribute pre-exported model assets' — must compile via ai-hub-models.  
  <https://huggingface.co/qualcomm/Llama-v3.1-8B-Instruct>
- GenieX accepts any GGUF from Hugging Face ('Any GGUF model on the llama.cpp runtime'); Qualcomm AI Engine Direct (QAIRT) bundles are NPU-only by design (CPU/GPU requests coerced to NPU); Q4_0 recommended for Hexagon NPU.  
  <https://geniex.aihub.qualcomm.com/en/resources/faq>
- GenieX CLI: model spec `<repo>[:<precision>]` (e.g. `geniex pull unsloth/Qwen3-8B-GGUF:Q4_0`); `--nctx` default 4096 (scales up to trained max for GGUF; baked into the compiled bundle for QAIRT, `--sliding-window` evicts old context); `--max-tokens` default 2048 = maximum generation length per response; `--compute npu|gpu|cpu|hybrid|HTP0..HTP3`; `--ngl` llama.cpp only.  
  <https://geniex.aihub.qualcomm.com/en/run/cli/reference>
- GenieX v0.6.0 (2026-09-03): parse Qwen3.5, GPT-OSS and LFM2 tool calls (#1417); stream text and tool calls as they complete (#1405); llama.cpp bumped to 0eadef; bundled QAIRT runtime (--qairt-lib); ModelScope pulls; disk-space preflight. v0.6.1 (same day): VLM chat-template fix only.  
  <https://github.com/qualcomm/GenieX/releases>
- GenieX PR #1417: tool-call parsing is GenieX's own CLI/server code, not llama.cpp's common/chat parser. Adds Qwen3.5 format `<tool_call><function=NAME><parameter=KEY>` (also covers Qwen3-Coder, Nemotron Nano 3, StepFun-3.5), GPT-OSS harmony (`to=functions.`), LFM2/LFM2.5 python-style `[NAME(`. Limitations: no tool schema reaches the parser (type inference), GPT-OSS streaming emits headers as content noise, Qwen3.5 without `<tool_call>` wrapper unhandled; MiniMax-M3, DeepSeek, MiniCPM5, Mistral, Kimi, Functionary, Cohere2 remain unparsed.  
  <https://github.com/qualcomm/GenieX/pull/1417>
- GenieX PR #1298 (merged 2026-08-06): server matches tool-call JSON by balanced braces instead of wrapper tags; returns the first object that decodes with a string `name` plus `arguments` (object or string); wrapper tags like <tool_call> become transparent.  
  <https://github.com/qualcomm/GenieX/pull/1298>
- GenieX PR #1345 (merged 2026-08-14): server parses Gemma 4 format `<|tool_call>call:NAME{key:value}<tool_call|>` via toolcall_gemma4.go; other models keep the JSON path; parse failure falls back to plain text. Also merged: #1305 reasoning_format splits thinking into reasoning_content; #1407 keeps tool messages in the prompt.  
  <https://github.com/qualcomm/GenieX/pull/1345>
- GenieX issues: #1409 (open, 2026-08-29) feature request for MoE expert streaming/caching to run large MoE models; #1048 'Add LFM MoE for Android' merged 2026-02-24. No issue found stating MoE GGUFs fail on the CPU lane.  
  <https://github.com/qualcomm/GenieX/issues?q=moe>
- Upstream llama.cpp now uses PEG-based chat parsing (common_chat_format enum: CONTENT_ONLY, PEG_SIMPLE, PEG_NATIVE, PEG_GEMMA4, PEG_MINIMAX_M3) that auto-derives tool-call grammars from the Jinja template; the older docs still list native formats Llama 3.x, Functionary, Hermes/Qwen 2.5, Mistral Nemo, Firefunction, Command R7B, DeepSeek R1 plus a generic fallback. Irrelevant to GenieX's server, which parses independently.  
  <https://raw.githubusercontent.com/ggml-org/llama.cpp/master/common/chat.h>
- Qwen3.5-9B model card: 262,144 ctx; enable_thinking toggle via chat_template_kwargs; tool parser qwen3_coder; benchmarks 9B/4B: BFCL-V4 66.1/50.3, TAU2-Bench 79.1/79.9, LiveCodeBench v6 65.6/55.8, IFEval 91.5/89.8, MMLU-Pro 82.5/79.1 (GPT-OSS-20B column: LCB 74.6, IFBench 65.1).  
  <https://huggingface.co/Qwen/Qwen3.5-9B>
- GGUF sizes: unsloth/Qwen3.5-9B-GGUF Q4_0 5.38 GB, Q4_K_M 5.68 GB, UD-Q4_K_XL 5.97 GB; unsloth/Qwen3.5-4B-GGUF Q4_0 2.58 GB, Q4_K_M 2.74 GB.  
  <https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/tree/main>
- Qwen3-Coder-30B-A3B-Instruct: 30.5B total / 3.3B active, 262K ctx, non-thinking only, recommended temp 0.7/top_p 0.8; SWE-bench Verified 51.6 (OpenHands, 100 turns, per HF discussion #30). GGUF (unsloth): Q4_0 17.4 GB, Q4_K_S 17.5, UD-Q4_K_XL 17.7, Q4_K_M 18.6, Q3_K_S 13.3.  
  <https://huggingface.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF/tree/main>
- Qwen3.6-35B-A3B: 35B/3B active, 262K ctx, thinking model, SWE-bench Verified 73.4, Terminal-Bench 2.0 51.5; GGUF UD-Q4_K_S 20.9 GB, UD-Q4_K_M 22.1 GB, UD-Q3_K_S 15.4 GB — Q4 does not fit the ~22–24 GB host budget comfortably.  
  <https://huggingface.co/Qwen/Qwen3.6-35B-A3B>
- Gemma 4 family: E2B, E4B, 12B, 26B-A4B, 31B; native function calling; thinking via `<|think|>` at start of system prompt, disable with `--chat-template-kwargs '{"enable_thinking":false}'`; sampling temp 1.0/top_p 0.95/top_k 64; 4-bit RAM E4B 5.5–6 GB, 26B-A4B 16–18 GB. unsloth/gemma-4-E4B-it-GGUF Q4_0 4.84 GB, Q4_K_M 4.98 GB; gemma-4-26B-A4B UD-Q4_K_M 16.9 GB, MXFP4_MOE 16.6 GB.  
  <https://unsloth.ai/docs/models/gemma-4>
- Gemma 4 model card benchmarks span E2B→31B: LiveCodeBench v6 44.0→80.0, Tau2 24.5→76.9, MMLU Pro 60.0→85.2; no SWE-bench Verified, BFCL, Terminal-Bench or Aider published; E-series ctx 128K, larger 256K.  
  <https://ai.google.dev/gemma/docs/core/model_card_4>
- LFM2.5-8B-A1B (Liquid AI): 8.3B total / 1.5B active, 128K ctx, tool calls as `<|tool_call_start|>[fn(arg="v")]<|tool_call_end|>` (JSON optional via system prompt), explicit CoT before answers, BFCLv3 64.79, BFCLv4 49.73, IFEval 91.84; recommended temp 0.2/top_k 80/rep 1.05. GGUF Q4_0 4.84 GB, Q4_K_M 5.16 GB.  
  <https://huggingface.co/LiquidAI/LFM2.5-8B-A1B>
- gpt-oss-20b: SWE-bench Verified 60.7, Aider Polyglot 34.2, Tau-Bench Retail 54.8 (OpenAI model card, 2025-08-05); MXFP4 GGUF 12.1 GB; reasoning_effort low/medium/high; harmony format; ≥14 GB memory recommended for the 4-bit quant.  
  <https://arxiv.org/pdf/2508.10925>
- GLM-4.7-Flash: 30B-A3B (~3.6B active), 200K ctx, MIT, SWE-bench Verified 59.2, τ²-Bench 79.5, thinking model; ~18 GB at 4-bit; GGUF Q4_0 17.2 GB, UD-Q4_K_XL 17.5, Q4_K_M 18.3; tool-calling recommended temp 0.7; a Jan-21 llama.cpp scoring-function fix required re-downloading GGUFs. Tool-call parser flag in vLLM is glm47 (not a format GenieX parses).  
  <https://unsloth.ai/docs/models/tutorials/glm-4.7-flash>
- Devstral Small 2 (24B, Apache 2.0): SWE-bench Verified 68.0, Terminal-Bench 2 22.5, 256K ctx, no thinking mode, recommended temp 0.15; native Mistral tool parsing; llama.cpp needed PR #17945 / Dec-13-2025 template fix; vision not supported in llama.cpp. GGUF Q4_0 13.5 GB, Q4_K_M 14.3, UD-Q4_K_XL 14.5, Q3_K_M 11.5.  
  <https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512>
- Ministral-3-8B-Instruct-2512 GGUF: Q4_0 4.94 GB, Q4_K_M 5.2 GB; Ministral 3 shares architecture with Devstral 2 (Mistral tool format).  
  <https://huggingface.co/unsloth/Ministral-3-8B-Instruct-2512-GGUF/tree/main>
- IBM Granite 4.2-8B (released 2026-08-25): native thinking (enable_thinking / low_effort), tool calls emitted as `<tool_call><function=NAME><parameter=KEY>…` XML (same family as Qwen3.5 → parseable by GenieX #1417), 128K ctx (512K ext.), BFCL v4 52.39, SWE-bench Verified 47.67, LiveCodeBench v6 73.24, MMLU-Pro 74.04; bartowski Q4_K_M 5.54 GB. Granite 4.1 (2026-04-29): 3B/8B/30B dense, BFCL v3 60.8 (3B) / 68.3 (8B), JSON-in-<tool_call> format.  
  <https://huggingface.co/ibm-granite/granite-4.2-8b>
- NVIDIA Nemotron-3-Nano-30B-A3B: 30B / 3.5B active, 256K default ctx (1M max), enable_thinking toggle, qwen3_coder tool parser, SWE-Bench (OpenHands) 38.8, BFCL v4 53.8, LiveCodeBench v6 68.3, Terminal-Bench hard 8.5, TauBench V2 49.0; tool-calling temp 0.6/top_p 0.95. GGUF (unsloth) Q4_0 18.2 GB, UD-Q4_K_XL 22.8 GB, Q4_K_M 24.6 GB.  
  <https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16>
- Anthropic OpenAI-compat: base_url https://api.anthropic.com/v1/, Claude key passed as the OpenAI api_key (Authorization bearer), optional anthropic-workspace-id header; temperature 0–1 (values >1 capped); seed IGNORED; n must be 1; logprobs/response_format/presence_penalty/reasoning_effort ignored; tool_calls fully supported; strict ignored; system_fingerprint always empty; 'not considered a long-term or production-ready solution'.  
  <https://platform.claude.com/docs/en/api/openai-sdk>
- Mistral chat completions: Authorization: Bearer $MISTRAL_API_KEY at https://api.mistral.ai/v1/chat/completions (repo already uses https://api.eu.mistral.ai/v1); `random_seed`: 'If set, different calls will generate deterministic results'; temperature recommended 0.0–0.7; tools + tool_choice (auto/none/any/required). zai-glm-5-2: 1M input / 128K output, function calling, $1.4 in / $0.14 cached / $4.4 out per M tokens.  
  <https://docs.mistral.ai/api/endpoint/chat>
- OpenRouter: Authorization: Bearer <key>; optional HTTP-Referer and X-OpenRouter-Title (legacy X-Title) attribution headers; `seed` supported as passthrough ('should return the same result… not guaranteed for some models'); pin with provider.order + allow_fallbacks:false; require_parameters:true refuses providers that don't support every parameter sent.  
  <https://openrouter.ai/docs/features/provider-routing>
- Gemini OpenAI-compat endpoint: https://generativelanguage.googleapis.com/v1beta/openai/, Authorization: Bearer GEMINI_API_KEY; reasoning_effort maps to thinking levels; 'any other parameters not listed… will be silently ignored' — seed is not in the chat-completions supported list (only via extra_body for video); still beta.  
  <https://ai.google.dev/gemini-api/docs/openai>
- llama.cpp Hexagon backend docs: MUL_MAT_ID (the MoE op) is listed among ops needing REPACK buffers (GGML_HEXAGON_HOSTBUF=1 for testing); no statement about MoE model support or Snapdragon X Elite; multi-session via GGML_HEXAGON_DEVICES / GGML_HEXAGON_NDEV.  
  <https://github.com/ggml-org/llama.cpp/blob/master/docs/backend/snapdragon/README.md>

### Open questions the sources did not settle

- Does GET /v1/models/{model} on v0.6.1 expose an image/vision capability flag alongside the documented `audio` field? Probe the live server; the docs only quote `audio: false` for QAIRT models.
- What is the effective image-token budget of the QAIRT Qwen3-VL-4B-Instruct bundle (fixed encoder resolution? max_pixels baked in?) and its on-disk size for qualcomm-snapdragon-x-elite - neither AI Hub nor the HF card publishes it; check the bundle's metadata.json after `geniex pull`.
- Qwen2.5-VL-7B-Instruct HF card says context 2048 for GENIEX_QAIRT vs 4096 for the text bundles - confirm against /v1/models and whether a 7B w4a16 VLM fits the 2.93 GiB HTP vmem on this X126100 at all.
- Does the v0.6.0 'pin mtmd vision encoder to HTP for npu/hybrid' path work on this host's NPU driver for GGUF+mmproj models, or does the GGUF VLM on the NPU lane still lose to CPU as the text GGUFs do?
- Is Qwen3-VL listed in llama.cpp's multimodal.md at the 0eadefe hash GenieX ships (the fetched page listed Qwen2/2.5-VL and Gemma 3/4 explicitly; Qwen3-VL support is inferred from GenieX's own Qwen3-VL-4B serve example and unsloth GGUFs)?
- Exact Windows performance-counter path for the NPU (for a Get-Counter side channel) - Task Manager shows it, but the counter name was not confirmed from a source.
- SWE-bench Multimodal and MMBench licences were not found on their dataset cards; irrelevant if the synthetic-only default is adopted.
- Whether the owner meant QAIRT-direct (qnn-net-run on the Linux-staged QAIRT 2.49 SDK) rather than GenieX; the Linux SDK cannot reach the Windows-side HTP from WSL2, so QAIRT-direct would have to run on the Windows host anyway.
- Does `geniex serve --max-tokens N` (default 2048 per the CLI reference) raise the per-response output cap the benchmarks hit, and does the OpenAI server honour a per-request max_tokens above it? This decides whether §1j/§1k CUT rows are runtime facts or a default.
- Do MoE GGUFs (LFM2.5-8B-A1B, gpt-oss-20b, Qwen3-Coder-30B-A3B) run on GenieX's CPU lane at all, and at what decode rate? No GenieX doc or issue confirms or denies it; llama.cpp upstream supports MUL_MAT_ID on CPU, and LFM MoE was merged for Android (#1048).
- Is MXFP4 (gpt-oss-20b, gemma-4-26B-A4B MXFP4_MOE) a working quant path on GenieX's llama.cpp build, given IQ1–IQ3 are broken there? Needs the 3-trivial-question gate before any benchmark.
- What runtime does the AI Hub GPT-OSS-20B entry actually use on Snapdragon X Elite (page says X Elite supported but 'not supported on any Compute chipset' and gives no runtime)? A QAIRT MoE bundle would be the first non-Qwen NPU candidate; a llama.cpp path is just the unsloth GGUF.
- Does GenieX's brace-scanning JSON parser (#1298) accept Granite-4.1 / SmolLM3 / Phi-4-mini outputs (name+arguments JSON) and does it return only the first call when a model emits a JSON list? Multi-call turns would be silently truncated.
- Does GenieX's server honour `chat_template_kwargs: {enable_thinking:false}` (Qwen3.5, Granite 4.2, Nemotron) or Gemma's `<|think|>` convention, so the thinking tax can be toggled per request rather than per prompt?
- How large is the Qwen3.5-9B → Qwen3.8-9B-Distill delta on tool-calling and coding (the distill's published gains are MMLU/GSM8K only)? If zero, the 9B row in the ranking is really a Qwen3.5-9B row.
- Which provider will serve as the hosted control, and does the harness map `seed` to Mistral's `random_seed`? Only Mistral documents deterministic seeding; Anthropic compat ignores seed, Gemini compat drops it silently, OpenAI marks it deprecated.

---

## What the review did not cover

Named so the next round starts here rather than re-reading what was read.

- benchmark_openai_api.py — the throughput/TTFT/correctness tool that run_benchmarks.sh actually drives and whose legacy report shape the viewer reads — received no lens; its correctness gate (3 trivia questions), TTFT measurement under streaming, and --extra-params handling are unreviewed.
- bench_embeddings.py, bench_lanes.py's measurement logic (batching/SERIALISED verdict, lane-interference maths), geniex_toolcall_shim.py's parser and inspect_gguf.py's RISKY heuristic were touched only tangentially; no lens verified their graders or their tests.
- No lens exercised anything live: every finding is static reading plus scratch reproduction against fixtures. Whether GenieX v0.6.1 really samples at temperature 0 on GGUF lanes, whether `geniex serve --max-tokens` was the 2048 cap, whether /v1/models reports capabilities, and whether the QAIRT lane is still deterministic after v0.6.1 remain unmeasured.
- Reproducibility of the published tables was not attempted: no raw report JSON is stored for § 1i/1j/1k/1m/1n, so no lens could check a single published number against its inputs — only that the code paths could have affected them.
- The content of prompts/tool-disambiguation.md (the system prompt that moved a model 8/8→6/8 and 25/27) was not reviewed for leaking case answers or over-fitting to the 27 cases.
- The viewer's own statistics (wilson() in App.jsx) were not checked for parity with bench_stats.py; a divergence would print different intervals in the table and the page.
- Security of the agent path: bench_agent runs opencode with full network and HOME access under whatever global permission config exists (noted by one lens as methodology, not tested); the shim and the benchmarks execute model output with a sandbox only in bench_coding.
- Cost of the suite itself — wall time and energy of a full sweep per candidate, and how that bounds how many models/repeats the owner can realistically run — was not estimated by any lens.
- Windows-side scripts (Start-GeniexServers.ps1 and friends) were read for parameters but not tested; PowerShell test coverage of the lane starter is unknown.
- Memory/RAM footprint and HTP vmem measurement per model (the constraint that decides which candidates fit) has no tool in the suite and no lens proposed one beyond the research's rough per-model estimates.
- Inter-rater/human validity: no lens asked whether the 27 coding tasks or 27 tool cases correlate with the agent outcome on the same models — the suite has three benchmarks and one agent result but no analysis of which proxy predicted it (P3.1 says none did).

---

## Method

Seven lenses read the code statically and were allowed to import the modules
and call pure functions from scratch scripts, never to touch a network endpoint
or a repository file. Each lens returned typed findings with a file, a line and
quoted evidence. Every finding of kind *defect* went to a separate verifier
prompted to refute it and told to default to "refuted" when uncertain; the
verifier reported whether the defect is real, whether a test or a document
already pins it, and whether it is material. Findings of the other kinds
(methodology, improvement, stale documentation) were not individually verified
and appear here only where the synthesis merged them into a backlog item. Two
researchers used web search with the instruction to separate what they read on
a page from what they inferred. A final synthesis merged duplicates across
lenses, dropped anything the roadmap already lists as done, and wrote a
completeness critique of its own coverage, which is the section above this one.
