LLM endpoint benchmarking
=========================

``orchestrant-bench`` measures any OpenAI-compatible endpoint (Ollama, GenieX,
llama.cpp, vLLM, a hosted API) and is the runner half of the family's LLM
benchmark lab. The capability benchmarks (coding, tool calling, the agent loop)
live in ``benchmarks/`` and import this package rather than owning their own
transport. What each command measures, what every report records and how to
read it is the lab's manual, `benchmarks/README.md`_.

.. _benchmarks/README.md: https://github.com/Kataglyphis/OrchestrANT/blob/develop/benchmarks/README.md

Commands
--------

.. code-block:: text

   orchestrant-bench speed     --backend ollama --prompts 10
   orchestrant-bench lanes     --batching --backend ollama
   orchestrant-bench report    summary results.json
   orchestrant-bench contract  --backend geniex-npu --output npu.json
   orchestrant-bench runtimes  --output lanes-runtime.json geniex-npu geniex-cpu
   orchestrant-bench depth     --backend geniex-cpu --output depth.json

``speed``
   Throughput, time-to-first-token, decode vs prefill, time-to-answer and the
   correctness probe; with ``--output``, the result envelope the viewer reads.
   `Correctness <https://github.com/Kataglyphis/OrchestrANT/blob/develop/benchmarks/README.md#is-it-fast-or-is-it-working---correctness>`__,
   `the figures and the CPU and energy fields <https://github.com/Kataglyphis/OrchestrANT/blob/develop/benchmarks/README.md#reading-the-numbers-ttft-and-time-to-answer>`__.

``contract``
   Re-checks the server behaviours the tooling relies on, and ``--diff`` names
   every answer that moved between two runtimes. Run it after each server
   upgrade.
   `Details <https://github.com/Kataglyphis/OrchestrANT/blob/develop/benchmarks/README.md#did-the-runtime-change-under-you-orchestrant-bench-contract>`__.

``lanes``
   Streaming, batching and multi-lane additivity: does one server overlap
   concurrent requests, and do several lanes add up?
   `Details <https://github.com/Kataglyphis/OrchestrANT/blob/develop/benchmarks/README.md#concurrency-does-one-server-batch-do-lanes-add-up-orchestrant-bench-lanes>`__.

``report``
   Summaries over result directories, plus the viewer manifest the benchmark
   dashboard consumes.
   `The viewer <https://github.com/Kataglyphis/OrchestrANT/blob/develop/benchmarks/README.md#2-run-the-viewer-reflex>`__.

``runtimes``
   Run on the host that serves the lanes: snapshots each lane's build, serve
   flags and model files for a tool in WSL2, which cannot see the Windows-side
   lane process.
   `Details <https://github.com/Kataglyphis/OrchestrANT/blob/develop/benchmarks/README.md#what-every-report-records>`__.

``depth``
   One streamed reply after deep context: the prefill at that depth and the
   decode rate per window of generated tokens.
   `Details <https://github.com/Kataglyphis/OrchestrANT/blob/develop/benchmarks/README.md#how-fast-is-it-once-the-context-is-deep-orchestrant-bench-depth>`__.

Endpoints are named in the serving stack's registry (ANTfrastructure's
``linux/llm-stack/backends.json``):
`resolution order and lookup <https://github.com/Kataglyphis/OrchestrANT/blob/develop/benchmarks/README.md#backends>`__.
A report taken through the llm-stack gateway (a ``lab-*`` backend):
`Through the gateway <https://github.com/Kataglyphis/OrchestrANT/blob/develop/benchmarks/README.md#through-the-gateway-lab--backends>`__.

Viewer
------

The Reflex viewer in ``frontend/`` (the ``frontend`` extra) reads the manifest
the runner writes, with no build or copy step:
`how to run it <https://github.com/Kataglyphis/OrchestrANT/blob/develop/benchmarks/README.md#2-run-the-viewer-reflex>`__.
