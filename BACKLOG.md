# Backlog

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
