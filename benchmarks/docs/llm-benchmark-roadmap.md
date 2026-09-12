<!--
Copyright (c) 2025 Kataglyphis
SPDX-License-Identifier: MIT
-->

# LLM benchmark — where it stands and what to build next

The suite in [`linux/llm-stack/`](../linux/llm-stack/README.md) grew in a day,
driven by whatever the previous measurement got wrong. This page steps back:
what it can honestly claim today, what it cannot, and the order in which the
gaps are worth closing.

Written 2026-08-31, after the GenieX/Snapdragon measurement round. Every number
quoted here is measured on that host; see
[`geniex-local-ai-setup.md`](geniex-local-ai-setup.md) for the runs themselves.

> **Brought up to date 2026-09-05** against the panel review's backlog and the
> work it produced; the ranked backlog, the 32 confirmed defects and the model
> shortlist live in
> [`llm-benchmark-review-2026-09-05.md`](llm-benchmark-review-2026-09-05.md).

---

## Where it stands

Counts are deliberately not written down here: three of them rotted within a
week the last time they were. Derive them —
`ls linux/llm-stack/*.py | wc -l` for the tools,
`python3 -m pytest linux/llm-stack/tests --collect-only -q | tail -2` for the
tests (they run offline), and the task and case inventories from the two
one-liners in the [suite README](../linux/llm-stack/README.md) § Benchmarking.

| Tool | Answers |
|---|---|
| `benchmark_openai_api.py` | throughput, TTFT, decode vs prefill, time-to-answer, generic correctness |
| `bench_coding.py` | does the generated code RUN — Python, bash, CMake, Dockerfile, tagged by kind |
| `bench_tools.py` | tool calling: selection, typed arguments, parallel calls, restraint, irrelevance, multi-turn |
| `bench_agent.py` | the whole opencode loop against a scratch repository, scored by that repository's tests |
| `bench_embeddings.py` | embedding shape, speed and whether the vectors mean anything |
| `bench_lanes.py` | does one server batch; do several lanes add up |
| `inspect_gguf.py` | is this GGUF sane (tensor-type histogram) |
| `bench_sweep.py` | the whole suite over a candidates file, in one command |
| `bench_compare.py` | two reports: paired sign test, stored baselines, regression exit code |
| `bench_report.py` · `bench_stats.py` · `bench_provenance.py` · `bench_cli.py` | summaries and the viewer manifest · intervals and the paired tests · what produced a measurement · the one request path |

**What it can claim:** on one host, a defensible ranking on speed, on code that
executes in four languages, on tool calling, and — through `bench_agent` — on
one real agent loop, with cold start, unenforced constraints, inflated sample
counts and truncation artefacts all removed, and with a control endpoint to tell
a hard case from a broken one.

**What it cannot claim:** anything about a model family it has not run;
anything about the non-Python languages' *linting* on a host without
`shellcheck`/`hadolint`/`cmake` (those rows skip visibly); and any separation
between two close candidates that fewer than six cases flip the same way (see
P2.6, recomputed).

---

## Phase 1 — Make a claim survive scrutiny [S–M]

The measurement errors are fixed; the *statistics* are not.

- **P1.1 Report confidence intervals, not bare fractions** [S·★★★] **DONE**
  (`bench_stats.py`). Scores now print as `8/12 = 67% [39-86%]`, and the
  comparer refuses to call an overlapping difference a regression.
- **P1.2 Prompt-variation sensitivity** [M·★★★] **PARTLY DONE** (2026-08-31,
  widened 2026-09-05). `bench_tools --prompt-variants` asks every case in its
  paraphrases, and the paraphrases of the selection cases now share fewer than
  two content words with the tool description they must select — the old ones
  were near-verbatim copies and measured reading. **Still open:** it is opt-in,
  no *spread* is reported (only the combined score), and `bench_coding` has no
  paraphrases at all.
- **P1.3 A control model** [S·★★] **DONE 2026-09-05.** A `control` backend in
  `backends.json`, an example candidate row, and `mark_suspect_cases()` called
  from both rankings: a case the control also **fails** leaves every other
  candidate's score, interval and rank and is named above the table. The
  control keeps its own full score, and a case it merely *errored* on is not
  suspect. Hosted controls became usable at the same time (`api_key_env`).
  **Known gap:** the wall clock is not recomputed, so a suspect case's seconds
  still count toward the time tie-break.
- **P1.4 Partial credit** [M·★] **DONE** (b2d7b0f3, 2026-08-31; denominator
  corrected 2026-09-05). Per-assertion credit prints beside every FAIL. The
  correction matters for any published fraction: a
  `try: f(bad) / except ValueError` block is now **one assertion**, where it
  used to be classified as setup — so a candidate missing only that rule read
  as "test setup raised" with full credit. Nothing downstream aggregates
  partial credit; it is a per-row diagnostic, not a score.
- **P1.5 Record the environment, and serialise runs** [S·★★] **PARTLY DONE.**
  Live lanes are detected and warned about, and provenance records host, arch,
  git SHA and dirtiness. **Not** done: nothing *refuses* to compare two runs
  taken under different load, and liveness is not load — a lane that is up and
  idle looks the same as one under a sweep.

## Phase 2 — A tripwire, not a scrapbook [M] — the highest-value phase

Every tool writes a report and **nothing reads two of them**. Until that
exists, every measurement is a one-off and a regression is invisible.

- **P2.1 `bench_compare.py`** [M·★★★] **DONE.** Diffs two reports (either
  envelope), attributes a shift to the model, the runtime or the grader via
  `bench_provenance.compare()`, and exits non-zero on a regression.
- **P2.2 Accepted baselines** [S·★★★] **DONE.** `--save-baseline <name>` /
  `--baseline <name>`, stored under `baselines/`.

- **P2.0 — more cases** [L·★★★] **DONE for `bench_tools` (8 → 27).** Building
  the tripwire showed it had almost no power: removing the system prompt took a
  model from 8/8 to 6/8 — a real degradation with a known cause — and the
  comparer correctly reported *no regression*, because at n = 8 the intervals
  overlap. Detecting 100 % → 75 % needs 27 cases.

  The expansion immediately earned itself: two failure modes the 8-case suite
  could not see. The model mangled an identifier (`___init__.py` for
  `__init__.py`), and in one multi-turn case it *talked about* the tool call —
  "I already confirmed that `list_files` has been correctly called" — without
  ever telling the user what the files were.

  `bench_coding` grew the same way (2026-08-31, then 2026-09-05): the classic
  three, three novel, the extended set, and the bash/CMake/Dockerfile tasks —
  derive the current split rather than trusting a number here. Each task needs a
  test set, a reference solution and a known-wrong solution (its own tests
  enforce all three), so it is real authoring work rather than a copy-paste.

- **P2.6 Per-case diffing beat the statistics** [S·★★★] **DONE**, and
  **recomputed 2026-09-05** — the "119 cases" it used to quote was an artefact of the wrong
  test and is retired. Both candidates answer the *same* cases, so the aggregate
  is now judged by an exact two-sided **paired sign test** over the cases that
  disagreed, plus a Newcombe interval on the difference. The floor is **six
  cases flipping the same way with none flipping back**, and it does **not**
  depend on suite size. Under the old unpaired rule, 24/27 vs 18/27 with six
  discordant cases and none flipping back was "not separable"; paired, it is
  p = 0.031. Interval overlap survives only as the fallback for reports with no
  per-case detail, and says so in the finding.

  For a deterministic endpoint the aggregate is the wrong instrument anyway.
  The comparer now diffs **per case**: a case that passed and now fails is a
  concrete, attributable change needing no statistics at all. On the same pair
  of runs it names the five cases the system prompt fixes — and the **two it
  breaks** (`extract_query_with_symbols`, `use_listing`), which no aggregate
  score had revealed. It also catches a swap that leaves the score identical.

## Phase 3 — Measure the thing you actually run [M–L]

The suite measures *endpoints*. You run an *agent*. Nothing connects the two.

- **P3.1 End-to-end agent task** [L·★★★] **DONE 2026-09-04** —
  `linux/llm-stack/bench_agent.py`, written up as § 1m of the GenieX page. It
  did what it was supposed to: it disagreed with every proxy. opencode's fixed
  preamble measures **8,175 tokens**, so the recommended QAIRT bundle (4096
  compiled in) fails all three tasks with **zero tool calls** — the models were
  never the constraint, the short prompts in every prior benchmark were.
  Then "zero tool calls" turned out to be the *server*: GenieX v0.5.0 returned
  Qwen's `<tool_call>` template as plain content, so no agent ever saw a call.
  Behind `geniex_toolcall_shim.py`, on the CPU lane with a trimmed tool set,
  `Qwen3.8-9B-Distill` scored **3/3, verified by the repositories' own tests**,
  at 10-14 minutes per task. **The suite has now observed a pass** — until then
  it had only ever seen failures, and a bug that made everything fail would have
  looked identical. Re-run on **GenieX v0.6.1** (2026-09-05), which parses the
  template itself and has a prefix cache: still 3/3, no shim, **657 s for all
  three tasks** where one used to take 656 s.
- **P3.2 Long context *and* tool calling together** [M·★★★] **Answered by
  P3.1** for the QAIRT lane: what breaks first is the context, and it breaks
  before any tool call is attempted. Ten tool schemas are 5,286 tokens on their
  own — more than the whole 4096 budget, prompt excluded. **Instrumented for the
  GGUF lanes 2026-09-05**, where both fit and the question is quality rather
  than survival: `bench_tools --context-tokens N` pads every case with real
  repository source, `--tools opencode` advertises a ten-schema preamble of the
  size an agent really sends, and `--turn-growth` grows a loop until the context
  runs out and reports where. Measuring the GGUF lanes with them is the open
  half.
- **P3.3 Turn-count and context growth** [M·★★] **Answered, and worse than the
  question assumed.** On the 4096 model the answer is *zero* turns. On the GGUF
  lanes the limit is not the ceiling but the cost of approaching it: there is
  **no prefix cache** (the identical request twice costs 126 s then 122 s), so
  every turn re-prefills the whole conversation at 38-61 tok/s. Context growth
  is paid for again, in full, on each turn.

## Phase 4 — Widen the field [M, gated on downloads]

**Every model measured so far is from one family (Qwen3/Qwen3.8), and no
code-specialised model has been tried.** That is the single largest limit on
the ranking's authority.

- **P4.1 Qwen3-8B W4A16 on the NPU** [M·★★★] **MEASURED 2026-09-04 (816d80c0,
  § 1j) — and CONDITIONAL.** It did not change the recommendation: the 8B lost
  26 of 27 tasks to truncation. But that run was taken on GenieX v0.5.0, whose
  serve default capped every response at 2048 tokens, so the result is
  conditioned on a launch flag rather than on the model. **Re-measure** — the
  launcher now passes `-MaxTokens` (default 4096) and the grader's truncation
  rule has since been fixed. Command in the CHANGELOG entry of 2026-09-05.
- **P4.2 A code-specialised GGUF** [M·★★] **MEASURED 2026-09-04 (51ad1f8d,
  § 1k) — and CONDITIONAL** for the same reason as P4.1, and re-measured with
  it.
- **P4.3 A second family on the GGUF/CPU lane** [M·★] *Reworded 2026-09-05.*
  It used to read "the other QAIRT bundles", which is a dead end on this
  chipset: no further QAIRT bundle exists for the Snapdragon X Elite —
  `Gemma-4` is `GENIEX_LLAMACPP`-only here, `Ministral-3-3B` is X2-only, and
  `Llama-3.1-8B` has no downloadable bundle. Widening the field therefore means
  GGUFs on the CPU lane, where any family runs; the shortlist is in the review
  page § "Adding more models".
- **P4.4 Cross-family sanity** [S·★★] **DONE 2026-09-04 (23676f5a, § 1l).** With
  a non-Qwen model in the table the "model family" explanation was refuted: the
  findings are properties of *this hardware and this runtime*, not of Qwen.

## Phase 4b — Refactoring review, applied (2026-08-31) — CLOSED

A six-dimension review with an adversarial stage that rejects any proposal
unable to name a concrete future task it helps: **45 proposals, 26 rejected,
19 kept — all now applied.**

- **P4b.1 `bench_cli.py`** [DONE] The candidate-resolution block was
  byte-identical across 17 lines in both tools, comment included — and the
  None-label defect existed in **both copies and was fixed twice**, only
  because an audit happened to sweep every file. Extracting it also revealed a
  **third instance the earlier fix had missed**: the single-run branch still
  read `args.label or args.model or model`, so `--backend ollama` with no
  `--model` would have crashed the same way. `resolve_candidates()` and
  `write_report()` now have 12 tests where there were none — nothing in
  `tests/` imports `main()`, so the code deciding *which endpoint gets
  measured* was entirely uncovered. Report writing is also atomic now.
  **`bench_cli.py` is deliberately excluded from `tool_sha256`**: that hash
  means "the grader moved", and folding plumbing into it would fire the alarm
  on every schema edit while the grader is provably unchanged.
- **P4b.2 One report envelope** [DONE for the two newer tools] Both now write
  through `write_report()`; `bench_compare`'s adapter still covers
  `benchmark_openai_api`'s legacy shape, which the viewer reads.
- **P4b.3 `bench_report.py`** [DONE] The per-config summary, manifest generator
  and comparison table left `run_benchmarks.sh` (192 → 131 lines). They were
  heredocs: unreachable from pytest, un-lintable, quoting-fragile — and one had
  already grown a comment about a `KeyError` that "killed the whole comparison
  under `set -e` at the end of every multi-hour run". 12 tests now.
- **P4b.4 `--base-url`** [DONE] `bench_lanes` spelled it `--endpoint` while
  both siblings said `--base-url`; kept as an alias.
- **P4b.5 `resolve_lane(spec, path=)`** [DONE] Its tests were wired to the
  shipped `backends.json` and broke on any edit to it. A test that fails for an
  unrelated change is one people learn to ignore.

**Verified after the refactor rather than assumed:** a live 27-case run scored
**25/27 in 78.7 s** against 25/27 in 79.0 s before it. The comparer then
reported `BENCHMARK SOURCE CHANGED` *and* `unchanged` in the same breath —
exactly the separation the fingerprint exists for — and the baseline was
re-recorded, which the reviewer had priced as the real cost of this refactor
and the proposal had not.

The test count that stood here has been dropped rather than updated: it went
stale twice. `python3 -m pytest linux/llm-stack/tests -q` is the answer, and it
runs offline.

## Phase 5 — The remaining backlog items [M–L]

- **P5.1 Embeddings** [M·★★] **DONE 2026-09-04 (96321e8e)** —
  `bench_embeddings.py` measures shape, speed and *meaning* (do related texts
  land closer than unrelated ones), which is the check that catches a broken
  quantisation.
- **P5.2 Energy per token** [M·★] **PARTLY.** An `energy_proxy` is recorded;
  joules are still not measured, and a proxy is not a measurement.
- **P5.3 The lanes never swept** [S·★] **DONE 2026-09-01**, written up as § 1h
  of the GenieX page.

## Phase 6 — The panel review, applied (2026-09-05)

A seven-lens review of the suite produced a ranked backlog `R1`–`R15` and 32
confirmed defects `D1`–`D32`:
[`llm-benchmark-review-2026-09-05.md`](llm-benchmark-review-2026-09-05.md),
which carries a per-item status line. Most of it landed the same day. The
headline changes, because they alter how every earlier number should be read:

- **The grader's truncation rule was wrong and was live for the § 1n coding
  table.** A closing fence followed by a newline was read as an unclosed
  opener, so a syntax-error reply ending that way was graded CUT — excluded
  rather than counted wrong — while a server cut landing on a prefix that
  happened to compile was graded FAIL. Both are fixed; any table derived under
  the old rule is wrong in **both** directions.
- **Wall statistics now cover measured attempts only**, with the rest reported
  as `unmeasured_wall_s`. An 1800 s abandoned attempt used to decide the rank
  tie-break it was excluded from.
- **Separability is paired** (P2.6 above), the control endpoint calibrates the
  cases (P1.3), and rows nobody graded — overflow, skipped, blocked — are
  excluded on both sides of a comparison.
- **The suite now measures the languages this repository is written in** (bash,
  CMake, Dockerfile) and tags every task by kind, because 27 pure-Python
  spec-transcription tasks cannot predict an agent editing shell and CMake.
- **The agent verdicts refuse the cheap fakes** — editing the red test, writing
  no tests, aliasing the old name — and a context error *after* work has begun
  is now a real failure rather than an excluded row.
- **Adding a model is one command** (`bench_sweep.py`) with API keys read from
  the environment, and a sandbox with RLIMITs plus a grader self-check that
  aborts before contacting an endpoint.

Still open out of that backlog, and worth knowing before publishing a number:
no raw report JSON is stored for any already-published table (R4); and the
§ 1i/§ 1n coding tables have not been re-derived under the fixed grader
(R2/R7/R11) — the exact commands are in the CHANGELOG entry of 2026-09-05.
R3 and R8 closed on 2026-09-05: `bench_tools.evaluate()` has a direct test, and
the determinism probe and the tiered ranking rows are both called.

---

## Suggested order

1. **P2.1 + P2.2** — the tripwire. Everything already measured becomes
   defensible against future drift, and it is a day's work.
2. **P1.1** — stop publishing fractions that the sample cannot support.
3. ~~**P4.1**~~ — measured 2026-09-04, and to be re-measured: its conclusion is
   conditioned on a 2048-token serve default nobody recorded.
4. ~~**P3.1**~~ — done 2026-09-04. It was **not** predictive: the suite ranked
   models while the binding constraints were prompt size, prefill throughput,
   and a server that silently discarded every tool call. None of the three was
   measurable through a short prompt, and the third was indistinguishable from
   "the model is bad at tools" until someone read the raw response body. Prefer
   a cheap end-to-end check *early* over a deep proxy suite — and when a
   benchmark reports zero of something, look at the wire before believing it.
5. Everything else, as the need appears.

## What would change the recommendation

**Partly overturned on 2026-09-04, and not by a model.** The answer below
stands for chat and completion. For *agent* use it is wrong: the bundle cannot
run opencode at all (§ 1m of the GenieX page). Agent work belongs on a GGUF
lane, at roughly two minutes per turn.

Stated up front so it is falsifiable: today's answer is
`qualcomm/Qwen3-4B-Instruct-2507:W4A16` on the NPU lane with
`prompts/tool-disambiguation.md`. It would be overturned by any of:

- Qwen3-8B matching its latency while scoring higher on the **novel** task set;
- an end-to-end agent run where the 4096-token ceiling makes it fail tasks a
  slower long-context model completes;
- a code-specialised model whose prefill cost turns out to be tolerable in a
  real loop.

Two of those three were tested on 2026-09-04 (§ 1j, § 1k) and neither overturned
it — but both runs were taken under a `geniex serve --max-tokens` default of
2048 that nobody recorded, so **both verdicts are conditional until P4.1/P4.2
are re-measured.** The second bullet is settled: the ceiling does make it fail,
before it reads the task at all.
