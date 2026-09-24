<!--
Copyright (c) 2025 Kataglyphis
SPDX-License-Identifier: MIT
-->

# GenieX v0.6.1 → v0.7.0 on the Snapdragon X — the CPU and NPU lanes, measured

Taken 2026-09-24 on the lab host (Snapdragon X X126100, 8× Oryon, one Hexagon
HTP v73, 32 GB, Windows 11 25H2), before and after upgrading GenieX from
**v0.6.1** (QAIRT 2.45, llama.cpp `0eadefe`) to **v0.7.0** (QAIRT 2.45,
llama.cpp `4ff829e`). The raw reports are in the run directory
[`../benchmark_results/2026-09-23-geniex-upgrade/`](../benchmark_results/2026-09-23-geniex-upgrade/),
one `v061-*` and one `v070-*` file per measurement; all but the v0.6.1 coding
and first tools reports, taken before it existed, carry the new
`provenance.runtime` block that names the build that served it.

**Revised the same day.** A six-lens review of the lab, every finding
adversarially verified, corrected several numbers below and found two GenieX
defects that had been read as properties of the models; the `v070r2-*` and
`v070r3-*` files are the re-measurements, on lanes started fresh for them
(`--log none` except where a log-level comparison says otherwise; the answer
runs followed the contract on the same lane process), and `v070r2-probes/`
holds the hand probes, a within-reply decode trace and the lane-log lines the
page cites. The r2 speed reports were taken before two last edits to the
speed runner's source (the table's `cut` cell and the energy meter's
counter-reset handling), so their `tool_sha256` is not the committed code's;
neither edit changes a recorded number. What changed, and why, is in
[§ What the review corrected](#what-the-review-corrected).

The lab ran **on the Windows host itself** for the first time — the lanes, the
serving processes and the power rails are all there — with the coding grader
still in WSL2, whose sandbox needs Linux. That move is what exposed most of the
defects in [§ What the lab itself got wrong](#what-the-lab-itself-got-wrong).

## Protocol

Same host, same day, one measurement at a time, lanes launched with identical
flags (`serve --compute {npu,cpu} --nctx 16384 --keepalive 86400 --log info`;
the `*-lognone` runs and the v0.7.0 coding run use `--log none`, for the reason
in § Speed), and — for every speed and contract report — the same benchmark source hash on
both sides (`ed013852be63ab05` and `b50f97a8acdc290d`), so `bench_compare`
attributes a difference to the runtime rather than to the grader. The tool and
coding pairs do carry a changed hash: both fingerprint `provenance.py`, which
this change edited between the runs (plumbing: the lane probe and the
determinism probe) — the paired per-case diff below is what shows that no
grading moved.

| Lane | Model | Path |
|---|---|---|
| NPU (18181) | `qualcomm/Qwen3-4B-Instruct-2507:W4A16` | QAIRT bundle on the HTP |
| CPU (18184) | `unsloth/Qwen3-4B-GGUF:Q4_0` | llama.cpp on the 8 Oryon cores (a thinking model) |

## What the runtime does — `orchestrant-bench contract`, before and after

Every answer is from the lane itself, measured, not from a changelog. Only
two rows moved, and both are the release notes' own claims; everything else —
including three defects the notes do not mention — is unchanged. Rows marked
**r2** come from the contract's new checks, run on fresh v0.7.0 lanes
(`v070r2-*-contract.json`) after the review found that the back-to-back rows
above them measured a cache defect, not the sampler (§ The NPU lane is not
random). Read with that, the `seed` is honoured on both lanes, and the NPU
lane is reproducible at T=0 — from the bundle's fixed seed, not because T=0 is
greedy.

| Behaviour | NPU v0.6.1 | NPU v0.7.0 | CPU v0.6.1 | CPU v0.7.0 |
|---|---|---|---|---|
| `max_tokens` honoured, `finish_reason: length` | yes | yes | yes | yes |
| usage reported (and in a stream) | yes | yes | yes | yes |
| `prompt_tokens` survives a cached repeat | **no** — 39, then 26 | no — 39, then 26 | **no** — 26, then **0**, `cached_tokens` 0 | no — same |
| two T=0 requests agree, sent back to back | **no** | no | no | no |
| … with another request between them (r2) | — | **yes** | — | **no** |
| two near-greedy requests agree (`temperature` 0.01, `top_k` 1; r2) | — | yes | — | yes |
| T=0 is greedy decoding (r2) | — | **no** — read as "unset" | — | **no** — read as "unset" |
| an identical request twice in a row gets the same reply (r2) | — | **no** — 28, then 15 prompt tokens | — | **no** — 15, then 0; a stale first token |
| `seed` honoured, sent back to back | no | no | no | no |
| … with another request between them (r2) | — | **yes** | — | **yes** |
| emits `<think>` | no | no | yes | yes |
| chat `stop` honoured | **no** — "7" leaks, `finish_reason: stop` | **no** — unchanged | no — leaks | no — unchanged |
| `/v1/completions` `stop` honoured | **HTTP 500** `-100016` | **yes** (PR #1342) | yes | yes |
| tool calls parsed | yes, with stray text in `content` | same | yes | yes |
| prefix cache: identical repeat | **no** (1.74 → 2.41 s) | no (1.84 → 2.52 s) | yes (14.4 → 0.11 s) | yes (14.0 → 0.07 s) |
| prefix cache: conversation extended one turn | **yes** (0.60 s, 289 tok) | yes (0.62 s) | yes (3.6 s, 292 tok) | yes (3.8 s) |
| prefix cache: long prefix, different tail | **no** (2.07 s) | no (2.18 s) | **no** (19.9 s — full re-prefill) | no (16.6 s) |
| over-long prompt refused cleanly | yes, HTTP 400 `context_length_exceeded` | yes | (not probed: 16k context) | — |
| `power_mode` validated | no — ignored | **yes** — invalid value → HTTP 400; each change reloads (12.3–12.7 s) | no | **yes** — then ignored; still reloads (3 s) |
| cold prefill at ~1.8k tokens | ≈1050 tok/s | ≈995 tok/s | ≈126 tok/s | ≈130 tok/s |

Three of those contradict what this lab believed:

- **The QAIRT lane has a cache — for conversations.** The GenieX page says
  QAIRT bundles have no prefix cache, because it only ever re-sent an identical
  request. Extending the conversation by a turn is served from the dialog's
  KV: 0.6 s instead of a 1.7 s re-prefill. An agent loop would benefit; the
  4096-token context is still what rules the lane out for opencode.
- **No lane serves a shared prefix with a different tail.** Both lanes
  re-prefill in full, the CPU lane for 17–20 s at 1.8k tokens. That is exactly
  how `bench_tools --tools opencode` sends its cases — one ~5k-token preamble,
  a different question each time — so every case there pays the whole preamble.
- **llama.cpp's `usage.prompt_tokens` counts only what it prefilled**: 0 for a
  cached repeat, and `prompt_tokens_details.cached_tokens` stays 0 too. Any
  benchmark that reads prompt size from usage (`bench_coding`'s `ptok=`
  column, for one) sees the cache, not the prompt.

## The NPU lane is not random — an identical follow-up is what changes it

Two identical T=0 requests to the QAIRT lane return different text, which the
lab read as sampling. It is not random:

| Sequence (v0.6.1) | Answer |
|---|---|
| A after P | `ad86aeb2` "The sea whispers secrets to the wind…" |
| A after A | `476f6cec` "The sea breathes life into the world…" |
| A after P | `ad86aeb2` |
| A after A | `476f6cec` |
| A after Q | `ad86aeb2` |
| A after A | `476f6cec` |

Two distinct answers out of six: after an unrelated request the answer is
always the same, after itself always the other. The same six answers came back
across separate runs — and again, hash for hash, on v0.7.0 ten minutes later.
*Corrected after live probes* ([`v070r2-probes/`](../benchmark_results/2026-09-23-geniex-upgrade/v070r2-probes/README.md)):
it is not the order of the requests. A after B, after C and after D is the same
reply; only A directly after A differs. And the sampler is the request's:

- **`temperature: 0` is read as "unset"** and the bundle's default sampler
  (temp 0.8, top-k 40, seed 42, re-seeded per request) runs — a Go zero value.
  `top_k: 1` is honoured and gives the greedy reply ("The sea is a vast,
  ever-changing…" instead of "The sea whispers…"); `temperature: 0.01` is
  honoured too, but only as a low temperature — near-greedy, and on the QAIRT
  lane it left the greedy text within a sentence. The CPU lane has the same
  defect, without the fixed seed: its T=0 replies differ every time, its
  `top_k: 1` replies never; at `temperature: 0.01` two of three interleaved
  replies differed in two of the three probe runs.
- **An identical request sent twice in a row takes a cache path that changes
  the reply**, on both lanes. QAIRT prefills 15 of 28 prompt tokens and returns
  another sentence even at `top_k: 1`; llama.cpp prefills 0 and samples its
  first token from the *previous* reply's logits — the second reply opens
  `' seabed\n</think>'` or `' time to think'` where `<think>` belongs, and
  `cache_prompt: false` does not help. Any client retry, and every benchmark
  `--repeats`, hits it.

What that means for measuring:

- **`--repeats` on this lane was a chain of identical follow-ups**, not draws:
  attempt 2 always came directly after attempt 1 of the same case. That is why
  repeats differed while the whole suite reproduced exactly between versions.
  The task order never mattered.
- **Fixed in the harness**: `bench_tools` and `bench_coding` now send a
  throwaway request between repeats of one case (`client.spacer`), the
  determinism probe and the contract's determinism checks do the same, and two
  new contract checks — `temperature0_is_greedy` and `identical_repeat_intact`
  — name both defects so an upgrade that fixes them shows up in `--diff`. With
  the spacer, the NPU lane's repeats are identical (a fixed seed from a cold
  start), so they cost time and add no sample; the CPU lane's are real draws.
- **For a reproducible CPU-lane run, send `top_k: 1`** (a backend entry's
  `request_extra`), not `temperature: 0`.

## v0.7.0's new knobs, measured

**`--power-mode` is applied, and every change of it is a full model reload.**
v0.7.0 accepts `power_mode` per request and refuses nonsense. The mode is part
of the lane's model cache key: the `power_saver` request freed every QNN
context, reloaded the bundle (12.3–12.7 s) and applied HTP vote
`perf_profile=8`; the next request *without* `power_mode` reloaded it again,
back to the startup vote `perf_profile=5` (lane log, 00:50:37 and 00:51:07
local — [`v070r2-probes/lane-log-excerpts.txt`](../benchmark_results/2026-09-23-geniex-upgrade/v070r2-probes/lane-log-excerpts.txt)). *Corrected:*
the first version of this page read that second vote as the first and said
the bundle's pinned `burst` wins — it does not. What `power_saver` does to the
QAIRT lane's speed and energy was not measured. The CPU lane validates the
value, logs that it is "only meaningful on the NPU device", ignores it — and
still reloads (3 s). So a client that sends `power_mode` on some requests and
not on others reloads the model on every switch; send it on none, or on all.
Power modes also act on the llama.cpp HTP path (§ `geniex-bench` below).

**HTP multicore does nothing here.** The lane logs `HTP device reports 1 NSP
core(s)`; the X126100 has one HTP, so the new QAIRT multicore support has
nothing to spread across.

**The QAIRT system prompt now comes from bundle metadata** — and for this
bundle it changed nothing measurable: `metadata.json` says "You are a helpful
AI assistant.", and every NPU answer in the contract and history probes is
byte-identical before and after the upgrade.

**QNN logs are visible now (PR #1470) — and at `--log info` they cost
throughput.** v0.6.1 dropped every `[QNN]` line; v0.7.0 passes them through
whenever any `--log` level is set, and asks QNN for full verbosity. The NPU lane
then writes three INFO lines per graph execution, four graph parts per token:
about 240 lines per second of decode, 1.3 MB in three minutes. They also show
that the first context load fails once (`Error from rpc transport res[0]:8003`,
`Context create from binary failed … err 1002`) and succeeds on retry — on
every fresh load, whatever the log level (the r2 lanes: 10.4–10.5 s with
`--log info`, 10.0–10.1 s with `--log none`); whether v0.6.1 retried silently
is not known.

## Speed, CPU and energy — `orchestrant-bench speed`, measured over each request

Nine prompts per lane (`--stream`, the NPU lane also through the six-probe
correctness gate). CPU and joules are integrated over each request and
attributed to the process listening on the lane's port; J/token and decode
tok/s are ratios of sums (decode: the tokens after each first one over the
seconds spent decoding them, all nine requests pooled), and "net" subtracts
the idle baseline taken with the model loaded.

| Lane | Version | Decode tok/s | TTFT (avg) | Lane cores | CPU-rail J/token (net) | W | Correct |
|---|---|---|---|---|---|---|---|
| NPU | v0.6.1 | **23.0** | 0.16 s | 0.93 | 0.163 (0.079) | 3.7 | 6/6 |
| NPU | v0.7.0 | 19.6 (**−15 %**) | 0.16 s | 0.98 | 0.187 (0.121) | 3.6 | 6/6 |
| CPU | v0.6.1 | 18.2 | 0.29 s | 7.19 | 0.946 (0.795) | 16.9 | — |
| CPU | v0.7.0 | 19.2 (+5 %) | 0.30 s | 7.26 | 0.921 (0.771) | 17.3 | — |

*Corrected (2026-09-24):* the decode figures on this page were re-derived
under the pooled definition that `orchestrant/benchmark/speed_summary.py`
gives every printer (roadmap OPS-6). The first version averaged the nine
per-request decode rates: 22.7, 19.7, 18.4 and 19.4 here (−13 %), 22.5 for
`--log none` in the log-level table below, and a 29.9 mean for the quiet CPU
probe. The one-line replies of 8 and 12 tokens pulled that mean; pooled, the
NPU loss is −15 % (−14.8 %), as the per-prompt median `bench_compare` prints
(−14.7 %). The concurrency table's rates are the `lanes` tool's and did not
move.

Both CPU rows were taken with 0.5–1.0 cores of other work on the machine; on a
quiet one the same lane decodes at about 30 tok/s on a short reply (28–32) — see
[§ The CPU lane is at the mercy of everything else on the machine](#the-cpu-lane-is-at-the-mercy-of-everything-else-on-the-machine).
The NPU rows do not move with background load.

- **The NPU lane costs the CPU clusters 5–10× less energy per token, gross**
  — 0.15–0.16 J against 0.92–0.95 J on these 256-token runs (about 6×), 0.113
  against 1.13 J on the 2048-token r2 runs (10×, the CPU lane's long replies
  decoding slowly) — with under one core busy against 7.2 of 8. Net of idle
  the ratio is not stable: 6× to 29× depending on whose idle baseline is used
  (*corrected:* the first version said "a tenth", from one pair of baselines).
  What it does not include is the HTP's own draw, for which there is no rail.
- *Corrected:* the first version said the CPU lane's model spends "86–89 % of
  its output inside `<think>`" and reaches "a finished answer in 12.5–13.2 s
  against the NPU lane's 6.6–7.7 s". Both were computed over replies cut at
  the 256-token budget: 6 of 9 CPU replies never left `<think>`, 5 of 9 NPU
  replies were cut too, and the times were time to the cap. Re-measured with
  room to finish, below.
- **The NPU slowdown on v0.7.0 is the logging, measured below** — not the
  runtime.

### Time to a finished answer, re-measured (r2)

The same nine prompts with `--max-tokens 2048`, on fresh `--log none` lanes,
each row recording whether an answer arrived (`v070r2-{cpu,npu}-speed-answer.json`):

| | CPU lane (thinking `Q4_0`) | NPU lane (`Instruct-2507` W4A16) |
|---|---|---|
| answered inside 2048 tokens | 6 / 9 — both code prompts and the blog post still thinking or writing at the cap | 8 / 9 — the blog post cut |
| the five short prompts: time to a finished answer | 6.1–40.4 s | 0.46–18.5 s (the four one-liners 0.5–1.2 s) |
| the six prompts both answered, total | 216.8 s | 99.5 s |
| first answer token (after any thinking) | 14.8 s mean, 5.3–29.2 s | **0.14 s** |
| output that was thinking | 78 % (all rows; 42–94 % where it answered) | 0 % |
| decode, short replies → ~2k-token replies | 20–27 → **12–13 tok/s** | 22–23 → 19.5–20.5 tok/s |
| CPU-rail energy, gross | 1.13 J/token | 0.113 J/token |
| other load | 0.30–0.65 cores | 0.22–2.18 cores |

Three things this adds. **The thinking tax is the whole gap for chat**: the
CPU lane decodes as fast as the NPU on a short reply, and still needs 5–18 s
before the first word of the answer. **llama.cpp's decode falls steeply with
depth**: 26 → 12–13 tok/s by ~2k tokens here, while the QAIRT lane lost 10–14 %.
In this run the long rows ran last and back to back, so heat and order were
confounded with length; a within-reply trace settles it
([`v070r2-probes/depthtrace-cpu.json`](../benchmark_results/2026-09-23-geniex-upgrade/v070r2-probes/depthtrace-cpu.json)).
One reply, on a fresh lane after 30 s of rest, per 256-token window:
**31.6 → 25.2 → 19.3 → 13.4 → 13.2 → 11.3 → 10.6 → 10.0 tok/s** — and a second
reply started straight after, on the now-hot machine, climbed back to 29.7 and
fell along the same curve. It is the growing context, not the temperature: the
CPU lane loses two thirds of its decode rate by 2k tokens, and an agent turn at
8k of context would decode slower still (not measured). The runner's short
prompts had never shown it. And **one thinking reply in three does not fit
2048 tokens** at all. The idle baselines drifted in
both runs (0.9 W and 0.3 W), which the report now flags, so only gross energy
is quoted here.

### The v0.7.0 NPU slowdown is `--log info`, not the runtime

Same lane, same bundle, restarted with `--log none` instead of `--log info`
(the flag this lab and the hub launcher have always passed):

| NPU lane, v0.7.0 | Decode tok/s | Lane cores | CPU-rail J/token, gross (net) |
|---|---|---|---|
| `--log info` | 19.6 | 0.98 | 0.187 (0.121) |
| `--log none` | **22.4** | 0.94 | **0.154** (0.071) |
| v0.6.1, `--log info` (QNN lines dropped) | 23.0 | 0.93 | 0.163 (0.079) |

(The first version also had a cold-load column — 14.8 s against 10.2 s, read
as logging's cost. Fresh lanes load in 10.0–10.5 s at either level; the
14.8 s was the first load after the install.)

The review raised a confound: the `--log info` numbers all came from one lane
process that the contract's `power_mode` check had just reloaded (twice), the
`--log none` ones from a fresh process. So the r2 control started four fresh
lanes, ran nothing before the speed runner, and interleaved the levels
(`v070r2-npu-speed-log{info,none}-{1,2}.json`):

| fresh NPU lane, v0.7.0 | median decode tok/s | CPU-rail J/token, gross |
|---|---|---|
| `--log info`, run 1 · run 2 | 19.32 · 19.36 | 0.131 · 0.128 |
| `--log none`, run 1 · run 2 | 22.40 · 22.42 | 0.100 · 0.106 |

**−13.7 % decode, paired per prompt (every prompt between −12 % and −16 %),
and about +26 % gross energy per token.** The attribution holds, without the
reload. `bench_compare` on a none → info pair now prints `decode −13.6 % …
*** SLOWER ***`, and +0.0 % on none → none.

v0.7.0 decodes within about 3 % of v0.6.1 (decode −2.4 % pooled, the same as
the per-prompt median `bench_compare` prints; the mean of per-request rates
the first version quoted said −0.7 %, paired bootstrap [−2.5 %, +1.8 %]);
what costs 13 % of the decode rate is PR
#1470 handing every QNN line to the lane's logger once any `--log` level is
set, and it costs **+21 % CPU-side energy per token, gross**. *Corrected:* the
first version said +70 % — that compared **net** figures whose single 5-second
idle baselines differed by 0.6 W between the two runs (1.26 W against 1.84 W);
against a shared baseline the net difference is +25–29 %. The speed runner now
takes an idle baseline before *and* after the requests, nets every row against
their mean, and says when they drifted. **Launch v0.7.0 lanes with `--log none`
(or `error`) for anything measured, and switch `info` on to diagnose.** The hub
launcher's default is `-LogLevel info`.

## Concurrency: NPU and CPU lanes at the same time

`orchestrant-bench lanes --lanes geniex-npu geniex-cpu`: each lane alone, then
both at once with one request each.

| | NPU alone | CPU alone | NPU together | CPU together | Sum of rates | Delivered | vs best single lane |
|---|---|---|---|---|---|---|---|
| v0.5.0 (hub page, § 2) | 19.5 | 23.7 | 18.85 | 20.81 | 39.7 tok/s | — | 1.7× (sum) |
| v0.6.1 | 22.9 | 11.5 | 8.8 (−62 %) | 6.2 (−46 %) | 14.9 tok/s | 12.4 tok/s | **0.54×** |
| v0.7.0, NPU `--log info` | 19.6 | 22.8 | 2.6 (−87 %) | 2.6 (−89 %) | 5.2 tok/s | 5.2 tok/s | **0.23×** |
| v0.7.0, NPU `--log none` | 22.5 | 23.7 | 8.9 (−61 %) | 6.7 (−72 %) | 15.5 tok/s | 13.3 tok/s | **0.56×** |
| v0.7.0, both `--log none`, fresh prompts (r2) | 22.6 | 20.7 | 11.0 (−51 %) | 8.9 (−57 %) | 19.9 tok/s | 17.6 tok/s | **0.78×** |
| v0.7.0, fresh lanes, NPU `--log info` (r3) | 19.4 | 29.0 | 4.8 (−75 %) | 5.2 (−82 %) | 10.0 tok/s | 9.6 tok/s | **0.33×** |
| v0.7.0, fresh lanes, NPU `--log none` (r3) | 22.4 | 29.5 | 12.1 (−46 %) | 9.8 (−67 %) | 21.8 tok/s | 19.3 tok/s | **0.65×** |

*Corrected:* the first version reported the **sum** of the two lanes' rates
(0.65×, 0.66×). But each rate is over its own request, and the NPU request
finished 10–12 s before the CPU one, so the CPU lane's rate includes seconds it
ran alone. What the machine delivered — 512 tokens over the joint wall — is
0.54× and 0.56× of the best single lane. `orchestrant-bench lanes` now prints
both, and sends a fresh prompt per phase: here the CPU lane's "together"
request was the "alone" prompt again, served from its cache (TTFT 0.015 s
against 0.3 s).

The re-runs, with a fresh prompt in each phase and every lane started fresh
(`v070r2-lanes.json`, `v070r3-lanes-npulog{info,none}.json`), delivered 0.78×
and 0.65× at `--log none` — better than the first two, still below the best
single lane (in r3, on a quiet machine, that is the CPU lane at 29.5 tok/s).
One sample per cell, and four runs spread from 0.54× to 0.78×: the direction
is settled, the size is not. The r3 pair also re-measures `--log info` on
fresh lanes, without the contract's reloads: it **halves** what the pair
delivers (9.6 against 19.3 tok/s).

The hub's topology advice — "NPU + CPU is the best pair" — is a v0.5.0 result
and no longer holds on any build measured here: since v0.6 the pair delivers
less than the NPU lane alone. llama.cpp on this host spreads over all eight
cores (7.2 busy), and any other thread stalls every one of its matrix-multiply
barriers — but *the mechanism is not settled*: the NPU lane's host threads
were pinned the same way (`n-threads 3`, `cpu-mask 0xe0`) when v0.5.0 gave
1.7×, so they alone do not explain the regression, and restricting the CPU
lane to the other cores was never tried. v0.7.0's `--log info` makes it far
worse (0.23× in the first run, 0.33× on fresh lanes). **Run the CPU lane alone, or not at all while the NPU lane
works.** (One sample per cell; v0.6.1's 11.5 tok/s "CPU alone" is out of line
with its own 18.2 in the speed run and is probably noise.)

## Capability: tool calling and code on the NPU lane

| | v0.6.1 | v0.7.0 |
|---|---|---|
| `bench_tools`, 42 cases × 3 draws | **98/124** (2 errored), 369 s | **98/124** (2 errored), 419 s |
| per category | call 7/15 · selection 13/21 · arguments 15/18 · typed 9/12 · parallel 9/12 · restraint 9/9 · irrelevance 12/12 · use_result 12/12 · recover 5/6 · long 1/1 · deep history 3/3 · repeated error 3/3 | identical |
| `bench_coding --task-set all`, 1 draw | 19/31 measured, 1 cut, 1 skipped (no `cmake`), 464 s | **19/31** measured, the same cut and skip, 475 s (lane at `--log none`) — every task's output token count identical |

Identical scores, category by category — which is what byte-identical QAIRT
outputs predict: `bench_compare` finds 0 cases worse and 0 better (paired sign
test, p = 1.0), after naming `SERVING RUNTIME CHANGED` above the score — and,
stronger than any test, all 124 measured tool replies and all 33 coding outputs
are byte-identical across the versions (the two errored rows have no reply on
either side). The upgrade changed nothing the model says on this lane; the 14 %
longer wall is the v0.7.0 lane's `--log info` (above). The printed interval,
98/124 = 79 % [71–85 %], is too narrow: the three draws of a case are strongly
correlated (5 of 42 cases mixed), and a case-clustered interval is
[66–88 %] (design effect 2.6; `bench_compare` prints it since 2026-09-24 —
the [67–90 %] first written here was p ± 1.96·SE). The weak spots are the model's, and stable. *Corrected — the first
version swapped them:* `call` (7/15) is choosing `list_files` where
`read_file` was wanted (`simple_read`, `nested_path`, 5 misses) plus prose for
"run one test file" (3); `selection` (13/21) is prose — "I don't have direct
access…" — for "show me the contents" (3), `git_status` for an overwrite (2),
the wrong diff (2) and one more prose reply. The "I cannot directly read files"
answers are `arguments`' `path_not_query`. Both failure modes are the ones
`prompts/tool-disambiguation.md` was written for.

## The CPU lane is at the mercy of everything else on the machine

The CPU lane's decode rate in the protocol runs (18–19 tok/s) is not what the
lane does on a quieter machine. Re-measured afterwards, same build, same model:
**about 30 tok/s** — the stored `v070-cpu-q4_0-probe.json` decodes at
28.4–31.8 (30.0 pooled) with 0.13–0.42 other cores, with or without an idle
NPU lane loaded beside it (which uses 0.00 cores). *Corrected:* the first
version said 31–32; that was the runner's "Overall" line, which then counted
prompt tokens too (since OPS-6 it counts completion tokens only, and reads
29.2 for this probe), not the decode rate.

What differed can be recomputed from the reports as `other_cores` — system
busy cores over each request minus the lane's own, `cpu_percent` × 8 −
`lane_cores`. (*Corrected:* the first version said the reports carry that
field; it was added after these runs, and the `v070r2-*` reports carry it.)
During the protocol, VS Code, Defender (scanning the freshly installed
binaries) and the harness held **0.5–1.0 cores**, and the CPU lane's decode
tracks it inversely — 0.52 other cores: 23.2 tok/s; 0.86: 18.7; 0.93: 14.1;
over all 18 protocol rows about −14.5 tok/s per core of other load (bootstrap
[−19, −10], r = −0.90). The NPU lane over the same conditions: 22.3–22.8 tok/s
from 0.1 to 2.0 other cores. llama.cpp keeps every core busy, so one core's
worth of anything else stalls all of its barriers; the HTP does not care.

So **every CPU-lane number, in this page and in the hub's, is conditional on
background load nobody recorded until now**, including the v0.6.1 → v0.7.0 CPU
comparison above (18.2 → 19.2 is inside that noise). The speed runner now
prints `Other load: X cores` beside every run; read a CPU-lane number only
together with it, and take CPU-lane numbers on a machine with the IDE closed.

## `geniex-bench`: Qualcomm's own tool as a reference

v0.7.0 ships `geniex-bench` (llama-bench style: random-token prefill of N, then
N generated, the KV cache reset between runs). Run with every lane stopped,
pp512/tg128, three repetitions each (reports in `geniex-bench/`):

| Cell | Prefill tok/s | Decode tok/s |
|---|---|---|
| QAIRT 4B-Instruct, NPU | 1298 | 18.4 |
| llama.cpp `Q4_0`, CPU | 208–211 | 25.8–26.2 |
| llama.cpp `Q4_0`, NPU (`ggml-hexagon`), `--power-mode burst` | 667 | 12.1 |
| llama.cpp `Q4_0`, NPU, `--power-mode power_saver` | 445 (−33 %) | 7.6 (−37 %) |

- **Power modes are real on the llama.cpp HTP path** — `power_saver` costs a
  third of the speed there. On the QAIRT bundle the vote changes (above) but
  its effect was not measured. What either saves cannot be measured here: the
  HTP has no rail.
- **The tool cannot be used as a clean QAIRT reference as shipped.** It logs at
  trace level — 517,766 lines for one QAIRT cell — and `GENIEX_LOG=none` does
  not silence it; its 18.4 tok/s QAIRT decode sits nearer our lane *with*
  logging on (19.6) than without it (22.4), but it decodes after a 512-token
  prefill where our lane decodes after ~20, so the comparison is not clean.
  Its llama.cpp cells log little (~1,600 lines) and are usable.
- **It is the one place the NPU runs a GGUF here**, and at 12.1 tok/s against
  the QAIRT bundle's 18.4 in the same tool (about 1.5×) it confirms the hub
  page's lane advice: GGUFs belong on the CPU lane, the NPU lane to QAIRT
  bundles.
- Getting it to start at all needs its own `lib\` first on `PATH`: the GenieX
  CLI directory is on the user `PATH`, and a `geniex-bench` of a different
  version then loads the CLI's `geniex.dll` and dies with
  `STATUS_ENTRYPOINT_NOT_FOUND` (it did, against v0.6.1, before the upgrade).

## One `IQ3_XXS` file answers correctly on llama.cpp `4ff829e`

The hub page's rule — never use sub-4-bit i-quants (`IQ1_*`, `IQ2_*`, `IQ3_*`)
on GenieX, because they produce fluent garbage — was measured on llama.cpp
`873e5d8` and `0eadefe`. On v0.7.0's `4ff829e`, the same file, asked three
trivial questions with thinking off (`17 × 3`, the capital of Australia, the
r's in "strawberry") — a hand probe whose replies were not archived; the
speed-runner probes below are (`v070-cpu-iq3_xxs-probe.json`,
`v070-cpu-q4_0-probe.json`):

| `unsloth/Qwen3-4B-GGUF` | v0.6.1, `0eadefe` (hub page § 1n, its three questions) | v0.7.0, `4ff829e` (the three above) |
|---|---|---|
| `IQ3_XXS` | 0/3, `'一侧osesnce majority coli…'` | **3/3** — 51, Canberra, 3 |
| `Q2_K` (control) | 3/3 | 2/3 (miscounts the r's in "strawberry") |
| `Q4_0` (control) | 3/3 | 2/3 (the same miss) |

and every `IQ3_XXS` answer to the speed runner's prompts opens with coherent
reasoning. It decodes at about half `Q4_0`'s rate on the CPU (14–18 against
28–32 tok/s — `Q4_0` gets the repacked ARM kernels), so `Q4_0` stays the
speed choice; but an i-quant may again be a way to fit a larger model. One
file (an Unsloth Dynamic `IQ3_XXS`) and three questions is a smoke test, not a
rule about i-quants: run the correctness gate on any i-quant before relying on
it, and note that `inspect_gguf.py`'s `RISKY` verdict describes GenieX ≤ v0.6.1.

## What the lab itself got wrong

Running the lab on the Windows host, where the lanes, their worker processes
and the power rails live, exposed defects that WSL2 had hidden. Every one either
produced a number that looked right or cost time without saying so. All are
fixed; the fixes and their tests are in this change.

| # | Defect | Effect | Fix |
|---|---|---|---|
| 1 | The determinism probe asked for "the single word: ready" in 8 tokens | A sampling lane repeats a zero-entropy answer verbatim: it recorded the v0.6.1 NPU lane `deterministic: true`, and `bench_compare` then reads that lane's single-draw flips as regressions | 48 open-ended tokens; the lane now records `false` |
| 2 | `cpu_percent` = mean of one sample before and one after the request | Neither saw the inference: a CPU lane pinning 7.2 of 8 cores read as idle | integrated over the request (`cpu_percent_method: "window"`) |
| 3 | No per-lane CPU or energy at all | `energy_proxy()` imports POSIX-only `resource` and counted the *harness's* CPU time, never the server's | `lane_cpu_s`/`lane_cores` from the process listening on the lane's port; joules from the Energy Meter's CPU-cluster rails (below) |
| 4 | No report named the serving runtime | every GenieX number carried its version in prose; `bench_compare` could not say a runtime moved | `provenance.runtime` (CLI, QAIRT, llama.cpp, serve flags); `SERVING RUNTIME CHANGED` |
| 5 | Model auto-detect took `/v1/models[0]` | GenieX lists its whole cache there: `--base-url` alone benchmarked — and hot-loaded — `empero-ai/Qwen3.8-2B-Distill`; on the NPU lane that is the documented GGUF-after-QAIRT crash | a listing of several is refused and named |
| 6 | Glances probed on every resource sample | a refused localhost connect costs 2–4 s on Windows: ≈33 s of dead time per prompt (a 9-prompt run took ~7 min instead of ~2) | a failed Glances is asked once per run |
| 7 | `busy_lanes()` probed every registry entry in turn | ~40 s per report on Windows, and a `GET /v1/models` to the paid host marked `probe: false` on every run | parallel, and `probe: false` honoured |
| 8 | "Busiest process" on Windows | `System Idle Process (pid 0) 737 %` ranked first | pid 0 skipped |
| 9 | Speed reports had no `provenance` block (the Sphinx page said they did) and `bench_compare` read the `hardware` dict as provenance | a lane-speed number could not be tied to a runtime | block added, and compared |
| 10 | Docs: "the QAIRT/NPU path is deterministic" | a v0.5.0 fact; since v0.6 the bundle samples, and an identical follow-up changes its reply (above) | corrected |

## What the review corrected

After this page was first written, six independent reviewers went over the lab
— statistics, measurement fidelity, workload validity, the published claims
against the raw JSON, operations, and outside practice — and every proposal
was handed to a verifier told to refute it: of 68, 25 were confirmed, 33
survived in part, 9 were judged not worth doing and 1 was refuted. The
arithmetic held (about 45 published numbers recomputed from the
JSON, all matching); the readings did not always. Fixed in the code, with
tests, and re-measured where a number moved:

| # | What was wrong | Effect on this page | Fix |
|---|---|---|---|
| 11 | A `<think>` that never closed scored **0 %** thinking, the summary averaged only the rows that closed, and time to the token cap was printed as "time to a finished answer" | "86–89 % inside `<think>`" was ~95 %; "12.5–13.2 s to a finished answer" was 12.5 s to the 256-token cap — 6 of 9 CPU answers never left `<think>` | `answers.py`: `finish_reason`, `answered`, `ttfa_s`; cut rows have no time to an answer; re-measured below |
| 12 | `temperature: 0` is read as "unset" by GenieX, on both lanes | "T=0 samples" was recorded as a property of the models since v0.5 | contract `temperature0_is_greedy`; send `top_k: 1` for greedy |
| 13 | An identical follow-up request takes a cache path that changes the reply, on both lanes | the determinism probe, the contract's T=0 and seed rows, and every `--repeats` attempt after the first measured that path; the NPU "order dependence" was this | `client.spacer` between repeats, spaced probes, contract `identical_repeat_intact` |
| 14 | `bench_compare` reduced a speed report to its correctness score plus summed latency (25 % tolerance) | passed the v0.7.0 NPU −13 % (−15 % pooled) as "no regression detected"; compared nothing between CPU runs; exit 0 on contract pairs | `compare_speed.py`: decode/prefill/TTFT paired per prompt, noise-scaled; CPU-lane verdicts withheld above 0.3 other cores; exit 3 when nothing was compared |
| 15 | Net energy against one 5-s idle baseline per run | "+70 % CPU J/token" for `--log info` (really +21 % gross) | baseline before and after, rows netted against the mean, drift reported |
| 16 | `lanes` summed per-lane rates over unequal windows | 0.65×/0.66× (delivered: 0.54×/0.56×) | delivered throughput printed beside the sum; fresh prompt per phase |
| 17 | `power_mode`'s second HTP vote read as its first | "the bundle wins" — it does not | corrected above; the contract restores the lane after checking it |
| 18 | On a redirected Windows stdout (cp1252), `→` crashed `contract --diff` and `bench_compare` with exit 1 — their "changed"/"REGRESSION" code | none published; an unattended run would have reported a false regression | `client.utf8_stdio()` in every CLI |
| 19 | `tool_sha256` hashed CRLF bytes and was taken at the end of a run | a Windows and a WSL checkout of one commit disagree; a mid-run edit mislabels a report (one happened here) | line endings normalised for every tool; the speed runner also takes the hash at start and names a change (every other tool since 2026-09-24: roadmap P1.5) |
| 20 | Agreeing repeats counted as independent trials; `--turn-growth` dropped `--tools`/`--context-tokens`; `bench_sweep` put the repository's parent on `PYTHONPATH`; four contract checks could answer yes/no on empty evidence | intervals too narrow by up to √repeats; a mislabelled turn-growth run | each fixed, with a test |

The fixes were then audited the same way — code, claims and docs, each finding
handed to a verifier — and that caught the fixes' own mistakes: a speed pair
whose only change was `max_tokens` read as a regression, the answer column of
`report table` and the viewer favoured runs that cut more replies, `contract
--base-url` detected the model on the wrong server, the load rule hid a slower
run when only the *old* run was loaded, the lanes report still stored the sum,
and this page overstated four readings (the depth effect, "triples", the
energy ratio, the greedy setting). Each is fixed above or in the code, with a
test, and the depth and log-level readings were re-measured (r3).

## Energy: what the CPU-cluster rails can and cannot say

The Snapdragon X exposes Windows Energy Meter Interface rails `CPU_CLUSTER_0`
and `CPU_CLUSTER_1` (cumulative picowatt-hours, stamped in milliseconds since
1601, published about once a second); `SYS`, `PSU_USB` and `USBC_TOTAL` read
zero, and there is no NPU or GPU rail. So the lab can now say what an answer
costs the **CPU**, per token, measured — which is the half of the NPU-vs-CPU
question that the lanes actually differ on — and nothing about the HTP's own
draw. The meter's timestamps agree with the system clock to within 0.1 s
(measured: a new sample is observed 0.03–0.09 s after its stamp), which is what
lets `energy_between()` interpolate onto a request's exact bounds. Two cautions
from these runs: a sub-second answer's joules are dominated
by the meter's one-second granularity and background load (the summary is
therefore a ratio of sums, not a mean of per-request ratios), and the host is
not idle — VS Code held about one core and Defender woke after the install —
which the idle baseline nets out only while it is steady.

## What to do next

In the order the evidence above supports:

1. **Run `orchestrant-bench contract` on both lanes after every GenieX update,
   and `--diff` it against the previous run** — before any capability number is
   trusted. It found in minutes what the upgrade notes did not say.
2. **Report the two GenieX defects upstream**: `temperature: 0` read as
   "unset" (a Go zero value; `top_k: 1` is greedy, 0.01 only near-greedy), and the identical
   follow-up request that is answered from stale logits (llama.cpp: 0 prompt
   tokens, a first token from the previous reply) or a different dialog state
   (QAIRT). The probes and the two contract checks are the reproduction. Until
   fixed: `top_k: 1` for greedy runs, and never an identical retry.
   *(This replaces the `--primer` idea of the first version: the NPU lane was
   never order-dependent, and the harness's spacer now does what a primer
   would have.)*
3. **Send `power_mode` on every request of a lane or on none**: each change is
   a model reload (12.3–12.7 s NPU, 3 s CPU). Its effect on the QAIRT lane's speed
   and energy is the open measurement.
4. **Give the lane launcher (ANTfrastructure `Start-GeniexServers.ps1`) four
   upstream fixes**: a `-LogLevel` default of `none` (its `info` costs v0.7.0's
   NPU lane 13 % of its decode rate and halves what an NPU+CPU pair delivers, above),
   a way to start only the lanes asked for (it always starts a GPU lane),
   `-PowerMode` applied to every request of a lane or none, and a bundle-kind
   check that does not read `/v1/models[0]` — that listing is the whole model
   cache, not the loaded model.
5. **Retire the NPU+CPU topology advice.** The hub's "NPU + CPU is the best
   pair, 39.7 tok/s" was a v0.5.0 measurement; since v0.6 the pair delivers
   0.54–0.78× the NPU lane alone (three single-sample runs).
6. **Take CPU-lane numbers on a quiet machine, and read them with `Other
   load`.** Under 0.5–1.0 cores of background work the lane lost about 20–55 %
   of its decode rate; the NPU lane lost nothing. `bench_compare` now withholds
   a CPU-lane speed verdict that load could explain — a slower run measured
   over 0.3 other cores, or a faster one against a loaded baseline — and derives
   the load for reports older than the field; refusing to *take* such a
   measurement is the next step.
7. **Update the hub's GenieX page with a v0.7.0 section** from this one; its
   § 1n table is the v0.6.1 record and several of its claims no longer hold:
   QAIRT has no cache, QAIRT is deterministic, T=0 samples on the GGUF lanes
   (it is a server defect), the NPU lane is immune to contention, and one
   `IQ3_XXS` file now answers correctly where the page says every sub-4-bit
   i-quant is broken. Add what decides a lane here: on a short reply and a
   quiet machine the `Q4_0` CPU lane decodes faster than the NPU (~30 against
   22.4 tok/s), at ~2k tokens of depth far slower (10–13 against ~20), and it
   pays 5–30 s of `<think>` before any answer — the NPU's case for chat is
   time to an answer, energy and load immunity, not the short-reply decode
   rate.
8. **Measure what the agent decision turns on**, from the review's backlog: a
   prefill and decode curve at agent sizes (`contract --only prefix_cache
   --prefix-tokens 5000`, then 8000 and 12000, no code needed; and the
   depth trace above at 8k) for the recommended 9B-Distill; the same
   model on both runtimes (`unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_0` on the
   CPU lane) so lane and model stop being confounded; `bench_agent --repeats`
   with pass^k; and case-clustered intervals — at today's pool sizes a
   10-point drop is caught with 8–24 % power.
