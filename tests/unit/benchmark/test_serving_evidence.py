"""The prompt file, the hub registry and the P8.1 reports carry one raw-byte sha256; see AGENTS.md § 3."""

import hashlib
import json
import pathlib

import pytest

from orchestrant.benchmark import openai_api


REPO = pathlib.Path(__file__).resolve().parents[3]
PROMPTS = REPO / "benchmarks" / "prompts"
P8 = REPO / "benchmarks" / "benchmark_results" / "2026-09-25-p8"
P81_REPORTS = ("npu-tools-disamb.json", "cpu-4b-instruct-tools-disamb-r3.json")
PROMPT = "tool-disambiguation"


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def pin():
    """The registry's pin for the tools prompt: {"file", "sha256"}."""
    with open(openai_api.BACKENDS_FILE, encoding="utf-8") as f:
        serving = json.load(f).get("serving") or {}
    spec = ((serving.get("gateway") or {}).get("prompts") or {}).get(PROMPT)
    assert spec, f"{openai_api.BACKENDS_FILE} pins no {PROMPT!r} prompt"
    return spec


def test_the_registry_pins_this_repos_prompt_file_by_its_raw_bytes(pin):
    path = PROMPTS / pin["file"]
    assert path.name == "tool-disambiguation.md"
    assert _sha256(path) == pin["sha256"]


def test_the_pinned_bytes_are_the_crlf_copy():
    # Fails when a checkout converted the line endings: the -text rule is gone or the blob went LF.
    raw = (PROMPTS / "tool-disambiguation.md").read_bytes()
    assert b"\r\n" in raw
    assert raw.count(b"\n") == raw.count(b"\r\n"), "bare LF in the pinned prompt"


@pytest.mark.parametrize("report", P81_REPORTS)
def test_every_p81_report_measured_the_pinned_bytes(pin, report):
    with open(P8 / report, encoding="utf-8") as f:
        config = json.load(f)["config"]
    assert config["system_prompt"] == "benchmarks/prompts/tool-disambiguation.md"
    assert config["system_prompt_sha256"] == pin["sha256"]
