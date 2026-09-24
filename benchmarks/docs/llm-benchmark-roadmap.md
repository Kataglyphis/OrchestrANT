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

  **Still open:** the flag is still opt-in, and no lane has been measured with
  it yet. The spread of the recommended models is the next measurement to take.
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
  runs out and reports where. Measuring the GGUF lanes with them is the open
  half.
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
  not. Per-turn latency of a real session is the open measurement.

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
no raw report JSON is stored for any already-published table (R4); and the
§ 1i/§ 1n coding tables have not been re-derived under the fixed grader
(R2/R7/R11) — the exact commands are in the CHANGELOG entry of 2026-09-05.
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
  tokens of depth.
- **P7.3 One model on both runtimes** [S·★★] — `unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_0`
  on the CPU lane, so NPU-vs-CPU stops confounding the lane with a thinking vs
  an instruct model; also a non-thinking long-context agent candidate.
- **P7.4 `bench_agent --repeats` with pass^k** [S·★★] **DONE 2026-09-24** —
  `--repeats N`, a fresh repository and opencode data/state per trial,
  `per_task`, `pass_hat_k` (the shared `stats.pass_hat_k` of P7.1, which also
  gives `bench_compare` its pass^k line for any report whose cases were drawn
  more than once) and a stored `wilson_95`. `bench_sweep` forwards its
  `--repeats` to the agent step (2026-09-24). **Open:** no live run yet.
- **P7.5 An upgrade-check command** [M·★★] **DONE 2026-09-24** —
  `benchmarks/upgrade_check.py`, a separate file rather than part of
  `bench_sweep` (which works from a candidates file). It runs contract
  (+ `--diff`), speed (+ `--max-tokens 2048`), `bench_tools` and
  `bench_coding` (WSL on Windows) per lane in a fixed order, then
  `bench_compare --dir`, into a fresh dated directory with `steps.jsonl` and
  `MANIFEST.md`. **Open:** its first live run. The v0.7.0 run directory uses
  hand-made `v070r2-*` names, so the first `--previous` needs a baseline
  written under the new names (see the lab's README).
- **P7.6 PowerShell tasks and a medium-repo agent fixture** [L·★★] **DONE
  2026-09-24, both halves.** PowerShell: six tasks from the repository's own
  traps, graded by a `powershell` runner (pwsh in the bash sandbox,
  PSScriptAnalyzer at error severity). Medium repo: `fix_medium_repo`, 32
  files, its bug two imports from its red tests. **Open:** no model has been
  measured on either.
- **P7.7 A chat-quality instrument** [M·★] **BUILT 2026-09-24** —
  `bench_chat.py`: instruction following, JSON-schema adherence, multi-turn,
  and document QA at ~1k/~3.5k/~8k tokens (on the NPU lane the ~8k document is
  an OVERFLOW row). `bench_sweep --tools …,chat` runs it since 2026-09-24 (not
  in the default `--tools`). Measured on the NPU lane 2026-09-24: 27/30 = 90 %
  [74–97 %], the three `doc_8k` rows OVERFLOW. **Open:** the CPU lane's
  measurement, before the chat recommendation cites either.

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
