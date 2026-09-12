"""Tests for the named-backend registry.

The registry exists so the two backends this repo actually serves -- the Ollama
service in docker-compose.yml and the Snapdragon GenieX lanes -- are addressable
by name instead of by a URL typed from memory.

The resolution ORDER is the part worth pinning down. Getting it wrong is the
kind of bug that wastes an afternoon: you export an env var, pass --backend,
and quietly benchmark the wrong machine.
"""

import json
import os
import re
import subprocess
import sys

import pytest

from orchestrant.benchmark.openai_api import (
    load_backends,
    resolve_backend,
    resolve_backend_entry,
)


# Every field an entry may carry. A typo ("api_key_evn") silently means no
# auth against a live endpoint, and reads as a model result, not a config error.
KNOWN_FIELDS = {
    "base_url",
    "model",
    "note",
    "api_key_env",
    "headers",
    "request_extra",
    "probe",
}


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "backends.json"
    path.write_text(
        json.dumps(
            {
                "default": "ollama",
                "backends": {
                    "ollama": {"base_url": "http://localhost:11434"},
                    "npu": {
                        "base_url": "http://127.0.0.1:18181/",
                        "model": "org/Model:W4A16",
                    },
                },
            }
        )
    )
    return str(path)


class TestShippedRegistry:
    def test_repo_registry_parses_and_lists_both_backends(self):
        backends, default = load_backends()
        assert default == "ollama", "ollama must stay the default backend"
        assert "ollama" in backends
        assert any(n.startswith("geniex-") for n in backends)

    def test_every_entry_has_a_base_url(self):
        backends, _ = load_backends()
        missing = [n for n, e in backends.items() if not e.get("base_url")]
        assert missing == [], f"entries without base_url: {missing}"

    def test_no_entry_carries_an_unknown_field(self):
        backends, _ = load_backends()
        unknown = {
            n: sorted(set(e) - KNOWN_FIELDS)
            for n, e in backends.items()
            if set(e) - KNOWN_FIELDS
        }
        assert unknown == {}, f"misspelled or undocumented fields: {unknown}"

    def test_no_entry_holds_a_key_rather_than_a_variable_name(self):
        # backends.json is committed. api_key_env is the NAME of an environment
        # variable; anything that looks like a key itself must never land here.
        backends, _ = load_backends()
        for name, entry in backends.items():
            assert "api_key" not in entry, f"{name} holds a literal api_key"
            var = entry.get("api_key_env")
            if var is not None:
                assert re.fullmatch(r"[A-Z][A-Z0-9_]*", var), (
                    f"{name}: api_key_env {var!r} is not an environment variable name"
                )

    def test_optional_fields_have_the_documented_types(self):
        backends, _ = load_backends()
        for name, entry in backends.items():
            assert isinstance(entry.get("headers", {}), dict), f"{name}.headers"
            assert isinstance(entry.get("request_extra", {}), dict), (
                f"{name}.request_extra"
            )
            assert isinstance(entry.get("probe", True), bool), f"{name}.probe"

    def test_a_probe_false_backend_names_its_model(self):
        # Nothing asks a paid host what it serves, so the id has to be here or
        # on the command line; without either the run dies after the gate.
        backends, _ = load_backends()
        for name, entry in backends.items():
            if entry.get("probe", True) is False:
                assert entry.get("model"), f"{name} is probe:false but names no model"


class TestBackendEntry:
    """resolve_backend_entry carries what the 3-tuple cannot: the api key
    variable, extra headers and per-backend request_extra.
    """

    @pytest.fixture
    def keyed(self, tmp_path):
        path = tmp_path / "backends.json"
        path.write_text(
            json.dumps(
                {
                    "default": "ollama",
                    "backends": {
                        "ollama": {"base_url": "http://localhost:11434"},
                        "paid": {
                            "base_url": "https://api.example.test",
                            "model": "org/M",
                            "api_key_env": "EXAMPLE_KEY",
                            "probe": False,
                            "request_extra": {"num_ctx": 16384},
                        },
                    },
                }
            )
        )
        return str(path)

    def test_named_entry_comes_back_whole(self, keyed):
        entry = resolve_backend_entry("paid", path=keyed)
        assert entry["api_key_env"] == "EXAMPLE_KEY"
        assert entry["request_extra"] == {"num_ctx": 16384}
        assert entry["probe"] is False

    def test_an_explicit_url_alone_carries_no_entry(self, keyed):
        # Matching a hand-typed URL against the registry would attach someone's
        # API key to an endpoint they never named.
        assert resolve_backend_entry(None, "https://api.example.test", keyed) == {}

    def test_the_backend_wins_over_an_explicit_url(self, keyed):
        # --base-url moves the endpoint; the named backend still says how to
        # authenticate to it.
        entry = resolve_backend_entry("paid", "https://elsewhere.test", keyed)
        assert entry["api_key_env"] == "EXAMPLE_KEY"

    def test_the_default_backend_supplies_the_entry(self, keyed):
        assert resolve_backend_entry(path=keyed)["base_url"] == "http://localhost:11434"

    def test_an_unknown_backend_fails_loudly(self, keyed):
        with pytest.raises(SystemExit) as e:
            resolve_backend_entry("typo", path=keyed)
        assert "typo" in str(e.value)

    def test_mutating_the_entry_does_not_edit_the_registry(self, keyed):
        entry = resolve_backend_entry("paid", path=keyed)
        entry["api_key_env"] = "SOMETHING_ELSE"
        assert resolve_backend_entry("paid", path=keyed)["api_key_env"] == "EXAMPLE_KEY"


class TestResolutionOrder:
    @pytest.fixture(autouse=True)
    def _ambient_env_cleared(self, monkeypatch):
        # Env legitimately beats the registry (TestEnvironmentPrecedence pins
        # that); these cases assert the order BELOW env, so the host's/CI's own
        # endpoint vars must not leak in (the v1-api-contract job exports
        # OLLAMA_BASE_URL for its service container and turned all three red).
        for var in ("LLM_BASE_URL", "OLLAMA_BASE_URL", "OLLAMA_HOST"):
            monkeypatch.delenv(var, raising=False)

    def test_named_backend(self, registry):
        url, model, source = resolve_backend("npu", path=registry)
        assert url == "http://127.0.0.1:18181"  # trailing slash stripped
        assert model == "org/Model:W4A16"
        assert "npu" in source

    def test_default_when_nothing_given(self, registry):
        url, _, source = resolve_backend(path=registry)
        assert url == "http://localhost:11434"
        assert "default" in source

    def test_explicit_url_beats_named_backend(self, registry):
        url, _, source = resolve_backend(
            "npu", base_url="http://elsewhere:1/", path=registry
        )
        assert url == "http://elsewhere:1"
        assert source == "--base-url"

    def test_unknown_backend_fails_loudly_and_lists_options(self, registry):
        # Silently falling back to the default would benchmark the wrong host.
        with pytest.raises(SystemExit) as e:
            resolve_backend("typo", path=registry)
        assert "typo" in str(e.value) and "ollama" in str(e.value)

    def test_missing_registry_still_resolves_an_explicit_url(self, tmp_path):
        # A broken config must not block someone benchmarking a plain URL.
        gone = str(tmp_path / "nope.json")
        url, _, _ = resolve_backend(base_url="http://h:1", path=gone)
        assert url == "http://h:1"

    def test_malformed_registry_does_not_raise(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{ not json")
        backends, default = load_backends(str(bad))
        assert backends == {} and default is None


class TestEnvironmentPrecedence:
    """Env beats --backend on purpose: a wrapper script that exports the
    variable must not be silently overridden by a stale config default.
    """

    def _resolve(self, env, args):
        repo = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        code = (
            "import json;"
            "import orchestrant.benchmark.openai_api as b;"
            "print(json.dumps(b.resolve_backend(*%r)))" % (args,)
        )
        child = dict(os.environ)
        child["PYTHONPATH"] = repo + os.pathsep + child.get("PYTHONPATH", "")
        for k in ("LLM_BASE_URL", "OLLAMA_BASE_URL"):
            child.pop(k, None)
        child.update(env)
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=repo,
            env=child,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)

    # D32 — resolved against the tmp `registry` fixture, not the shipped
    # backends.json: editing one model field used to redden a precedence test.
    def test_env_beats_named_backend(self, registry):
        url, _, source = self._resolve(
            {"LLM_BASE_URL": "http://env:9"}, ("npu", None, registry)
        )
        assert url == "http://env:9"
        assert source == "environment"

    def test_legacy_env_name_also_beats_backend(self, registry):
        url, _, _ = self._resolve(
            {"OLLAMA_BASE_URL": "http://legacy:9"}, ("npu", None, registry)
        )
        assert url == "http://legacy:9"

    def test_env_still_yields_the_backend_default_model(self, registry):
        # The URL comes from the env, but the named backend's model is still
        # useful -- otherwise you would have to retype it every time.
        _, model, _ = self._resolve(
            {"LLM_BASE_URL": "http://env:9"}, ("npu", None, registry)
        )
        assert model == "org/Model:W4A16"
