LLM endpoint benchmarking
=========================

``orchestrant-bench`` measures any OpenAI-compatible endpoint -- Ollama, GenieX,
llama.cpp, vLLM, a hosted API -- and is the runner half of the family's LLM
benchmark suite. The capability benchmarks (coding, tool calling, the agent
loop) live in ``benchmarks/`` and import this package rather than owning their
own transport.

Commands
--------

.. code-block:: text

   orchestrant-bench speed     --backend ollama --prompts 10
   orchestrant-bench lanes     --batching --backend ollama
   orchestrant-bench report    summary results.json
   orchestrant-bench contract  --backend geniex-npu --output npu.json
   orchestrant-bench runtimes  --output lanes-runtime.json geniex-npu geniex-cpu

``speed``
   Throughput, time-to-first-token, decode vs prefill, time-to-answer and a
   generic correctness probe. Each row says whether an answer arrived at all
   (``finish_reason``, ``answered``, ``ttfa_s``): a reply cut at
   ``max_tokens`` has no time to an answer, and the summary prints
   ``Answered k/n``. Writes the result envelope the viewer reads, with a
   ``provenance`` block (runtime build, serve flags, host power mode, and the
   source hash -- with ``source_changed_during_run`` ``false`` when the start
   hash was checked and matched, ``true`` plus ``tool_sha256_at_start`` when
   the source moved while the run went, and absent when no start hash was
   taken) and an ``energy`` block, with ``--output``.

``contract``
   Re-checks the server behaviours the tooling relies on -- ``max_tokens``,
   usage reporting, temperature-0, near-greedy and ``seed`` determinism, whether
   temperature 0 *is* greedy, whether an identical request sent twice in a row
   gets the same reply, stop sequences, tool call parsing, the prefix cache and
   prefill rate, context overflow, and whether a per-request ``power_mode`` is
   validated -- and ``--diff`` names every answer that moved between two
   runtimes. Run it after each server upgrade.

``lanes``
   Streaming, batching and multi-lane additivity: does one server overlap
   concurrent requests, and do several lanes add up? Named lanes resolve from
   the backend registry; a full ``name=URL,model=MODEL`` spec also works.

``report``
   Summaries over result directories, plus the viewer manifest the benchmark
   dashboard consumes.

``runtimes``
   Run on the host that serves the lanes: snapshots each lane's build, serve
   flags and model files into a JSON file. A tool run from WSL2, where the
   Windows-side lane process is invisible, reads it through
   ``LLM_LANE_RUNTIMES``.

Host GPU
--------

Result files record the local accelerator under ``hardware.gpu`` (vendor,
name, VRAM total/used, backend) whenever one is readable -- NVML for NVIDIA,
ADL on Windows or amdgpu sysfs on Linux. Each per-prompt result also carries
``gpu_utilization_percent``, ``gpu_memory_used_gb`` and ``gpu_power_watts``
when the probe answers, so a run that silently fell back to CPU is visible in
the table. Against a remote endpoint, or on a host with no readable GPU, the
fields stay absent rather than reporting a fake zero. The viewer renders them
as hardware rows, a comparison column and a chart.

CPU and energy over the request
-------------------------------

When the harness shares a host with the lane, each per-prompt result measures
the request itself rather than sampling around it: ``cpu_percent`` is the
system busy share integrated over the request (``cpu_percent_method:
"window"``), ``lane_cpu_s`` / ``lane_cores`` are the CPU-seconds of the process
tree listening on the lane's port, and on Windows hosts that expose the Energy
Meter Interface ``cpu_rail_energy_j`` and ``cpu_rail_j_per_token`` come from the
CPU-cluster rails, interpolated onto the request's bounds, with ``*_net_*``
variants net of the mean of two idle baselines, taken before and after the
requests (``energy.idle_drift_w`` says how far they moved). ``other_cores`` is
everything else the machine did during the request -- a CPU lane's rate falls
with it, and ``bench_compare`` does not judge a CPU-lane speed change measured
over more than 0.3 of them. The rails cover the CPU clusters only; an NPU
or GPU lane's own draw is not metered, and the report's ``energy.scope`` says
so. Against a remote endpoint, or from WSL2 in front of a Windows-host lane,
these fields are absent and ``cpu_percent_method`` reads ``"before/after
snapshots"``.

Named backends
--------------

The registry is owned by the serving stack (ANTfrastructure's
``linux/llm-stack/backends.json``), because the lanes it names are host
configuration. Resolution order, most specific first:

1. ``--base-url`` on the command line
2. ``LLM_BASE_URL`` / ``OLLAMA_BASE_URL`` in the environment
3. ``--backend <name>`` from the registry
4. the entry the registry marks as ``default``

The registry file is found through ``LLM_BACKENDS`` when set; otherwise the
family checkout's hub file when OrchestrANT is developed with its submodule
(``third_party/ANTfrastructure/linux/llm-stack/backends.json``); otherwise a
copy next to the module. A missing registry is not an error -- an explicit
``--base-url`` still benchmarks.

Viewer (Reflex)
---------------

``frontend/`` holds the Reflex app (the ``frontend`` extra). It reads the
manifest the runner writes directly — no build or copy step:

.. code-block:: bash

   cd frontend
   reflex run

The default manifest is ``benchmarks/benchmark_results/_manifest.json``; point
it at a run-scoped directory with ``ORCHESTRANT_BENCHMARK_MANIFEST``. The table,
interval and manifest shaping lives in pure Python
(``frontend/frontend/benchmark_data.py``) and is tested without Reflex.

Offline tests
-------------

``tests/unit/benchmark`` runs offline: a conftest refuses any connection to a
port no test in the process is listening on, and the two live-contract modules
(``test_v1_api.py``, ``test_harness_against_ollama.py``) skip themselves when
nothing answers on the endpoint.
