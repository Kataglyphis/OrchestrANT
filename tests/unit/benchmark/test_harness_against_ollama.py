"""End-to-end checks against a live Ollama; skipped when none answers, and CI deselects `inference`."""

import os

import pytest
import requests

from orchestrant.benchmark import openai_api as bench


BASE_URL = (
    os.environ.get("LLM_BASE_URL")
    or os.environ.get("OLLAMA_BASE_URL")
    or "http://localhost:11434"
)


def _reachable():
    try:
        return requests.get(f"{BASE_URL}/v1/models", timeout=5).status_code < 500
    except requests.RequestException:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(), reason=f"no Ollama-compatible server at {BASE_URL}"
)


@pytest.fixture(scope="module")
def model():
    # detect_model_via_api refuses to guess among several models, so pick one explicitly.
    names = bench.list_models_via_api(BASE_URL)
    if not names:
        pytest.skip("server reachable but serving no models")
    return names[0]


class TestDiscovery:
    def test_detects_a_real_model(self, model):
        # The model comes from the server, never from a hardcoded name.
        assert model and model != "unknown"

    def test_backend_resolution_reaches_this_server(self):
        url, _, source = bench.resolve_backend("ollama")
        # Env wins over the named backend by design; either way it must be the server under test.
        assert url.rstrip("/") == BASE_URL.rstrip("/")
        assert source in ("environment", "backend 'ollama'", "default backend 'ollama'")


@pytest.fixture(scope="module")
def result(model):
    """One streamed request for the metric tests; module level, as method fixtures are deprecated in pytest 8."""
    results = list(
        bench.benchmark_chat(
            model,
            ["Say hi."],
            max_tokens=16,
            temperature=0,
            stream=True,
            warmup=False,
            base_url=BASE_URL,
        )
    )
    assert len(results) == 1
    r = results[0]
    assert "error" not in r, f"streaming request failed: {r.get('error')}"
    return r


class TestStreamingMetrics:
    """TTFT and SSE parsing against the real server."""

    def test_stream_is_parsed_at_all(self, result):
        # Ollama sends "data: " with the space, so this guards the parser's spaced direction.
        assert result["completion_tokens"] > 0

    def test_ttft_is_measured_and_sane(self, result):
        assert result["ttft_s"] is not None, "no first-token time recorded"
        # latency_s, not wall_s_to_answer: at max_tokens=16 a cut reply has no time to an answer.
        assert 0 < result["ttft_s"] <= result["latency_s"]

    def test_decode_rate_excludes_prefill(self, result):
        if result["completion_tokens"] < 2:
            pytest.skip("too few tokens to separate decode from prefill")
        if result["decode_tok_per_sec"] is None:
            # Ollama can send a short reply in one burst: the row then has no rate and must say why.
            assert result["decode_rate_note"]
            pytest.skip(f"no decode rate: {result['decode_rate_note']}")
        assert result["decode_tok_per_sec"] > 0
        # Dividing by the whole request can only be slower than dividing by the decode window.
        assert result["decode_tok_per_sec"] >= result["tokens_per_sec"]

    def test_usage_is_reported_not_estimated(self, result):
        # Ollama honours stream_options.include_usage, so the chunk-count fallback must not kick in.
        assert result["tokens_estimated"] is False
        assert result["prompt_tokens"] > 0

    def test_answer_time_is_recorded_only_for_a_finished_answer(self, result):
        # A reply cut at max_tokens reports no time to an answer, not the time to the cap.
        assert result["finish_reason"] in ("stop", "length")
        if result["answered"]:
            assert result["wall_s_to_answer"] == result["latency_s"] > 0
        else:
            assert result["wall_s_to_answer"] is None


@pytest.mark.inference
class TestCorrectnessProbe:
    """Real generations: asserts the probe runs and scores, not that a micro model passes."""

    def test_probe_returns_a_scored_result(self, model):
        probe = bench.run_correctness_probe(model, max_tokens=512, base_url=BASE_URL)
        assert probe is not None, "probe could not reach the server"
        assert probe["total"] == len(bench.CORRECTNESS_PROBES)
        assert 0 <= probe["score"] <= probe["total"]
        assert (
            probe["score"] + probe["wrong"] + probe["truncated"]
            >= probe["total"] - probe["errors"]
        )
        for item in probe["items"]:
            assert "expected" in item and "correct" in item
