"""Tests for the Ollama v1 (OpenAI-compatible) API.

Run against a running Ollama instance:
    pytest linux/llm-stack/tests/ -v
    OLLAMA_BASE_URL=http://localhost:11434 pytest linux/llm-stack/tests/ -v

Skip slow inference tests:
    pytest linux/llm-stack/tests/ -v -m "not inference"
"""

import json
import os
import time

import pytest
import requests


OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

TIMEOUT = float(os.environ.get("TEST_TIMEOUT", "30"))
STREAM_TIMEOUT = float(os.environ.get("TEST_STREAM_TIMEOUT", "120"))
MODEL_LOAD_TIMEOUT = float(os.environ.get("TEST_MODEL_LOAD_TIMEOUT", "300"))
MODEL_GEN_TIMEOUT = float(os.environ.get("TEST_MODEL_GEN_TIMEOUT", "120"))


def _v1(path):
    return f"{OLLAMA_BASE_URL}/v1/{path.lstrip('/')}"


def _ollama_api(path):
    return f"{OLLAMA_BASE_URL}/{path.lstrip('/')}"


def _reachable():
    """Is anything serving at all?

    Six of these tests take only the `session` fixture, so a fixture-level skip
    left them running into the 30s timeout each — the suite took two minutes to
    tell you nothing was listening. Its sibling
    (test_harness_against_ollama.py) skips in seconds; that inconsistency is
    what gets a test suite ignored.
    """
    try:
        requests.get(_v1("models"), timeout=2)
        return True
    except requests.RequestException:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(), reason=f"no OpenAI-compatible server at {OLLAMA_BASE_URL}"
)


@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    s.headers.update({"Accept": "application/json"})
    yield s


@pytest.fixture(scope="session")
def wait_for_ollama(session):
    deadline = time.monotonic() + 120
    last_err = None
    while time.monotonic() < deadline:
        try:
            r = session.get(_v1("models"), timeout=5)
            if r.status_code < 500:
                return
        except requests.RequestException as e:
            last_err = e
        time.sleep(2)
    pytest.fail(
        f"Ollama did not become reachable at {OLLAMA_BASE_URL} in 120s: {last_err}"
    )


@pytest.fixture(scope="session")
def available_models(session, wait_for_ollama):
    r = session.get(_v1("models"), timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return [m["id"] for m in data.get("data", [])]


@pytest.fixture(scope="session")
def default_model(available_models):
    if not available_models:
        pytest.skip(
            "No models pulled — run `ollama pull <model>` or let compose auto-pull on first start"
        )
    return available_models[0]


@pytest.fixture(scope="session")
def wait_for_model(session, default_model):
    deadline = time.monotonic() + MODEL_LOAD_TIMEOUT
    last_err = None
    while time.monotonic() < deadline:
        try:
            r = session.post(
                _v1("chat/completions"),
                json={
                    "model": default_model,
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 1,
                },
                timeout=MODEL_GEN_TIMEOUT,
            )
            if r.status_code == 200:
                return
            last_err = f"status {r.status_code}: {r.text[:200]}"
        except requests.RequestException as e:
            last_err = str(e)
        time.sleep(5)
    pytest.fail(
        f"Model {default_model} not ready after {MODEL_LOAD_TIMEOUT}s: {last_err}"
    )


# ─── Connectivity ────────────────────────────────────────────────────────────


class TestConnectivity:
    def test_ollama_reachable(self, session, wait_for_ollama):
        r = session.get(_ollama_api("/api/tags"), timeout=TIMEOUT)
        assert r.status_code == 200

    def test_v1_reachable(self, session, wait_for_ollama):
        r = session.get(_v1("models"), timeout=TIMEOUT)
        assert r.status_code == 200


# ─── GET /v1/models ─────────────────────────────────────────────────────────


class TestListModels:
    def test_returns_openai_format(self, session, wait_for_ollama):
        r = session.get(_v1("models"), timeout=TIMEOUT)
        assert r.status_code == 200
        body = r.json()
        assert "object" in body
        assert "data" in body
        assert isinstance(body["data"], list)

    def test_model_structure(self, session, available_models):
        for m in available_models:
            r = session.get(_v1(f"models/{m}"), timeout=TIMEOUT)
            assert r.status_code == 200
            model = r.json()
            assert model["id"] == m
            assert "object" in model
            assert "created" in model
            assert "owned_by" in model

    def test_unknown_model_returns_404(self, session):
        r = session.get(_v1("models/does-not-exist-12345"), timeout=TIMEOUT)
        assert r.status_code in (404, 400)


# ─── POST /v1/chat/completions ──────────────────────────────────────────────


@pytest.mark.inference
class TestChatCompletions:
    def test_basic_chat(self, session, default_model, wait_for_model):
        payload = {
            "model": default_model,
            "messages": [
                {"role": "user", "content": "Say exactly 'Hello' and nothing else."}
            ],
            "max_tokens": 10,
        }
        r = session.post(
            _v1("chat/completions"), json=payload, timeout=MODEL_GEN_TIMEOUT
        )
        assert r.status_code == 200
        body = r.json()
        assert body["object"] == "chat.completion"
        assert body["model"] == default_model
        assert len(body["choices"]) == 1
        assert body["choices"][0]["message"]["role"] == "assistant"
        assert "content" in body["choices"][0]["message"]
        assert body["choices"][0]["finish_reason"] in ("stop", "length")

    def test_usage_included(self, session, default_model, wait_for_model):
        payload = {
            "model": default_model,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 5,
        }
        r = session.post(
            _v1("chat/completions"), json=payload, timeout=MODEL_GEN_TIMEOUT
        )
        assert r.status_code == 200
        usage = r.json().get("usage", {})
        assert "prompt_tokens" in usage
        assert "completion_tokens" in usage
        assert "total_tokens" in usage

    def test_multiple_messages(self, session, default_model, wait_for_model):
        payload = {
            "model": default_model,
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say OK"},
            ],
            "max_tokens": 5,
        }
        r = session.post(
            _v1("chat/completions"), json=payload, timeout=MODEL_GEN_TIMEOUT
        )
        assert r.status_code == 200

    def test_streaming(self, session, default_model, wait_for_model):
        payload = {
            "model": default_model,
            "messages": [{"role": "user", "content": "Count 1 2 3"}],
            "max_tokens": 20,
            "stream": True,
        }
        r = session.post(
            _v1("chat/completions"), json=payload, stream=True, timeout=STREAM_TIMEOUT
        )
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "").lower()
        chunks = []
        for line in r.iter_lines(decode_unicode=True):
            if line.startswith("data: "):
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                chunks.append(json.loads(data))
        assert len(chunks) > 0
        assert chunks[0]["object"] == "chat.completion.chunk"
        final = chunks[-1]
        assert final["choices"][0].get("finish_reason") in ("stop", "length")

    def test_temperature_and_top_p(self, session, default_model, wait_for_model):
        payload = {
            "model": default_model,
            "messages": [{"role": "user", "content": "Pick a number"}],
            "max_tokens": 10,
            "temperature": 0.0,
            "top_p": 1.0,
        }
        r = session.post(
            _v1("chat/completions"), json=payload, timeout=MODEL_GEN_TIMEOUT
        )
        assert r.status_code == 200

    def test_missing_model_returns_error(self, session):
        payload = {
            "messages": [{"role": "user", "content": "Hi"}],
        }
        r = session.post(_v1("chat/completions"), json=payload, timeout=TIMEOUT)
        assert r.status_code == 400

    def test_unknown_model_returns_error(self, session):
        payload = {
            "model": "this-model-does-not-exist",
            "messages": [{"role": "user", "content": "Hi"}],
        }
        r = session.post(_v1("chat/completions"), json=payload, timeout=TIMEOUT)
        assert r.status_code in (400, 404)


# ─── POST /v1/completions (legacy) ──────────────────────────────────────────


@pytest.mark.inference
class TestCompletions:
    def test_legacy_completion(self, session, default_model, wait_for_model):
        payload = {
            "model": default_model,
            "prompt": "Say exactly 'Hello' and nothing else.",
            "max_tokens": 10,
        }
        r = session.post(_v1("completions"), json=payload, timeout=MODEL_GEN_TIMEOUT)
        assert r.status_code == 200
        body = r.json()
        assert body["object"] == "text_completion"
        assert len(body["choices"]) == 1
        assert "text" in body["choices"][0]

    def test_legacy_completion_streaming(self, session, default_model, wait_for_model):
        payload = {
            "model": default_model,
            "prompt": "Count 1 2",
            "max_tokens": 15,
            "stream": True,
        }
        r = session.post(
            _v1("completions"), json=payload, stream=True, timeout=STREAM_TIMEOUT
        )
        assert r.status_code == 200
        chunks = []
        for line in r.iter_lines(decode_unicode=True):
            if line.startswith("data: "):
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                chunks.append(json.loads(data))
        assert len(chunks) > 0


# ─── POST /v1/embeddings ────────────────────────────────────────────────────


@pytest.mark.inference
class TestEmbeddings:
    def test_embedding(self, session, default_model, wait_for_model):
        payload = {
            "model": default_model,
            "input": "Hello world",
        }
        r = session.post(_v1("embeddings"), json=payload, timeout=MODEL_GEN_TIMEOUT)
        assert r.status_code == 200
        body = r.json()
        assert body["object"] == "list"
        assert len(body["data"]) > 0
        assert "embedding" in body["data"][0]
        assert isinstance(body["data"][0]["embedding"], list)
        assert body["model"] == default_model

    def test_embedding_batch(self, session, default_model, wait_for_model):
        payload = {
            "model": default_model,
            "input": ["Hello world", "Goodbye world"],
        }
        r = session.post(_v1("embeddings"), json=payload, timeout=MODEL_GEN_TIMEOUT)
        assert r.status_code == 200
        body = r.json()
        assert len(body["data"]) == 2

    def test_embedding_usage(self, session, default_model, wait_for_model):
        payload = {
            "model": default_model,
            "input": "Usage test",
        }
        r = session.post(_v1("embeddings"), json=payload, timeout=MODEL_GEN_TIMEOUT)
        assert r.status_code == 200
        usage = r.json().get("usage", {})
        assert "prompt_tokens" in usage
        assert "total_tokens" in usage


# ─── Error handling ──────────────────────────────────────────────────────────


class TestErrorHandling:
    def test_empty_body(self, session):
        r = session.post(_v1("chat/completions"), data="", timeout=TIMEOUT)
        assert r.status_code in (400, 415)

    def test_invalid_json(self, session):
        r = session.post(_v1("chat/completions"), data="not json", timeout=TIMEOUT)
        assert r.status_code == 400

    def test_unsupported_endpoint(self, session):
        r = session.get(_v1("this-does-not-exist"), timeout=TIMEOUT)
        assert r.status_code in (404, 400)


# ─── Ollama native API (supplementary) ───────────────────────────────────────


class TestOllamaNativeApi:
    def test_tags(self, session, wait_for_ollama):
        r = session.get(_ollama_api("/api/tags"), timeout=TIMEOUT)
        assert r.status_code == 200
        body = r.json()
        assert "models" in body

    def test_version(self, session, wait_for_ollama):
        r = session.get(_ollama_api("/api/version"), timeout=TIMEOUT)
        assert r.status_code == 200
        body = r.json()
        assert "version" in body


if __name__ == "__main__":
    pytest.main(["-v", __file__])
