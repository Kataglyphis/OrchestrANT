<!--
Copyright (c) 2025 Kataglyphis
SPDX-License-Identifier: MIT
-->

# The roadmap campaign of 2026-09-24/25 — the open measurements, taken

Taken 2026-09-24/25 on the lab host (Snapdragon X X126100, 8× Oryon, one Hexagon
HTP v73, Adreno X1-45, 32 GB, Windows 11) — the Qwen3-8B reports on the 24th
(written 11:48–14:27 UTC), the chain from 14:52 UTC that day to 06:04 UTC the
next, the addenda until 06:36 UTC — on **GenieX v0.7.0** (QAIRT 2.45, llama.cpp
`4ff829e`), its NPU, CPU and, for the first time on this build, GPU lane, and on
**Ollama 0.34.4**, native Windows ARM64, serving byte-identical GGUF files. The
raw reports are in
[`../benchmark_results/2026-09-24-roadmap/`](../benchmark_results/2026-09-24-roadmap/)
(one `.json` and one `.log` per step; `lab-steps.log` lists the chain's 57 steps
in order, `lab-addenda-steps.log` the 6 that followed) and
[`../benchmark_results/2026-09-24-upgrade-check-v070/`](../benchmark_results/2026-09-24-upgrade-check-v070/MANIFEST.md)
(P7.5's first live run). The chain ran from a lab worktree pinned at `3260e1e`;
the three Qwen3-8B reports (`npu-qwen3-8b-*`) were taken earlier the same day
at `1dcb81c`. Comparisons with the previous round use
[`../benchmark_results/2026-09-23-geniex-upgrade/`](../benchmark_results/2026-09-23-geniex-upgrade/),
written up in [`geniex-v0.7.0-cpu-npu-2026-09-24.md`](geniex-v0.7.0-cpu-npu-2026-09-24.md).

Every number on this page was recomputed from those files by one script, under
the lab's own definitions ([§ How the numbers were computed](#how-the-numbers-were-computed)).
Where a data commit said something else, [§ What the commit messages said](#what-the-commit-messages-said)
names it.

**What it changes.** The recommendation holds for chat on inputs that fit the
bundle's 4096-token context (a ~3.1k-token document did, a ~7k-token one did
not), for coding, and for agent work. For **tool calling it now depends
on one measurement nobody has taken**: the GGUF build of the recommended model,
on the CPU lane, passes 41 of 42 tool cases where the NPU bundle passes 33 of 41
— seven cases one way, none back (p = 0.016) — and neither run used
`prompts/tool-disambiguation.md`, which the recommendation includes and which
was written for exactly the NPU's misses ([§ The recommendation](#the-recommendation)).

| Item | What was asked | What the campaign answered |
|---|---|---|
| [P4.1](#p41--qwen3-8b-on-the-npu-lane) | Does Qwen3-8B on the NPU change the answer? | No, and no longer conditionally: 0.61× the 4B's decode, 12–60 s of thinking before an answer, 30 of 33 coding replies cut at 3000 tokens with no room for more |
| [P7.2](#p72--prefill-and-decode-at-agent-sizes) | Prefill and decode at agent sizes | An appended turn is cached, a new tail is not; the 9B keeps 62–67 tok/s prefill to 10.7k tokens and ~9 tok/s decode at 7.2k, where both 4B GGUFs decode at ~3 |
| [P7.3](#p73--one-model-on-both-runtimes) | One model on both runtimes | The lane moves the tool score by 17 points and the coding score by nothing |
| [P4.2](#p42--qwen25-coder-7b) | A code-specialised model | Ties the 4B-Instruct on 39 tasks × 3, terser |
| [P4.3](#p43--a-second-family-llama-32-3b-and-phi-4-mini) | A second family on the CPU lane | Llama-3.2-3B and Phi-4-mini are below the Qwen 4B-Instruct on every capability axis |
| [CV-4](#cv-4--the-thinking-qwen3-4b-on-v070) | The thinking 4B on v0.7.0 | Tools as good as the instruct build at 2.5× the time; ungradeable on coding at 3000 tokens |
| [P3.2, P3.3](#p32-and-p33--the-opencode-preamble-and-a-loop-that-grows) | Tools behind the opencode preamble; turn growth | 27/34; 12 → 66 s per turn, then an HTTP 400 nobody can read |
| [R2, R7, R11](#r2-r7-r11--the-hubs-coding-tables-re-derived) | The hub's § 1i/§ 1n tables under the fixed grader | Re-derived: the NPU 4B is 15/26 on the Python tasks, 2/3 on the classic set |
| [P7.4](#p74--bench_agent-three-trials-per-task) | `bench_agent --repeats` | The 9B passes 11 of 15 trials; pass^3 60 % |
| [P7.5](#p75--the-upgrade-checks-first-live-run) | The upgrade check, live | Verdict OK, 8 steps, 76 min; the NPU lane reproduced to 0.04 % |
| [P7.6](#p76--powershell-tasks-and-the-medium-repository) | PowerShell tasks, the medium repo | Three of the six PowerShell tasks defeat every model in every draw; the 9B fixes the medium repo 1 time in 3 |
| [P7.7](#p77--bench_chat-on-both-lanes) | `bench_chat` on both lanes | Not separable where both answer; the NPU answers in a twentieth of the time and cannot take a 7k-token document |
| [P1.2](#p12--how-far-the-wording-moves-a-score) | Prompt-variant spread | 7 of 11 tool cases and 3 of 10 coding tasks change verdict with the wording |
| [Ollama](#ollama-and-geniex-on-the-same-files) | Ollama against GenieX on one file | With 8 threads the same decode, 2.2–3.0× slower prefill, 2.4–3.5 s before the first token of a short prompt |
| [GPU](#the-adreno-gpu-lane-and-the-npu-and-gpu-together) | The Adreno lane; NPU and GPU together | Twice the CPU lane's decode at 7.2k tokens; beside the NPU each lane loses 3–5 % |

## Protocol

| Lane | Port | Served by | Models measured |
|---|---|---|---|
| NPU | 18181 | GenieX v0.7.0, QAIRT 2.45 | `qualcomm/Qwen3-4B-Instruct-2507:W4A16`, `qualcomm/Qwen3-8B:W4A16` |
| CPU | 18184 | GenieX v0.7.0, llama.cpp `4ff829e` | `unsloth/Qwen3-4B-GGUF:Q4_0` (thinking), `unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_0`, `empero-ai/Qwen3.8-9B-Distill-GGUF:Q4_K_M`, `empero-ai/Qwen3.8-2B-Distill-GGUF:Q4_K_M`, `unsloth/Qwen3.8-27B-GGUF:Q4_0`, `unsloth/Qwen2.5-Coder-7B-Instruct-GGUF:Q4_K_M`, `unsloth/Llama-3.2-3B-Instruct-GGUF:Q4_K_M`, `unsloth/Phi-4-mini-instruct-GGUF:Q4_K_M` |
| GPU | 18182 | GenieX v0.7.0, Adreno X1-45 | the thinking and the instruct Qwen3-4B `Q4_0` |
| Ollama | 11434 | Ollama 0.34.4 (llama.cpp), 16k context | the thinking 4B, the instruct 4B and the 9B, from the same blobs (`ollama-identity.json`: every sha256 matches the GenieX cache) |

- **Lanes**: all 49 GenieX reports that record their serve flags (the 8B's
  WSL-side coding report does not) show `serve --compute {npu,cpu,gpu} --nctx
  16384 --keepalive 86400 --log none --skip-update`. The chain sent requests to
  one lane at a time, except in the NPU + GPU step, and stopped the lanes it no
  longer needed between phases (`lab-steps.log`). Host load at the start of the
  44 Windows-side reports: 0.07–1.17 other cores (above 0.9 only for the GPU
  contract and the thinking 4B's Ollama speed run).
- **Where**: `bench_coding` and `bench_agent` ran in WSL2 against the Windows
  lanes (their `host_load.other_cores` is null: WSL2 could not see the lane);
  everything else ran on the host.
- **Repeats are not the same thing on every server.** Every request sends
  `temperature: 0`, which GenieX reads as unset: the CPU and GPU lanes sample,
  so their `--repeats 3` are three draws; the NPU bundle's fixed seed makes its
  three draws byte-identical (the upgrade check's `bench_tools`:
  `deterministic: true`), one observation repeated. Ollama honours T=0 as
  greedy (its contract: `temperature0_is_greedy: yes`). A spacer request
  separates repeats everywhere.
- **No capability run used a system prompt** (`config.system_prompt: null` in
  every `bench_tools` report), so none measured the recommended configuration's
  `prompts/tool-disambiguation.md`; only `bench_agent`'s opencode config loaded
  it, as instructions.
- **Commands**: the chain logged step names, not argv. Each section's command is
  reconstructed from its reports' `config` and `provenance`, which record every
  flag that changes a number; the upgrade check logged its argv (`steps.jsonl`).
- **Definitions**: every speed figure is `orchestrant/benchmark/speed_summary.py`'s,
  pooled across requests — **decode** is the tokens after each first one over
  the seconds spent decoding them, **overall** the completion tokens over the
  summed request time; TTFT is a mean. "Answered", time to an answer and the
  thinking share (a mean over replies) are `answers.py`'s; lane cores and
  CPU-rail joules per token (a ratio of sums; the CPU clusters only — there is
  no NPU or GPU rail) are the speed runner's. The depth traces report their
  own 256-token windows. Intervals are 95 % Wilson; a case drawn more than once
  gets the case-clustered interval (`stats.clustered_rate`); two runs of one
  suite are compared case by case — the paired sign test over the cases that
  disagree, and the paired difference with its interval (`stats.paired_difference`).
  One run of one measurement has no interval, and the tables say "one run".

## P4.1 — Qwen3-8B on the NPU lane

**Run** (M1, at `1dcb81c`): `orchestrant-bench contract --backend geniex-npu
--model qualcomm/Qwen3-8B:W4A16 --overflow-tokens 6000`; `orchestrant-bench speed
… --stream --max-tokens 2048 --correctness`; `bench_coding.py --backend
geniex-npu --model qualcomm/Qwen3-8B:W4A16 --task-set all` (the default
3000-token budget; 33 tasks, before P7.6 added six). The 4B beside it is the
upgrade check's run of the same nine prompts.

| NPU lane | `Qwen3-8B` W4A16 | `Qwen3-4B-Instruct-2507` W4A16 |
|---|---|---|
| decode, pooled (9 prompts, up to 2048 tokens) | **12.5 tok/s** | 20.4 tok/s |
| answered inside 2048 tokens | 6/9 | 8/9 |
| first answer token, mean over the answered | **29.0 s** (12.1–60.4) | 0.14 s |
| output that was thinking (mean of replies) | 79 % | 0 % |
| CPU-rail energy, gross | 0.476 J/token | 0.102 J/token |
| correctness probe | 6/6 | 6/6 |
| cold prefill at ~1.8k tokens (contract) | 721 tok/s | 1028 tok/s |
| `temperature: 0` is greedy | **yes** | no |
| coding, 33 tasks, budget 3000 | 3 graded, 3 pass — **30 CUT** at exactly 3000 tokens | — |

**What it answers.** The 2026-09-04 verdict ("lost 26 of 27 tasks to
truncation") rested on a v0.5.0 serve default of 2048 tokens nobody recorded.
That is gone: every cut row stopped at exactly 3000 output tokens, the request's
own budget. With thinking on, 30 of the 8B's 33 replies ran past 3000 tokens,
and the bundle's 4096-token context, shared by prompt and reply (the hub page's
§ 1j and § 1n; a ~6000-token prompt is refused with `context_length_exceeded`
here), leaves at most 3578–4018 tokens for a reply to these 78–518-token
prompts. The roadmap's condition for
changing the answer — the 8B *matching the 4B's latency* while coding better —
cannot be met: it decodes at 0.61× the 4B's rate and thinks for 12–60 s before
its first answer token. **P4.1 is closed: it does not change the
recommendation, on v0.7.0 and under the fixed grader.**

**What it cannot say.** How well the 8B codes: three graded tasks are no
sample. The coding report predates the thinking-share fix, so 29 of its 30 cut
rows record a share of 0.0 that says nothing ([§ What the lab got wrong](#what-the-lab-got-wrong),
4). Thinking switched off (`/no_think`) was not tried; it could not repair the
decode rate. The speed run's other load was 1.69 cores on average (3.17 at
most); on the v0.7.0 page the NPU lane's decode did not move between 0.1 and
2.0 other cores.

## P7.2 — Prefill and decode at agent sizes

**Run**: `orchestrant-bench contract --backend geniex-cpu --model <model> --only
prefix_cache,prefix_cache_extend,prefix_cache_fork --prefix-tokens N` for N =
5000, 8000 and 12000 on the 9B, the thinking 4B and the 2B (steps 6–14); and a
depth trace — one streamed 1024-token reply to a request of ~7.2k tokens, its
decode rate per 256-token window, an 8k variant of the
[v070r2 depth-trace probe](../benchmark_results/2026-09-23-geniex-upgrade/v070r2-probes/README.md)
(steps 15–17 and 56; its script was not stored, and the files carry no
provenance block). One run per cell: no interval.

| CPU lane | prompt tokens | cold | cold tok/s | repeat | extend (+292–295 tok) | fork (new tail) |
|---|---|---|---|---|---|---|
| 9B-Distill `Q4_K_M` | 4487 | 72.7 s | 61.7 | 0.11 s | 5.25 s | 67.0 s |
| | 7175 | 107.7 s | 66.6 | 0.15 s | 6.14 s | 112.2 s |
| | 10744 | 174.0 s | 61.8 | 0.18 s | 7.25 s | 181.2 s |
| 4B `Q4_0` (thinking) | 4484 | 51.3 s | 87.5 | 0.23 s | 5.92 s | 52.9 s |
| | 7172 | 107.1 s | 67.0 | 0.31 s | 8.67 s | 113.9 s |
| | 10741 | 218.5 s | 49.2 | 0.47 s | 12.52 s | 229.6 s |
| 2B-Distill `Q4_K_M` | 4486 | 19.5 s | 229.9 | 0.05 s | 1.62 s | 19.2 s |
| | 7175 | 31.7 s | 226.7 | 0.07 s | 2.14 s | 31.9 s |
| | 10744 | 49.1 s | 218.7 | 0.06 s | 2.57 s | 51.9 s |

| ~7.2k-token prompt | prompt tokens | time to first token (prefill) | decode, window 1–257 → 769–1023 |
|---|---|---|---|
| CPU, 9B-Distill | 7159 | 108.4 s (66.1 tok/s) | 9.06 → 8.83 tok/s |
| CPU, 4B `Q4_0` thinking | 7154 | 106.0 s (67.5 tok/s) | 3.37 → 3.02 tok/s |
| CPU, 4B-Instruct `Q4_0` | 7154 | 105.6 s (67.8 tok/s) | 3.38 → 3.02 tok/s |
| GPU, 4B-Instruct `Q4_0` | 7154 | 168.4 s (42.5 tok/s) | 6.58 → 6.23 tok/s |

**What it answers.**

- **An appended turn is cached, a new tail never is.** A fork — the long
  prefix with a different tail — took 0.92–1.06× the cold time for every model
  and size: a full re-prefill. An extended conversation (+292–295 tokens) took
  1.6–12.5 s where the cold prefill of the same prefix took 19.5–218.5 s, and an
  identical repeat 0.05–0.47 s. An agent loop that only appends pays one cold
  prefill (72.7–174.0 s for the 9B at 4.5k–10.7k tokens) and then seconds per
  turn; one that rewrites earlier context pays the whole prefill every time.
- **The 9B holds its rates with depth; the 4B GGUFs do not.** The 9B prefills
  at 61.7–66.6 tok/s from 4.5k to 10.7k tokens and decodes at ~9 tok/s after
  7.2k, losing 2.5 % across the 1024-token reply. The thinking 4B's prefill
  falls from 87.5 to 49.2 tok/s between 4.5k and 10.7k — at 10.7k it is slower
  than the 9B, 218.5 against 174.0 s — and both 4B `Q4_0` files decode at
  3.0–3.4 tok/s at 7.2k: a third of the 9B's rate, and a tenth of the thinking
  build's own 32.0 tok/s on 256-token replies (the upgrade check). Ollama's serve log
  hints at why without settling it: at a 16k context it allocates 2304 MiB of
  KV cache for the 4B and 512 MiB for the 9B (`ollama-serve.excerpt.log`), so
  the 9B keeps KV state for far fewer layers or heads, which fits a cost that
  barely grows with context. The architectures were not inspected.
- **For agent-sized contexts on this lane the 9B is the faster model, not the
  4B**: the same ~106–108 s to the first token at 7.2k, then 2.7–2.9× the decode
  rate.
- The GPU lane decodes the 4B-Instruct at 6.2–6.6 tok/s at 7.2k — about twice
  the CPU lane — and prefills at 42.5 tok/s, 0.63× the CPU lane
  ([§ The Adreno GPU lane](#the-adreno-gpu-lane-and-the-npu-and-gpu-together)).

**What it cannot say.** Decode after 10.7k tokens (not traced); the 9B on the
GPU lane (not run); the contract records host load at its start only (0.07–0.90
other cores across these nine runs), and the depth script records none.

## P7.3 — One model on both runtimes

**Run**: for the QAIRT bundle, the upgrade check's NPU steps (argv in
[§ P7.5](#p75--the-upgrade-checks-first-live-run)) and `bench_coding.py
--backend geniex-npu --task-set all --prompt-variants` (step 3; its as-written
phrasings are the comparable run). For the GGUF: `orchestrant-bench contract
--backend geniex-cpu --model unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_0`, `speed …
--stream --max-tokens 2048 --correctness`, `bench_tools.py --backend geniex-cpu
--model … --repeats 3` and `bench_coding.py --backend geniex-cpu --model …
--label cpu-4b-instruct --task-set all --repeats 3` (steps 18–21), and the depth
trace (step 16).

| `Qwen3-4B-Instruct-2507` | NPU, QAIRT W4A16 | CPU, GGUF `Q4_0` |
|---|---|---|
| `bench_tools`, 42 cases × 3, no system prompt | 99/123 = 80 %, clustered [66–90 %]; **33/41 cases** (one case, 3 rows, HTTP 400) | 123/126 = 98 %, clustered [87–100 %]; **41/42 cases** |
| … seconds per call | 2.90 s | 6.91 s |
| `bench_coding`, 39 tasks | 21/38 = 55 % [40–70 %] as written, one draw (1 CUT) | 66/117 = 56 %, clustered [42–70 %], three draws |
| decode, pooled (9 prompts, up to 2048 tokens) | 20.4 tok/s | 22.2 tok/s |
| overall | 20.4 tok/s | 22.0 tok/s |
| TTFT, mean | 0.15 s | 0.25 s |
| answered inside 2048 tokens | 8/9 | 8/9 |
| lane cores | 0.94 | 7.41 |
| CPU-rail energy, gross | 0.102 J/token | 0.844 J/token |
| cold prefill at ~1.8k tokens (contract) | 1028 tok/s | 146 tok/s |
| a ~7.2k-token prompt | refused: the bundle's context is 4096 | 105.6 s to the first token, then 3.4 → 3.0 tok/s |
| correctness probe | 6/6 | 5/6 — "5" r's in "strawberry" |

Paired over the cases both measured: **tools, 7 cases pass on the CPU lane and
fail on the NPU, none the other way — sign test p = 0.016, paired difference
+17.1 points [+5.4, +28.7]**. Coding, 4 tasks one way and 5 the other — p = 1.0,
+2.6 points [−8.2, +13.5].

**What it answers.** With the model held fixed, the lane moves the tool score by
17 points and the coding score by nothing measurable. P7.3 existed because
NPU-vs-CPU had always confounded the lane with a thinking against an instruct
model; on tools, the lane alone makes a difference. The NPU's eight misses are
`list_files` where `read_file` was wanted (`simple_read`, `nested_path`), a
refusal in prose — "I cannot directly read files…", "I cannot run tests…" — for
four read-and-run cases (`contents_not_names`, `path_not_query`,
`typed_int_max_lines`, `run_one_test_file`), one call where two were wanted and
one answer past a permission error. The GGUF misses only `contents_not_names`.
On speed the CPU lane decodes this model at least as fast as the NPU on a quiet
host (22.2 against 20.4 pooled; the 2048-token replies at 19.6 and 19.5), at
7.9× the lane cores, 8.3× the CPU-side energy per token and a seventh of the
prefill rate.

**What it cannot say.** *Which* part of the lane costs the NPU its tool cases:
it bundles the quantisation (W4A16 against `Q4_0`), the runtime, the bundle's
own system prompt (the contract's `bundle_system_prompt: yes` — seven tokens
the NPU lane adds when a request has no system message), the bundle's sampler
(seed 42, T 0.8, top-k 40) against llama.cpp's, and the 4096-token context. It
also cannot say whether the gap survives `prompts/tool-disambiguation.md`,
which targets exactly these misses and was in no run. One run per cell; the
NPU's coding score is one draw, which on this deterministic lane is its rate.

## P4.2 — Qwen2.5-Coder-7B

**Run**: `orchestrant-bench speed --backend geniex-cpu --model
unsloth/Qwen2.5-Coder-7B-Instruct-GGUF:Q4_K_M --stream --max-tokens 2048
--correctness` and `bench_coding.py --backend geniex-cpu --model … --label
cpu-coder7b --task-set all --repeats 3` (steps 22–23).

| CPU lane, 39 tasks × 3 | 4B-Instruct `Q4_0` | Coder-7B `Q4_K_M` |
|---|---|---|
| `bench_coding` | 66/117 = 56 %, clustered [42–70 %] | 62/117 = 53 %, clustered [39–66 %] |
| by language | Python 51/81, bash 10/12, CMake 0/3, Dockerfile 3/3, PowerShell 2/18 | Python 43/81, bash 11/12, CMake 1/3, Dockerfile 3/3, PowerShell 4/18 |
| tokens per graded attempt, mean | 175 | 115 |
| CUT | 0 | 0 |
| decode, pooled (the 9 speed prompts) | 22.2 tok/s | 17.9 tok/s |
| completion tokens for the 9 prompts | 6079 | 2714 |
| CPU-rail energy, gross | 0.844 J/token | 1.203 J/token |
| correctness probe | 5/6 | 5/6 — "2" r's |

Paired: 5 tasks one way, 6 the other — p = 1.0, −3.4 points [−14.6, +7.8].

**What it answers.** The hub page's § 1k reading survives v0.7.0 and the fixed
grader: the code specialist ties the general model and is terser. It no longer
rests on the 2048-token cap either: no Coder attempt was cut. It is therefore
not the recommendation's third condition — "a code-specialised model whose
prefill cost turns out to be tolerable in a real loop" — because it is not
better at code in the first place. **P4.2 is closed.**

**What it cannot say.** Its tool calling (§ 1k's 27/27 was the old 27-case
suite; not re-run) and an agent loop (not run).

## P4.3 — A second family: Llama-3.2-3B and Phi-4-mini

**Run**: per model, `orchestrant-bench speed … --max-tokens 2048 --correctness`,
`bench_tools.py --backend geniex-cpu --model …` and `bench_coding.py … --task-set
all` (steps 26–31, one draw each); addendum 1, `bench_tools.py … --model
unsloth/Llama-3.2-3B-Instruct-GGUF:Q4_K_M --accept-text-json`. The comparison is
the first draw (attempt 0) of the 4B-Instruct's three, so one draw meets one
draw.

| CPU lane, one draw | Llama-3.2-3B `Q4_K_M` | Phi-4-mini `Q4_K_M` | 4B-Instruct `Q4_0`, attempt 0 |
|---|---|---|---|
| correctness probe | 3/6: "2" r's, 25 minutes, 9.11 | 3/6: Sydney, "2" r's, 9.11 | 5/6 |
| `bench_tools`, 42 cases | 8/42 = 19 % [10–33 %]; with `--accept-text-json` 20/42 = 48 % [33–62 %] | 13/42 = 31 % [19–46 %] | 41/42 |
| `bench_coding`, 39 tasks | 12/39 = 31 % [19–46 %] | 14/39 = 36 % [23–52 %] | 22/39 |
| … PowerShell, bash | 0/6, 0/4 | 0/6, 1/4 | — |
| decode, pooled | 28.9 tok/s | 25.5 tok/s | 22.2 tok/s |

Paired against the 4B-Instruct: coding, Llama 10 tasks worse and none better (p =
0.002, −25.6 points [−39.5, −11.8]), Phi 8 and none (p = 0.008, −20.5 [−33.4,
−7.7]); tools, Llama 33–0, Phi 28–0, Llama with text-JSON parsing 22–1 (every
p < 0.0001).

**What it answers.** Neither family competes on this lane; both are faster
decoders and worse at everything the lab grades. Phi-4-mini makes no tool call
in any of the 26 call, selection, argument, typed-argument and parallel cases,
and answers in prose instead ("I'm sorry…", "As an…"). **Llama-3.2-3B
answers every case with a call — written as text JSON**, `{"name": …,
"parameters": …}`, which GenieX does not parse into `tool_calls`: in the
`--accept-text-json` run all 42 rows carry one. That makes its 8/42 misleading in
a specific way: 7 of the 8 are restraint and irrelevance cases, "passed" because
the grader could not see the call in the text. Parsed, it calls a tool in all 7
(0/7) and passes 20, 19 of them call, selection, argument and recovery cases —
so neither number is the model's tool score alone: its call format and GenieX's
parser are confounded
([§ What the lab got wrong](#what-the-lab-got-wrong), 5). **P4.3 is answered: no
non-Qwen model measured here is a candidate.**

**What it cannot say.** One draw each, one quantisation each, one lane.

## CV-4 — The thinking Qwen3-4B on v0.7.0

**Run**: the upgrade check's CPU steps (contract, speed, speed-answer,
`bench_tools --repeats 3`); `bench_tools.py --backend geniex-cpu` (step 24);
`bench_coding.py --backend geniex-cpu --label cpu-4b --task-set all` (step 25);
`bench_chat.py --backend geniex-cpu` (step 5).

| CPU lane | thinking 4B `Q4_0` | 4B-Instruct `Q4_0` |
|---|---|---|
| `bench_tools`, 42 cases × 3 | 122/126 = 97 %, clustered [92–99 %]; 4 cases mixed | 123/126 = 98 %, clustered [87–100 %] |
| … one draw (step 24) | 39/42 = 93 % [81–98 %] | 41/42 (attempt 0) |
| … seconds per call | 17.3 s | 6.9 s |
| `bench_coding`, 39 tasks, budget 3000 | 11/14 graded = 79 % [52–92 %]; **25 CUT**, each 98–100 % thinking | 22/39 (attempt 0), 0 CUT |
| decode, pooled: 256-token · 2048-token replies | 32.0 · 19.4 tok/s | — · 22.2 tok/s |
| answered inside 2048 tokens; first answer token | 5/9; 10.9 s mean (4.4–23.0) | 8/9; 0.19 s |
| `bench_chat` | 31/33 = 94 % [80–98 %] | not run |

Paired tools, thinking against instruct: 1 case one way, 4 the other — p = 0.375,
+0.8 points [−4.9, +6.5].

**What it answers.** On v0.7.0 the thinking build calls tools as well as the
instruct build — not separable — at 2.5× the time per call, and it cannot be
graded on coding at the default budget: 25 of 39 replies were still thinking at
3000 tokens. Its decode on the upgrade check, 19.4 tok/s pooled over replies of
up to 2048 tokens, is well above the v0.7.0 page's r2 run of the same command
(13.9; the 2048-token replies at 17.3–18.4 against 11.8–13.1 tok/s), with less
other load (0.20 against 0.48 cores on average).

**What it cannot say.** Why r2 was that much slower: the v0.7.0 page's load
slope (−14.5 tok/s per core, on short replies) accounts for part; depth was not
measured under load.

## P3.2 and P3.3 — The opencode preamble, and a loop that grows

**Run**: `bench_tools.py --backend geniex-cpu --model
empero-ai/Qwen3.8-9B-Distill-GGUF:Q4_K_M --tools opencode` (step 32) and the same
with `--turn-growth` (step 33).

Behind the ten-schema opencode preamble the 9B passes **27/34 = 79 % [63–90 %]**
(8 cases have no single defensible answer under that tool set and are skipped),
in 2899 s — 85 s per case. Its misses: `read` for a patch and for a diff, `bash`
for a search `grep` would do, a test run without "test" in the command, two
results answered with another call, and `sudo cat /etc/shadow` invented after a
permission error.

| turn | history (chars / 4) | seconds |
|---|---|---|
| 1 | ~10 | 79.7 |
| 2 | ~322 | 12.4 |
| 3 | ~635 | 18.5 |
| 4 | ~947 | 25.1 |
| 5 | ~1260 | 32.7 |
| 6 | ~1572 | 41.1 |
| 7 | ~1885 | 53.9 |
| 8 | ~2197 | 66.1 |
| 9 | — | `HTTP Error 400: Bad Request` |

**What it answers.** The 9B still calls tools behind an agent-sized preamble,
and every case pays the whole preamble. Turn 1's 79.7 s is mostly its cold
prefill — at the 9B's measured 61.7–66.6 tok/s that would be 4.9–5.3k tokens,
less whatever the reply itself took — and each case of the suite re-sends the
preamble with a new tail: P7.2's fork, never cached. In a loop
that appends, a turn costs 12.4 s at ~0.3k tokens of history and 66.1 s at
~2.2k, growing 6.0–12.8 s per turn.

**What it cannot say.** Whether the preamble costs the 9B anything: it was not
run on the default tool set, so 27/34 has no baseline. Where the per-turn growth
goes: the report records no tokens per turn, so decode slowing with depth and
longer replies (the 9B thinks) cannot be told apart. Why turn 9 failed: the
body of the 400 was not recorded; ~2.5k tokens of history plus the ~5k preamble
is far inside the 16k context ([§ What the lab got wrong](#what-the-lab-got-wrong), 3).

## R2, R7, R11 — The hub's coding tables, re-derived

The panel review of 2026-09-05 left the GenieX page's § 1i and § 1n coding
tables standing under a grader that has since changed in both directions, with
no raw report behind them. All three can now be read from stored reports. The
hub page itself is not edited here.

**R7, § 1i — the 27 Python tasks on the NPU lane.** Read from the stored
`v061-npu-coding.json` and `v070-npu-coding.json` (2026-09-23, `--task-set all`,
budget 3000, the grader after the 2026-09-05 fixes). The two are identical row
for row — output hash and verdict on all 33 — so the table is one measurement:

| | § 1i as published (v0.5.0, 2048-token cap, old grader) | re-derived (v0.6.1 = v0.7.0, fixed grader) |
|---|---|---|
| 27 Python tasks | 17/27 = 63 % [44–78 %], CUT counted as a miss | **15/26 = 58 % [39–74 %]**, 1 CUT (`parsing_pipe_record`, 3/13 at the cut) |
| `validation_parse_kv_pairs` | 20/21 | 22/23 |
| `validation_parse_range_spec` | 20/25 | 19/25 |
| `stateful_run_machine` | 16/18 | 16/18 |
| `lists_merge_pairs_longest_value_wins` | 12/14 | 12/14 |
| `strings_normalize_tag` | 11/12 | 11/12 |
| `strings_dominant_case` | 11/12 | 11/12 |
| `rank_quants` | 2/6 | 2/6 |
| `parsing_item_list` | no code produced | 11/17 |
| new failures | — | `parse_version` 16/19, `validation_parse_seat_code` 19/20, `numbers_round_to_step` 14/16 |

Nine of the eleven failures pass at least 75 % of their assertions; `rank_quants`
and `parsing_item_list` are the two far misses. § 1i's shape — "mostly right,
loses edge cases" — holds; its score does not compare, because the runtime, the
cap, two tasks' tests (D11) and the CUT accounting all moved. Across all 33
tasks: 19/31 graded, the CMake task skipped (no `cmake` then).

**R2 and R11, § 1n — the classic set, three draws.** Steps 34–37:
`bench_coding.py --backend geniex-cpu --model <model> --label <label> --task-set
classic --repeats 3`; the NPU row is `npu-coding-variants.json` as written, one
draw (deterministic).

| Model | Lane | § 1n as published (v0.6.1, one draw) | **v0.7.0, fixed grader** |
|---|---|---|---|
| QAIRT Qwen3-4B-Instruct W4A16 | NPU | 3/3, 0 cut | **2/3** — `parse_version` 16/19 |
| GGUF Qwen3.8-27B `Q4_0` | CPU | 3/3, 0 cut | 9/9, 0 cut |
| GGUF Qwen3.8-9B-Distill `Q4_K_M` | CPU | 3/3, 0 cut | 7/7 graded, 2 CUT (`parse_version` cut, passed at 2071 tokens, cut) |
| GGUF Qwen3-4B `Q4_0` | CPU | 1/1, 2 cut | 6/6 graded, 3 CUT (`parse_version` in every draw) |
| QAIRT Qwen3-1.7B W4A16 | NPU | 1/2, 1 cut | not run |
| GGUF Qwen3.8-2B-Distill `Q4_K_M` | CPU | 1/3, 0 cut | 3/9 (`merge_sorted` 3/3, the other two 0/3) |

**What it answers.** § 1n's "the QAIRT 4B-Instruct is still 3/3" does not survive
the fixed grader: `parse_version`'s strengthened tests (D11) fail it. R2 asked
what became of § 1n's three cuts. The 4B `Q4_0`'s two — `merge_sorted` and
`balanced`, both of which then finished in 1213 and 1527 tokens at an 8000-token
budget — finish inside 3000 tokens in all six draws today (862–2442 tokens),
which is what the R2 grader defect (a closing fence plus a newline graded CUT)
would predict; without the old replies that stays a reading, not a proof. Its
cuts today are `parse_version`, all thinking. The 1.7B's cut was not re-run.
R11 is P4.1 and P4.2 above: re-measured on v0.7.0 with the budget, not a server
cap, deciding every cut.

**What it cannot say.** The 1.7B row; anything about the 27B, 9B and 2B beyond
three tasks.

## P7.4 — bench_agent, three trials per task

**Run** (WSL2): `bench_agent.py --model
geniex-cpu/empero-ai/Qwen3.8-9B-Distill-GGUF:Q4_K_M --repeats 3 --timeout 1800`
(step 38): opencode 1.18.31 with `task`, `webfetch`, `todowrite`, `skill`,
`grep` and `glob` disabled and `tool-disambiguation.md` as instructions; a fresh
repository and opencode home per trial.

| Task | Trials passed | Seconds per trial |
|---|---|---|
| `fix_failing_test` | 3/3 | 192, 180, 170 |
| `add_function_and_test` | 3/3 | 224, 213, 242.5 |
| `multi_file_rename` | 3/3 | 178, 201, 175 |
| `fix_bash_quoting` | 1/3 — the two failures edited `check.sh` and were refused: "tests were modified" | 194, 174, 207 |
| `fix_medium_repo` | 1/3 — its own tests still red in two trials | 608 (pass), 1398, 926 |
| `fix_cmake_link` | skipped: no `make`/`ninja` in WSL | — |

**11/15 trials = 73 %, case-clustered [38–92 %]** (design effect 2.05 over five
tasks; the report's stored interval, [48–89 %], treats the fifteen trials as
independent). **pass^1 73 %, pass^2 60 %, pass^3 60 %.** 5283 s in all; a
trial's median is 200.7 s.

**What it answers.** The recommendation's agent half — the 9B distill on the
CPU lane — has a repeated measurement: it solves the three flat Python
fixtures in every trial and the bash and medium-repository fixtures one time in
three. The refused
`check.sh` edits are the cheat-refusal of the 2026-09-05 review working on a
live run.

**What it cannot say.** Anything about another model — no other one was run
end to end — or about CMake. Five tasks give a wide interval.

## P7.5 — The upgrade check's first live run

**Run**, as `steps.jsonl` records it: `upgrade_check.py --lanes
geniex-npu,geniex-cpu --out benchmarks/benchmark_results/2026-09-24-upgrade-check-v070
--steps contract,speed,speed-answer,tools --overflow-tokens geniex-npu=6000`,
started 2026-09-24 17:22:30 UTC.

| Lane | contract | speed | speed-answer | tools (`--repeats 3`) |
|---|---|---|---|---|
| NPU | 245.2 s, exit 0 | 120.4 s, 0 | 360.8 s, 0 | 396.5 s, 0 |
| CPU | 297.3 s, 0 | 328.5 s, 0 | 605.5 s, 0 | 2231.5 s, 0 |

Verdict **OK** (exit 0), 4585.7 s of steps; the compare step skipped (no
`--previous`); no coding step (`--steps` left it out).

**What it answers.** The command works end to end on this host, and its
directory is the `--previous` baseline for the next GenieX upgrade under the
file names `upgrade_check` writes, which closes the README's "needs a baseline
written under the new names". It also shows what reproduces: the NPU
speed-answer matches the v0.7.0 page's r2 run to 0.04 % (20.40 → 20.41 tok/s
pooled, 6176 completion tokens both), and the NPU tools score is the 2026-09-23
one (99/123 against 98/124; 2 cases one way, 3 the other, p = 1.0) — with the
spacer every case's three draws are now byte-identical, where 5 cases mixed
before. The CPU lane did not reproduce its r2 speed (CV-4).

**Next.** Add the coding step (`--wsl`). On the NPU lane `--tools-repeats 3`
buys no information: its draws are identical.

## P7.6 — PowerShell tasks and the medium repository

The six PowerShell tasks, from this repository's own traps, as each model
scored them (`bench_coding --task-set all`; three draws unless noted):

| Model | Passed | Passing tasks |
|---|---|---|
| NPU 4B-Instruct (one draw) | 2/6 | `error_action_stop`, `null_comparison` |
| CPU 4B-Instruct | 2/18 | `error_action_stop` 2/3 |
| Coder-7B | 4/18 | `error_action_stop` 2/3, `null_comparison` 1/3, `requires_version` 1/3 |
| Ollama 4B-Instruct | 3/18 | `error_action_stop` 3/3 |
| thinking 4B (one draw) | 0/2 graded, 4 CUT | — |
| Llama-3.2-3B, Phi-4-mini (one draw) | 0/6 each | — |

No model passed `nested_module_import`, `pipeline_output` or
`single_element_array` in any draw — the module-private nested import, the
pipeline that returns more than the value, and the one-element array that
unrolls. The CMake task, graded for the first time (WSL now has `cmake`), passed
once in 13 graded attempts across the models (the Coder, 1/3). The
medium-repository agent fixture: the 9B, 1/3 (P7.4).

**What it answers.** A local 4B–7B model is not a PowerShell author for this
repository: the traps it was built from catch every model measured.

## P7.7 — bench_chat on both lanes

**Run**: `bench_chat.py --backend geniex-npu` (step 1) and `bench_chat.py
--backend geniex-cpu` (step 5), 33 cases, one draw.

| | NPU, 4B-Instruct W4A16 | CPU, thinking 4B `Q4_0` |
|---|---|---|
| score | 27/30 = 90 % [74–97 %] | 31/33 = 94 % [80–98 %] |
| ~7k-token documents | 3 OVERFLOW (`context_length_exceeded`) | 3/3, 151–172 s each |
| ~3.1k-token documents | 3/3, 3.4–4.1 s each | 3/3, 55–61 s each |
| a non-document case, median | 1.03 s | 10.3 s |
| suite, measured | 50.4 s | 1022.1 s |
| failed | `avoid_a_word` ("the"), `json_booleans` (strings), `json_capitals` ("London") | `words_exactly_5` (4 words), `json_escaping` (invalid `\` escape) |

Paired over the 30 cases both graded: 2 one way, 3 the other — p = 1.0, +3.3
points [−11.5, +18.1]. (`json_capitals` is graded right: London has not been an
EU capital since 2020.)

**What it answers.** Where both lanes answer, they are not separable; the NPU
answers in a twentieth of the time. The NPU cannot take a ~7k-token document at
all — its 4096-token context — while ~3.1k tokens fit.

**What it cannot say.** The CPU lane's instruct build, the natural long-document
candidate, was not run on `bench_chat`. One draw each (the NPU's is its rate;
the CPU's one draw of a sampling lane); 33 cases.

## P1.2 — How far the wording moves a score

**Run**: `bench_tools.py --backend geniex-npu --prompt-variants` (step 2) and
`bench_coding.py --backend geniex-npu --task-set all --prompt-variants` (step 3),
one draw per phrasing. The NPU lane is deterministic after the spacer (the
determinism probe, and the upgrade check's identical draws), so a disagreement
between phrasings is the wording, not a draw.

| NPU, 4B-Instruct | cases asked more than one way | changed verdict with the wording | as written | paraphrase 1 | paraphrase 2 |
|---|---|---|---|---|---|
| `bench_tools` | 11 | **7 = 64 % [35–85 %]** | 9/11 | 4/11 | 6/9 |
| `bench_coding` | 10 | 3 = 30 % [11–60 %] | 6/10 | 4/10 | 3/6 |

Five tool cases pass only as written against paraphrase 1, none the other way
(p = 0.0625). The seven: `simple_read` and `contents_not_names` (pass only in
paraphrase 2), `names_not_contents`, `overwrite_not_patch`,
`what_changed_in_them`, `diff_one_path` and `extract_query`. The coding three:
`attempt_verdict`, `balanced`, `parse_lane_spec`.

**What it answers.** On the NPU lane, for 7 of the 11 tool cases asked more
than one way, whether the model picks the right tool depends on the phrasing:
the paraphrases pass 4 of 11 and 6 of 9 where the as-written phrasings pass 9 of
11. The selection cases' paraphrases share fewer than two content words with
the tool description by construction, so they test understanding where the
as-written ones partly test reading; the as-written tool score (33/41 cases) is
the optimistic reading of this lane.

**What it cannot say.** Any other lane or model (not measured); 11 and 10 cases.

## Ollama and GenieX on the same files

**Run**: steps 39–52 — the contract, speed-answer, depth trace and prefix
cache, and `bench_tools`/`bench_coding --repeats 3` for the instruct 4B, against
`--backend ollama --model hf.co/…` — on Ollama's default of 4 threads; addenda
2–6 repeat speed and depth with `PARAMETER num_thread 8` (`Modelfile.*`; the
serve log reads `n_threads = 8`).

| 4B-Instruct `Q4_0` unless noted | GenieX CPU | Ollama, 4 threads | Ollama, 8 threads |
|---|---|---|---|
| decode, pooled (9 prompts) | 22.2 tok/s | 13.2 tok/s | 24.7 tok/s (24.6 without three one-burst replies) |
| overall | 22.0 tok/s | 12.3 tok/s | 22.0 tok/s |
| the two replies of equal length (1415/1413 and 2048 tokens) | 23.8, 19.6 tok/s | — | 25.4, 21.6 tok/s (+7 %, +10 %) |
| TTFT, mean | 0.25 s | 3.42 s | 3.15 s |
| lane cores | 7.41 | 2.80 | 4.95 |
| CPU-rail energy, gross | 0.844 J/token | 1.065 J/token | 1.080 J/token |
| ~7.2k-token prompt: to first token (prefill) | 105.6 s (67.8 tok/s) | 455.8 s (15.7), 4.3× | 235.0 s (30.4), 2.2× |
| … decode windows | 3.38 → 3.02 | 2.43 → 2.17 | 3.49 → 3.12 |
| 9B at ~7.2k: to first token (prefill) | 108.4 s (66.1 tok/s) | 611.6 s (11.7), 5.6× | 323.6 s (22.1), 3.0× |
| … decode windows | 9.06 → 8.83 | 5.88 → 5.69 | 9.53 → 9.13 |
| 9B, 4.5k-token prefix: cold · fork | 72.7 s · 67.0 s | 376.5 s · 113.9 s (0.30× cold) | — |
| `bench_tools`, 42 × 3 | 123/126, 41/42 cases | 114/126, 38/42 cases | — |
| `bench_coding`, 39 × 3 | 66/117 | 60/114, 3 CUT | — |

| Contract, 4B-Instruct | GenieX CPU | Ollama |
|---|---|---|
| `temperature: 0` is greedy | no | **yes** |
| an identical repeat keeps its reply | no | **yes** |
| chat `stop` honoured | no | **yes** |
| `prompt_tokens` survives a cached repeat | no | **yes** |
| a long prefix with a new tail is reused | no | **yes** |
| cold prefill, ~1.8k tokens | 12.4 s (146 tok/s) | 89.1 s (20.4 tok/s), 4 threads |

**What it answers.** On one file, Ollama with all eight threads decodes like
GenieX — 3–5 % faster in the depth windows, 7–10 % on the two replies of equal
length — and delivers the same overall rate, 22.0 tok/s, because every request
waits for its first token: 2.5–3.3 s on the 11–35-token prompts (2.4–3.5 s at
four threads; GenieX 0.13–0.28 s). The serve excerpt times the evaluation of
8–12-token prompts at 0.18–0.27 s, so most of that wait is outside llama.cpp's
own timing. Its prefill is 2.2–3.0× slower at 7.2k tokens with eight
threads and 4.3–5.6× with the default four. It keeps the OpenAI contract GenieX
breaks, and its cache reuses a shared prefix — but its cold prefill is so slow
that GenieX's full re-prefill still wins (67.0 against 113.9 s, 9B, 4.5k). On
tools and coding the two are not separable (4 cases one way and 1 the other on
each; p = 0.375 both), and they do not sample alike — GenieX draws at T=0,
Ollama is greedy — so draw-level differences are expected. **For an agent's
prefill-bound turns GenieX's CPU lane stays the choice.**

**What it cannot say.** The 8000-token prefix on Ollama (the cold request timed
out at the contract's 600 s); capability at 8 threads (not re-run; the thread
count should not change a greedy reply, but that was not checked); where the
2.5–3.3 s go.

## The Adreno GPU lane, and the NPU and GPU together

**Run**: `orchestrant-bench contract --backend geniex-gpu`, `speed … --stream
--max-tokens 2048 --correctness` for both 4B files, the depth trace (steps
53–56), then `orchestrant-bench lanes --lanes geniex-npu geniex-gpu` (step 57).

| Same files | GPU | CPU |
|---|---|---|
| thinking 4B, decode pooled (answered) | 11.4 tok/s (5/9) | 19.4 tok/s (5/9) |
| 4B-Instruct, decode pooled (answered) | 11.4 tok/s (8/9) | 22.2 tok/s (8/9) |
| 4B-Instruct at ~7.2k tokens: to first token (prefill) | 168.4 s (42.5 tok/s) | 105.6 s (67.8 tok/s) |
| … decode windows | **6.58 → 6.23** | 3.38 → 3.02 |
| lane cores | 0.82–0.83 | 7.41–7.78 |
| CPU-rail energy, gross (the GPU's own draw is not metered) | 0.294 J/token | 0.844–0.913 J/token |
| correctness probe | 6/6 thinking, 5/6 instruct ("4" r's) | 6/6, 5/6 |

The GPU lane's contract is the CPU lane's: `temperature: 0` read as unset, an
identical repeat answered from stale state, a prefix cache for repeats and
extensions but not forks (cold ~1.8k tokens in 21.1 s, 86 tok/s), and
`power_mode` validated (a valid mode answered in 8.1 s, an invalid one refused
with HTTP 400).

| `lanes`, one 256-token request each | alone | together | change |
|---|---|---|---|
| NPU, 4B-Instruct | 23.0 tok/s | 22.2 tok/s | −3.4 % |
| GPU, thinking 4B | 12.1 tok/s | 11.5 tok/s | −5.1 % |
| both | — | 22.6 tok/s delivered (0.98× the NPU alone); 33.7 summed (1.46×) | |

The delivered rate is capped by the slower lane's single request (the GPU
finished at 22.7 s, the NPU at 11.7 s). NPU beside the CPU lane on v0.7.0 at
`--log none` lost 46–61 % (three runs in the 2026-09-23 directory).

**What it answers.** The NPU and GPU lanes do not contend; the NPU and CPU lanes
do. A second model that must run beside the NPU lane belongs on the GPU lane.
On its own the GPU lane decodes the speed prompts at half the CPU lane's rate
(11.4 against 22.2 tok/s) and at twice it after 7.2k tokens of context, with 0.8
cores busy against 7.4.

**What it cannot say.** One run of the pair; the GPU's energy; the 9B on the GPU
lane. The NPU's "alone" request waited 16.6 s for its first token — the first
request after the lane started, loading the bundle — which does not enter the
decode rate.

## What the lab got wrong

| # | Defect | What it did in this campaign | Status |
|---|---|---|---|
| 1 | The correctness probe mixes kernel-integrity items with capability items | It exists to catch broken kernels (the i-quant garbage). The instruct 4B on the CPU, GPU and Ollama lanes and the Coder fail only the "r"s-in-"strawberry" item ("5", "4", "5", "2") with every arithmetic item right; Llama and Phi fail three capability items. `upgrade_check` fails its speed step on any wrong answer, so a healthy runtime serving an instruct model would fail the check | **being fixed in this round** (probe item kinds) |
| 2 | A per-row decode rate is published for a reply that arrived in one burst | Ollama at 8 threads delivered three 8–12-token replies all at once (TTFT = latency, 2.51–2.58 s): their `decode_tok_per_sec` reads 9,733–26,712 tok/s, and the runner at `3260e1e` printed "Decode only: 6548.1 tok/s", their mean with the rest. The pooled figure barely moves (24.74, 24.62 without them), but the per-row field feeds the viewer's per-request range and `bench_compare`'s per-prompt pairs | **being fixed in this round** (per-row guards) |
| 3 | An HTTP 400 is recorded without its body | Turn 9 of the turn growth and all three draws of the NPU's `long_result_find_failure` read `HTTP Error 400: Bad Request`, the server's reason dropped. The turn-growth loop also answers only the first call of a turn (`tool_calls[0]` in `bench_tools.turn_growth`) and records no call count; a turn with two calls leaves one unanswered, which a strict server refuses — one candidate, unverified | open |
| 4 | The thinking share cannot be recovered when a cut reply has no opening `<think>` | The 9B distill's template opens `<think>` in the prompt, so a reply cut before `</think>` carries neither tag and scores 0.0 (its `parse_version` cuts; its passes read 0.42–0.96). The 8B's coding report predates the unclosed-`<think>` fix: 0.0 on 29 of 30 cut rows | open |
| 5 | A tool call written as text JSON makes restraint cases pass | Llama-3.2-3B wrote a call as text in every case it was asked; without `--accept-text-json` its seven restraint and irrelevance "passes" were calls the grader could not see ([§ P4.3](#p43--a-second-family-llama-32-3b-and-phi-4-mini)) | open |
| 6 | The chain recorded step names, not commands; the depth traces carry no provenance and their script is not stored | Every command on this page is reconstructed from report configs; the depth files cannot be tied to a runtime or host load | open |
| 7 | The contract's 600 s request timeout is shorter than a slow lane's cold prefill | Ollama's 8000-token prefix check timed out | open |

## What the commit messages said

The data commits carried a first reading of the results. Recomputed under the
definitions above, these differ:

- `3260e1e` (M1): "12.8 tok/s decode" is the mean of per-request rates; pooled,
  **12.5**. "Every cut reply was still thinking" cannot be read from the report
  (defect 4).
- `5ea17a7`: the instruct GGUF's "decode 26.1 tok/s" and the Coder's "17.4" are
  per-request means; pooled, **22.2** and **17.9**. "+~295 tokens in 2.6-12.5 s"
  is **1.6–12.5 s** (the 2B's extensions take 1.62–2.57 s).
- `c8f8e58`: Ollama's instruct 4B "16.6 tok/s (GenieX 26.1)" is **13.2 against
  22.2** pooled; the GPU lane's "decode ~11.5-11.8 tok/s, prefill ~51-55 tok/s"
  is **11.4** for both files, and the speed runner's prefill on 11–145-token
  prompts (pooled 58–66 tok/s) is mostly request overhead — at 7.2k tokens the
  GPU lane prefills at **42.5 tok/s**. "(NPU beside the CPU lane lost 22-46 %)"
  is **46–61 %** at `--log none` (75–87 % at `--log info`).
- `b486930`: "overall speed (23.2 tok/s both)" counted prompt tokens (23.15 and
  23.21); completion tokens only, **22.0 both**.

## The recommendation

The roadmap's answer, stated so it can be falsified: **`qualcomm/Qwen3-4B-Instruct-2507:W4A16`
on the NPU lane with `prompts/tool-disambiguation.md`** for chat and completion;
agent work on a GGUF lane, the 9B distill on the CPU lane. Read use by use,
comparing like with like — one model and suite, the same repeats, the same day:

- **Chat — unchanged, bounded by input length.** Where both lanes answered,
  NPU and CPU are not separable (27/30 against 31/33; 2 cases one way, 3 the
  other, p = 1.0), and the NPU answers in a twentieth of the time with its first
  answer token after 0.14 s. An input that does not fit its 4096-token context
  it cannot answer at all (the ~7k-token documents; ~3.1k fitted); there a 16k
  GGUF lane is the answer — *which* GGUF for long-document chat is open (the
  instruct build was not run on `bench_chat`).
- **Tool calling — it depends on the prompt.** Same model, same 42 cases, three
  draws, no system prompt: the GGUF on the CPU lane passes 41 of 42 cases, the
  NPU bundle 33 of 41 (+17 points [+5, +29], p = 0.016). The NPU's misses are
  the ones `tool-disambiguation.md` was written for, and it was in no run. **If
  the prompt closes the gap on the NPU lane, the recommendation stands** — at
  2.9 s per call against 6.9, an eighth of the CPU-side energy per token and one
  core instead of 7.4, beside a GPU lane it does not slow. **If it does not, the
  GGUF build of the same model on the CPU lane is the better tool caller**, at
  those costs and without the NPU working beside it. The next measurement
  decides it ([§ What to do next](#what-to-do-next), 1).
- **Coding — unchanged.** Nothing measured separates: the NPU bundle against
  the same model's GGUF splits 4–5 tasks, the GGUF against the Coder-7B 5–6 and
  against Ollama's build 4–1 (p = 1.0, 1.0, 0.375). Qwen3-8B and the thinking 4B
  cannot be graded at 3000 tokens. No model is a PowerShell author here.
- **Agent work — unchanged: the 9B distill on the CPU lane**, now with an
  interval: 11/15 trials, 73 % [38–92 %], pass^3 60 %. It is the only model run
  end to end; the NPU bundle cannot hold opencode's preamble. Moving the agent
  to the 4B-Instruct GGUF would not buy speed: at 7.2k tokens it decodes at a
  third of the 9B's rate for the same prefill time.

Of the three conditions the roadmap named, the first (Qwen3-8B) and the third
(a code-specialised model) are now tested on v0.7.0 and neither overturns it —
no longer conditionally; the second was settled on 2026-09-04. The campaign
adds a fourth: **the GGUF build of the recommended model calling tools better
than the bundle with its prompt.**

## What to do next

In the order the evidence supports:

1. **Measure the recommended tool configuration.** From the repository root,
   `python benchmarks/bench_tools.py --backend geniex-npu --system
   benchmarks/prompts/tool-disambiguation.md` (one draw is the NPU lane's rate)
   and the same against the instruct GGUF on the CPU lane with `--repeats 3`,
   then `bench_compare` against this campaign's runs. It decides tool calling.
2. **`bench_chat` on the instruct GGUF** (CPU lane): the long-document half of
   chat.
3. **Fix defects 3–7**: the 400's body and the turn-growth call count (then
   re-run the turn growth to find the 400), a thinking share of "unknown"
   rather than 0.0, a restraint case that sees a call written as text, the
   chain's argv and the depth script with provenance, a longer contract timeout
   for a slow lane.
4. **The 9B on the GPU lane** (depth trace and prefix cache): the GPU lane
   doubled the 4B's decode at depth and does not slow the NPU lane; if it does
   the same for the 9B, the agent gets a lane of its own.
5. **Give P3.2 its baseline**: the 9B on the default tool set.
6. **The upgrade check**: add `--wsl` for its coding step; one tools repeat for
   a deterministic lane.

## How the numbers were computed

One script computed every number above, from the stored reports only — it
contacts no lane and writes nothing. It is kept beside the reports as a
snapshot, like the v070r2 probes, not as a lab tool:

```bash
# from the repository root, in the project's environment
PYTHONPATH=. uv run --no-sync python benchmarks/benchmark_results/2026-09-24-roadmap/derive.py.snapshot
```

It prints one block per section of this page, using the lab's own code:
`orchestrant.benchmark.speed_summary` for every speed figure, `answers` for
"answered", time to an answer and the thinking share, the speed runner's lane
cores and CPU-rail joules (summed as `hostload.summary_lines` sums them), and
`stats` for every interval, clustered interval, pass^k, sign test and paired
difference. The figures above are its output, rounded. The § 1i and § 1n
columns "as published" are quoted from the hub's GenieX page, whose raw reports
were never stored.
