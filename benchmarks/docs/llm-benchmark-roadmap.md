<!--
Copyright (c) 2025 Kataglyphis
SPDX-License-Identifier: MIT
-->

# LLM benchmark — where it stands and what to build next

The suite grew in a day in the hub's
[`linux/llm-stack/`](../../third_party/ANTfrastructure/linux/llm-stack/README.md),
driven by whatever the previous measurement got wrong; it lives in `benchmarks/`
here now, and that stack is the server it points at. This page steps back:
what it can honestly claim today, what it cannot, and the order in which the
gaps are worth closing.

Written 2026-08-31, after the GenieX/Snapdragon measurement round. Every number
quoted here is measured on that host; see
[`geniex-local-ai-setup.md`](../../third_party/ANTfrastructure/docs/geniex-local-ai-setup.md) for the runs themselves.

> **Brought up to date 2026-09-05** against the panel review's backlog and the
> work it produced; the ranked backlog, the 32 confirmed defects and the model
> shortlist live in
> [`llm-benchmark-review-2026-09-05.md`](llm-benchmark-review-2026-09-05.md).
>
> **And 2026-09-25**, against the campaign that took every open measurement on
> GenieX v0.7.0 and Ollama:
> [`roadmap-campaign-2026-09-24.md`](roadmap-campaign-2026-09-24.md). Each item
> it measured links its section there; its follow-ups are Phase 8.

---

## Where it stands

Counts are deliberately not written down here: three of them rotted within a
week the last time they were. Derive them, from the repository root —
`ls benchmarks/*.py | wc -l` for the capability tools (the request path and the
speed/lane runner are the `orchestrant/benchmark/` package),
`uv run --extra test pytest benchmarks/tests --collect-only -q | tail -2` for the tests (they
run offline), and the task and case inventories from the two one-liners in this
lab's [`benchmarks/README.md`](../README.md) § Benchmarking.

| Tool | Answers |
|---|---|
| `orchestrant/benchmark/openai_api.py` | throughput, TTFT, decode vs prefill, time-to-answer, generic correctness |
| `bench_coding.py` | does the generated code RUN — Python, bash, CMake, Dockerfile, PowerShell, tagged by kind |
| `bench_tools.py` | tool calling: selection, typed arguments, parallel calls, restraint, irrelevance, multi-turn |
| `bench_chat.py` | chat quality: instruction following, JSON-schema replies, multi-turn memory, long-document QA |
| `bench_agent.py` | the whole opencode loop against a scratch repository, scored by that repository's tests |
| `bench_embeddings.py` | embedding shape, speed and whether the vectors mean anything |
| `orchestrant/benchmark/lanes.py` | does one server batch; do several lanes add up |
| `inspect_gguf.py` | is this GGUF sane (tensor-type histogram) |
| `bench_sweep.py` | the whole suite over a candidates file, in one command |
| `upgrade_check.py` | after a runtime upgrade: contract, speed, tools and coding per lane in a fixed order, then `bench_compare` against the previous run |
| `bench_compare.py` | two reports: paired sign test, stored baselines, regression exit code |
| `orchestrant/benchmark/` `report.py` · `stats.py` · `provenance.py` · `client.py` | summaries and the viewer manifest · intervals and the paired tests · what produced a measurement · the one request path |

**What it can claim:** on one host, a defensible ranking on speed, on code that
executes in five languages, on tool calling, and — through `bench_agent` — on
one real agent loop, with cold start, unenforced constraints, inflated sample
counts and truncation artefacts all removed, and with a control endpoint to tell
a hard case from a broken one.

**What it cannot claim:** anything about a model family it has not run;
anything about the non-Python languages' *linting* on a host without
`shellcheck`/`hadolint`/`cmake`/`pwsh` or the PSScriptAnalyzer module (those
rows skip, or say the linter was skipped, visibly); and any separation
between two close candidates that fewer than six cases flip the same way (see
P2.6, recomputed).

---

## Phase 1 — Make a claim survive scrutiny [S–M]

The measurement errors are fixed; the *statistics* are not.

- **P1.1 Report confidence intervals, not bare fractions** [S·★★★] **DONE**
  (`bench_stats.py`). Scores now print as `8/12 = 67% [39-86%]`, and the
  comparer refuses to call an overlapping difference a regression.
- **P1.2 Prompt-variation sensitivity** [M·★★★] **DONE 2026-09-24** (partly
  done 2026-08-31, widened 2026-09-05). `bench_tools --prompt-variants` asks
  every case in its paraphrases. The paraphrases of the selection cases share
  fewer than two content words with the tool description they must select —
  the old ones were near-verbatim copies and measured reading.

  Both tools now report the **spread**:
  - how many cases passed in one phrasing and failed in another
    (`variant_spread`, `variant_spread_rate`)
  - each phrasing's score over the cases asked more than one way (`by_variant`)
  - per case, whether its phrasings disagreed

  `bench_coding --prompt-variants` does the same for the classic and novel
  tasks (two paraphrases each) and four others. Each paraphrase is checked
  mechanically against its original's signature, literals and worked examples.

  Paraphrases no longer inflate `effective_n`: a case asked three ways is one
  observation, observed as written. After a control's suspect cases leave,
  `mark_suspect_cases()` recounts through the same rule (`bench_variants.py`,
  one owner; the second call each producer made is gone).

  **Measured 2026-09-24** on the NPU lane
  ([campaign § P1.2](roadmap-campaign-2026-09-24.md#p12--how-far-the-wording-moves-a-score)):
  7 of the 11 tool cases asked more than one way change verdict with the
  wording (64 % [35–85 %]; as written 9/11, the paraphrases 4/11 and 6/9), and
  3 of 10 coding tasks (30 % [11–60 %]). The lane is deterministic after the
  spacer, so that is the wording, not a draw. **Still open:** the flag is
  opt-in, and no other lane or model has been measured with it.
- **P1.3 A control model** [S·★★] **DONE 2026-09-05.** A `control` backend in
  `backends.json`, an example candidate row, and `mark_suspect_cases()` called
  from both rankings: a case the control also **fails** leaves every other
  candidate's score, interval and rank and is named above the table. The
  control keeps its own full score, and a case it merely *errored* on is not
  suspect. Hosted controls became usable at the same time (`api_key_env`).
  The wall statistics — total, `wall_measured_s`, avg, median, stdev — are
  recomputed over the kept rows too (2026-09-24), so a suspect case's seconds
  leave both the tie-break and `bench_compare`'s timing verdict.
- **P1.4 Partial credit** [M·★] **DONE** (b2d7b0f3, 2026-08-31; denominator
  corrected 2026-09-05). Per-assertion credit prints beside every FAIL. The
  correction matters for any published fraction: a
  `try: f(bad) / except ValueError` block is now **one assertion**, where it
  used to be classified as setup — so a candidate missing only that rule read
  as "test setup raised" with full credit. Nothing downstream aggregates
  partial credit; it is a per-row diagnostic, not a score.
- **P1.5 Record the environment, and serialise runs** [S·★★] **DONE
  2026-09-24.** Live lanes are detected and warned about, and provenance
  records host, arch, git SHA and dirtiness. Every tool —
  the speed runner included — now also records its host load at the start
  (`host_load.other_cores`, net of the lane when the lane is local) and hashes
  its source at the start. `compare()` names runs taken under different load
  (difference > 0.3 cores) and a busy host (> 1.0), and `bench_compare`
  refuses to judge a speed or timing verdict across them (exit 4,
  `CONDITIONS DIFFER`). The two points first left open are closed
  (2026-09-24):
  - **It refuses, not only warns.** When a load note fires, `bench_compare`
    withholds the speed tripwire, per-attempt time and lane-throughput
    verdicts and exits 4 (`--allow-load-difference` judges them anyway); a CPU
    lane's decode verdict its own requests' load left `NOT judged` is withheld
    too. Scores, per-case flips and batching stay judged; an NPU-lane speed
    pair is still judged up to 2.0 other cores at the start, and says so; the
    order is 1 > 4 > 3 > 0. `upgrade_check` fails on it as
    `conditions-differ`, with no override of its own. `contract --diff`
    prints the notes but is deliberately not gated (its answers are
    behaviours, not rates).
  - **A WSL2 harness pointed at a Windows lane**, where `bench_coding` and
    `bench_agent` run, reads the Windows host through interop
    (`host_load.via: "wsl-interop"`: one `powershell.exe` call, CIM counters,
    the lane's process tree subtracted); it used to record
    `other_cores: null`.

  **Still open:** from WSL2 only the run-start record reads Windows (the speed
  rows' per-request `other_cores` stay null), and NAT-mode WSL, which reaches
  the lane through the host's address, reads as a remote lane.
  `bench_tools`, `bench_coding` and `bench_agent` record no lane share, so a
  busy start withholds their timings on the NPU lane too.

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
  `benchmarks/bench_agent.py`, written up as § 1m of the GenieX page. It
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
  runs out and reports where. **Measured on the CPU lane 2026-09-25**
  ([campaign § P3.2 and P3.3](roadmap-campaign-2026-09-24.md#p32-and-p33--the-opencode-preamble-and-a-loop-that-grows)):
  behind the opencode preamble the 9B distill passes 27/34 = 79 % [63–90 %], at
  85 s per case — every case re-prefills the whole preamble, since a long
  prefix with a new tail is never cached. **Still open:** a baseline; the 9B was
  not run on the default tool set, so what the preamble costs it is unknown.
- **P3.3 Turn-count and context growth** [M·★★] **Answered, and worse than the
  question assumed.** On the 4096 model the answer is *zero* turns. On the GGUF
  lanes the limit is not the ceiling but the cost of approaching it: on GenieX
  v0.5.0 there was **no prefix cache** (the identical request twice cost 126 s
  then 122 s), so every turn re-prefilled the whole conversation at 38-61
  tok/s. *Superseded for v0.6+ (2026-09-24, `orchestrant-bench contract`):* the
  CPU lane serves an identical repeat (14 s → 0.1 s) and a conversation
  extended by one turn (3.4–3.8 s for 292 new tokens) from its cache, but not a
  long shared prefix with a different tail (17–20 s, a full re-prefill). An
  agent loop that only appends is cached; one that rewrites earlier context is
  not. **Per-turn latency measured 2026-09-25** (same campaign section): behind
  the opencode preamble the 9B takes 79.7 s for the first turn, then 12.4 s at
  ~0.3k tokens of history rising to 66.1 s at ~2.2k (6.0–12.8 s more per turn),
  and turn 9 fails with an HTTP 400 whose body the harness did not record.
  **Still open:** the 400's cause, and tokens per turn — without them the growth
  cannot be split between decode slowing with depth and longer replies.

## Phase 4 — Widen the field [M, gated on downloads]

**Every model measured so far is from one family (Qwen3/Qwen3.8), and no
code-specialised model has been tried.** That is the single largest limit on
the ranking's authority.

- **P4.1 Qwen3-8B W4A16 on the NPU** [M·★★★] **CLOSED 2026-09-24: re-measured
  on GenieX v0.7.0, it does not change the recommendation**
  ([campaign § P4.1](roadmap-campaign-2026-09-24.md#p41--qwen3-8b-on-the-npu-lane)).
  The 2026-09-04 run (816d80c0, § 1j) lost 26 of 27 tasks to truncation under a
  v0.5.0 serve default of 2048 output tokens, so it was conditioned on a launch
  flag rather than on the model. On v0.7.0, with the fixed grader, every cut
  stopped at the request's own 3000-token budget: 30 of 33 coding replies were
  cut at it with thinking on, and the bundle's 4096-token context leaves little
  room for a larger budget (578–1018 tokens more for these prompts). It decodes
  at 12.5 tok/s pooled against the 4B's 20.4, and its speed replies thought for
  12–60 s before their first answer token (the six that answered; a seventh
  reached one at 85.6 s and was cut), so it cannot match the 4B's latency
  either. Its coding ability itself stays unmeasured.
- **P4.2 A code-specialised GGUF** [M·★★] **CLOSED 2026-09-25: re-measured, not
  separable from the general model**
  ([campaign § P4.2](roadmap-campaign-2026-09-24.md#p42--qwen25-coder-7b)).
  Qwen2.5-Coder-7B `Q4_K_M` on the CPU lane, 39 tasks × 3 under the fixed
  grader: 62/117 = 53 % [39–66 %] against the Qwen3-4B-Instruct GGUF's 66/117 on
  the same lane (paired 6 tasks one way, 5 the other, p = 1.0; −3.4 points
  [−14.6, +7.8], so not shown better rather than tied), terser (115 against 175
  tokens per attempt) and never cut. § 1k's reading holds without the 2048 cap
  it was taken under. Its tool calling was not re-run.
- **P4.3 A second family on the GGUF/CPU lane** [M·★] *Reworded 2026-09-05.*
  It used to read "the other QAIRT bundles", which is a dead end on this
  chipset: no further QAIRT bundle exists for the Snapdragon X Elite —
  `Gemma-4` is `GENIEX_LLAMACPP`-only here, `Ministral-3-3B` is X2-only, and
  `Llama-3.1-8B` has no downloadable bundle. Widening the field therefore means
  GGUFs on the CPU lane, where any family runs; the shortlist is in the review
  page § "Adding more models". **MEASURED 2026-09-25**
  ([campaign § P4.3](roadmap-campaign-2026-09-24.md#p43--a-second-family-llama-32-3b-and-phi-4-mini)):
  `Llama-3.2-3B-Instruct` and `Phi-4-mini-instruct` (`Q4_K_M`, one draw each)
  are below the Qwen3-4B-Instruct GGUF on coding (12/39 and 14/39 against
  22/39 on its first draw; paired p = 0.002 and 0.008) and on tools (8/42 and
  13/42 against 41/42), and score 3/6 on the correctness probe. Llama writes
  its tool calls as text JSON, which GenieX does not parse: counted with
  `--accept-text-json` it reaches 20/42 and fails all seven restraint and
  irrelevance cases it had "passed" in the first run — for one of them the
  reply is byte-identical across the runs, so that pass was an invisible call;
  for the other six, different draws, it is inferred.
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
stale twice. `uv run --extra test pytest benchmarks/tests -q -p no:unraisableexception`
(the command .github/workflows/benchmarks.yml runs) is the answer, and it runs
offline.

## Phase 5 — The remaining backlog items [M–L]

- **P5.1 Embeddings** [M·★★] **DONE 2026-09-04 (96321e8e)** —
  `bench_embeddings.py` measures shape, speed and *meaning* (do related texts
  land closer than unrelated ones), which is the check that catches a broken
  quantisation.
- **P5.2 Energy per token** [M·★] **PARTLY → CPU rails measured (2026-09-24).**
  The `energy_proxy` counted the harness's own CPU time, never the server's.
  Run on the Windows host itself, `orchestrant-bench speed` now reads the
  Energy Meter's `CPU_CLUSTER_0/1` rails per request (joules, J/token, net of
  idle) and the lane process's own CPU-seconds; the idle baseline is taken
  before and after the requests since one 5-s window proved unstable (0.6 W
  between runs). **Still open:** the Snapdragon X exposes no NPU or GPU rail,
  so an NPU lane's joules are its CPU-side orchestration only; and nothing is
  measurable from WSL2.

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
no raw report JSON is stored for any table the hub page published (R4).
*Updated 2026-09-25:* the § 1i/§ 1n coding tables are re-derived under the fixed
grader (R2/R7/R11) from reports that are stored
([campaign § R2, R7, R11](roadmap-campaign-2026-09-24.md#r2-r7-r11--the-hubs-coding-tables-re-derived)):
§ 1i's 27 Python tasks read 15/26 = 58 % [39–74 %] with one CUT on the NPU lane
(published: 17/27), nine of the eleven failures still near misses; § 1n's
classic-set row for the QAIRT 4B reads 2/3, because `parse_version`'s
strengthened tests fail it; and the 4B `Q4_0`'s two § 1n cuts finish inside the
budget in all six draws today, as the R2 grader defect predicts. The hub page
itself is not edited here.
R3 and R8 closed on 2026-09-05: `bench_tools.evaluate()` has a direct test, and
the determinism probe and the tiered ranking rows are both called.

---

## Phase 7 — The six-lens review, applied (2026-09-24)

A review of the lab after the GenieX v0.7.0 upgrade — six lenses, every
proposal handed to a verifier told to refute it — is written up in
[`geniex-v0.7.0-cpu-npu-2026-09-24.md` § What the review corrected](geniex-v0.7.0-cpu-npu-2026-09-24.md#what-the-review-corrected).
Done in the same change: reply accounting (`answered`, `ttfa_s`), the speed
tripwire and exit-3 `NOTHING COMPARED` in `bench_compare`, the before/after
idle baseline, delivered lane throughput, spaced determinism probes and a
spacer between repeats (after two GenieX defects: `temperature: 0` read as
unset, and identical follow-ups answered from a different cache state), a
CRLF-safe `tool_sha256` (mid-run-safe in the speed runner), and UTF-8 output
on Windows. The backlog, in the order the review ranked it; all but P7.2 and
P7.3 were built on 2026-09-24:

- **P7.1 Case-clustered intervals and a power statement** [S·★★★] **DONE
  2026-09-24.** `stats.clustered_rate` (CR1, Wilson on the effective n;
  tools-r3 [71–85 %] → [66–88 %], design effect 2.6), `stats.paired_difference`
  behind the `paired diff` interval, and `stats.paired_mde`, whose minimum
  detectable drop at 80 % power follows every `unchanged` and `no regression
  detected` (26 points at 42 cases; 31 cases catch a 10-point drop 8–11 % of
  the time).
- **P7.2 The agent-sized prefill and decode curve** [S·★★★] — three runs of
  `contract --only prefix_cache --prefix-tokens N` (5000, 8000, 12000) on the
  recommended 9B-Distill, the 4B and the 2B, and the within-reply decode trace
  at 8k; no code needed. Every agent-latency number is a v0.5.0 replay, and on
  v0.7.0 the CPU lane's decode already falls from ~31 to ~10 tok/s by 2k
  tokens of depth. **MEASURED 2026-09-24/25**
  ([campaign § P7.2](roadmap-campaign-2026-09-24.md#p72--prefill-and-decode-at-agent-sizes)):
  an appended turn is cached (+292–295 tokens in 1.6–12.5 s) and a new tail
  never is (0.92–1.06× the cold time). The 9B holds 61.7–66.6 tok/s prefill
  from 4.5k to 10.7k tokens and decodes at 8.7–9.1 tok/s after 7.2k; both 4B
  `Q4_0` files decode at 3.0–3.4 tok/s there, and the thinking one's prefill
  falls to 49.2 tok/s by 10.7k. For agent contexts on the CPU lane the 9B is
  the faster model. One run per cell. **Still open:** decode past 10.7k. The
  9B on the GPU lane was measured on 2026-09-25 (P8.3).
- **P7.3 One model on both runtimes** [S·★★] — `unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_0`
  on the CPU lane, so NPU-vs-CPU stops confounding the lane with a thinking vs
  an instruct model; also a non-thinking long-context agent candidate.
  **MEASURED 2026-09-24**
  ([campaign § P7.3](roadmap-campaign-2026-09-24.md#p73--one-model-on-both-runtimes)):
  with the model fixed, the lane moves the tool score and not measurably the
  coding score. Same 42 tool cases, three draws, no system prompt: the GGUF
  passes 41/42 cases, the QAIRT bundle 33/41 (7 cases one way, none back,
  p = 0.016; +17 points [+5, +29]); coding 66/117 against 21/38 as written (4
  tasks one way, 5 the other, p = 1.0). The GGUF decodes about as fast as the
  bundle (22.2 against 20.4 tok/s pooled, one run each) at 7.9× the lane cores
  and 8.3× the CPU-side energy per token. `tool-disambiguation.md` narrows the
  gap to one case, no longer separable (P8.1, 2026-09-25). **Still open:** which part of the lane costs the tool
  cases without it, and the GGUF as an agent: at 7.2k tokens it decodes at a
  third of the 9B's rate, and was not run end to end.
- **P7.4 `bench_agent --repeats` with pass^k** [S·★★] **DONE 2026-09-24** —
  `--repeats N`, a fresh repository and opencode data/state per trial,
  `per_task`, `pass_hat_k` (the shared `stats.pass_hat_k` of P7.1, which also
  gives `bench_compare` its pass^k line for any report whose cases were drawn
  more than once) and a stored `wilson_95`. `bench_sweep` forwards its
  `--repeats` to the agent step (2026-09-24). **First live run 2026-09-25**
  ([campaign § P7.4](roadmap-campaign-2026-09-24.md#p74--bench_agent-three-trials-per-task)):
  the 9B distill on the CPU lane passes 11/15 trials = 73 %, case-clustered
  [38–92 %] (the stored `wilson_95`, [48–89 %], counts the trials as
  independent); pass^1 73 %, pass^2 60 %, pass^3 60 %. The three flat Python
  fixtures pass every trial; `fix_bash_quoting` and `fix_medium_repo` one in
  three, two of the bash failures refused for editing `check.sh`.
  `fix_cmake_link` skipped (no `make`/`ninja` in WSL).
- **P7.5 An upgrade-check command** [M·★★] **DONE 2026-09-24** —
  `benchmarks/upgrade_check.py`, a separate file rather than part of
  `bench_sweep` (which works from a candidates file). It runs contract
  (+ `--diff`), speed (+ `--max-tokens 2048`), `bench_tools` and
  `bench_coding` (WSL on Windows) per lane in a fixed order, then
  `bench_compare --dir`, into a fresh dated directory with `steps.jsonl` and
  `MANIFEST.md`. **First live run 2026-09-24**
  ([campaign § P7.5](roadmap-campaign-2026-09-24.md#p75--the-upgrade-checks-first-live-run)):
  verdict OK, eight steps in 4585.7 s on both lanes, and its directory,
  `benchmark_results/2026-09-24-upgrade-check-v070/`, is the first `--previous`
  baseline under the names `upgrade_check` writes (the v0.7.0 run's hand-made
  `v070r2-*` names were the obstacle). The NPU speed-answer reproduced the
  v0.7.0 page's r2 run to 0.04 %. **Open:** the coding step (`--wsl`) and a
  first `--previous` comparison.
- **P7.6 PowerShell tasks and a medium-repo agent fixture** [L·★★] **DONE
  2026-09-24, both halves.** PowerShell: six tasks from the repository's own
  traps, graded by a `powershell` runner (pwsh in the bash sandbox,
  PSScriptAnalyzer at error severity). Medium repo: `fix_medium_repo`, 32
  files, its bug two imports from its red tests. **Measured 2026-09-24/25**
  ([campaign § P7.6](roadmap-campaign-2026-09-24.md#p76--powershell-tasks-and-the-medium-repository)):
  no model passes `nested_module_import`, `pipeline_output` or
  `single_element_array` in any graded draw; the best is the Coder, 4/18 attempts,
  the NPU 4B passes 2/6 as written. The 9B distill fixes the medium repository
  in 1 trial of 3.
- **P7.7 A chat-quality instrument** [M·★] **BUILT 2026-09-24** —
  `bench_chat.py`: instruction following, JSON-schema adherence, multi-turn,
  and document QA at ~1k/~3.5k/~8k tokens (on the NPU lane the ~8k document is
  an OVERFLOW row). `bench_sweep --tools …,chat` runs it since 2026-09-24 (not
  in the default `--tools`). Measured on the NPU lane 2026-09-24: 27/30 = 90 %
  [74–97 %], the three `doc_8k` rows OVERFLOW. **CPU lane measured 2026-09-24**
  ([campaign § P7.7](roadmap-campaign-2026-09-24.md#p77--bench_chat-on-both-lanes)):
  the thinking 4B, 31/33 = 94 % [80–98 %] with the ~7k-token documents
  answered, in 1022 s against the NPU's 50 s — 531 s of it on the 30 cases
  both graded, where the lanes are not separable (2 cases one way, 3 the other,
  p = 1.0). The instruct GGUF, the long-document candidate, was measured on
  2026-09-25 (P8.2).

Also from the review, done the same day:

- **PROV-1 Which model file and bundle config served; which drivers** [S·★★]
  **DONE 2026-09-24.** `runtime.model_files` (GGUF and ctx-bins: size,
  first-MiB and sampled sha256; QAIRT: `genie_config.json` sha256 with its
  sampler and context, the ctx-bins, the extensions file, the bundle's QAIRT
  build) and `runtime.drivers` (NPU/GPU from the registry). Single-lane
  reports carry it too — `write_report` passes the served model to
  `collect()` — and `compare()` prints `model_files_notes()`: MODEL FILES
  CHANGED behind the same id, an edited `genie_config.json`, an edited HTP
  extensions file.
- **OPS-7 Per-lane runtimes** [S·★★] **DONE 2026-09-24.** Each `lanes` row
  carries its own `runtime`; lane-runtime files, written on the host by
  `orchestrant-bench runtimes` and named by `LLM_LANE_RUNTIMES`, give WSL2
  tools the host's view. `bench_compare` diffs them lane by lane since
  2026-09-24 (`compare_lanes.py`): provenance's runtime notes per lane, and a
  lane on one side only. When the lane set changed, no tok/s is judged: the
  aggregate sums another set, and each lane's rate is its rate beside the
  others. A lanes pair with nothing else like-for-like is then `NOTHING
  COMPARED`.
- **OPS-6 One shared results summariser, and the viewer up to date**, viewer
  half **DONE 2026-09-24.** The Reflex viewer shows answered k/n and time to
  first answer, thinking share, lane and other cores, CPU-rail J/token gross
  and net with `net_reliable`, the serving runtime and its flags, and the
  contract reports as a grid of checks against runs. `build_manifest` carries
  the runtime, endpoint, energy block and thread count per run. Building the
  page for the first time found five errors that had kept `reflex run` from
  ever serving it; a test now builds it, skipping without Reflex, and CI's
  viewer job installs Reflex so it runs there. Review fixes: the viewer reads
  its manifest path from the repository root (it never loaded when started
  from `frontend/`), the Hardware card takes a real hardware block, contract
  runs read oldest first by timestamp, a baseline-less run reads "gross
  only", and a test pins the viewer to the tracked 2026-09-23 run's published
  figures. The other half, **DONE 2026-09-24**: one speed summariser,
  `orchestrant/benchmark/speed_summary.py`. It computes Decode (tokens after
  each first one over the seconds spent decoding them), Overall (completion
  tokens over the summed request time), Prefill (prompt tokens over the summed
  TTFTs), all pooled across requests, and TTFT (a mean). `print_table`,
  `report summary` / `report table` and the viewer (through the manifest's new
  per-run `speed` block) all print it. For the tracked `v070-npu-speed` they
  had printed 18.3 (`Tokens/sec` avg, `T/s`, and the viewer's "overall"),
  25.4 (`Overall`, which counted prompt tokens) and 19.7 (`Decode only`, a
  mean of per-request rates); every one now prints Decode 19.6 and Overall
  19.3. Pooling moved the v0.7.0 page's decode column by at most 1.3 % and the
  v0.6.1 → v0.7.0 NPU loss from −13 % to −15 %, where the per-prompt median
  `bench_compare` prints is −14.7 %.

## Phase 8 — What the roadmap campaign left open (2026-09-25)

The campaign of 2026-09-24/25,
[`roadmap-campaign-2026-09-24.md`](roadmap-campaign-2026-09-24.md), took every
measurement Phases 3–7 had left open, plus three of its own: the thinking 4B's
capability on v0.7.0 (CV-4: tools not separable from the instruct build's, at
2.5× the time, ungradeable on coding at 3000 tokens), Ollama against GenieX on
byte-identical files (with eight threads decode 2.5–12 % ahead, one run each,
and 2.2–3.0× slower prefill), and the Adreno GPU lane (about twice the CPU
lane's decode for the 4B-Instruct at 7.2k tokens, one trace each; beside the
NPU lane each lost 3–5 % in one run). What it found to do next, in the order the evidence
supports:

- **P8.1 The recommended tool configuration, measured** [S·★★★] —
  `bench_tools --system benchmarks/prompts/tool-disambiguation.md` on the NPU
  bundle (one draw is its rate) and on the Qwen3-4B-Instruct GGUF on the CPU
  lane (`--repeats 3`). No run of the campaign used the prompt, and without it
  the GGUF out-calls the bundle by 7 cases to 0 (P7.3). It decides the
  recommendation for tool calling. **MEASURED 2026-09-25**
  ([campaign § P8.1](roadmap-campaign-2026-09-24.md#p81--the-recommended-tool-configuration)):
  the gap narrows from 7 cases to 1, no longer separable. The campaign's rule
  ("if the prompt closes the gap, the recommendation stands") never said what
  "closes" means; read as "no longer separable", **the recommendation
  stands.** With the prompt the bundle passes 40 of the 41 cases it answered
  (one draw) and the GGUF 42/42 (three draws): 1 case one way, none back,
  p = 1.0, +2.4 points [−2.3, +7.2] for the GGUF. The prompt fixed all eight
  of the bundle's P7.3 misses and cost it one case (8 to 1, p = 0.039). Not
  separable is not equal: the point estimate still favours the GGUF and the
  interval allows it 7 points better. The bundle's one errored case, a 400 on
  the suite's longest request, was unexplained (the run predates the fix that
  keeps its body); it errored in 10 of the 12 draws of that case tracked since
  2026-09-23, and counted as a miss the split is 2 to 0, +4.8 points
  [−1.8, +11.3]. **Explained 2026-09-25** by the gateway's acceptance, whose
  runs keep the body: `context_length_exceeded`, 4353 prompt tokens without
  the prompt, against the bundle's 4096
  ([§ Stage A](gateway-acceptance-2026-09-25.md#stage-a--transparency)).
- **P8.2 `bench_chat` on the instruct GGUF** [S·★★] — the long-document half of
  chat: the NPU refuses a ~7k-token document, and the only GGUF measured is the
  thinking 4B, about 10× slower than the NPU's instruct 4B over the 30 cases
  both answered (531 against 50 s; the suite's 20× includes the three documents
  the NPU refused). **MEASURED 2026-09-25**
  ([campaign § P8.2](roadmap-campaign-2026-09-24.md#p82--bench_chat-on-the-instruct-gguf)):
  32/33 = 97 % [85–99 %], the three ~7k-token documents answered; not
  separable from the bundle where both answer (2 cases to 0, p = 0.5) or from
  the thinking 4B (2 to 1, p = 1.0), and the suite in 0.57× the thinking 4B's
  time on a busier host, mostly because it writes no thinking tokens.
  Long-document chat goes to the instruct GGUF.
- **P8.3 The 9B on the GPU lane** [S·★★] — the depth trace and the prefix
  cache. The GPU lane about doubled the 4B's decode at 7.2k tokens (one trace
  each) and cost the NPU lane 3.4 % (one run); if it does the same for the 9B,
  the agent has a lane that runs beside the NPU. **MEASURED 2026-09-25**
  ([campaign § P8.3](roadmap-campaign-2026-09-24.md#p83--the-9b-on-the-gpu-lane)):
  **it does not.** After 7.2k tokens the GPU lane decodes the 9B at 4.7 tok/s,
  0.52–0.54× the CPU lane, and prefills it 16–26 % slower (45.9–51.9 against
  61.7–66.6 tok/s cold); one trace each, on different days, and one run per
  size. The agent stays on the CPU lane. **Open:** why — the 4B files are
  `Q4_0`, the 9B `Q4_K_M`.
- **P8.4 The lab defects the campaign found** [S·★★] — **seven DONE
  2026-09-25**:
  - the correctness probe's capability items failed a healthy runtime serving
    an instruct model. Each item now has a kind
    (`orchestrant/benchmark/correctness.py`), and only the integrity items
    decide the verdict, the `--correctness-only` exit code, `upgrade_check`'s
    speed step, `bench_sweep`'s gate and the viewer's banner; capability misses
    are printed and recorded apart. Four integrity items were added, unasked of
    any model when they went in. **Live-checked 2026-09-25:** all ten campaign
    models answer all six integrity items and read `OK`
    (`benchmark_results/2026-09-25-probe-kinds/`).
  - a reply that arrives in one burst got a per-row decode rate of up to
    26,712 tok/s. A speed row records its window as `decode_s`, and a window
    under 50 ms (`answers.MIN_DECODE_WINDOW_S`) stores a null
    `decode_tok_per_sec` with a `decode_rate_note`; `bench_compare` reads an
    older report's window back and pairs no rate under the floor. A partial
    burst (the t8 run's 44-token reply at 137.9 tok/s) still keeps its rate.
  - an HTTP 400 was recorded without its body, and the turn-growth loop
    answered only a turn's first call. `bench_tools --turn-growth` records a
    failed turn's `http_status` and `response_body`, and the case suite's
    errored rows (the NPU's long-result case, three draws) record them too;
    the loop answers every call, one tool message per `tool_call_id`, and
    records `tool_call_count`. Why turn 9 failed is still unknown: it has not
    been re-run.
  - a cut reply scored 0.0 thinking where the template, not the reply, opened
    `<think>`. `answers.thinking_share` now records such a reply as null with a
    `thinking_share_note`, in speed and `bench_coding` rows (`think=  ?%`). A
    finished reply with no marker keeps 0.0. The runner's summary and the
    viewer read an older report's cut 0.0 as unknown, and count unknown shares
    apart.
  - a tool call written as text passed restraint cases. Restraint, irrelevance
    and the follow-up cases now read the text with `--accept-text-json`'s
    parser whatever the flag, and fail such a call, saying so on the row. The
    shim joins every case-suite run's `tool_files`.
  - the chain logged step names, and the depth traces came from a script that
    was never stored. Every report's provenance now records its `argv`
    (credentials redacted), and the trace is `orchestrant-bench depth`: the
    script's prompt and windows in the shared envelope, with host load read
    over the rest before the measured request.
  - the contract's fixed 600 s timed out Ollama's 8000-token prefix. `contract
    --timeout` sets every request's timeout; the prefix-cache and overflow
    requests get at least their prompt's tokens / 6 s (about half the slowest
    cold prefill measured), recorded in the report's config. An explicit
    `--timeout` raises that floor and cannot lower it.

  Open, and needing a lane: why turn 9 failed (re-run `--turn-growth` on the
  9B), and the 8000-token prefix on Ollama under the longer timeout. Open by
  design: the thinking-share rule is per row, so the cut rows of instruct
  runs — 37 in 15 tracked reports — read unknown too, though their 0.0 was
  probably true; a per-run rule (a template that opens `<think>` leaves
  `</think>` in every finished reply) could give them back.
- **P8.5 A baseline for P3.2** [S·★] — the 9B distill on the default tool set,
  so its 27/34 behind the opencode preamble has something to be compared with.
  **MEASURED 2026-09-25**
  ([campaign § P8.5](roadmap-campaign-2026-09-24.md#p85--a-baseline-for-p32)):
  on the same 34 cases the 9B passes 33 without the preamble, 7 cases to 1
  (sign test p = 0.070; paired difference −17.6 points [−33.1, −2.2]) — the
  opencode condition probably costs it tool accuracy, suggested and not shown.
  It is more than the preamble: `--tools opencode` also swaps in its own ten
  approximated schemas, so the preamble's share is not isolated. On all
  42 default cases, 38/42 = 90 % [78–96 %]; its three misses beyond the 34 are
  booleans sent as strings.
- **P8.6 The upgrade check's remaining steps** [S·★] — its coding step
  (`--wsl`), and one tools repeat for a lane whose repeats are byte-identical.

## Phase 9 — Serving what the lab chose (2026-09-25)

What the lab chooses now has somewhere to run: ANTfrastructure's APISIX
gateway ([the hub's llm-stack README](../../third_party/ANTfrastructure/linux/llm-stack/README.md#gateway)).
What it does to a request, and how the lab measures through it:
[`benchmarks/README.md`](../README.md#through-the-gateway-lab--backends).

- **P9.1 The gateway, the lab its only client** [M·★★★] — **ACCEPTED
  2026-09-25** on the NPU and GPU lanes
  ([`gateway-acceptance-2026-09-25.md`](gateway-acceptance-2026-09-25.md)).
  - The gateway is transparent. On `raw-npu` and `raw-gpu` no contract answer
    changed. All 42 tool cases got the direct verdict and 41 byte-identical
    replies. The NPU paid a median 10 ms to first token, and decode is
    unchanged.
  - `lab-chat` is not separable from P8.1's configuration (41 ties). It
    answers the NPU's refused tool case and the ~7k-token documents on the
    GPU.
  - Not yet run:
    - the overflow fallback on a real lane;
    - `bench_chat` and `speed --correctness-only` on `raw-*`;
    - the CPU lane, `lab-chat-long` and `lab-agent`.
- **P9.2 The size rule** [S·★★] — **owner's decision.**
  - Now: `ceil(bytes / 3.0) + max_tokens` against 3900. It also sends the
    ~3.1k-token documents to the GPU: 45.5 s against 3.4–4.1 s on the NPU.
  - Measured: tool JSON runs 2.75–2.88 bytes a token and prose 4.20, and the
    NPU does not reserve `max_tokens`.
  - The option: loosen the rule (for example 4.2 and no `max_tokens` term)
    and let the overflow fallback catch what slips through.
  - Before choosing, run P9.1's overflow row live, and find out what the NPU
    does with a reply that runs past its context.
- **P9.3 The lab's measurements behind the gateway** [S·★] — three
  measurements do not see the lane when the lab goes through the gateway.
  `orchestrant-bench lanes` sends no key. `host_load` and the speed rows' lane
  CPU look at the gateway's port, so every gateway report records
  `other_cores: null`. The speed runner also has no `--label`, so bench_compare
  cannot pair its gateway runs with direct ones.
- **P9.4 A chat front end** [M·★★] — Open WebUI on the `webui` key (P2 of the
  serving plan), after P9.2.
- **P9.5 Keep it running** [S·★★] — WSL stops a distro soon after its last
  session ends, and it took the gateway down during the acceptance. A
  supervisor that holds the distro and restarts the gateway, and a
  `hold`/`release` for lab windows that serving must not disturb.
  - **Seen the same evening, idle, cause not established.** An hour after the
    acceptance, with no traffic, the gateway container restarted by itself,
    twice within four minutes (restart count 15 over the day, WSL restarts
    included).
  - Its shim and log forwarder used about a core each, and the Glances
    container's shim 1.3 cores. WSL logged RCU stalls, and the rootless
    containerd stopped answering, even `nerdctl ps`.
  - `wsl --shutdown` recovered it. The acceptance's own windows are
    unaffected: each step's log matches its report.
  - A supervisor has to notice this state, not only a stopped distro.

---

## Suggested order

1. **P2.1 + P2.2** — the tripwire. Everything already measured becomes
   defensible against future drift, and it is a day's work.
2. **P1.1** — stop publishing fractions that the sample cannot support.
3. ~~**P4.1**~~ — measured 2026-09-04 under a 2048-token serve default nobody
   recorded, re-measured on v0.7.0 on 2026-09-24: closed, it does not change
   the recommendation.
4. ~~**P3.1**~~ — done 2026-09-04. It was **not** predictive: the suite ranked
   models while the binding constraints were prompt size, prefill throughput,
   and a server that silently discarded every tool call. None of the three was
   measurable through a short prompt, and the third was indistinguishable from
   "the model is bad at tools" until someone read the raw response body. Prefer
   a cheap end-to-end check *early* over a deep proxy suite — and when a
   benchmark reports zero of something, look at the wire before believing it.
5. ~~**P8.1**~~ — the one measurement the tool-calling recommendation turned
   on; measured 2026-09-25: the gap narrows to one case, not separable, and
   it stands.
6. Everything else, as the need appears.

## What would change the recommendation

**Partly overturned on 2026-09-04, and not by a model.** The answer below
stands for chat and completion. For *agent* use it is wrong: the bundle cannot
run opencode at all (§ 1m of the GenieX page). Agent work belongs on a GGUF
lane, at roughly two minutes per turn (v0.5.0; on v0.7.0, with the prefix cache,
79.7 s for the first turn behind the opencode preamble and 12–66 s for each
appended one — P3.3).

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

**Re-read 2026-09-25 against the roadmap campaign**
([campaign § The recommendation](roadmap-campaign-2026-09-24.md#the-recommendation)).
P4.1 and P4.2 are re-measured on GenieX v0.7.0 under the fixed grader, and
neither verdict is conditional any more: Qwen3-8B decodes at 0.61× the 4B's
rate and cannot be graded on coding (30 of 33 replies cut at 3000 tokens,
little room for more in 4096), and the Coder-7B is not separable from the
4B-Instruct on 39 tasks × 3. Read use by use, comparing like with like where
the campaign could:

- **Chat — unchanged** for inputs that fit the bundle's 4096-token context; a
  longer document goes to the instruct GGUF on the CPU lane (P8.2).
- **Tool calling — unchanged, with `tool-disambiguation.md`** (P8.1): with it
  the bundle and the same model's GGUF are not separable (1 case to 0,
  p = 1.0; the GGUF still +2.4 points [−2.3, +7.2]); without it the GGUF
  out-calls the bundle 7 to 0.
- **Coding — unchanged**: nothing measured separates the candidates.
- **Agent work — unchanged**: the 9B distill on the CPU lane; on the GPU lane
  it decoded at about half the CPU lane's rate at depth (P8.3, one trace each).

The evidence, intervals and costs behind each line are on the campaign page,
not repeated here.

A fourth condition joins the three above: **the GGUF build of the recommended
model calling tools better than the bundle with its prompt.** Tested on
2026-09-25 (P8.1), it was not shown to on this suite: 1 case to 0, p = 1.0,
with the interval allowing the GGUF up to 7 points better, 11 counting the
bundle's refused case
([campaign § P8](roadmap-campaign-2026-09-24.md#p8--measured-2026-09-25)).
