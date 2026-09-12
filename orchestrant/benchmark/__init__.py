"""Benchmark any OpenAI-compatible endpoint.

The runner half of the family's LLM benchmark suite: one request path, endpoint
metrics (throughput, TTFT, decode vs prefill, time-to-answer), streaming and
batching probes, statistics, and provenance capture. The capability benchmarks
(coding, tool calling, the agent loop) live in ``benchmarks/`` -- they import
this package rather than owning their own transport.

    orchestrant-bench speed   --backend ollama --prompts 10
    orchestrant-bench lanes   --batching --backend ollama
    orchestrant-bench report  summary results.json

Named backends come from the registry the serving stack owns
(``linux/llm-stack/backends.json`` in ANTfrastructure); resolution order is
``--base-url`` > ``LLM_BASE_URL`` / ``OLLAMA_BASE_URL`` > ``--backend <name>``
> the registry's default entry. See ``orchestrant.benchmark.openai_api``.
"""

from orchestrant.benchmark import client, lanes, openai_api, provenance, report, stats


__all__ = ["client", "lanes", "openai_api", "provenance", "report", "stats"]
