<!--
Copyright (c) 2025 Kataglyphis
SPDX-License-Identifier: MIT
-->

# The gateway's acceptance, 2026-09-25 — the lab measures through APISIX

Measured on 2026-09-25 between 16:44 and 17:52 UTC, on the lab host of the
[GenieX v0.7.0 page](geniex-v0.7.0-cpu-npu-2026-09-24.md#protocol). The software
was GenieX v0.7.0 (QAIRT 2.45, llama.cpp `4ff829e`), with the NPU lane
(`qualcomm/Qwen3-4B-Instruct-2507:W4A16`) and the GPU lane
(`unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_0`) up together and the CPU lane down.
The gateway was the llm-stack's APISIX 3.18.0 (`apache/apisix:3.18.0-debian@sha256:84e6b5e7…`),
rootless under nerdctl in WSL, rendered from ANTfrastructure `feeb0b75`. The
config sha was `8975a754ebd6`, and the registry, the two Lua files and the tools
prompt hash exactly as `/gateway/info` reported them in every gateway report. The
lab client was this repository at `4b0942c`. How the gateway works:
[the Gateway section of the hub's llm-stack README](../../third_party/ANTfrastructure/linux/llm-stack/README.md#gateway).
What the acceptance asks:
[`benchmarks/README.md`, *Through the gateway*](../README.md#through-the-gateway-lab--backends).

The raw files are in
[`../benchmark_results/2026-09-25-gateway-acceptance/`](../benchmark_results/2026-09-25-gateway-acceptance/):

- one `.json` and one `.log` per step;
- `steps.log`, on a local clock two hours ahead of UTC;
- `gateway/requests.jsonl`, the gateway's own log, one line per request;
- `gateway/apisix.json`, the rendered config, with keys as `${{GW_KEY_*}}`
  placeholders only;
- `derive.py.snapshot`, which computed every number below from those files.

The steps ran twice. The first run's gateway steps were all refused: WSL stops
a distro soon after its last session closes, and it took the gateway with it.
The second run held one WSL session open and repeated every gateway step. The
direct baselines are the first run's, taken against lane processes started
for that run (NPU pid 16152). The gateway reports are the second run's (NPU pid
20532), with the same binary and serve flags.

**What it answers.**

- **The gateway is transparent** on the two lanes measured.
  - On `raw-npu` and `raw-gpu` no contract answer changed: 21 of 21 checks on
    each lane.
  - All 42 tool cases got the direct run's verdict, and 41 got byte-identical
    replies. The 42nd is the case the NPU refuses as too long, both ways.
  - On the NPU it adds a median 10 ms to the time to the first token (−8 to
    +28 ms per prompt, nine prompts) and changes no decode rate.
- **`lab-chat` shapes as designed.**
  - It scores what the NPU with the tools prompt scored (41 shared cases, no
    flip).
  - It answers the two things the NPU cannot: the tool case the NPU refuses,
    and the three ~7k-token documents. It does so by sending them to the GPU
    before the NPU sees them.
- **It also shows a cost.** The size rule that moves requests is
  conservative. It sent the three ~3.1k-token documents, which the NPU answers
  in 3.4–4.1 s, to the GPU, where they took 45.5–45.8 s. How to loosen it is
  an owner decision ([§ The size rule](#the-size-rule)).
- **It closes an open question from P8.1.** The NPU's refusal of
  `long_result_find_failure`, unexplained in
  [P8.1](roadmap-campaign-2026-09-24.md#p81--the-recommended-tool-configuration),
  is a context overflow. The refusal body, which the lab now keeps, reads
  `context_length_exceeded` with 4353 prompt tokens, against the bundle's 4096.

## Stage A — transparency

The same benchmark was run on each direct lane and on its `raw-*` alias
through the gateway. A `raw-*` route applies no shaping: it re-encodes the
body and forwards it.

| `geniex-npu` → `lab-raw-npu` | direct | through the gateway |
|---|---|---|
| contract, 21 checks | — | **0 answers changed** |
| `bench_tools`, 42 cases, no system prompt | 33/41 = 80 % [66–90 %], 1 errored | **33/41 = 80 % [66–90 %], the same errored case** |
| … paired | — | 41 ties, no flip either way; same verdict 42/42; byte-identical reply (`message_sha256`) 41/42 |
| … seconds per call | 2.90 | 2.89 |
| `speed --stream`, 9 prompts, decode (pooled) | 22.68 tok/s | 22.75 tok/s |
| … time to first token, median | 0.141 s | 0.155 s (+10 ms median per prompt, −8 to +28) |
| … prefill (pooled) | 318.6 tok/s | 300.3 tok/s |
| … completion tokens | identical on all nine prompts | |

On `geniex-gpu` → `lab-raw-gpu`, the contract also changed no answer: 21 of
21 checks. `overflow_is_clean_error` was skipped both ways, because the
contract was run without `--overflow-tokens`.

**The gateway's log agrees with the lab.** In every gateway step, the lines
the gateway logged equal the replies that the report's
`provenance.gateway.served` counted: 48, 46, 10 and 47 for the four Stage A
steps. Each carries its step's route and lane and `rerouted: no`. The 400s in
the log are the lane's own refusals, passed through unchanged:

- the contract's overflow probe (5390 prompt tokens);
- one request each on `raw-npu` and `raw-gpu`, logged with 0 prompt tokens;
- the tool case above (4353).

Outside the steps, the log holds only the lab's `/gateway/info` and
`/v1/models` reads and one keyless request, which got a 401.

**Not measured in Stage A**, although the README asks for it:

- `bench_chat` and `speed --correctness-only` on a `raw-*` lane;
- anything on `raw-cpu`: the CPU lane was down.

## Stage B — shaping, `lab-chat`

**Tools.** `bench_tools.py --backend lab-chat` sends no system prompt. The
gateway adds `tool-disambiguation.md`, pinned to the bytes P8.1 measured:
`prompts_sha256` in every report's `/gateway/info` is `970a8e4f…`.

| | P8.1: `geniex-npu --system tool-disambiguation.md` | `lab-chat` |
|---|---|---|
| score | 40/41 = 98 % [87–100 %], `long_result_find_failure` errored | **41/42 = 98 % [88–100 %]**, nothing errored |
| failed | `deep_history_version` | `deep_history_version` |
| paired over the 41 shared cases | — | 41 ties, no flip either way |
| `long_result_find_failure` | HTTP 400 | **passed**, on the GPU: estimated too large, sent there first; 89.5 s, 4787 prompt tokens |
| seconds per call | 2.75 | 4.77 (2.71 without that one case) |

P8.1 ran from an older lab commit (`8e3db14`), with the GPU lane down; this
run had both lanes up. bench_compare names both differences.

**Chat.** `bench_chat.py --backend lab-chat`, 33 cases, one draw:

| | NPU (P7.7) | instruct GGUF, CPU lane (P8.2) | **`lab-chat`** |
|---|---|---|---|
| score | 27/30 = 90 % [74–97 %], 3 OVERFLOW | 32/33 = 97 % [85–99 %] | **30/33 = 91 % [76–97 %]** |
| failed | `avoid_a_word`, `json_booleans`, `json_capitals` | `avoid_a_word` | `avoid_a_word`, `json_booleans`, `json_capitals` |
| ~7k-token documents | refused | 3/3, 122–142 s | **3/3 on the GPU, 164.8–165.0 s** |
| ~3.1k-token documents | 3/3, 3.4–4.1 s | 3/3, 36–38 s | **3/3 on the GPU, 45.5–45.8 s** |
| a non-document case, median | 1.03 s | 1.79 s | 0.99 s |

Paired results:

- Against the NPU, over its 30 cases: 30 ties, the same three misses.
- Against the instruct GGUF: 2 cases one way (`json_booleans` and
  `json_capitals`, the NPU's misses) and none back. p = 0.5, −6.1 points
  [−14.3, +2.2]: not separable.

Where the NPU answers, `lab-chat` answers as the NPU does. Where it does not,
the GPU answers.

**Contract.** `contract --backend lab-chat --overflow-tokens 6000` against
the direct NPU:

| check | direct NPU | `lab-chat` | why |
|---|---|---|---|
| `power_mode_understood` | yes | no | the gateway drops `power_mode`, as designed; every setting is "accepted" and none reaches the lane |
| `overflow_is_clean_error` | yes | no | as designed, the ~6000-token request is answered (HTTP 200, 105.4 s, on the GPU), not refused |
| `prefix_cache_extend` | yes | no | the extended request is 2110 tokens, but its estimate crossed the budget, so it went to the GPU and re-prefilled cold (25.7 s against 1.8 s) |
| the other 18 | | unchanged | |

The first two changes are the ones the acceptance expects. The third is the
size rule again.

**Not measured in Stage B:** the NPU refusing an overflow and the hook
retrying it on the GPU, streamed or not. It is covered by the hub's e2e suite
against fake lanes (63 passed), but on these runs the size estimate caught
every large request first, so the fallback never ran on a real lane. The
acceptance's last Stage B item raises `bytes_per_token` until the estimate
never fires, and that run was not made. `lab-chat-long` and `lab-agent` were
not run.

## The size rule

`chat` sends a request to the GPU first when
`ceil(body bytes / estimate.bytes_per_token) + max_tokens` exceeds the route's
`budget_tokens`. The registry sets 3.0 bytes a token and a budget of 3900
tokens. When `max_tokens` is absent, the rule counts `default_reserve`
(1024). On this run it moved 10 requests, and every one got a 200 from the
GPU: one tool case, six documents and three contract requests.

Only the two components can be measured: bytes per token for each kind of
content, and whether `max_tokens` needs counting.

| request | body bytes | tokens counted by the lane | bytes a token |
|---|---|---|---|
| `long_result_find_failure`, no prompt (the NPU's 400) | 11,964 | 4353 | **2.75** |
| … with the tools prompt (the GPU's reply) | 13,787 | 4787 | **2.88** |
| `doc_3.5k_door_code` (the GPU's reply) | 13,159 | 3130 | **4.20** |

JSON tool traffic runs denser than 3.0 bytes a token and prose sparser, so no
single ratio fits both. The three ~3.1k-token documents went to the GPU for
two independent reasons: at 3.0 bytes a token, the body alone is estimated at
4387 tokens, over 3900; and the rule adds `max_tokens` (2048). The NPU does
not reserve the reply's budget: it answered these very prompts under that
budget in P7.7.

Keeping them on the NPU takes both changes: a ratio of at least 3.4 and no
`max_tokens` term. At 4.2 with no term, their estimates are 3134. The
refused tool case (4353 true tokens) would then also be estimated under the
budget, at 2849 without the prompt and 3283 with it. The NPU would refuse it
in about 50 ms, and the hook would retry it once on the GPU. That is the
overflow fallback the previous section says was never run on a real lane.

**The owner's decision.** A looser rule makes that fallback, not the estimate,
the gate for tool traffic.

- **Keep the rule as it is.** Safe, but the medium-sized half of chat pays
  about 11–13 times the time.
- **Loosen it** (for example 4.2 bytes a token and no `max_tokens` term),
  after running the overflow row live. Before that, answer one question: what
  the NPU does with a reply that runs past its context. That was never
  measured, and it is the reason the term is there.

## What the procedure got wrong

- **bench_compare pairs by label.** A direct report is keyed by the model id,
  a gateway report by its alias, so every pairing above printed
  NOTHING COMPARED. For `bench_tools` and `bench_chat`, give the gateway run
  the direct run's label (`--label qualcomm/Qwen3-4B-Instruct-2507:W4A16`).
  bench_compare then pairs them and prints its gateway and `backend_entry`
  notes (checked on this run's tool reports: 33/41 → 33/41, unchanged). The
  speed runner has no `--label`, so a speed pairing is by hand: this page's
  snapshot pairs by `prompt_index`.
- **WSL must stay up.** The first run lost its gateway when the last WSL
  session closed. The hub README now says so, and the gateway serves only
  while one session, such as a `sleep infinity` keeper, holds the distro.
- **Host load is blind behind the gateway.** Every gateway report records
  `host_load.other_cores: null`, because the runner looks for a local process
  on the gateway's port, not the lane's. The speed rows' lane CPU has the same
  blind spot. The direct runs recorded 0.14–1.94 other cores at their starts.

## How the numbers were computed

```bash
# from the repository root, in the project's environment
PYTHONPATH=. uv run --no-sync python benchmarks/benchmark_results/2026-09-25-gateway-acceptance/derive.py.snapshot
```

It reads only the stored reports, the gateway's log, `steps.log`, and the P8
and campaign reports that Stage B is compared with. It rebuilds two request
bodies from `bench_tools`' and `bench_chat`'s own case tables to measure their
bytes. It contacts no lane and writes nothing. Its definitions are § P8's:
Wilson intervals, the paired sign test and paired difference over shared
cases, and no errored, cut, overflowed or skipped row counted as measured.
