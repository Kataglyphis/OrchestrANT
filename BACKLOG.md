# Backlog

- [ ] **LAI-P3: the NAS document AI** (L, ★★). Hub language-AI milestone 2 (`docs/language-ai.md`).
- [ ] **LAI-P4: classification, entities, summaries and translation** (M, ★★). German and English with
  a JSON schema; hub language-AI milestone 2.
- [ ] **LAI-P5: `yolo-monitor --describe`** (M, ★). Hub language-AI milestone 4.

- [x] **llguidance probe on the hub's tiny GenAI model (ai-fleet clause 12).** GenAI 0.17.0 in
  `:latest` (index `7d4d4129`) ACCEPTS the tiny model's byte-level tokenizer: `set_guidance("json_schema", ...)`
  then greedy decoding of `abc` under
  `{"type":"object","properties":{"a":{"type":"string","maxLength":3}},"required":["a"],"additionalProperties":false,"x-guidance":{"whitespace_flexible":false}}`
  yields `{"a":"#$%"}`, ids `[123, 34, 97, 34, 58, 34, 35, 36, 37, 34, 125]`, then end token 256.
  So the schema tests use the tiny model and need no SmolLM2-135M fallback; SmolLM2 accepts the same
  schema too (`{"a":"par"}`). A schema without `maxLength` and with flexible whitespace makes the tiny
  model loop on `\t\n` until the budget, which is why the tests pin the tight one. Seeds 1 to 4 at
  temperature 1.0 give two distinct 24-token sequences on the tiny model (seed 1 differs). The Windows
  GenAI builds without llguidance (hub LAI-H2), so a schema there is `json_schema_unsupported`.

- [ ] **Regenerate and prove the CPU and GPU release inventories.** The tracked
  `packaging/THIRD-PARTY-NOTICES.txt` predates the GPU-specific inventory labels. Build
  the four CPU archive variants for the intended release round and a representative
  archive for each GPU variant being shipped, generate the notice from those archives,
  then rebuild the packages with the resulting notice. Run the notice and package
  licence checks on every finished CPU and GPU artifact. Both Windows DirectML image
  builds share the `windows-x64-directml` label: check each vendor's ZIP independently
  against the notice; do not assume that their inventories match. Validate the original
  model and native license texts in the corresponding DEB, AppImage, MSI and MSIX too.
  The DirectML candidate's licence gate remains red until this is done.
  Its `inventory-only` workflow input produces separately labelled, unverified ZIPs for
  that round without exposing the candidate as an installer or skipping the normal gate.

  The cached 2026-10-27 packages under `ANTfrastructure/logs/goal/art3` are useful
  historical inputs, not a current release round. Both cached Windows ZIPs contain
  Pillow 13.0.0 but their embedded notices list Pillow 12.3.0, and their notice checks
  fail. The local AMD candidate also used a proposed hub overlay and cached Python
  3.14.4, so it must not be combined with those archives as proof of the next release.
