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

Offline tests
-------------

``tests/unit/benchmark`` runs offline: a conftest refuses any connection to a
port no test in the process is listening on, and the two live-contract modules
(``test_v1_api.py``, ``test_harness_against_ollama.py``) skip themselves when
nothing answers on the endpoint.
