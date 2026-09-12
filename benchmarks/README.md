> The serving stack (Ollama + Open WebUI compose) is owned by
> ANTfrastructure's `linux/llm-stack/`; this page documents the benchmark
> lab that runs against it. The runner half is the `orchestrant.benchmark`
> package (`orchestrant-bench`).

# LLM Stack

Ollama + Open WebUI for serving LLMs with an OpenAI-compatible API.
Designed for integration with Nextcloud Assistant.

## Quick start

```bash
# 1. Set the required Open WebUI secret (compose refuses to start without it).
#    The .env must sit next to the compose file so compose picks it up.
cp linux/llm-stack/.env.example linux/llm-stack/.env
# then edit benchmarks/.env and set WEBUI_SECRET_KEY, e.g.:
#    printf 'WEBUI_SECRET_KEY=%s\n' "$(openssl rand -hex 32)" > benchmarks/.env

# 2. Pull images and start all services (auto-pulls gemma4:26b on first start)
nerdctl compose -f linux/llm-stack/docker-compose.yml pull
nerdctl compose -f linux/llm-stack/docker-compose.yml up -d
```

First start downloads the model (~17GB for `gemma4:26b`) — this takes a while.
Watch progress:

```bash
nerdctl compose -f linux/llm-stack/docker-compose.yml logs -f
```

## GPU mode (NVIDIA)

The default stack is CPU-only. To run the Ollama service on all NVIDIA GPUs,
use the GPU override file:

```bash
# 1. On the host, install the NVIDIA container toolkit ONCE:
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# 2. Start the stack with the GPU override:
docker compose -f linux/llm-stack/docker-compose.yml -f linux/llm-stack/docker-compose.gpu.yml up -d
```

The override is a compose overlay — `docker-compose.yml` stays CPU-only. It
grants the ollama service all NVIDIA GPUs (`deploy.resources.reservations
.devices`) and raises the default context window via `OLLAMA_CONTEXT_LENGTH`.
Verify GPU placement:

```bash
docker exec llm-stack-ollama-1 ollama ps   # PROCESSOR column = 100% GPU
```

## VRAM & context sizing

The context length Ollama lists for a model is its **maximum supported**
window, not what fits your VRAM. Ollama loads as many layers as fit on GPU; the
rest spill to CPU/RAM and crater throughput. Size `num_ctx` to the VRAM free
*after* the weights. Rule of thumb at q8_0 KV: a Qwen3-class 30B A3B model uses
~104 KB of KV per context token.

| Total GPU VRAM | `qwen3-coder:30b` (Q4_K_M, ~19 GB) | Reasonable context (q8_0 KV) |
|----------------|------------------------------------|------------------------------|
| 24 GB          | fits, ~5 GB left                   | ~32K |
| 28 GB (e.g. 2× 12+16 GB) | fits, ~9 GB left          | ~64K |
| 48 GB          | fits, ~29 GB left                  | ~256K (model max) |

`qwen3-coder:30b` advertises 256K, but that needs ~27 GB of KV cache alone —
i.e. >45 GB total VRAM alongside the 19 GB of weights. On a 28 GB stack, 64K
is the realistic ceiling; a host with 48 GB can run the full 256K.

## Services

| Service | Port | URL | Purpose |
|---------|------|-----|---------|
| Ollama | 11434 | http://localhost:11434/v1 | OpenAI-compatible API |
| Open WebUI | 3000 | http://localhost:3000 | Chat UI for debugging |
| Glances | 61208 | http://localhost:61208 | System monitoring dashboard |
| Benchmark Viewer | 4173 | http://localhost:4173 | Interactive benchmark charts (profile `viewer`) |

### Benchmark Viewer

The `benchmark-viewer` is a standalone nginx container (not part of the compose
stack). Start it after building the viewer app.

## Nextcloud Assistant configuration

Settings → AI → OpenAI-compatible endpoint:
- **URL**: `http://localhost:11434/v1`
- **API key**: *(leave blank)*
- **Model**: `gemma4:26b`

## Managing models

```bash
# Pull additional models
nerdctl compose -f linux/llm-stack/docker-compose.yml exec ollama ollama pull qwen2.5-coder:7b

# List pulled models
nerdctl compose -f linux/llm-stack/docker-compose.yml exec ollama ollama list

# Remove a model
nerdctl compose -f linux/llm-stack/docker-compose.yml exec ollama ollama rm gemma4:26b
```

## Change default model

Edit the `ollama pull` line in the `command` block in `docker-compose.yml`, then restart:

```bash
nerdctl compose -f linux/llm-stack/docker-compose.yml up -d
```

## Network access (Windows firewall)

To access services from other devices on your network, open the required ports in Windows Firewall:

```pwsh
New-NetFirewallRule -DisplayName "Allow OpenWebUI Port 3000" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 3000
New-NetFirewallRule -DisplayName "Allow Ollama API Port 11434" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 11434
New-NetFirewallRule -DisplayName "Allow Glances Port 61208" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 61208
```

## Standalone run (without compose)

```bash
nerdctl run -d --name llm-stack -p 11434:8080 \
  -v ollama-models:/root/.ollama \
  -e OLLAMA_HOST=0.0.0.0:8080 \
  ollama/ollama:latest
```

## Benchmarking

The stack includes an automated benchmark suite and an interactive React viewer.

### 1. Run benchmarks

```bash
cd linux/llm-stack
bash run_benchmarks.sh
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
[`docs/geniex-local-ai-setup.md`](../../docs/geniex-local-ai-setup.md)).

```bash
# quick health check on its own — exits non-zero if any answer is wrong
python3 benchmark_openai_api.py --correctness-only

# or alongside a normal run, recorded into the result JSON
python3 benchmark_openai_api.py --stream --correctness --output result.json
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
  a 4B-Instruct at 19.5 tok/s answered in 26.8 s. **Rank by time to answer.**

`tokens_per_sec` divides by the whole request and therefore mixes prefill with
decode; `decode_tok_per_sec` reports decode alone.

The summary also names the **busiest process** during the run. On some stacks
the process owning the serving port is not the one doing the work (GenieX
spawns a separate worker: the port owner read 11 % of 800 % while the worker
sat at 752 %), so the report says which PID actually burned the CPU.

### Concurrency: does one server batch? do lanes add up? (`bench_lanes.py`)

```bash
# Does ONE server overlap two concurrent requests?
python3 bench_lanes.py --batching --endpoint http://127.0.0.1:11434 --model llama3

# Do SEVERAL servers add up, or fight each other?
python3 bench_lanes.py --lanes geniex-npu geniex-cpu --output lanes.json
```

`--batching` fires two simultaneous requests at one endpoint. If the second
one's first token arrives only after the first has finished, the server
serialises — **more throughput then needs more servers, not more clients**.
Measured on GenieX twice, on different prompts and both `SERIALISED`: the
second request waited out the first exactly (27.6 s in one run, 74.27 s against
a 74.10 s first request in a longer one).

`--lanes` measures each endpoint alone, then all of them at once, and reports
the per-lane change plus the aggregate. Compute units differ sharply — the NPU
lane is essentially immune to contention while a CPU and a GPU lane fight over
the same cores. The measured matrix is not restated here; it lives in
[`docs/geniex-local-ai-setup.md`](../../docs/geniex-local-ai-setup.md) § 2.
Aggregate throughput only appears if you really have that many concurrent
requests — one agent waiting for one answer still sees a single lane's speed.

`--output` writes the shared report envelope (`benchmark: bench_lanes`), so
`bench_report` labels the run correctly and `bench_compare` reads it as
throughput. None of its rows carries `passed`/`total`: a lane result is not a
score, and for a while the manifest rendered it as `/ = 0 %`.

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
(`python`/`bash`/`cmake`/`dockerfile`) and a `kind` (`spec-transcription`/
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
Dockerfile tasks) or **`all`, which is now the default**. Run `classic` against
`novel` and compare — a model much stronger on the first is recalling rather
than reasoning. Measured: the QAIRT 4B-Instruct scores 3/3 classic and 2/3
novel.

**Non-Python tasks are executed, not eyeballed.** bash runs under
`bash -euo pipefail` with an assertion prelude and is additionally linted with
`shellcheck -S error`; CMake runs under `cmake -P`; a Dockerfile is linted with
`hadolint --failure-threshold error` and then parsed into its instruction list
and asserted over. **A language whose tool is not installed produces a visible
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

Excluded attempts are listed, counted separately in the report
(`truncated`, `abandoned`, `overflow`, `errored`, `skipped`) and their seconds
are reported as **`unmeasured_wall_s`** — a 1800 s abandoned attempt used to
decide the very rank tie-break it was excluded from.

**`--repeats N` — because a single run measures one draw, not the model.**
The llama.cpp lanes sample even at `temperature=0`: five identical requests to
one 2B produced five different answers, four passing the same task and one
failing it. That model scored 2/3 in one sweep and 0/3 in the next; over 9
attempts its real rate is 44 %. The QAIRT/NPU path *is* deterministic (four
requests, one unique output), so repeats there only cost time. (GenieX v0.6.1
does honour `max_tokens` — measured 2026-09-05, § 1n of the GenieX page — it is
only `temperature` that it still ignores.)

**`--context-tokens N` — because ~40-token prompts are not what an agent
sends.** Prepends real repository source before each task. Prefill and any hard
context ceiling only appear under a realistic prompt: on one NPU bundle accuracy
fell from 3/3 to 2/3 once 1000 tokens of context were added, and past its
4096-token limit the lane now answers HTTP 400 `context_length_exceeded`, which
is where the `OVERFLOW` state comes from.

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

**The grader checks itself before it checks a model.** Every task carries a
`reference` solution, and each one is run through the *real* grading path at
the start of every invocation; a failure aborts the run with
`GRADER SELF-CHECK FAILED` naming the task, before any endpoint is contacted.
Without it, a host where the sandbox does not work scores every model
identically with the same stderr — indistinguishable from "the models are
bad", which this suite has already been fooled by once. The result, the
sandbox limits and which of `bash`/`shellcheck`/`cmake`/`hadolint` were found
are recorded in the report as `grader_selfcheck`.

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
| `--prompt-variants` | Also ask each case in its paraphrases. A score that swings on wording is fragile in a way one phrasing hides |
| `--accept-text-json` | Count a call the model wrote as prose. Measures what an agent-side fallback parser would recover; threaded into the multi-turn graders too, so a follow-up written as text is neither a false PASS nor a false FAIL |
| `--context-tokens N` | Prepend repository source to every single-turn case. Long context and tool calling were only ever measured apart; together is what an agent turn is. The padding excludes `bench_tools.py` itself — it used to prepend the case table, answers included |
| `--turn-growth` | Instead of the case suite, grow an agent loop turn by turn until the context runs out, and report where |
| `--system FILE` | Prepend a system prompt to every case. Agents that cannot override a runtime's built-in tool *descriptions* can still disambiguate this way — measure whether it helps before shipping it |

Determinism here is decided on the **output**, per `(case, variant)`: identical
message hashes across the measured repeats. `repeats_agreed` is the weaker
"same verdict, different text" signal and is reported separately, because a
sampling endpoint that fails every draw also produces it.

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
```

`--model` takes an **opencode** `<provider>/<model>` id, so the provider key
must exist in your `opencode.jsonc` — see
[`docs/geniex-local-ai-setup.md`](../../docs/geniex-local-ai-setup.md) § Step 3.
Point it at a GGUF lane: the QAIRT bundle's compiled 4096-token context is less
than opencode's own preamble, so it fails every task before reading one (§ 1m).

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

What the scoring does that a naive pass count does not:

- **The verdicts refuse the cheap fakes, and each refusal is pinned by a test.**
  Editing, deleting or adding a test file fails `fix_failing_test` outright
  ("tests were modified"). `add_function_and_test` runs the agent's own tests
  against four mutants of the required function and requires each to be caught,
  so a passing clamp with `assert True` beside it does not count. A rename is
  decided on the **syntax tree** — a name, an attribute, a def, an `import … as`
  alias or a string constant — so a comment mentioning the old name is not a
  failure and an alias is.
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
  `bench_stats.format_score`. It is a bare fraction, not an interval: unlike the
  other tools, this one does not yet publish a confidence interval, and `3/3`
  should be read as the [44 %, 100 %] it is.
- **The run is reproducible from the report.** Provenance records the resolved
  provider `base_url`, the opencode version, the path and SHA-256 of the
  opencode config, the disabled tools and the instructions; `--keep-output`
  stores each workspace's `git diff` (first 20 kB). opencode's data directory is
  redirected to a per-run scratch dir and removed unless `--keep`, so runs stop
  leaking sessions into `~/.local/share/opencode`.

Expect **minutes per task** on this hardware. That is prefill cost, not model
quality — see `docs/geniex-local-ai-setup.md` § 1m.

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
| `--tools a,b,c` | `speed,coding,tools` | Any of `speed`, `coding`, `tools`, `agent`, `lanes` |
| `--repeats N` | `1` | Passed to `bench_coding` / `bench_tools` |
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

### Did anything regress? (`bench_compare.py`)

```bash
python3 bench_compare.py old.json new.json
python3 bench_compare.py --baseline geniex-npu new.json     # against a stored one
python3 bench_compare.py --save-baseline geniex-npu new.json
python3 bench_compare.py --dir results/prev results/now     # every shared report
```

Baselines live in `baselines/<name>.json`. Three things it refuses to do,
because each is a way to be confidently wrong:

- **Call a difference a regression the sample cannot support.** Both candidates
  answered the *same* cases, so the aggregate is judged by an exact two-sided
  **paired sign test** over the cases that disagreed, plus a Newcombe interval
  on the difference. Unpaired interval overlap survives only as the fallback for
  reports with no per-case detail, and the finding says so. The practical floor
  is **six cases flipping the same way with none flipping back**, and that floor
  does not depend on suite size — where the old unpaired rule needed 119 cases
  to separate 93 % from 81 %.
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
as timing), and a lane that stops overlapping concurrent requests is a
regression in its own right.

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

The `config` block records what would change a score, so two runs can be told
apart: for `bench_coding` and `bench_tools` that includes
`config.backend_entry` — the merged `request_extra`, the header **names** and
the api-key **variable** name, never a value, because reports are committed. A
report written before that key existed will therefore be reported as
`! config.backend_entry changed` on its first comparison, which is correct: the
runs are not like-for-like if one of them was sending an `Authorization`
header.

`bench_coding` and `bench_tools` now send a **determinism probe** — two
identical eight-token requests to the lane provenance names — and record
`temperature`, `seed` and `determinism_probe` beside the run. `bench_compare`
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
  endpoint (the QAIRT/NPU path) every repeat returns the identical answer, so
  counting repeats inflates the apparent sample without adding information.
  Determinism is decided on the **output** — one hash per task across its
  measured repeats — not on pass/fail agreement, which a sampling endpoint
  that fails every draw also produces; the latter is reported separately as
  `repeats_agreed`. When the lane is deterministic the unit is the task:
  `effective_k`/`effective_n` are counts of tasks observed and passed, never a
  ratio rounded back into a count (that printed 8/9 for seven passes).
  Errored and cut attempts do not vote. `bench_tools` keys all of this per
  `(case, variant)`, so paraphrases do not collapse onto a case name.
- **Two candidates are compared pairwise, not by interval overlap.** They
  answered the same cases, so the question is which cases flipped and in which
  direction — see `bench_compare` above. An aggregate interval is still printed
  beside every score, and it is wide: `3/3` is [44 %, 100 %].
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

### The NAS census (`nas_census.py`)

Day 1 of [`docs/nas-document-ai.md`](../../docs/nas-document-ai.md) § 6:
before any model is chosen, what is actually on the NAS? It walks a tree and
publishes **the four numbers** — total PDF pages, scanned fraction, German
fraction, table density — plus the gate: scanned+image-only under ~10 % of
classified pages means the VLM is a footnote and the budget belongs to
extraction + embeddings + retrieval.

```bash
python3 nas_census.py /mnt/nas                                   # summary only
python3 nas_census.py /mnt/nas --output census.json --tables     # JSON archive + table density
```

Stdlib-only, with one optional dependency: `pip install pymupdf` enables PDF
page classification (born-digital / degenerate-layer / image-only / sparse).
Without it the extension census still runs and page classification is
reported as **SKIPPED** — visibly, in the summary and the JSON, never as a
fabricated zero scanned pages. Likewise table density says `not measured`
until `--tables` asks for it, PDFs over `--page-sample` pages (default 40)
are sampled evenly with the extrapolation announced, and `--max-files`
truncation is loud. Walks are sorted, so two runs over the same tree diff
cleanly.

### Backends

Endpoints are named in `backends.json`, so neither the Ollama service nor a
Snapdragon lane has to be addressed by a URL typed from memory:

```bash
python3 benchmark_openai_api.py --list-backends

python3 benchmark_openai_api.py --backend ollama      --stream   # this stack
python3 benchmark_openai_api.py --backend geniex-npu  --stream   # NPU lane
python3 bench_lanes.py --lanes geniex-npu geniex-cpu             # both at once
BENCH_BACKEND=geniex-cpu bash run_benchmarks.sh                  # whole sweep
```

`ollama` is the default — it is the service `docker-compose.yml` brings up. A
backend entry may pin a default model, which is why `--backend geniex-npu`
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
| **Ollama** | stub tests for the dialect differences, plus `tests/test_harness_against_ollama.py` which runs the harness against a **live** server — it skips locally when none is up, and CI starts a digest-pinned `ollama/ollama` service, so that is where it is confirmed |

The Ollama dialect differs from GenieX in three ways that this harness had to
learn, each with a test in `tests/test_backend_compat.py`: Ollama sends
`data: ` **with** the space (GenieX omits it), it offers `/api/tags` when
`/v1/models` is unavailable, and existing scripts set `OLLAMA_BASE_URL`. Those
paths are exercised, but a stub is not a server — run one command against your
real instance before trusting a long sweep:

```bash
LLM_BASE_URL=http://your-ollama:11434 python3 benchmark_openai_api.py \
    --prompts 1 --stream --correctness-only
```

### Pointing the harness at something other than Ollama

Set `LLM_BASE_URL` (the old `OLLAMA_BASE_URL` still works). Model detection
asks the portable `/v1/models` first and only then falls back to Ollama's
native `/api/tags`, so GenieX, llama.cpp and vLLM endpoints work unchanged.

### 2. Build the viewer

```bash
cd benchmarks/benchmark-viewer
bash build-viewer.sh
```

Builds the React + Recharts app using a Node 20 container (no host Node needed).
It copies every `*.json` from `benchmark_results/` **with its run
subdirectory**, then promotes the newest `_manifest.json` it finds in the source
tree to the one fixed path the app fetches. That is what reconnects the viewer
to run-scoped output; before it, `build-viewer.sh` copied a flat directory that
`run_benchmarks.sh` had stopped writing.

```bash
bash build-viewer.sh --copy-only SRC DST   # just the copy step, no container
```

**What the viewer shows.** A **correctness banner** sits above every speed
number — a broken model is fast, so "is it working?" has to outrank "how
quickly?". Below it the comparison table leads with **time to a finished
answer** (the metric to rank by), then TTFT, decode rate, overall tok/s and the
share of output spent thinking. Drilling into a run adds per-prompt prefill
speed and the process that actually burned CPU.

Older result files predate these metrics. They render `-` and are dropped from
the charts rather than being drawn as `0`, which would claim an instant first
token.

**Smoke-render check** (needs `npm install` in `benchmark-viewer` once):

```bash
cd benchmarks/benchmark-viewer && npm run smoke
```

`vite build` only proves the JSX compiles. This renders every component
server-side against the real manifest — including legacy runs — and asserts the
new numbers reach the DOM. It exists because both failure modes it checks for
actually happened while these metrics were added: a component that throws only
at render time, and an edit that silently failed to apply so the table rendered
empty cells.

### 3. View results

```bash
# Start the viewer (nginx container, available at http://localhost:4173)
bash benchmarks/serve-viewer.sh

# Stop it when done
nerdctl stop llm-benchmark-viewer
```

The viewer shows hardware info, a config comparison table, bar charts for T/s /
latency / CPU / RAM, and an expandable per-prompt drill-down for each config.

### Adding new configs

Edit the `CONFIGS` array in `run_benchmarks.sh` and re-run. Each config is a
`num_ctx:max_tokens` pair. The manifest regenerates automatically, and the
viewer picks up all configs — rebuild the viewer (`build-viewer.sh`) to deploy
updates.

## Architecture notes

- Standalone subproject (not part of the cross-build chain)
- The compose stack pulls the official `ollama/ollama` image. A separate custom
  `Dockerfile` + `scripts/download-ollama.sh` also exist for an offline / pre-baked
  binary lane (bake the tarball with `bash linux/llm-stack/scripts/download-ollama.sh`,
  then `nerdctl build linux/llm-stack`); compose does **not** use that image.
- Model auto-pulled on container startup via compose `command` override
- Multi-arch: amd64, arm64 (riscv64 unsupported — Ollama does not ship riscv64 binaries)
- CPU-only by default; an optional GPU override grants the Ollama service all
  NVIDIA GPUs (see § GPU mode)
- Models persist in Docker volumes across restarts
