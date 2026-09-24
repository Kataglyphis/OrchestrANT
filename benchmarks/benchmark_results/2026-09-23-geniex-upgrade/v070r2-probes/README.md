<!--
Copyright (c) 2025 Kataglyphis
SPDX-License-Identifier: MIT
-->

# Live probes behind two GenieX v0.7.0 defects (2026-09-24)

Hand probes, run against fresh lanes (`serve --compute {cpu,npu} --nctx 16384
--log none`) while the lab's review was being verified. The scripts are stored
as `*.py.snapshot` so the linters do not treat them as source; each wrote the
JSON next to it. They are what the results page's two defect findings and the
`orchestrant-bench contract` checks `temperature0_is_greedy` and
`identical_repeat_intact` rest on; the contract reports `v070r2-*-contract.json`
reproduce both with the lab's own tool.

| Script | Lane | Output | What it shows |
|---|---|---|---|
| `staletok.py` | CPU | `staletok.json` | An identical request sent twice in a row reports `prompt_tokens` 0 and starts mid-sentence (`' is going to write…'`, `' most of the time…'`): its first token comes from the previous reply's logits. `cache_prompt: false` is ignored. |
| `staletok2.py` | CPU, restarted | `staletok2.json` | Interleaved T=0 requests (A, B, A, B, …) still differ after ~60 characters: the lane samples at `temperature: 0`. |
| `temp0.py` | CPU | `temp0.json` | Interleaved: T=0 gives 3 different replies of 3; `temperature: 0.01` and `top_k: 1` give 1 here. T=0 is read as "unset". |
| `lanecheck.py` | CPU | `lanecheck-cpu.json` | The first version: T=0 vs `temperature: 0.01` (interleaved 0.01: **2** distinct replies of 3 — a low temperature, not greedy), then three back-to-back requests at 0.01 — the repeats start `' time to think'`, `' seabed\n</think>'`. |
| `lanecheck.py` (edited to add `top_k: 1`, as stored) | CPU | `lanecheck-cpu-topk.json` | `top_k: 1` interleaved is fully reproducible (0.01 again gave 2 of 3); back to back, the same stale first tokens. |
| same | NPU | `lanecheck-npu.json` | The QAIRT lane honours a nonzero per-request sampler (`top_k: 1` gives the greedy "The sea is a vast, ever-changing…", `temperature: 0.01` starts the same and leaves it within the sentence, T=0 gives the bundle default's "The sea whispers…"); back to back, `prompt_tokens` drops 28 → 15 and the reply changes. |
| `order.py` | NPU | `order-npu.json` | A after B, C or D is the same reply, under the default sampler and greedy alike: the lane is not order-dependent — only an identical follow-up changes the answer. |
| `depthtrace.py` | CPU, fresh | `depthtrace-cpu.json` | Decode rate per 256-token window inside one 2048-token reply, after 30 s of rest: 31.6 → 10.0 tok/s; a second reply straight after climbs back to 29.7 and falls the same way. The slowdown is depth, not heat or run order. |

`lane-log-excerpts.txt` holds the lane-log lines the page cites (the `power_mode`
HTP votes, the QNN load retry, the CPU lane ignoring `power_mode`), with their
source file and line number.
