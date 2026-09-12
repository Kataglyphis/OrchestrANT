"""Backend-compatibility tests for the changes that touched Ollama's paths.

This harness was Ollama-only and was generalised to serve GenieX too. Three of
those edits sit directly on Ollama's code path and could have broken it
silently:

  * SSE framing -- the space after "data:" is OPTIONAL in the spec. Ollama
    sends it, GenieX does not. The parser matched only the spaced form, which
    is why it reported 0 tok/s against GenieX; the fix must not now break the
    spaced form.
  * model detection -- the hardcoded POST /api/show probe for "gemma4:26b" was
    replaced by /v1/models with an /api/tags fallback.
  * the env var -- LLM_BASE_URL replaced OLLAMA_BASE_URL, which existing
    scripts still set.

These run against a stub speaking Ollama's dialect, so they need no server.
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from orchestrant.benchmark import openai_api as bench


def make_stub(*, models_ok=True, tags_ok=True, spaced_sse=True):
    """A stub speaking Ollama's dialect (spaced SSE, /api/tags, usage chunk)."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def _json(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/v1/models":
                if not models_ok:
                    return self._json(500, {"error": "nope"})
                return self._json(200, {"data": [{"id": "gemma4:26b"}]})
            if self.path == "/api/tags":
                if not tags_ok:
                    return self._json(500, {"error": "nope"})
                return self._json(200, {"models": [{"name": "llama3:8b"}]})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            prefix = "data: " if spaced_sse else "data:"
            try:
                for _ in range(3):
                    chunk = json.dumps({"choices": [{"delta": {"content": "x"}}]})
                    self.wfile.write(f"{prefix}{chunk}\n\n".encode())
                    self.wfile.flush()
                usage = json.dumps(
                    {
                        "choices": [],
                        "usage": {
                            "prompt_tokens": 7,
                            "completion_tokens": 3,
                            "total_tokens": 10,
                        },
                    }
                )
                self.wfile.write(f"{prefix}{usage}\n\n".encode())
                self.wfile.write(f"{prefix}[DONE]\n\n".encode())
                self.wfile.flush()
            except BrokenPipeError:
                pass

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


class TestSseDialects:
    def _one(self, spaced):
        srv, url = make_stub(spaced_sse=spaced)
        try:
            return list(
                bench.benchmark_chat(
                    "m", ["hi"], stream=True, warmup=False, base_url=url
                )
            )[0]
        finally:
            srv.shutdown()

    def test_ollama_style_spaced_data_prefix(self):
        # The original harness only ever saw this form.
        r = self._one(True)
        assert r.get("error") is None
        assert r["completion_tokens"] == 3
        assert r["ttft_s"] is not None

    def test_geniex_style_unspaced_data_prefix(self):
        # The form that used to parse as nothing at all.
        r = self._one(False)
        assert r.get("error") is None
        assert r["completion_tokens"] == 3
        assert r["ttft_s"] is not None

    def test_reported_usage_is_not_flagged_as_estimated(self):
        # When the server DOES report usage, counts must be exact.
        r = self._one(True)
        assert r["tokens_estimated"] is False
        assert r["prompt_tokens"] == 7


class TestModelDetection:
    def test_prefers_openai_models_endpoint(self):
        srv, url = make_stub()
        try:
            assert bench.detect_model_via_api(url) == "gemma4:26b"
        finally:
            srv.shutdown()

    def test_falls_back_to_ollama_api_tags(self):
        srv, url = make_stub(models_ok=False)
        try:
            assert bench.detect_model_via_api(url) == "llama3:8b"
        finally:
            srv.shutdown()

    def test_unknown_when_nothing_answers(self):
        srv, url = make_stub(models_ok=False, tags_ok=False)
        try:
            assert bench.detect_model_via_api(url) == "unknown"
        finally:
            srv.shutdown()


class TestEnvVarCompat:
    """Checked in a SUBPROCESS on purpose.

    importlib.reload() mutates the module in place, so restoring the
    environment afterwards silently re-overwrites the very value under test --
    the first version of these tests failed for that reason, not because the
    code was wrong. A subprocess with a real environment is both simpler and
    closer to how the script is actually invoked.
    """

    def _resolve(self, env):
        import subprocess

        repo = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        child = dict(os.environ)
        child["PYTHONPATH"] = repo + os.pathsep + child.get("PYTHONPATH", "")
        for key in ("LLM_BASE_URL", "OLLAMA_BASE_URL"):
            child.pop(key, None)
        child.update(env)
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "import orchestrant.benchmark.openai_api as b; print(b.LLM_BASE_URL)",
            ],
            cwd=repo,
            env=child,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()

    def test_legacy_ollama_base_url_still_honoured(self):
        # Existing scripts set this; breaking it would be a silent regression.
        assert (
            self._resolve({"OLLAMA_BASE_URL": "http://legacy:1234"})
            == "http://legacy:1234"
        )

    def test_new_name_wins_over_legacy(self):
        assert (
            self._resolve(
                {"OLLAMA_BASE_URL": "http://old:1", "LLM_BASE_URL": "http://new:2"}
            )
            == "http://new:2"
        )

    def test_default_is_ollama_localhost(self):
        assert self._resolve({}) == "http://localhost:11434"


class TestModelResolution:
    """Which model gets benchmarked, decided without contacting anything.

    The probe:false case is the paid-host path: asking /v1/models there costs
    money and may not be offered, so the id has to be named rather than
    discovered.
    """

    def _detect(self, answer="detected/M"):
        calls = []

        def detect(entry=None):
            calls.append(entry)
            return answer

        detect.calls = calls
        return detect

    def test_an_explicit_model_wins(self):
        detect = self._detect()
        assert bench.resolve_model("org/M", "backend/M", {}, detect) == "org/M"
        assert detect.calls == []

    def test_the_backend_default_is_used_next(self):
        detect = self._detect()
        assert bench.resolve_model(None, "backend/M", {}, detect) == "backend/M"
        assert detect.calls == []

    def test_otherwise_the_endpoint_is_asked(self):
        detect = self._detect()
        assert bench.resolve_model(None, None, {}, detect) == "detected/M"
        assert len(detect.calls) == 1

    def test_the_probe_carries_the_entry_so_a_hosted_endpoint_authenticates(self):
        detect = self._detect()
        entry = {"api_key_env": "K"}
        bench.resolve_model(None, None, entry, detect)
        assert detect.calls == [entry]

    def test_a_probe_false_backend_is_never_asked(self):
        detect = self._detect()
        with pytest.raises(SystemExit) as e:
            bench.resolve_model(None, None, {"probe": False}, detect)
        assert detect.calls == []
        assert "--model" in str(e.value)

    def test_a_probe_false_backend_with_a_model_is_fine(self):
        detect = self._detect()
        assert bench.resolve_model(None, "paid/M", {"probe": False}, detect) == "paid/M"
