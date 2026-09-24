# LLM benchmark lab

The serving stack — the Ollama + Open WebUI compose files, the NVIDIA overlay,
the backend registry `backends.json` — lives in ANTfrastructure's
[`linux/llm-stack/`](../third_party/ANTfrastructure/linux/llm-stack/README.md);
this page documents the lab that measures it. What lives **here**: the runner,
`orchestrant.benchmark` (the `orchestrant-bench` console script — `speed`,
`lanes`, `report`, `contract`, `runtimes`; see [`docs/source/benchmark.rst`](../docs/source/benchmark.rst)),
the capability benchmarks `bench_*.py` in this directory, the NAS census
[`nas_census.py`](nas_census.py), `prompts/`, the tracked results under
`benchmark_results/` and `baselines/`, the review, the roadmap and the
document-AI page under [`docs/`](docs/), and the Reflex viewer in
[`frontend/`](../frontend).
The `bench_*.py` commands below run from `benchmarks/`; `orchestrant-bench`
runs from anywhere in the project (`uv run orchestrant-bench …`).

## Benchmarking

The lab ships an automated sweep and an interactive Reflex viewer.

### 1. Run benchmarks

```bash
# from the repo root
bash benchmarks/run_benchmarks.sh
```

This runs 5 configurations (different `num_ctx` × `max_tokens`) through a set of
short and medium prompts, measuring tokens/sec, latency, CPU, and RAM via the
Glances API. Results land in a **run-scoped** directory —
`benchmark_results/<backend>-<model>/` (override with `BENCH_OUTDIR`) — as
individual JSON files plus that run's `_manifest.json`. Per-run on purpose: the
manifest and the comparison table both glob every `*.json` beside them, and one
shared directory silently mixed two models into one table.

### Is it fast, or is it *working*? (`--correctness`)

Speed metrics cannot tell a working model from a broken one — a model emitting
fluent nonsense scores **excellent** tokens/sec. That is not hypothetical: a
GenieX i-quant kernel bug produced fast garbage that every throughput number
rated as a good run (see
[`docs/geniex-local-ai-setup.md`](../third_party/ANTfrastructure/docs/geniex-local-ai-setup.md)).

```bash
# quick health check on its own — exits non-zero if any answer is wrong
uv run orchestrant-bench speed --correctness-only

# or alongside a normal run, recorded into the result JSON
uv run orchestrant-bench speed --stream --correctness --output result.json
```

Six prompts with **verifiable** answers at `temperature=0` (arithmetic, a
capital city, letter counting, a one-step logic puzzle). The `<think>` block is
stripped before matching and matches are anchored on word boundaries, so a
discarded intermediate value cannot score a false positive.

**Truncation is reported apart from wrongness.** A reasoning model cut off
before it answers was not *wrong* — it was not *measured*. Conflating the two
makes a healthy model look degraded, and a check that cries wolf is a check
people stop reading. Exit codes reflect that:

| Exit | Verdict | Meaning |
|---|---|---|
| `0` | `OK` | every answer correct |
| `1` | `DEGRADED` / `BROKEN` | genuinely wrong answers — act on it |
| `2` | `INCONCLUSIVE` | only ran out of tokens — raise `--correctness-max-tokens` |

It is a smoke test, not a capability benchmark — but it is sharply
discriminating in practice. Measured on Qwen3-4B at `temperature=0`:

| Build | Score |
|---|---|
| `Q4_0` | 6/6 `OK` |
| `Q2_K` (2-bit) | 4/6 — both losses were reasoning items |
| `IQ3_XXS` (broken i-quant kernels) | 0/6 `BROKEN` |

The probes are deliberately cheap. An earlier version asked for `847 * 293`,
which a healthy 4B could not finish within 4000 thinking tokens — so it
reported `INCONCLUSIVE` on a perfectly good model.

### Reading the numbers: TTFT and time-to-answer

Two metrics were added because ranking by `tokens/sec` ranks models *wrongly*:

- **`ttft_s` / `prefill_tok_per_sec`** — time to first token. For an agent this
  is usually the dominant wait (13.1 s on a 2.5k-token prompt in one measured
  case) while `tokens_per_sec` looks healthy. Requires `--stream`.
- **`wall_s_to_answer` / `thinking_char_share`** — a reasoning model can be the
  fastest per token *and* the slowest to a usable answer: Qwen3-1.7B measured
  31.7 tok/s but spent ~1900 tokens thinking, giving 60.8 s to an answer, while
  a 4B-Instruct at 19.5 tok/s answered in 26.8 s. **Rank by time to answer** —
  but only over replies that *have* one. Each row records `finish_reason`,
  `answered` (answer text arrived and the budget did not cut it) and `ttfa_s`
  (the first token after any thinking); a cut row's `wall_s_to_answer` is null,
  a `<think>` that never closed counts as all thinking, and the summary prints
  `Answered k/n`. Until every row answers, raise `--max-tokens` before ranking:
  the default 256 cut 7 of 9 replies of a thinking Qwen3-4B, and the old summary
  published its time to the cap as its time to an answer.

`tokens_per_sec` divides by the whole request and therefore mixes prefill with
decode; `decode_tok_per_sec` reports decode alone.

A run's headline figures come from one summariser,
`orchestrant/benchmark/speed_summary.py`. The runner's summary, `report
summary`, `report table` and the viewer print the same number under the same
name:

- **Decode**: the tokens after each first one over the seconds spent decoding
  them.
- **Overall**: completion tokens over the summed request time (each request's
  latency, sent one after another), prefill and thinking included. Prompt
  tokens are not output.
- **Prefill**: prompt tokens over the summed TTFTs. The request's overhead is
  in it, so on the runner's short prompts it reads far below a prefill
  benchmark.
- **TTFT**: a mean.

The rates are pooled across requests, like CPU-rail J/token: a mean of
per-request rates let an 8-token reply weigh as much as a 256-token one. Every
completed request counts, cut or thinking-only included; a row with an `error`
key, even with an empty message, leaves every figure and is counted apart.

The summary also names the **busiest process** during the run. On some stacks
the process owning the serving port is not the one doing the work (GenieX
spawns a separate worker: the port owner read 11 % of 800 % while the worker
sat at 752 %), so the report says which PID actually burned the CPU.

**CPU and energy are measured over each request, when the harness shares the
lane's host.** `cpu_percent` used to be the mean of one sample taken before the
request and one after it — neither saw the inference, so a CPU lane pinning 7.5
of 8 cores read as idle. It is now the integral over the request
(`cpu_percent_method: "window"`), and the old average survives only where the
local counters describe another machine (a remote server, or the WSL VM in
front of a Windows-host lane), labelled `"before/after snapshots"`. Beside it:

| Field | What it is |
|---|---|
| `lane_cpu_s`, `lane_cores` | CPU-seconds of the process tree **listening on the lane's port**, over the request — the "1.65 cores" of the GenieX page, measured instead of quoted |
| `other_cores` | everything else the machine did during the request (busy cores minus the lane's). A llama.cpp CPU lane loses throughput to every one of them — 0.9 other cores took one from about 30 to 14 tok/s — so read a CPU-lane number with it; the summary prints it as `Other load` |
| `cpu_rail_energy_j`, `cpu_rail_j_per_token` | joules on the **CPU-cluster rails** of the Windows Energy Meter (EMI), interpolated onto the request's exact bounds (the meter publishes once a second) |
| `cpu_rail_net_*`, `cpu_rail_idle_w` | the same, net of an idle baseline with the model loaded, taken after the warmup **and** after the last request (`--idle-seconds` each, default 5); every row is netted against their mean, and `energy.idle_drift_w` / `net_reliable` say when the two disagree by more than 0.2 W — one 5-s window once moved 0.6 W between runs and turned +21 % into a published "+70 %". Read gross when in doubt |

The Snapdragon X exposes `CPU_CLUSTER_0` and `CPU_CLUSTER_1` rails and no NPU or
GPU rail, so an NPU lane's joules are **its CPU-side orchestration only** — the
report's `energy.scope` says so on every run. Nothing here is measurable from
WSL2, where the Windows-side worker and rails are invisible; the fields are
simply absent there and the report says why. `--no-energy` turns the meter off.

### Concurrency: does one server batch? do lanes add up? (`orchestrant-bench lanes`)

```bash
# Does ONE server overlap two concurrent requests?
uv run orchestrant-bench lanes --batching --endpoint http://127.0.0.1:11434 --model llama3

# Do SEVERAL servers add up, or fight each other?
uv run orchestrant-bench lanes --lanes geniex-npu geniex-cpu --output lanes.json
```

`--batching` fires two simultaneous requests at one endpoint. If the second
one's first token arrives only after the first has finished, the server
serialises — **more throughput then needs more servers, not more clients**.
Measured on GenieX twice, on different prompts and both `SERIALISED`: the
second request waited out the first exactly (27.6 s in one run, 74.27 s against
a 74.10 s first request in a longer one).

`--lanes` measures each endpoint alone, then all of them at once, with a
fresh prompt per phase, and reports the per-lane change plus what the machine
**delivered** (tokens over the joint wall) beside the sum of per-lane rates —
the sum overstates it whenever one lane finishes early. The NPU lane shrugs
off ordinary background load, but not a second lane: since GenieX v0.6 it loses
about half its decode rate or more beside the llama.cpp CPU lane, and the pair
delivers 0.54–0.78× of the best single lane
([`docs/geniex-v0.7.0-cpu-npu-2026-09-24.md`](docs/geniex-v0.7.0-cpu-npu-2026-09-24.md)
§ Concurrency). The v0.5.0 matrix lives in
[`docs/geniex-local-ai-setup.md`](../third_party/ANTfrastructure/docs/geniex-local-ai-setup.md) § 2.
Aggregate throughput only appears if you really have that many concurrent
requests — one agent waiting for one answer still sees a single lane's speed.

`--output` writes the shared report envelope (`benchmark: bench_lanes`), so
`bench_report` labels the run correctly and `bench_compare` reads it as
throughput. None of its rows carries `passed`/`total`: a lane result is not a
score, and for a while the manifest rendered it as `/ = 0 %`.

### Did the runtime change under you? (`orchestrant-bench contract`)

Every GenieX release has moved something this lab depended on: v0.6 dropped the
2048-token cap, honoured `max_tokens`, parsed tool calls and added a prefix
cache; v0.7 added host-side stop sequences for QAIRT bundles, a per-request
`power_mode`, and a system prompt read from bundle metadata. Each was found by
hand, usually after it had distorted a number. The contract probe asks those
questions — the old output cap and the bundle's system prompt included — in a
few minutes (most of it one 3000-token reply, several minutes on a CPU lane)
and writes a report two runtimes can be diffed on:

```bash
uv run orchestrant-bench contract --backend geniex-npu --overflow-tokens 6000 --output npu.json
uv run orchestrant-bench contract --backend geniex-cpu --output cpu.json
uv run orchestrant-bench contract --diff before.json after.json   # exit 1 if any answer moved
```

Every check answers `yes`, `no`, `inconclusive`, `error` or `skipped` with its
evidence — a neutral fact, not a pass or a fail: `max_tokens` honoured, usage
reported (and usable in a stream: `prompt_tokens` 0 is not), identical text for
two T=0 requests, for two near-greedy ones (`temperature` 0.01, `top_k` 1), and
for two with the same `seed`; whether T=0 **is** greedy decoding (GenieX v0.7.0
reads it as "unset"), whether an identical request sent twice **in a row** gets
the same reply (on GenieX it does not: llama.cpp answers the repeat from stale
logits, QAIRT from a different dialog state), a thinking block emitted (inline
or in `reasoning_content`), chat and `/v1/completions` stop sequences
(inconclusive when no stop string came up in the budget), tool calls parsed,
how an over-long prompt is refused (`--overflow-tokens`, off by default because
a 16k GGUF lane would prefill for minutes; a 5xx is not a clean refusal), and
whether `power_mode` is **validated** rather than merely tolerated (a nonsense
value must be refused — "yes" says nothing about an effect, and the check puts
the lane back as launched, because each mode change reloads the model).

Three checks cover what the probe used to admit it missed:

- **`output_cap`** asks for 3000 tokens on a reply that runs longer. It answers
  `yes` when the server stops short: with `finish_reason: length`, or at
  exactly 256/512/1024/2048 tokens reported as a normal finish;
  `stopped_at_tokens` says where. GenieX v0.5.0's unrecorded 2048-token default
  is the case it exists for. A reply that finishes on its own is inconclusive.
- **`bundle_system_prompt`** looks for a default system prompt in usage, not
  in what the model says. An explicit system message replaces a default, so
  comparing its cost with a doubled copy separates a system turn's framing
  from a default it displaced (`hidden_tokens`: negative without a default,
  the default's size with one). The model's own quote of its instructions is
  kept as `self_report` and never decides the answer. A default sent in
  addition to an explicit message is invisible to it and reads as `no`, so
  `no` means no default that a system message displaces. A throwaway request
  with no system turn goes first, so an `--only bundle_system_prompt` run
  measures the same as a full probe.
- **`response_format_json_schema`** sends a schema with a prompt that never
  mentions JSON (a 1024-token budget, so a thinking lane can finish). It
  answers `yes` only when the reply is exactly the schema's object, and
  `outcome` says what a `no` was: `ignored`, `json_only`, `fenced` or
  `refused` (a 4xx). `--diff` compares answers, so a move between ignored and
  refused shows only in `outcome`.

Every determinism check sends an unrelated request before each draw: an
identical follow-up would measure the cache path above, not the sampler.

The prefix cache is three answers from one measurement, because a cache can
serve one agent pattern and not another: an identical request, a conversation
**extended** by one turn (the agent loop), and a long prefix **forked** with a
different tail (many questions over one preamble — which is how `bench_tools
--tools opencode` sends its cases). The cold request also yields the prefill
rate at ~2k tokens, the size an agent waits on and the speed runner's short
prompts never reach. Run it after every runtime upgrade, before trusting
anything else.

### Which model writes code that actually runs? (`bench_coding.py`)

The correctness probe answers "is this model working at all". It cannot answer
"is this model good at code" — a model can recite Canberra and still emit a
broken function.

```bash
python3 bench_coding.py --backend geniex-npu
python3 bench_coding.py --compare candidates.json --repeats 3 --output coding.json
```

Each task pins an **exact required signature**; the reply's code is extracted,
executed in a temporary directory as a separate process with a hard timeout,
and checked against hidden tests chosen to catch plausible-but-wrong answers —
a merge that silently drops duplicates, a bracket matcher that counts instead
of nesting. Nothing is judged by eye.

**Ranking is by pass rate over *measured* attempts, then by how many attempts
were measured, then by wall time.** Rate rather than raw count, because
excluded transport errors were costing rank; coverage as the tie-break, so one
lucky surviving attempt cannot outrank twenty clean ones; and the wall is now
the wall of the **measured** attempts only (see the exclusion table below).

A `<think>` block is stripped before extraction, so a draft the model itself
discarded is never graded in place of its real answer.

**Tasks, kinds and languages.** Every task declares a `lang`
(`python`/`bash`/`cmake`/`dockerfile`/`powershell`) and a `kind`
(`spec-transcription`/
`from-examples`/`bug-fix`/`design`), with **no default** — a task that forgets
one fails its own test, because a silent default makes a whole set's per-kind
rate quietly wrong. The run prints, and the report records, a pass rate per
lang and per kind beside the aggregate: 27/27 Python next to 0/4 bash is a
different finding from 27/31.

```bash
# current inventory, derived — never re-typed here
python3 -c "import bench_coding as b, collections; t=b.TASKS+b.NOVEL_TASKS+b.EXTENDED_TASKS+b.LANGUAGE_TASKS; \
print(len(t), collections.Counter(x['lang'] for x in t), collections.Counter(x['kind'] for x in t))"
```

`--task-set` selects `classic` (3 textbook tasks, recall-prone and far too
small to prove a drop), `novel` (3 tasks built from formats invented in *this*
repository, which cannot have been memorised), `extended` (the 21 authored
tasks, sized so a regression is provable), `languages` (the bash/CMake/
Dockerfile/PowerShell tasks) or **`all`, which is now the default**. Run
`classic` against `novel` and compare — a model much stronger on the first is
recalling rather than reasoning. Measured: the QAIRT 4B-Instruct scores 3/3 classic and 2/3
novel.

**Non-Python tasks are executed, not eyeballed.** bash runs under
`bash -euo pipefail` with an assertion prelude and is additionally linted with
`shellcheck -S error`; CMake runs under `cmake -P`; a Dockerfile is linted with
`hadolint --failure-threshold error` and then parsed into its instruction list
and asserted over. PowerShell runs under `pwsh -NoProfile -NonInteractive
-File`: the harness dot-sources the candidate's own file and runs the checks
one top-level statement at a time. A check that throws fails and the next one
still runs; a setup statement that throws stops the checks, like `set -e`. The
helpers are defined after the candidate loads, so its own `assert_eq` cannot
stand in for them, and the harness then restores PowerShell's defaults
(`$ErrorActionPreference = 'Continue'`, strict mode off), whatever the
candidate set at its top level; a check that sets strict mode itself keeps it
for the checks after it. A `break` or `continue` with no loop of its own
unwinds to the harness's loop, and an `exit` ends the harness: when the
candidate's code does either, the row says so (`a break/continue/exit in the
solution, while checking`) instead of reading as a silent exit 0. The
candidate is also linted with PSScriptAnalyzer at `Error`/`ParseError`
severity where the module is installed; where it is not, every row says
`[PSScriptAnalyzer SKIPPED: module not installed]`. **A language whose tool is
not installed produces a visible
`SKIP`, never a pass** — the row leaves the rate, the interval, the wall and
the rank, the reason is printed, and `skipped` is recorded per row and per
report. An absent linter does not skip the task (bash and the structural checks
still grade it) but appends `[shellcheck SKIPPED: not on PATH]` to the row's
`detail` on **every** verdict, passing or failing, so a host without it says so
rather than reading clean. The row also carries it as `linter`, and
`config.grader_selfcheck.tools` records once per run which linters were on PATH.

**Results are reported in more than two states.** Which ones count:

| State | Printed | In the rate, interval and rank? |
|---|---|---|
| pass / wrong answer | `PASS` / `FAIL` | **yes** — this is the measurement |
| reply truncated mid-answer | `CUT ` | no — unmeasured. `finish_reason: "length"`, then `usage.completion_tokens`, then the streamed delta count, always against *the request's own budget* |
| abandoned at `--deadline` | `CUT ` (detail `GAVE UP …`) | no — the attempt never finished |
| prompt did not fit the context | `OVERFLOW` | no — a 4xx naming the context. The model never saw the task |
| transport or in-stream error | `ERROR` | no — including a `{"error": …}` payload or a bare `error:` SSE line, which used to be graded "no code found" |
| the tool that grades that language is absent | `SKIP` | no — nobody graded it |

An unclosed final code fence counts as a cut when the server gave no finish
reason. With a real one (`stop`), it counts as a cut only for Python code that
does not compile (a mid-token cut). PowerShell, bash, CMake and Dockerfile have
no parser here, so such a reply is graded on its code: a wrong one is `FAIL`,
not `CUT`.

Excluded attempts are listed, counted separately in the report
(`truncated`, `abandoned`, `overflow`, `errored`, `skipped`) and their seconds
are reported as **`unmeasured_wall_s`** — a 1800 s abandoned attempt used to
decide the very rank tie-break it was excluded from.

**`--repeats N` — because a single run measures one draw, not the model.**
The llama.cpp lanes sample at `temperature=0` — GenieX reads 0 as "unset" and
runs its default sampler (`top_k: 1` in a backend's `request_extra` gives
greedy decoding; `temperature: 0.01` is only a low temperature — on the CPU
lane two of three such replies still differed): five requests to one 2B produced five
different answers, four passing the same task and one failing it. That model
scored 2/3 in one sweep and 0/3 in the next; over 9 attempts its real rate is
44 %. The QAIRT bundles sample too, from a fixed seed (`temp 0.8, top-k 40,
top-p 0.95, seed 42`, re-seeded per request), so after any other request the
same prompt gets the same reply. What made repeats differ there was the
**identical follow-up**: GenieX answers a request sent directly after an
identical one along a cache path that changes the reply, on both lanes
([`docs/geniex-v0.7.0-cpu-npu-2026-09-24.md`](docs/geniex-v0.7.0-cpu-npu-2026-09-24.md)).
Both tools therefore send a throwaway request between repeats of one case
(`config.repeat_spacer`); with it, NPU repeats are identical and the effective
sample is the case count, and CPU-lane repeats are real draws. When every
repeat of every case agrees on pass/fail, `effective_n` is the case count too —
agreeing draws are one observation of that case's rate. (GenieX v0.6.1 does
honour `max_tokens` — measured 2026-09-05,
`third_party/ANTfrastructure/docs/geniex-local-ai-setup.md` § 1n.)

**`--context-tokens N` — because ~40-token prompts are not what an agent
sends.** Prepends real repository source before each task. Prefill and any hard
context ceiling only appear under a realistic prompt: on one NPU bundle accuracy
fell from 3/3 to 2/3 once 1000 tokens of context were added, and past its
4096-token limit the lane now answers HTTP 400 `context_length_exceeded`, which
is where the `OVERFLOW` state comes from.

**`--prompt-variants` — because one wording of a task is also only one draw.**
Asks each task in its paraphrases as well. The three classic and three novel
tasks have two each; `strings_normalize_tag`,
`lists_chunk_with_remainder_policy`, `stateful_classify_ticket` and
`bash_split_comma_list` have one each. A paraphrase keeps the signature line,
every rule and every worked example, and
[`tests/test_bench_coding_variants.py`](tests/test_bench_coding_variants.py)
checks each paraphrase against its original literal by literal and runs the
worked examples against the reference. So a task that passes in one phrasing
and fails in another was decided by the wording — or by the draw: with one
draw per phrasing, a sampling lane shows spread from an unlucky draw alone (a
third of two-way cases at 80 % per draw, with no wording effect). The run says
so, and `--repeats 3` shrinks that share.

The run prints that **spread**, and the report stores it:

- `variant_spread` out of `variant_case_count`: the tasks that disagreed, out
  of the tasks asked more than one way. `variant_spread_rate` is the ratio and
  `variant_spread_cases` names them. A task counts as spread when one phrasing
  never passes while another does; on a sampling lane, a failed draw on every
  phrasing does not count.
- `by_variant`: the score of each phrasing, counted over those tasks only. v0
  is the prompt as written.
- `variant_outcomes`: for each task, `[passes, attempts]` per phrasing and
  whether they disagreed.

Paraphrases are not extra tasks. `effective_n` counts a task once — on a
sampling lane with `--repeats`, once per round, where a round asks every
phrasing once. `effective_k` observes each task through its prompt as written
(v0, or the first phrasing measured when v0 was not), which is the observation
a run without the flag makes, so the two intervals compare like for like.
Requiring every phrasing to pass would charge a sampling lane for its noise
once per paraphrase: with no wording effect at all, three phrasings at 80 % per
draw scored 51 %. The wording is what `variant_spread` and `by_variant` report.
When a control's suspect cases leave the score, `mark_suspect_cases()`
recounts `effective_n`/`effective_k` and the spread by the same rule. The rule
lives in `bench_variants.py`, shared by `bench_tools` and `bench_coding`, and
under the flag that file is part of the report's `tool_sha256`.
`config.prompt_variants` records the flag, so a comparison against a run
without it prints `! config.prompt_variants changed`.

**`--deadline N` (default 1800 s) — because `urlopen`'s timeout is per socket
read.** A model that keeps emitting tokens never trips it; one blocked a sweep
for over an hour. The deadline bounds the whole attempt, and the clock starts
before the request is sent, so a slow prefill counts against it.

**`--keep-output`** stores the generated code in the report. Use it for any
number you intend to publish: a stored reply is the only way a past PASS can be
re-audited.

**Partial credit.** Beside `FAIL` the run prints how many of the task's hidden
assertions passed — 6-of-7 is a different engineering problem from 0-of-7. A
`try: f(bad); raise AssertionError / except ValueError: pass` block counts as
**one assertion**, not as test setup; it used to be classified as setup, which
reported a candidate that missed only the ValueError rule as "test setup
raised" with full credit.

**Constraints stated in a prompt are enforced.** The merge task says "do not
use `sorted()`" and for a while nothing checked it — `return sorted(a + b)`
passed every assertion. Each task may declare `forbidden` tokens, checked on
the syntax tree: a name, an attribute, an `import … as` alias, or a string
handed to `getattr`/`__builtins__[…]` counts, while a docstring that merely
*mentions* `sorted()` does not, and a candidate that defines its own `sort` is
not punished for the name. Tasks whose prompt says "standard library only"
declare `stdlib_only` and any import outside `sys.stdlib_module_names` fails
them.

> **This executes model-generated code.** Each candidate runs in a temp dir as
> a subprocess in its own session, under `unshare -rn` where the kernel allows
> it, with a scrubbed environment, a hard timeout, a process-group kill and
> RLIMITs (address space 1 GiB, file size 8 MiB, 64 processes, 1 MiB captured
> per stream). Do not point it at an untrusted endpoint.
>
> pwsh gets an 8 GiB address-space ceiling and a 1 GiB managed-heap cap
> (`DOTNET_GCHeapHardLimit`) instead of the 1 GiB RLIMIT_AS, and runs with
> .NET's W^X off: .NET reserves about 62 GiB of address space and cannot start
> under 1 GiB, and its W^X double mapping creates a file larger than the 8 MiB
> file-size ceiling. With the heap cap and W^X off, pwsh started 0/8 times at
> 3 GiB, 33/33 at 4 GiB, 23/25 at 5 GiB (two startup OutOfMemoryExceptions)
> and 68/68 at both 6 and 8 GiB, so 8 GiB is outside the flaky band. An
> allocation loop still stops at the heap cap, as a catchable
> OutOfMemoryException at about 960 MB. Measured on pwsh 7.6.6, aarch64 WSL2,
> 2026-09-24.

**The grader checks itself before it checks a model.** Every task carries a
`reference` solution, and each one is run through the *real* grading path at
the start of every invocation; a failure aborts the run with
`GRADER SELF-CHECK FAILED` naming the task, before any endpoint is contacted.
Without it, a host where the sandbox does not work scores every model
identically with the same stderr — indistinguishable from "the models are
bad", which this suite has already been fooled by once. The result, the
sandbox limits, which of `bash`/`shellcheck`/`cmake`/`hadolint`/`pwsh` were
found and whether the PSScriptAnalyzer module is installed are recorded in the
report as `grader_selfcheck` (the pwsh ceilings as `rlimits.pwsh_as_bytes` /
`pwsh_gc_heap_bytes`).

### Can it call tools at all? (`bench_tools.py`)

An agent lives on tool calls. A model that writes flawless code but cannot emit
a valid one never gets to read a file, run a test or apply a patch — so this is
worth checking *before* ranking anyone on code quality.

```bash
python3 bench_tools.py --backend geniex-npu
python3 bench_tools.py --compare candidates.json --repeats 3 --prompt-variants
python3 bench_tools.py --backend geniex-cpu --tools opencode
```

The case inventory lives in `bench_tools.py` (`TOOLS`, `CASES`,
`MULTI_CASES`) and is deliberately not duplicated here — an earlier version of
this section enumerated the cases and was wrong within a day of the suite
growing, then stayed wrong for weeks. What the cases *cover*:

- **calling at all** rather than describing the call in prose;
- **near-neighbour selection** — with only distinct tools a model can succeed
  by elimination, which is not what agents fail at. The paraphrases of these
  cases deliberately share fewer than two content words with the tool
  description they must select, so the case measures selection and not reading;
- **argument extraction**, including values with spaces and symbols;
- **typed arguments** — an integer, a boolean, an enum member and an array,
  each checked against the type the schema declares. `True` is not `1`, `"40"`
  is not `40`, and an enum value outside the declared set fails;
- **parallel calls** — cases that need exactly N calls at once, matched in any
  order, including two different tools in one turn;
- **restraint** — cases that expect **no** tool call, and that also require a
  real answer: an endpoint returning HTTP 200 with an empty body used to score
  as "correctly answered without a tool";
- **irrelevance** — questions that never mention a tool at all, which is the
  harder half of restraint (the other cases say "do not use any tool", which
  measures instruction-following);
- **multi-turn** — is a returned tool result actually *used*; after a tool
  *error* does the model admit the failure rather than inventing file contents;
  can it find one failure in ~2k tokens of output; does it survive five turns
  of history; and does it stop repeating a call that has already failed twice?

```bash
# current counts, derived
python3 -c "import bench_tools as b; print(len(b.CASES), 'single-turn +', len(b.MULTI_CASES), 'multi-turn')"
```

Grading is strict on tool names, on argument types and on required values, and
lenient only where the model cannot be blamed: **one** leading `./` and **one**
trailing `/`, and only on path-like parameters. (It used to strip every leading
and trailing `.` and `/` from every string argument, so `done.`, `.done` and
`done/` all passed for `content="done"`.) Arguments are accepted as a JSON
string or a dict, since servers differ.

Flags worth knowing:

| Flag | What it changes |
|---|---|
| `--tools opencode` | Advertise the ten-schema preamble a real agent sends (~5k tokens) instead of the eight terse ones (~0.6k). Cases with no single defensible answer under it are **skipped and listed**. `tools_opencode.py` is an authored approximation, and says so in the report — it is not a wire capture |
| `--prompt-variants` | Also ask each case in its paraphrases, and report the spread. A score that swings on wording is fragile in a way one phrasing hides. The report fields, the `effective_n` rule and the one-draw caveat are `bench_coding`'s — see its [`--prompt-variants`](#which-model-writes-code-that-actually-runs-bench_codingpy) paragraph |
| `--accept-text-json` | Count a call the model wrote as prose. Measures what an agent-side fallback parser would recover; threaded into the multi-turn graders too, so a follow-up written as text is neither a false PASS nor a false FAIL |
| `--context-tokens N` | Prepend repository source to every single-turn case. Long context and tool calling were only ever measured apart; together is what an agent turn is. The padding excludes `bench_tools.py` itself — it used to prepend the case table, answers included |
| `--turn-growth` | Instead of the case suite, grow an agent loop turn by turn until the context runs out, and report where |
| `--system FILE` | Prepend a system prompt to every case. Agents that cannot override a runtime's built-in tool *descriptions* can still disambiguate this way — measure whether it helps before shipping it |

Determinism here is decided on the **output**, per `(case, variant)`: identical
message hashes across the measured repeats. `repeats_agreed` is the weaker
"same verdict, different text" signal and is reported separately, because a
sampling endpoint that fails every draw also produces it.

### Does it do what a chat user asked? (`bench_chat.py`)

```bash
python3 bench_chat.py --backend geniex-npu
python3 bench_chat.py --compare candidates.json --repeats 3 --output chat.json
python3 bench_chat.py --backend geniex-npu --category instruction --category json
```

Speed plus six trivia probes cannot say whether a model keeps to "at most 20
words", answers in JSON a program can parse, remembers turn 1, or finds a fact
3,000 tokens into a document. `bench_chat` asks 33 cases. Code grades every
one; no model does.

| Category | Cases | What is checked |
|---|---|---|
| `instruction` | 13 | word, sentence, paragraph and bullet counts (an abbreviation such as e.g. does not end a sentence); an answer in German and a translation into it; no Markdown; capitals only; a planet followed by a fixed sign-off; a banned word |
| `json` | 8 | the whole reply parses, matches a JSON schema (a small built-in validator for the keywords used; any other keyword raises), and has the values the prompt dictates; a fenced reply fails and says why |
| `multiturn` | 3 | a fact and a rule from turn 1, a correction in turn 2 |
| `doc_1k`, `doc_3.5k`, `doc_8k` | 3 each | a seeded document with three facts at 15/50/85 % depth, each with a decoy; the answer must name the value and not the decoy |

- **Document sizes.** The ~3.5k document is built to fit the NPU lane's
  4096-token context; the ~8k one does not, and a lane that refuses it records
  OVERFLOW rows, not graded, as in `bench_coding`. Sizes come from a
  tokenizer-free estimate (`config.doc_tokens_estimated`) that runs about 9 %
  high on Qwen3. Counted with the NPU bundle's own tokenizer, the three prompts
  are about 955, 3,145 and 7,075 tokens, so the ~3.5k one leaves about 950
  tokens of a 4096 context for the reply. Every row also carries the lane's own
  `prompt_tokens`; on a lane whose prefix cache serves the document the three
  questions share, that is only the uncached tail.
- **A lane that checks prompt plus `max_tokens` against its context** will
  record the `doc_3.5k` rows as OVERFLOW at the default `--max-tokens 2048`;
  rerun that category with `--max-tokens 512`. This is untested on GenieX,
  whose v0.7 refusal says only "prompt is longer than the model's context
  window".
- **Unmeasured rows are counted, not scored.** Thinking is stripped first, so
  an unclosed `<think>` counts as no answer. A reply cut at `--max-tokens` is
  CUT: excluded from the score and counted beside it.
- **Repeats and the control.** Repeats are separated by a spacer request, and
  repeats that agree count once. Each category prints with its Wilson
  interval, and cases the control endpoint also fails are marked suspect, as
  in `bench_tools`; with a control in the run, the written categories exclude
  the suspect cases and keep `excluded`, `cases` and `cases_passed`.
- `tool_sha256` covers `bench_chat.py` and `determinism.py`, whose probe
  verdict sets `bench_compare`'s strict mode.

### Does the whole agent loop work? (`bench_agent.py`)

Every other benchmark here measures an **endpoint**. You run an **agent**. This
one connects them: a scratch git repository, a task with a verifiable outcome,
and success defined as *the repository's tests pass afterwards* — never by
reading the transcript. An agent that says it fixed the bug and did not is
exactly the failure a transcript cannot catch.

```bash
python3 bench_agent.py --self-test          # prove the fixtures, no model
python3 bench_agent.py --list
python3 bench_agent.py --model geniex-cpu/empero-ai/Qwen3.8-9B-Distill-GGUF:Q4_K_M \
                       --timeout 1800 --keep-output --output agent.json
python3 bench_agent.py --model geniex-cpu/empero-ai/Qwen3.8-9B-Distill-GGUF:Q4_K_M \
                       --timeout 1800 --repeats 3 --output agent.json
```

`--model` takes an **opencode** `<provider>/<model>` id, so the provider key
must exist in your `opencode.jsonc` — see
[`docs/geniex-local-ai-setup.md`](../third_party/ANTfrastructure/docs/geniex-local-ai-setup.md) § Step 3.
Point it at a GGUF lane: the QAIRT bundle's compiled 4096-token context is less
than opencode's own preamble, so it fails every task before reading one (`third_party/ANTfrastructure/docs/geniex-local-ai-setup.md` § 1m).

Run `--self-test` first, and read a run without it with suspicion. It applies a
known-good solution to each fixture by hand and asserts the verification is red
before and green after, and it also applies the known *cheats* and asserts they
are refused. Without that, a column of failures is unreadable — a broken fixture
and a weak model look identical.

Fixtures cover Python, bash and CMake. A fixture whose tools this host does not
have (`cmake`, `ctest`, `make`/`ninja`) is **dropped, announced and recorded**
in `skipped_tasks`, and selecting only unbuildable fixtures exits non-zero —
running nothing and exiting 0 is worse than an error, because `bench_compare`
then reads it as a result.

**One fixture is a medium-size repository.** The other fixtures are one to
three files, so they measure whether the loop can read a file and edit it,
never whether it can *find* the file. `fix_medium_repo` is `tally`, a 32-file
toy ledger CLI: modules, tests, README, docs, a CLI. It has one planted bug in
its money parser, which loses the sign of a refund. The two red tests are in
the report and budget tests, two imports away from the cause (test →
`tally.ledger` or `tally.budget` → `tally.money`), and an aggregation test
shows refunds netting correctly, so guessing the nearest module does not work.
It is verified by the fixture's own suite, then by the CLI run on a ledger and
budget the agent never sees. That refuses a hardcoded answer and any patch
that only exists inside pytest; a correct fix passes wherever it was made.

**`--repeats N` — because one trial per fixture cannot say *every time*.** On
the llama.cpp lanes the trials are real draws. Each trial gets a fresh scratch
repository and a fresh opencode data and state directory: sessions, snapshots,
the recent-model list and the prompt history do not carry over from one trial
to the next. The config and cache directories are shared; they hold the
installed plugin, `models.json` and ripgrep, which a fresh directory would
download again. Trials run round-robin over the tasks, so a lane that drifts
over a long run spreads the drift over every task. No spacer is needed, even
for `--task X --repeats N`: opencode puts the working directory into its
system prompt, and that path is fresh for every trial (read from the 1.18.31
binary). Rows carry `attempt`; the report adds `repeats`, `trials_run`,
`per_task` (passes and attempts), `pass_hat_k` for k = 1..N and `wilson_95`.
**pass^k** is the chance that k fresh trials of a task *all* pass, averaged
over tasks: C(c, k)/C(n, k) per task, over the tasks with at least k trials. A
task that passes 2 of 3 is pass^1 67 %, pass^2 33 %, pass^3 0 %. A
context-blocked trial is not an attempt. `--repeats` takes a whole number of at
least 1; anything else is a usage error (exit 2). No lane has been measured
with it yet.

What the scoring does that a naive pass count does not:

- **The verdicts refuse the cheap fakes, and each refusal is pinned by a test.**
  Editing, deleting or adding a test file fails `fix_failing_test` outright
  ("tests were modified"). An added `conftest.py`, `sitecustomize.py`,
  `pytest.ini`, `.pytest.ini`, `tox.ini`, `setup.cfg` or `pyproject.toml` in
  the tests' directory or any directory above it is refused too: a
  `pyproject.toml` carrying `addopts = -k 'not empty'` used to deselect
  `fix_failing_test`'s red test and read as `1 passed, 1 deselected`, a PASS.
  `add_function_and_test` runs the agent's own tests
  against four mutants of the required function and requires each to be caught,
  so a passing clamp with `assert True` beside it does not count. A rename is
  decided on the **syntax tree** — a name, an attribute, a def, an `import … as`
  alias or a string constant — so a comment mentioning the old name is not a
  failure and an alias is. `fix_medium_repo` refuses an edited existing test,
  any new `conftest.py`, and the two visible numbers hardcoded. An edit to its
  tracked `pyproject.toml` cannot deselect the red tests through `addopts`
  (`-o addopts=`). It can through `python_functions`, so the final verdict
  comes from the CLI run on unseen inputs, which no pytest configuration
  reaches.
- **Blocked is not failed, and "blocked" is now a short list of markers.** Only
  an explicit `context_length_exceeded` / `prompt too long` / `maximum context
  length` / `Input prompt too long`, and only **before any tool or step event**,
  counts as never having reached the model; those rows are excluded from the
  denominator and their (usually timeout-length) wall is excluded from the
  total. The same marker *after* the agent started working is status
  `CONTEXT_GROWTH` and a **real failure** — that is the context-growth mode the
  roadmap says would overturn the recommendation, and it used to be silently
  dropped.
- **A timeout keeps its evidence** — the events captured before the deadline are
  parsed, so an agent that made twenty tool calls and ran long is not reported
  as having made none — and the agent runs in its own session, so killing it on
  timeout kills its bash children too.
- **`0/0` prints `n/a`**, not `0 %`. The score line goes through
  `bench_stats.format_score`, and its Wilson interval is now stored in the
  report as `wilson_95` (no interval for 0/0). With `--repeats` it pools
  attempts, which are clustered by task: read `pass_hat_k` and `per_task`
  beside it.
- **The run is reproducible from the report.** Provenance records the resolved
  provider `base_url`, the opencode version, the path and SHA-256 of the
  opencode config, the disabled tools and the instructions; `--keep-output`
  stores each workspace's `git diff` (first 20 kB). opencode's data and state
  directories are redirected to a fresh scratch dir per trial and removed
  unless `--keep`, so runs stop leaking sessions into
  `~/.local/share/opencode` and `~/.local/state/opencode`. opencode 1.18.31
  reads `XDG_STATE_HOME`, and when the config names no model its default model
  is the most recent entry in `state/model.json`; a run without `--model`
  therefore no longer runs whatever model the host used last.

Expect **minutes per task** on this hardware. That is prefill cost, not model
quality — see the hub's `docs/geniex-local-ai-setup.md` § 1m.

### Do the embeddings mean anything? (`bench_embeddings.py`)

```bash
python3 bench_embeddings.py --backend ollama --model nomic-embed-text --output emb.json
```

An embedding endpoint can return well-formed vectors of the right dimension at
a fine rate and still be useless, because the numbers carry no semantic
structure. So it checks three things in increasing order of what can go wrong:
**shape** (dimension, finite values, stable across calls), **speed** (texts per
second and per-text latency by input size) and **meaning** — do related texts
land closer together than unrelated ones? That last one is what catches a broken
quantisation or a mis-wired pooling layer, and it is the check a shape test
cannot make.

Its rows are not scored rows in the manifest: an embedding result has no
`passed`/`total`, and rendering it as `0 %` ranked a working endpoint last.

### Adding a model: one command (`bench_sweep.py`)

Ranking a new candidate used to be five commands with hand-typed `--output`
paths, and both failure modes were silent — a second candidate written to
`coding.json` overwrote the first, and two lanes serving the same GGUF
collapsed into one label.

```bash
cp candidates.example.json candidates.json      # then edit it
python3 bench_sweep.py --candidates candidates.json --outdir results/2026-09-05 \
    --tools speed,coding,tools --repeats 3
```

| Flag | Default | What it does |
|---|---|---|
| `--candidates FILE` | required | JSON list, the same format every tool's `--compare` takes |
| `--outdir DIR` | required | Where `<tool>_<slug(label)>.json` is written |
| `--tools a,b,c` | `speed,coding,tools` | Any of `speed`, `coding`, `tools`, `chat`, `agent`, `lanes` |
| `--repeats N` | `1` | Passed to `bench_coding`, `bench_tools`, `bench_chat` and `bench_agent`; below 1 is refused before anything runs |
| `--task-set S` | `all` | Passed to `bench_coding` |
| `--baseline NAME` | none | Compare every written report against a stored baseline |
| `--title T` | derived | Manifest title |
| `--skip-gate` | off | Skip the correctness probe. Only for a paid endpoint you have already verified |

It **refuses before it runs**: an output path that already exists, two labels
that slug to the same file name, or a `--baseline` that is not in `baselines/`
all stop the sweep up front rather than after several hours. The correctness
gate runs first per candidate, because a dead lane answers every benchmark with
a full set of plausible failures — `unreachable` skips that candidate, `wrong`
and `truncated` are recorded and measured anyway. It ends with the
`bench_report` manifest and, with `--baseline`, a per-report `bench_compare`,
and writes `_sweep.json` (leading underscore, so it is not mistaken for a
result) holding the gate verdict, every step's exact argv and its exit code.
The coding step always runs with `--keep-output`, so a published table's raw
replies exist without remembering to ask; the agent step does not, so run
`bench_agent.py` directly when that report has to be auditable.

The chat step (`chat` in `--tools`; not in the default) runs `bench_chat.py` on
the candidate's endpoint with its label and `--repeats`, into
`chat_<slug(label)>.json`. It keeps `bench_chat`'s own `--max-tokens` (2048,
the budget a thinking model needs to answer) and runs every category, so the
file is always the whole instrument. The agent step gets `--repeats` too, so
its `pass_hat_k` is over the draws the sweep asked for. A candidate that names
a `backend` and a `base_url` is measured at that URL, the one the gate probed;
the backend's entry still supplies headers and keys. With `--baseline`,
`_sweep.json` records each comparison's `regressed` (exit 1) and
`conditions_differ` (exit 4, a verdict withheld for load); both are advisory
there.

`candidates.json` is **not** in `.gitignore`. Keep secrets out of it — the API
key lives in an environment variable named by `backends.json`, never in either
file.

**Label the lanes.** A candidate without a `label` is keyed on its bare model
id, so two lanes serving the same GGUF would appear as one row and a 3/3 → 0/3
collapse would read `unchanged`. Colliding *derived* labels are disambiguated
with the backend name; colliding *explicit* labels are refused, because only
the author knows which is which.

**API keys never live in a file.** A `backends.json` entry may carry
`api_key_env` (the NAME of an environment variable, sent as
`Authorization: Bearer …`), `headers`, `request_extra` (body keys merged
*before* the caller's own, so an explicit `model`/`max_tokens`/`temperature`
always wins — this is where Ollama's `num_ctx` belongs) and `probe: false` (do
not ask `/v1/models` what a paid host serves; such an entry must name its
`model`). An unset or empty key variable **aborts the run naming the variable**
and never prints a value, and a report records only the header *names* and the
variable *name*, under `config.backend_entry`. All of it goes through one
request path, `bench_cli.post_json`.

**The `control` backend is the calibration point.** When every candidate fails a
case there is no way to tell a hard case from a broken one, so point `control`
at the strongest endpoint you have. A case the control *also fails* is marked
**suspect** and removed from every other candidate's score, interval and rank,
named in one line above the ranking table and listed in each report row as
`suspect_cases`. A case is suspect only when the control failed **every**
measured attempt of it — one flaky draw out of three is not evidence about the
case. The control keeps its own full score — it is the calibration, not a
competitor — and for that reason it is printed beside the ranking rather than
inside it: it is scored on the full case set while the candidates are scored on
the reduced one. A case the control merely *errored* on is not suspect, because
that is evidence about nothing. Everything derived from the surviving rows is
recomputed with the score: `wrong`, `effective_n`/`effective_k`, `by_kind`,
`by_lang`, `categories` and the wall statistics, so no table in a report can
disagree with its own headline.

### After a runtime upgrade: one command (`upgrade_check.py`)

Every GenieX release so far has changed something this lab relied on. The
v0.6.1 → v0.7.0 round was run by hand, one command at a time, and its review
had to rule the order out as a confound: the `--log info` speed numbers came
from a lane that the contract's `power_mode` check had just reloaded twice, and
it took a control on fresh lanes to show that the logging, not the reload,
cost the 13.7 %. `upgrade_check.py` runs that protocol the same way every time:

```bash
# from the repo root, on the Windows host that runs the lanes
uv run --no-sync python benchmarks/upgrade_check.py --lanes geniex-npu,geniex-cpu \
    --out benchmarks/benchmark_results/2026-10-01-geniex-v080 \
    --previous benchmarks/benchmark_results/2026-09-24-geniex-v070 \
    --overflow-tokens geniex-npu=6000 --wsl
```

Every step runs with the interpreter that runs the check, hence `uv run`.
Per lane, in this order, never two lanes at once:

| Step | Command | File |
|---|---|---|
| contract | `orchestrant-bench contract` (+ `contract --diff` against `--previous`) | `<lane>-contract.json`, `<lane>-contract-diff.log` |
| speed | `orchestrant-bench speed --stream --correctness` — fails the step on a wrong correctness answer or a correctness check that scored no probe (a truncated probe does not fail it) | `<lane>-speed.json` |
| speed-answer | `orchestrant-bench speed --stream --max-tokens 2048` | `<lane>-speed-answer.json` |
| tools | `bench_tools.py --repeats 3` | `<lane>-tools.json` |
| coding | `bench_coding.py --task-set all --keep-output` — Linux-only | `<lane>-coding.json` |

Either speed step fails when fewer prompts completed than were sent. After the
last lane, `bench_compare.py --dir <previous> <out>` writes `compare.log`. Each
report's full output is in the `.log` beside it. `steps.jsonl` records every
step's argv, exit code, start, end and duration as it finishes. `MANIFEST.md`
maps each file to the exact command that wrote it and its exit code, and names
each lane's serving runtime next to the previous run's. It is rewritten after
every step, so a killed run still shows how far it got.

| Flag | Default | What it does |
|---|---|---|
| `--lanes a,b` | required | `backends.json` names, run in the order given |
| `--out DIR` | required | Must not exist — not even empty |
| `--previous DIR` | none | An earlier upgrade-check directory: `contract --diff` and `bench_compare --dir` against it (files are matched by name) |
| `--steps` | all | A subset of `contract,speed,speed-answer,tools,coding`; the order never changes |
| `--tools-repeats N` / `--coding-repeats N` | 3 / 1 | Passed through |
| `--no-correctness` | off | Leave the six-probe correctness gate out of the speed step |
| `--overflow-tokens LANE=N` | none | `contract --overflow-tokens` for that lane only (6000 overflows the NPU bundle; on a 16k GGUF lane it would prefill for about a minute) |
| `--wsl` | off | On Windows, run `bench_coding` inside WSL (`wsl -d Ubuntu-26.04`, against `/mnt/c/...`) instead of recording it as skipped |
| `--wsl-distro`, `--wsl-python` | `Ubuntu-26.04`, the lab's `~/.local/bin/uv run --no-project --with …` | Where and how that runs |
| `--dry-run` | off | Print the plan; create and contact nothing |

Files are matched by name, and the tracked v0.7.0 run
(`benchmark_results/2026-09-23-geniex-upgrade/`) predates these names — its
reports are hand-named `v070r2-*` — so the first `--previous` needs a baseline
written under the new names. The check has not had its first live run yet.

The coding step runs directly in the WSL distro, with no container runtime
involved. This host has no Rancher Desktop: its container runtime is rootless
`nerdctl` inside the same Ubuntu-26.04 distro, which runs the Glances container
the speed runner reads on :61208. From WSL2 the lanes are reached over
mirrored networking, and that report's `provenance.runtime` is
`verified: false`. A `LLM_BACKENDS` set on Windows is passed into WSL as a
`/mnt/...` path, so both sides resolve the lanes from one registry.

**It refuses before it runs:** an existing `--out`, a missing `--previous`, an
unknown or repeated lane, and `LLM_BASE_URL`/`OLLAMA_BASE_URL` being set (that
variable beats `--backend` in every tool, so every lane would measure one
endpoint). A lane that does not answer `/v1/models` is not measured. A lane
whose runtime version, process start time or serve flags differ between its
first and last step fails the check, because its later reports came from a
different lane process. So does a lane whose model files changed behind the
same id during the check: a re-pull, or an edited `genie_config.json` or HTP
extensions file. Both runtime probes pass the lane's `backends.json` model, so
a GenieX runtime records `model_files`, and `provenance.model_files_notes()`
names what moved (`MODEL FILES CHANGED behind …`). Only a lane whose process
is unchanged is checked for this; a relaunch or upgrade is named instead. A
lane whose `backends.json` entry names no model records no files, and the
check cannot see its weights.

**Exit codes.** 0 means at least one step ran, every step that ran passed, no
lane moved during the check and, with `--previous`, `bench_compare` compared
something, found no regression and withheld no verdict. 1 means a failed step,
a lane problem, a regression, a verdict withheld for load, or nothing
compared. 130 means Ctrl-C.

- A tool exiting 0 still fails the step if its report shows a dead lane (every
  contract check `error`, fewer prompts completed than sent, nothing measured)
  or, for the speed step, a wrong answer from the correctness check.
- `bench_compare`'s codes stay separate in `steps.jsonl`: 1 counts as
  `regression` only when it printed REGRESSION and its closing summary; an
  unreadable report also exits 1 and is `failed`. 3 is `nothing-compared`. 4
  is `conditions-differ` (again only after the closing summary): a speed or
  timing verdict was withheld for load. Each is named at the top of
  `MANIFEST.md`.
- `conditions-differ` has no override flag here, on purpose: a check that
  fails on unlike load should not pass itself. Re-run the busy side on a quiet
  host. That side may be the previous run, which after an upgrade can no
  longer be re-run; then judge by hand with
  `bench_compare.py --dir --allow-load-difference <previous> <out>`.
- If the two directories share no file name, `bench_compare` is not run and the
  step is recorded as nothing compared.
- `contract --diff` exit 1 counts as a moved answer only when the tool printed
  CHANGED rows and the reports really differ; after a failed contract step the
  diff is skipped, because a dead lane moves every answer to `error`.
- A contract answer that moved is listed under "Contract answers that moved"
  and does **not** fail the check. After an upgrade that change is the finding,
  so read `<lane>-contract-diff.log` first.
- A check in which every step was skipped is `NOTHING RAN`, exit 1.

### Did anything regress? (`bench_compare.py`)

```bash
python3 bench_compare.py old.json new.json
python3 bench_compare.py --baseline geniex-npu new.json     # against a stored one
python3 bench_compare.py --save-baseline geniex-npu new.json
python3 bench_compare.py --dir results/prev results/now     # every shared report
```

Baselines live in `baselines/<name>.json`. Four things it refuses to do,
because each is a way to be confidently wrong:

- **Call a difference a regression the sample cannot support.** Both candidates
  answered the *same* cases, so the aggregate is judged by an exact two-sided
  **paired sign test** over the cases that disagreed, plus the paired estimate
  of the difference (the mean per-case change, with a case-clustered interval —
  identical per-case outcomes read `[+0, +0]`); Newcombe on the two aggregates
  only when a report has no per-case detail. Unpaired interval overlap
  survives only as the fallback for such reports, and the finding says so. The
  practical floor is **six cases flipping the same way with none flipping
  back**, and that floor does not depend on suite size — where the old unpaired
  rule needed 119 cases to separate 93 % from 81 %.
- **Pass off "cannot tell" as "no regression".** After every `unchanged` and
  every `no regression detected`, the comparer prints the **minimum detectable
  drop at 80 % power** for the number of paired cases. This is the exact power
  of the paired sign test when each case independently gets worse, or flips
  back at the observed rate, never taken below 5 % (none or one back-flip in a
  few dozen cases does not pin the rate down). At today's pool sizes that drop
  is large: 26 points at 42 cases, 33 at 31. A 10-point drop at 31 cases is
  caught 8–11 % of the time. After a comparison of several labels, the closing
  line names the **weakest pairing**: the one with the largest detectable
  drop, not the one with the fewest cases.
- **Cry wolf on a single draw.** At `--repeats 1` on a lane not known to be
  deterministic, per-case flips are reported as
  `flipped (single draw — rerun with --repeats 3)` and do **not** set the
  regression flag. The old rule fired on 92 % of same-model re-runs.
- **Blame the model when the *grader* moved.** Provenance carries a hash of the
  benchmark's own source, and a mismatch is stated before any score. Runs from
  different hosts or architectures are never compared silently.

Rows nobody measured — errored, truncated, blocked, `CONTEXT`, `overflow`,
`skipped` — are excluded on both sides, so an ungraded row can no longer read as
a row the model failed. Duplicate labels in one report are a hard error naming
the file. Lane reports are compared as throughput (`tok_per_sec`, same tolerance
as timing; an aggregate row is delivered throughput since 2026-09-24, and
against an older report, which stored the sum of per-lane rates, the sums are
compared), and a lane that stops overlapping concurrent requests is a
regression in its own right.

**A lanes report's runtimes are diffed lane by lane** (`compare_lanes.py`).
Each lane row carries its own `runtime` (build, serve flags, model files,
drivers), while the provenance block covers one URL: the first lane's, or the
batching endpoint's under `--batching`. For every lane both reports ran,
`bench_compare` prints the notes `provenance.compare()` prints for the
envelope, prefixed `! lane NAME:` — `SERVING RUNTIME CHANGED`, different serve
flags, `MODEL FILES CHANGED`, an edited `genie_config.json` or HTP extensions
file — so the lane the block describes repeats the envelope's notes under its
own name. A lane row older than the field takes the provenance block's runtime
when its URL is the block's (the 2026-09-23 lanes reports recorded their first
lane there), and a runtime the tool could not read (`{"error": ...}`) counts
as not recorded. None of these lines is a regression; they say what else
moved. A lane in one report only is named too, and then every rate moved
with the set: the aggregate sums another set of lanes, and each lane's own
tok/s is its rate beside the others (the NPU lane decodes 22.9 tok/s alone and
8.8 beside the CPU lane). So no tok/s is judged -- each prints `NOT judged: the
lane set changed` -- and a lanes pair with nothing else like-for-like is
`NOTHING COMPARED`, exit 3, rather than a dropped lane reading as the runtime
going `SLOWER` or an added one as the surviving lane doing so.

**Speed reports are compared prompt by prompt** (`compare_speed.py`): decode,
prefill and TTFT are paired per prompt, and a median decode ratio that falls by
more than 5 % — or by more than the prompts' own scatter, if larger — is
`SLOWER`. Prefill and TTFT are reported, not alarmed (on the runner's short
prompts they measure request overhead), and CPU-rail J/token is reported as a
ratio of sums. On a CPU lane (4+ cores busy) a change its own requests' load
could explain is printed `NOT judged` — a slower run measured over 0.3 cores
of other load, or a faster or unchanged one against a loaded baseline, which
understates the old rate and so hides a real drop too — with the load derived
from `cpu_percent` and `lane_cores` for reports older than `other_cores`. A
slower run against a loaded baseline is still judged: the real drop is only
larger. A decode verdict left `NOT judged` counts as withheld (exit 4, below,
not 0) unless `--allow-load-difference`, which lets it pass unjudged -- it is
never turned into a verdict. This replaced a latency comparison
against a 25 % tolerance, which passed GenieX v0.7.0's 13 % NPU decode loss as
"no regression detected"; the correctness score is still compared.

**Exit codes:** 1 is a regression, 0 is "compared, nothing regressed", **3 is
`NOTHING COMPARED`** — two reports that share no score, timing or speed metric
(contract reports among them: use `orchestrant-bench contract --diff`) — and
**4 is `CONDITIONS DIFFER`**: a speed or timing verdict was withheld because a
run started on a busy host, the two started under different load, or a CPU
lane's own requests ran under other load (see the load notes under
[*What every report records*](#what-every-report-records)), and nothing that
was judged regressed. 3 used to print "no regression detected" and exit 0. (2
is argparse's usage error; an unreadable report still exits 1.) The order, for
one pair and over `--dir`, is 1 > 4 > 3 > 0: a score regression exits 1 even
beside a withheld timing verdict, a withheld verdict outranks both a pass and
a blind pairing, and 3 needs every pairing blind. `--dir` counts the pairings
with a verdict withheld for load in its closing line.
`--allow-load-difference` judges the gated verdicts anyway and lets a
`NOT judged` line pass unjudged, as before the gate; the notes still print.

### When the lane loses the tool call (`geniex_toolcall_shim.py`)

> **Not needed on GenieX v0.6.0 and later**, which added tool-call parsing for
> the Qwen template — verified on v0.6.1 (2026-09-05): `Qwen3.8-9B-Distill`
> returns a populated `tool_calls` with `finish_reason: "tool_calls"` straight
> from the lane. Point the agent at the lane. The shim stays for older builds
> and is harmless in front of a new one; it skips any message the server has
> already parsed.

If an agent run scores **zero tool calls**, suspect the server before the model.
GenieX v0.5.0 served Qwen GGUFs over an OpenAI-compatible API without parsing
their chat template: the model correctly emits

```text
<tool_call><function=bash><parameter=command>…</parameter></function></tool_call>
```

and the server hands it back as `content`, with `tool_calls` empty and
`finish_reason` `"stop"`. The agent sees prose, runs nothing, and ends the turn.

```bash
python3 geniex_toolcall_shim.py --upstream http://localhost:18184 --port 18190
# then point the agent's provider at 18190
```

The shim translates the template into real `tool_calls` and sets
`finish_reason` accordingly. It never invents a call from markdown code fences —
a model that writes ```` ```bash ```` is not requesting execution, and guessing
there runs commands nobody asked for — and a call cut off mid-template yields no
call at all, only its markup removed.

### What every report records

All tools write a `provenance` block: UTC timestamp, host, OS, architecture,
git SHA **and whether the tree was dirty**, the served model ids, and a
`tool_sha256` fingerprint of the benchmark's own source. That last one matters
most — a ranking can move because the *grader* changed, and without the
fingerprint that is indistinguishable from a model regression.
`bench_provenance.compare()` diffs two blocks and says so in as many words.

Fields that cannot be determined are recorded as `null` and listed in
`incomplete` rather than omitted: a gap you can see is a gap you can fix.

**Every report carries a run-start record.** Before its first request, each
tool — the speed runner, `lanes`, `contract`, `bench_tools`, `bench_coding`,
`bench_agent`, `bench_embeddings` and `bench_chat` — hashes its own files and
measures the host for 3 s. It prints the result as `Host load: X other cores
over the 3 s before the first request`, with a WARNING above one core. From
WSL2, facing a Windows lane, the line reads `X other cores on the Windows
host`. Provenance then carries:

| Field | Meaning |
|---|---|
| `host_load` | `{busy_cores, lane_cores, other_cores, seconds, cpus, note, via}`. `other_cores` means what the speed rows' field means: busy cores on the lane's host minus the lane's own process tree. A WSL2 harness pointed at a Windows lane on loopback (mirrored networking, nothing inside WSL listening on the port) reads the Windows host through interop, `via: "wsl-interop"`: one `powershell.exe` call samples the `Win32_PerfRawData_PerfOS_Processor` idle ticks around a sleep and the `Win32_Process` CPU time of the process listening on the port and its children, and records the host's `cpus`. It costs about 1.5 s over its window (4.5 s for 3 s) and is given up after the window plus 20 s. Its `other_cores` includes the WSL VM itself — the harness and anything else running in WSL. Every other reading is `via: "local"`; a run start whose reading raised records only `other_cores: null` and `note`. When the lane's host cannot be read (a remote URL, NAT-mode WSL's host address among them, or interop failing) `other_cores` is null and `note` reads `the lane's host is not visible from here: <reason>`, from WSL2 continued by `; the Windows host through WSL interop: <why>`. When nothing on Windows listens on the port either, `busy_cores` is the host's and `lane_cores`/`other_cores` are null: a lane that cannot be found is unknown, not zero. Only this record reads Windows: from WSL2 the speed rows' per-request `other_cores` stay null |
| `run_started_utc` | when that record was taken (`timestamp_utc` is still the end of the run) |
| `tool_files` | the basenames `tool_sha256` covers |
| `source_changed_during_run` | `false` when the start hash matched at the end, `true` (plus `tool_sha256_at_start`) when the tool's source was edited mid-run; absent when no start hash was taken |

`tool_sha256` covers only what decides a report, concatenated in file-name
order (it used the full path, so the same source could hash differently
depending on where the checkout lives — a lowercase `e:\` drive, for example).
It no longer covers `provenance.py`: that file is plumbing, and every edit to
it used to read as "the grader changed". `determinism.py` is hashed where the
determinism probe runs, because its verdict sets `bench_compare`'s strict mode.

| Tool | Files `tool_sha256` covers |
|---|---|
| speed runner | `openai_api.py`, `answers.py`, `energy.py`, `hostload.py` |
| `lanes` | `lanes.py`, `answers.py` |
| `contract` | `contract.py` |
| `bench_tools` (case suite) | `bench_tools.py`, `tools_opencode.py`, `determinism.py`, `geniex_toolcall_shim.py` under `--accept-text-json`, where the shim's parser decides which prose answers pass, and `bench_variants.py` under `--prompt-variants` |
| `bench_tools --turn-growth` | `bench_tools.py`, `tools_opencode.py` |
| `bench_coding` | `bench_coding.py`, `determinism.py`, `bench_tasks.py` when `--task-set` is `extended`, `languages` or `all` (the default): it holds 21 of the default set's tasks, prompts and grading tests, and `bench_variants.py` under `--prompt-variants` |
| `bench_agent` | `bench_agent.py`, `bench_agent_medium.py`, `bench_agent_medium_files.py` |
| `bench_embeddings` | `bench_embeddings.py` |
| `bench_chat` | `bench_chat.py`, `determinism.py` |

The first comparison against a baseline saved before this record prints
`BENCHMARK SOURCE CHANGED` for every tool, because each one's file set or
source changed; re-save the baselines.

When comparing two reports, `bench_compare` and `contract --diff` also print
two load notes:

- `HOST WAS BUSY when the old/new run started (X vs Y other cores)…` whenever
  either run recorded more than 1.0 other cores, even if the other report
  predates the record; the missing side prints as `unrecorded`.
- `taken under different load — X vs Y other cores at the start…` when both
  runs recorded their load and it differs by more than 0.3 cores. The
  difference is rounded to 0.01 first, so runs exactly 0.30 apart do not
  trigger it.

In `bench_compare` the notes are a gate, not only a warning. When either fires
for a pairing, the verdicts load can move are **withheld**: the speed
tripwire's (decode, prefill, TTFT), the per-attempt time and a lane report's
throughput. Their numbers still print, marked `WITHHELD for load`, a closing
`WITHHELD for load: …` line names them, and the run exits **4, `CONDITIONS
DIFFER`** — in both directions, because a flat or faster result against a
busy baseline is exactly the reassurance load can fake. A CPU lane's decode
verdict that its own requests' load left `NOT judged` is withheld the same
way, even when neither start was busy. Scores, per-case flips and the batching
verdict are never withheld (load slows an answer, it does not change it), so a
score regression still exits 1. A report that predates the record is never
refused for lacking it; only a busy run on the other side shuts the gate. A
speed pairing whose rows show a lane under 4 cores on both sides (the NPU
lane, ~1.0 core in every tracked report) is still judged when neither recorded
start was busier than 2.0 other cores, the range it was measured unmoved over
(beside the CPU lane's 7.2 busy cores it lost 46–87 %), and a line says it was
judged despite the note. `bench_tools`, `bench_coding` and `bench_agent`
record no lane share, so a shut gate withholds their timings on any lane, the
NPU lane's included. The remedy is to re-run the busy side on a quiet host;
`--allow-load-difference` judges the gated verdicts anyway and lets a
`NOT judged` line pass unjudged, as before the gate, and the notes still
print. `contract --diff` is not gated: its answers are behaviours, not
rates, and its one timing-derived answer (the prefix cache) compares a repeat
with a cold request inside the same run.

**`runtime` names the server build and its launch flags.** Every GenieX number
published so far carried its version in prose, because no report recorded it.
When the harness shares the lane's host, the process listening on the port is
asked (`geniex --version`: CLI version, QAIRT runtime, llama.cpp hash) and its
command line is kept as `serve_args` — `--nctx`, `--keepalive`,
`--power-mode` — with `verified: true`. From WSL2 that process is invisible, so
a loopback lane that serves GenieX's root page is attributed to the *installed*
binary with `verified: false`; Ollama answers `/api/version` itself.
`bench_compare` then says `SERVING RUNTIME CHANGED — geniex v0.6.1 … → v0.7.0 …`
before any score, and names a lane launched with different serve flags.
`interpreter` records the Python build's own platform, because an x64 Python
under emulation on Windows on ARM still reports `architecture: ARM64`.

**`runtime.model_files` names the weights, `runtime.drivers` what runs them.**
A report whose rows all served one model id resolves it through the lane's
GenieX cache (`<cache>/<org>/<repo>/geniex.json`; the variant after `:` must
match one, ignoring case, and is never guessed among several).

- A GGUF, and each QAIRT context binary, is recorded by size, mtime,
  `head_sha256` (a sha256 of its first MiB; `head -c 1048576 FILE | sha256sum`
  reproduces it) and `sampled_sha256`: a sha256 over 16 windows of 64 KiB at
  offsets i·(size − 65536) // 15 for i = 0..15, hashed in order.
  - The head alone tells no quant apart. On this host all four Qwen3-4B quants
    share their first MiB, which is `general.*` and the vocabulary.
  - The sample reads the weights and tells every file in the cache apart, in
    about 10 ms each.
  - It is still not a content hash: an edit between the windows at the same
    size goes unseen.
- A QAIRT bundle records `genie_config.json` hashed in full, together with its
  `sampler` (the temp 0.8 / top-k 40 / seed 42 that answers `temperature: 0` on
  the NPU lane) and `context_size` (the hard-compiled 4096). It also records
  the context binaries it loads, the HTP extensions file that pins the perf
  profile, and `bundle_qairt`, the QAIRT the bundle was compiled with, which
  need not be the runtime's.
- `size_matches_manifest: false` on any file means it was replaced or edited
  in place. A file one run could not read (a lane holding it open) carries an
  `error` and is a gap, not a change.
- `drivers` lists the NPU and GPU drivers from the Windows registry, which
  Windows Update moves while the GenieX build stays the same. They are null
  with a reason off Windows.
- Nothing here writes to the cache.

`bench_compare` prints `MODEL FILES CHANGED behind <id>` when other files serve
the same model id, and names an edited `genie_config.json` or HTP extensions
file.

**From WSL2, give the report the host's view.** The lane process runs on
Windows, and WSL2 cannot see it. On the host, after the lanes start:

```powershell
uv run orchestrant-bench runtimes --output C:\bench\lanes-runtime.json geniex-npu geniex-cpu
```

then in WSL2 `export LLM_LANE_RUNTIMES=/mnt/c/bench/lanes-runtime.json`.
`runtime_info()` then prefers the lane process when it can see one, then the
snapshot (by exact URL, or the one loopback entry on the same port), then the
installed binary. The runtime names the file, its age and `stale`. Snapshots
older than 12 h are kept but marked `verified: false`, because a lane restart
since the snapshot is invisible from WSL2, and so is one whose build the
installed GenieX no longer reports (`snapshot.installed_cli_mismatch`). Take a
new snapshot after every `Start-GeniexServers.ps1 -Restart`. Inside a
container started with `nerdctl` in WSL2, `/mnt/c` is visible only when
mounted: pass `-v /mnt/c/Users:/mnt/c/Users:ro` so the Windows model cache
resolves, mount the snapshot's directory the same way, and pass
`-e LLM_LANE_RUNTIMES=/mnt/c/...`.

A `lanes` report carries each lane's `runtime` on its row; the provenance
block describes only the URL it was collected for.

`server_models` is **not** the loaded model: GenieX lists its whole local cache
on `/v1/models` (Ollama every pulled tag), so it changes whenever a model is
pulled. For the same reason `orchestrant-bench speed` no longer auto-detects a
model from a listing of several — it used to take the first id, which on a
GenieX lane is whichever cached model sorts first, and the lane then loaded it.
It now refuses and lists them; pass `--model` or use a `--backend` that names
one.

The `config` block records what would change a score, so two runs can be told
apart: for `bench_coding` and `bench_tools` that includes
`config.backend_entry` — the merged `request_extra`, the header **names** and
the api-key **variable** name, never a value, because reports are committed. A
report written before that key existed will therefore be reported as
`! config.backend_entry changed` on its first comparison, which is correct: the
runs are not like-for-like if one of them was sending an `Authorization`
header.

`bench_coding` and `bench_tools` now send a **determinism probe** — the same
open-ended 48-token request twice, with an unrelated request between them
(`determinism_probe.spacer`), to the lane provenance names — and record
`temperature`, `seed` and `determinism_probe` beside the run. (Until
2026-09-24 the two requests went back to back, and on GenieX an identical
follow-up takes a cache path that changes the reply, so the probe measured
that path; before that it asked for "the single word: ready" in 8 tokens.
Reports probed the old ways carry their prompt in `determinism_probe.prompt`
and no `spacer` key. Spaced, the QAIRT lane is reproducible — a fixed seed —
and the llama.cpp lanes are not.) `bench_compare`
reads it as `probe_deterministic`, so a `--repeats 1` flip on a lane the probe
found deterministic is a real regression rather than a coin toss. A probe that
could not run records its error and `deterministic: null`; read that as *nobody
could ask*, never as *this lane samples*. Both tools also emit
`wall_measured_s` — the wall over measured attempts only, the field
`bench_compare` prefers for its timing verdict.

### Reading a score honestly

- **Warm-up is on by default.** Without it the first task carries the model load
  time and the ranking partly ranks load order — measured: ~34 s of the 27B's
  128.7 s total was loading, 26 % of its score-deciding number.
- **`effective_n` is reported, not just the raw total.** On a deterministic
  endpoint (the QAIRT/NPU path samples from a fixed seed, so with the spacer
  between repeats it is one) every repeat returns the identical answer, so
  counting repeats inflates the apparent sample without adding information.
  Determinism is decided on the **output** — one hash per task across its
  measured repeats — not on pass/fail agreement, which a sampling endpoint
  that fails every draw also produces; the latter is reported separately as
  `repeats_agreed`. When the lane is deterministic the unit is the task:
  `effective_k`/`effective_n` are counts of tasks observed and passed, never a
  ratio rounded back into a count (that printed 8/9 for seven passes).
  Errored and cut attempts do not vote. Both tools vote on determinism per
  `(case, variant)`, because two phrasings are two prompts. With
  `--prompt-variants`, though, the effective sample counts a case once: its
  phrasings are correlated draws of that case, and it is observed through the
  prompt as written.
- **Two candidates are compared pairwise, not by interval overlap.** They
  answered the same cases, so the question is which cases flipped and in which
  direction — see `bench_compare` above. An aggregate interval is still printed
  beside every score, and it is wide: `3/3` is [44 %, 100 %].
  On a sampling lane the repeats of one case are correlated. When they
  disagree, `bench_compare` prints a **case-clustered** interval beside the
  score: CR1 cluster-robust standard error, design effect, then Wilson on the
  effective n. tools-r3's 98/124 gives [71–85 %] naive and [66–88 %] clustered,
  design effect 2.6. On a sampling lane run with `--repeats` above 1 it also
  prints **pass^k**, with k the repeats: the chance a case passes all k of its
  draws (the unbiased C(p,k)/C(m,k) estimator). The paraphrases of
  `--prompt-variants` are not draws. "Passes 79 % of draws" and "passes every
  time" are different promises.
- **A case the control endpoint also fails is suspect, not evidence.** It leaves
  every other candidate's score. Configure a `control` backend or the mechanism
  simply does not run.
- **Median, min, max and stdev** accompany every total, because a mean over a
  cold first run and two warm ones describes neither — and the wall statistics
  cover the *measured* attempts only, with the rest reported as
  `unmeasured_wall_s`.
- **Ranking rows are tiered.** Adjacent rows the paired sign test cannot
  separate (`bench_stats.tiers`) are printed in one tier, with a rule between
  tiers; the order inside a tier is not an ordering the data supports. Read the
  ranking with the intervals, not as a league table.

### Is this GGUF even sane? (`inspect_gguf.py`)

```bash
python3 inspect_gguf.py model.gguf            # human-readable
python3 inspect_gguf.py --json model.gguf     # machine-readable; exit 1 if RISKY
```

Reads only the file header (instant on a 16 GB model) and prints the
architecture, imatrix metadata and the **tensor quantisation histogram**. That
histogram is what diagnosed a real failure no benchmark could have caught:
files dominated by sub-4-bit i-quants (`IQ3_S`, `IQ3_XXS`, `IQ2_*`, `IQ1_*`)
produced fluent garbage on GenieX — v0.5.0 (llama.cpp `873e5d8`) and v0.6.1
(`0eadefe`) alike, so it is these kernels rather than one build — while plain
K-quants at the same bit
width (`Q3_K_M`) and `IQ4_XS` were fine. Verdicts:

| Verdict | Meaning |
|---|---|
| `OK` | no sub-4-bit i-quant tensors |
| `LIKELY OK` | under 5 % of them (a known-good file had 4 tensors) |
| `RISKY` | i-quant-dominated — exit code 1 |

**`RISKY` describes GenieX ≤ v0.6.1.** On v0.7.0 (llama.cpp `4ff829e`) the same
`IQ3_XXS` file answers the same kind of trivial questions correctly
([`docs/geniex-v0.7.0-cpu-npu-2026-09-24.md`](docs/geniex-v0.7.0-cpu-npu-2026-09-24.md)),
at about half the CPU decode rate of `Q4_0`. Treat the verdict as "check the
runtime's llama.cpp hash, then run the correctness gate".

### The NAS census (`nas_census.py`)

Day 1 of [`docs/nas-document-ai.md`](docs/nas-document-ai.md) § 6:
before any model is chosen, what is actually on the NAS? It walks a tree and
publishes **the four numbers** — total PDF pages, scanned fraction, German
fraction, table density — plus the gate: scanned+image-only under ~10 % of
classified pages means the VLM is a footnote and the budget belongs to
extraction + embeddings + retrieval.

```bash
# from the repo root
python3 benchmarks/nas_census.py /mnt/nas                                # summary only
python3 benchmarks/nas_census.py /mnt/nas --output census.json --tables  # JSON archive + table density
```

Stdlib-only, with one optional dependency: `pip install pymupdf` enables PDF
page classification (born-digital / degenerate-layer / image-only / sparse).
Without it the extension census still runs and page classification is
reported as **SKIPPED** — visibly, in the summary and the JSON, never as a
fabricated zero scanned pages. Likewise table density says `not measured`
until `--tables` asks for it, PDFs over `--page-sample` pages (default 40)
are sampled evenly with the extrapolation announced, and `--max-files`
truncation is loud. Walks are sorted, so two runs over the same tree diff
cleanly. The suite is [`tests/test_nas_census.py`](tests/test_nas_census.py),
which runs with the rest of `benchmarks/tests` and needs neither PyMuPDF nor a
network.

### Backends

Endpoints are named in the hub's `linux/llm-stack/backends.json`, so neither
the Ollama service nor a Snapdragon lane has to be addressed by a URL typed from
memory:

```bash
uv run orchestrant-bench speed --list-backends

uv run orchestrant-bench speed --backend ollama      --stream   # the hub's Ollama service
uv run orchestrant-bench speed --backend geniex-npu  --stream   # NPU lane
uv run orchestrant-bench lanes --lanes geniex-npu geniex-cpu    # both at once
BENCH_BACKEND=geniex-cpu bash benchmarks/run_benchmarks.sh      # whole sweep, from the repo root
```

`ollama` is the default — it is the reference server the hub's serving stack
brings up; how to start it is documented there, not here (see
[`linux/llm-stack/README.md`](../third_party/ANTfrastructure/linux/llm-stack/README.md)).
A backend entry may pin a default model, which is why `--backend geniex-npu`
needs no `--model`.

Resolution order, most specific first:

| # | Source | Notes |
|---|---|---|
| 1 | `--base-url` | explicit, wins over everything |
| 2 | `LLM_BASE_URL` / `OLLAMA_BASE_URL` | env beats `--backend` on purpose, so a wrapper script that exports it is not overridden by a stale config default |
| 3 | `--backend <name>` | from `backends.json` |
| 4 | the entry marked `default` | `ollama` |

An unknown name **fails loudly and lists the known ones** rather than falling
back to the default — silently benchmarking the wrong machine is the expensive
failure here. A missing or malformed `backends.json` never blocks an explicit
URL.

### Backend support — what is verified, and what is not

| Backend | Status |
|---|---|
| **GenieX** (Snapdragon NPU / GPU / CPU lanes) | verified end to end on real hardware |
| **Ollama** | stub tests for the dialect differences, plus `tests/unit/benchmark/test_harness_against_ollama.py` which runs the harness against a **live** server — it skips locally when none is up, and CI starts a digest-pinned `ollama/ollama` service, so that is where it is confirmed |

The Ollama dialect differs from GenieX in three ways that this harness had to
learn, each with a test in `tests/unit/benchmark/test_backend_compat.py`: Ollama sends
`data: ` **with** the space (GenieX omits it), it offers `/api/tags` when
`/v1/models` is unavailable, and existing scripts set `OLLAMA_BASE_URL`. Those
paths are exercised, but a stub is not a server — run one command against your
real instance before trusting a long sweep:

```bash
LLM_BASE_URL=http://your-ollama:11434 uv run orchestrant-bench speed \
    --prompts 1 --stream --correctness-only
```

### Pointing the harness at something other than Ollama

Set `LLM_BASE_URL` (the old `OLLAMA_BASE_URL` still works). Model detection
asks the portable `/v1/models` first and only then falls back to Ollama's
native `/api/tags`, so GenieX, llama.cpp and vLLM endpoints work unchanged.

### 2. Run the viewer (Reflex)

The viewer is a Reflex app in OrchestrANT's [`frontend/`](../frontend) (the
`frontend` extra). It reads the manifest the runner writes directly, so there is
no build or copy step:

```bash
cd frontend
reflex run
```

Default manifest: `benchmarks/benchmark_results/_manifest.json`, relative to the
repository root. Point it at a run-scoped directory — the one `run_benchmarks.sh`
prints at the end — with the override:

```bash
ORCHESTRANT_BENCHMARK_MANIFEST=benchmarks/benchmark_results/<run>/_manifest.json reflex run
```

A relative path is read from the repository root; one that exists from the
working directory (`../benchmarks/...` typed in `frontend/`) still wins. Before
2026-09-24 the viewer read it from `frontend/`, so the default and every
documented relative path failed to load.

**What the viewer shows.** A **correctness banner** sits above every speed
number — a broken model is fast, so "is it working?" has to outrank "how
quickly?". Below it the comparison table leads with **time to a finished
answer** (the metric to rank by), then TTFT, decode rate, overall tok/s and the
share of output spent thinking. Those speed columns and their charts read the
`speed` block that `report manifest` writes per run — the runner's own
summary, not a second average. A manifest built before 2026-09-24 shows "-"
there, and empty charts, until it is rebuilt. Drilling into a run adds
per-prompt prefill speed and the process that actually burned CPU. The table
and interval logic is plain Python in `frontend/frontend/benchmark_data.py`
and `lab_data.py`, tested without Reflex;
`tests/unit/frontend/test_page_compiles.py` builds and dry-run compiles the
page where Reflex is installed, which CI's viewer job does.

**The lab's per-run fields (2026-09-24).** Three cards follow the comparison
table.

- **Answers, load and energy**, one row per speed run:
  - answered k/n, orange when a reply was cut at `max_tokens`;
  - time to the first answer token, averaged over the answered replies only;
  - thinking share;
  - the lane's cores;
  - other load as mean (max), starred where it was derived (cpu_percent ×
    threads − lane cores) for a report older than the field;
  - CPU-rail J/token, gross and net, as a ratio of sums, and mean watts;
  - whether the net figure can be read: *steady*, *DRIFTED x W* (read gross),
    *unknown* (one idle baseline, or an older report), *gross only* (no idle
    baseline was taken, `--idle-seconds 0`) or *no meter*. The rails are the
    CPU clusters only, so an NPU lane's own draw is not in them.
- **Serving runtime**, one row per run: lane, model, the endpoint whose build
  is recorded (a lanes report names only its first lane's), the GenieX CLI,
  QAIRT and llama.cpp versions, whether they were read from the lane process
  or only from the installed binary (a WSL2 client cannot see a Windows lane),
  and the serve flags without `--host`.
- **Server contract**: every `orchestrant-bench contract` report in the
  directory as one grid of checks against runs, grouped by lane, oldest run
  first (by when each report was written, not by file name). An answer that
  differs from the same lane's previous answer to that check is highlighted:
  what `contract --diff` calls CHANGED, except that a check one of the two runs
  never asked shows `-` and is not highlighted (`--diff` prints `- -> yes` as
  CHANGED). Hover a cell for its evidence.

The Hardware card shows the first report's `hardware` block; a report's
provenance is used only when no report has one. Drilling into a run adds first
answer, lane and other cores, and J/token to each prompt, and shows `cut` for a
reply stopped at `max_tokens`. To see a run directory that was not written by
`run_benchmarks.sh`, index it first:

```bash
uv run orchestrant-bench report manifest benchmarks/benchmark_results/<run> \
    benchmarks/benchmark_results/<run>/_manifest.json --title "<run>"
ORCHESTRANT_BENCHMARK_MANIFEST=benchmarks/benchmark_results/<run>/_manifest.json reflex run
```

Older result files predate these metrics. They render `-` and are dropped from
the charts rather than being drawn as `0`, which would claim an instant first
token.

### Adding new configs

Edit the `CONFIGS` array in `run_benchmarks.sh` and re-run. Each config is a
`num_ctx:max_tokens` pair. The manifest regenerates automatically, and the
viewer picks up all configs — restart `reflex run` to see the new run.

## The serving stack

The server these benchmarks point at is not built here. Its images, services,
GPU overlay, model management and multi-arch support are documented once, in
ANTfrastructure:
[`linux/llm-stack/README.md`](../third_party/ANTfrastructure/linux/llm-stack/README.md).
