# Backlog

- [ ] **LAI-P3: the NAS document AI** (L, ★★). Hub language-AI milestone 2 (`docs/language-ai.md`).
- [ ] **LAI-P4: classification, entities, summaries and translation** (M, ★★). German and English with
  a JSON schema; hub language-AI milestone 2.
- [ ] **LAI-P5: `yolo-monitor --describe`** (M, ★). Hub language-AI milestone 4.

- **Recorded probe, llguidance on the hub's tiny GenAI model (ai-fleet clause 12).** GenAI 0.17.0 in
  `:latest` (index `7d4d4129`) ACCEPTS the tiny model's byte-level tokenizer: `set_guidance("json_schema", ...)`
  then greedy decoding of `abc` under
  `{"type":"object","properties":{"a":{"type":"string","maxLength":3}},"required":["a"],"additionalProperties":false,"x-guidance":{"whitespace_flexible":false}}`
  yields `{"a":"#$%"}`, ids `[123, 34, 97, 34, 58, 34, 35, 36, 37, 34, 125]`, then end token 256.
  So the schema tests use the tiny model and need no SmolLM2-135M fallback; SmolLM2 accepts the same
  schema too (`{"a":"par"}`). A schema without `maxLength` and with flexible whitespace makes the tiny
  model loop on `\t\n` until the budget, which is why the tests pin the tight one. Seeds 1 to 4 at
  temperature 1.0 give two distinct 24-token sequences on the tiny model (seed 1 differs). The Windows
  GenAI builds without llguidance (hub LAI-H2), so a schema there is `json_schema_unsupported`.

- [ ] **Prove the CPU and GPU release inventories on the installers.** The tracked
  `packaging/THIRD-PARTY-NOTICES.txt` carries the GPU labels since 6f37d0d, one per vendor on
  Windows (`windows-x64-directml-amd`, `-nvidia`). Release dry run 41491524238 passed the
  licence jobs of the Windows CPU and both DirectML builds. Left: confirm the original model and
  native license texts in the DEB, AppImage, MSI and MSIX of a real release round, and regenerate
  the notice for that round when an inventory moves. The `inventory-only` workflow input produces
  separately labelled, unverified ZIPs for this without exposing a candidate as an installer.

- [ ] **A clear failure or a CPU fallback when CUDA has no kernel for the card** (M, ★★). Seen on an RTX 2080 with OxidANT's package; these packages use the
  same image and are untested on such a card.
  `CUDAExecutionProvider` creates its session, then the first node stops with
  `cudaErrorNoKernelImageForDevice`: no detection, no fallback. The images build `86;87;89;120`, so RTX 20xx
  cards fail until hub CON217. Any card outside the list should get a message naming it, or the CPU.

- [ ] **The NVIDIA packages have not run on a GPU they support** (S, ★★★). The Linux and Windows packages are built and
  passed on the CPU, and hosted runners have no GPU. Done on an RTX 30xx or newer when
  `compare_detector_parity.py` passes against the CPU and the profile shows the provider; the hub's
  `docs/gpu-artifact-receipts.md` has the commands. DirectML on an NVIDIA card is unproven too.
  The owner runs this on a machine with an NVIDIA GPU. No release until this and the Linux AMD check are done.

- [ ] **The Linux AMD packages have not run on a GPU** (S, ★★★). They are built and passed on the CPU, and hosted runners
  have no GPU. Under WSL on the RX 9070 XT even a plain HIP copy does not return (hub CON202), so this takes native
  Linux: the owner runs it under Ubuntu on that machine. Done when `compare_detector_parity.py` passes against the
  CPU and the profile shows MIGraphX; the hub's `docs/gpu-artifact-receipts.md` has the commands.

- [ ] **`scripts/diagnostics/detector_parity.py` has no `directml` device** (S, ★★). Its `--device` takes `cpu`,
  `cuda` and `migraphx`, so the DirectML packages were compared through a scratch script on 2026-11-02. Add
  `directml` and make it the documented check for those packages.

## Quality queue

The top 5 of `logs/code-quality/queue.md` (`bash scripts/linux/quality-report.sh`); re-sync when it moves.

- [ ] `file-size.allow:16` budget 3092 (allow_budget_total)
- [ ] `file-size.allow:17` budget 2490 (allow_budget_total)
- [ ] `file-size.allow:18` budget 1759 (allow_budget_total)
- [ ] `file-size.allow:15` budget 1377 (allow_budget_total)
- [ ] `file-size.allow:19` budget 1195 (allow_budget_total)
