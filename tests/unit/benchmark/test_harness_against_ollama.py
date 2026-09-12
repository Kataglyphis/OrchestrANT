"""End-to-end checks of the benchmark harness against a LIVE Ollama.

Everything else covering Ollama uses a stub. A stub is not a server: it proves
the parser accepts the dialect we *believe* Ollama speaks. These tests run the
real code path against the real thing.

They skip cleanly when no Ollama is reachable, so a laptop without one stays
green -- but CI starts a digest-pinned ollama service with a micro model, so
this is where the Ollama backend actually gets verified.

Only the fast checks run by default. The correctness probe is marked
`inference` because it asks for real generations; CI runs `-m "not inference"`.
"""

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
    name = bench.detect_model_via_api(BASE_URL)
    if name == "unknown":
        pytest.skip("server reachable but serving no models")
    return name


class TestDiscovery:
    def test_detects_a_real_model(self, model):
        # Replaces the old hardcoded "gemma4:26b" probe, which returned that
        # name on any 200 and so mislabelled every other host.
        assert model and model != "unknown"

    def test_backend_resolution_reaches_this_server(self):
        url, _, source = bench.resolve_backend("ollama")
        # Env wins over the named backend by design; either way it must be the
        # server these tests are talking to.
        assert url.rstrip("/") == BASE_URL.rstrip("/")
        assert source in ("environment", "backend 'ollama'", "default backend 'ollama'")


@pytest.fixture(scope="module")
def result(model):
    """One streamed request, shared by the metric assertions below.

    Module-scoped and defined at module level on purpose: a class-scoped
    fixture written as an instance method is deprecated in pytest 8 and removed
    in 10.
    """
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
    """The metric that motivated all of this: TTFT was never measured, and the
    SSE parser only matched Ollama's spaced 'data: ' prefix. Both directions
    must hold against the real server.
    """

    def test_stream_is_parsed_at_all(self, result):
        # The regression that started this: matching "data: " WITH the space
        # against a server that omits it parsed nothing and reported 0 tokens.
        # Ollama does send the space, so this guards the other direction.
        assert result["completion_tokens"] > 0

    def test_ttft_is_measured_and_sane(self, result):
        assert result["ttft_s"] is not None, "no first-token time recorded"
        assert 0 < result["ttft_s"] <= result["wall_s_to_answer"]

    def test_decode_rate_excludes_prefill(self, result):
        if result["completion_tokens"] < 2:
            pytest.skip("too few tokens to separate decode from prefill")
        assert result["decode_tok_per_sec"] > 0
        # Dividing by the whole request (tokens_per_sec) can only ever be
        # slower than dividing by the decode window alone.
        assert result["decode_tok_per_sec"] >= result["tokens_per_sec"]

    def test_usage_is_reported_not_estimated(self, result):
        # Ollama honours stream_options.include_usage, so the chunk-count
        # fallback must NOT kick in here.
        assert result["tokens_estimated"] is False
        assert result["prompt_tokens"] > 0

    def test_answer_time_is_recorded(self, result):
        assert result["wall_s_to_answer"] > 0


@pytest.mark.inference
class TestCorrectnessProbe:
    """Runs real generations, hence the marker. Deliberately does NOT assert a
    passing score: a micro model getting an answer wrong is a model result, not
    a harness defect. What must hold is that the probe RUNS and scores.
    """

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
