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

   orchestrant-bench speed   --backend ollama --prompts 10
   orchestrant-bench lanes   --batching --backend ollama
   orchestrant-bench report  summary results.json

``speed``
   Throughput, time-to-first-token, decode vs prefill, time-to-answer and a
   generic correctness probe. Writes the shared result envelope (provenance
   included) with ``--output``.

``lanes``
   Streaming, batching and multi-lane additivity: does one server overlap
   concurrent requests, and do several lanes add up? Named lanes resolve from
   the backend registry; a full ``name=URL,model=MODEL`` spec also works.

``report``
   Summaries over result directories, plus the viewer manifest the benchmark
   dashboard consumes.

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
