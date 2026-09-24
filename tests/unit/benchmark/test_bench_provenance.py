"""Tests for provenance capture.

A result without provenance cannot be compared against a later one, which makes
regression detection impossible -- and an old number that looks authoritative
but cannot be reproduced is worse than no number.
"""

import hashlib
import json
import pathlib
import sys
import types
from datetime import UTC, datetime, timedelta

import pytest

from orchestrant.benchmark import provenance as bench_provenance
from orchestrant.benchmark.provenance import (
    collect,
    compare,
    determinism_probe,
    geniex_model_files,
    known_deterministic,
    load_lane_runtime,
    model_files_notes,
    tool_fingerprint,
    write_lane_runtimes,
)


FAKE_DRIVERS = {"npu": [{"version": "30.0.220.3000"}], "gpu": [], "reason": None}


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    # collect() probes every registry endpoint for live_lanes; the tests must
    # never leave this machine, and the DNS timeouts made this file take 90 s.
    monkeypatch.setattr(bench_provenance, "busy_lanes", lambda *a, **k: [])
    # runtime_info() may run a locally installed `geniex --version`; a unit
    # test must not depend on what this machine has installed.
    monkeypatch.setattr(bench_provenance, "runtime_info", lambda *a, **k: None)
    # ...nor on this machine's model cache (from WSL2 also the Windows one,
    # through /mnt/c), its driver registry, or a lane-runtime file exported in
    # the shell that runs the suite.
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(bench_provenance.glob, "glob", lambda pattern: [])
    monkeypatch.setattr(bench_provenance, "driver_versions", lambda: FAKE_DRIVERS)
    monkeypatch.delenv(bench_provenance.LANE_RUNTIMES_ENV, raising=False)
    # A snapshot is checked against the installed GenieX: none is installed
    # unless a test says so, so LOCALAPPDATA's real one is never run.
    monkeypatch.setattr(bench_provenance, "_installed_geniex", lambda: None)


class TestCollect:
    def test_records_the_fields_a_rerun_needs(self):
        p = collect()
        for key in (
            "schema_version",
            "timestamp_utc",
            "host",
            "os",
            "architecture",
            "python",
            "git_sha",
            "git_dirty",
        ):
            assert key in p, key

    def test_names_missing_fields_instead_of_omitting_them(self):
        # LB9's lesson: a silent gap is worse than a visible one.
        p = collect()
        assert isinstance(p["incomplete"], list)

    def test_unreachable_server_is_recorded_as_null_not_an_exception(self):
        p = collect(base_url="http://127.0.0.1:1")
        assert p["server_models"] is None

    def test_extra_fields_are_merged(self):
        p = collect(extra={"lane": "npu"})
        assert p["lane"] == "npu"


class TestToolFingerprint:
    def test_hashes_the_benchmark_source(self):
        assert tool_fingerprint("provenance.py")

    def test_same_files_same_hash_regardless_of_order(self):
        a = tool_fingerprint("provenance.py", "openai_api.py")
        b = tool_fingerprint("openai_api.py", "provenance.py")
        assert a == b

    def test_different_files_differ(self):
        assert tool_fingerprint("client.py") != tool_fingerprint("openai_api.py")

    def test_missing_file_yields_none(self):
        assert tool_fingerprint("does-not-exist.py") is None


class TestCompare:
    def test_two_identical_clean_runs_report_nothing(self):
        p = collect(extra={"git_dirty": False})
        assert compare(p, p) == []

    def test_a_dirty_run_is_flagged_even_against_itself(self):
        # Deliberate, not a quirk: with a dirty tree the recorded SHA does not
        # describe what actually ran, so the caveat belongs on every comparison
        # the run takes part in -- including with itself.
        p = collect(extra={"git_dirty": True})
        assert any("dirty working tree" in n for n in compare(p, p))

    def test_a_changed_grader_is_called_out_first(self):
        # The trap this exists for: the ranking moved because the BENCHMARK
        # changed, which is indistinguishable from a model regression without it.
        old = collect(extra={"tool_sha256": "aaaa"})
        new = collect(extra={"tool_sha256": "bbbb"})
        notes = compare(old, new)
        assert notes and "BENCHMARK SOURCE CHANGED" in notes[0]

    def test_different_served_models_are_flagged(self):
        old = collect(extra={"server_models": ["a"]})
        new = collect(extra={"server_models": ["b"]})
        assert any("served models differ" in n for n in compare(old, new))

    def test_a_dirty_tree_is_flagged(self):
        old = collect(extra={"git_dirty": False})
        new = collect(extra={"git_dirty": True})
        assert any("dirty working tree" in n for n in compare(old, new))

    def test_a_moved_repository_is_flagged(self):
        old = collect(extra={"git_sha": "1" * 40, "git_dirty": False})
        new = collect(extra={"git_sha": "2" * 40, "git_dirty": False})
        assert any("repository moved" in n for n in compare(old, new))


V061 = """GenieX CLI Version:     v0.6.1
QAIRT Runtime Version:  2.45
LlamaCPP Runtime Hash:  0eadefe
"""


def _geniex(cli, llama, serve_args=None):
    return {
        "server": "geniex",
        "cli": cli,
        "qairt": "2.45",
        "llama_cpp": llama,
        "serve_args": serve_args,
        "verified": True,
    }


class TestRuntime:
    """Every GenieX release moved something a benchmark depended on.

    Until now no report said which release produced it.
    """

    def test_parses_all_three_version_lines(self):
        assert bench_provenance.parse_geniex_version(V061) == {
            "cli": "v0.6.1",
            "qairt": "2.45",
            "llama_cpp": "0eadefe",
        }

    def test_unrecognised_output_parses_to_nothing(self):
        assert bench_provenance.parse_geniex_version("usage: geniex ...") == {}
        assert bench_provenance.parse_geniex_version(None) == {}

    def test_a_runtime_upgrade_is_named_on_compare(self):
        old = collect(
            extra={"git_dirty": False, "runtime": _geniex("v0.6.1", "0eadefe")}
        )
        new = collect(
            extra={"git_dirty": False, "runtime": _geniex("v0.7.0", "4ff829")}
        )
        notes = compare(old, new)
        hit = [n for n in notes if "SERVING RUNTIME CHANGED" in n]
        assert hit and "v0.6.1" in hit[0] and "v0.7.0" in hit[0] and "4ff829" in hit[0]

    def test_an_identical_runtime_says_nothing(self):
        p = collect(extra={"git_dirty": False, "runtime": _geniex("v0.7.0", "4ff829")})
        assert compare(p, p) == []

    def test_a_runtime_on_one_side_only_is_flagged(self):
        old = collect(extra={"git_dirty": False})
        new = collect(
            extra={"git_dirty": False, "runtime": _geniex("v0.7.0", "4ff829")}
        )
        assert any("one side only" in n for n in compare(old, new))

    def test_changed_serve_flags_are_named_but_a_port_move_is_not(self):
        base = ["serve", "--compute", "npu", "--nctx", "16384"]
        a = _geniex("v0.7.0", "4ff829", [*base, "--host", "127.0.0.1:18181"])
        b = _geniex("v0.7.0", "4ff829", [*base, "--host", "127.0.0.1:18191"])
        c = _geniex("v0.7.0", "4ff829", [*base[:-1], "4096"])
        clean = {"git_dirty": False}
        assert (
            compare(
                collect(extra={**clean, "runtime": a}),
                collect(extra={**clean, "runtime": b}),
            )
            == []
        )
        notes = compare(
            collect(extra={**clean, "runtime": a}),
            collect(extra={**clean, "runtime": c}),
        )
        assert any("different serve flags" in n for n in notes)


class TestRuntimeInfo:
    def test_the_lane_process_is_the_best_evidence(self, monkeypatch):
        from orchestrant.benchmark import hostload

        class FakeLane:
            available = True
            reason = None

            def __init__(self, url):
                pass

            def info(self):
                return {
                    "pid": 42,
                    "exe": r"C:\Users\u\AppData\Local\GenieX CLI\geniex.exe",
                    "cmdline": ["geniex.exe", "serve", "--compute", "npu"],
                }

        monkeypatch.setattr(hostload, "LaneProcess", FakeLane)
        monkeypatch.setattr(
            bench_provenance,
            "_geniex_version",
            lambda exe, timeout=30: bench_provenance.parse_geniex_version(V061),
        )
        info = _REAL_RUNTIME_INFO("http://127.0.0.1:18181")
        assert info["server"] == "geniex" and info["cli"] == "v0.6.1"
        assert info["verified"] is True
        assert info["serve_args"] == ["serve", "--compute", "npu"]
        # No model id from the caller: the files stay an explicit null, the
        # drivers are recorded regardless.
        assert info["model_files"] is None and info["drivers"] == FAKE_DRIVERS

    def test_an_unreachable_loopback_url_is_not_attributed_to_geniex(self, monkeypatch):
        monkeypatch.setattr(bench_provenance, "_installed_geniex", lambda: "geniex.exe")
        monkeypatch.setattr(bench_provenance, "_ollama_version", lambda *a, **k: None)
        assert _REAL_RUNTIME_INFO("http://127.0.0.1:1") is None


# ── what served: model files, drivers, lane-runtime files ───────────────────

GGUF_ID = "unsloth/Qwen3-4B-GGUF:Q4_0"
QAIRT_ID = "qualcomm/Qwen3-4B-Instruct-2507:W4A16"
SAMPLER = {"version": 1, "seed": 42, "temp": 0.8, "top-k": 40, "top-p": 0.95}
MIB = 1 << 20
WINDOW = 1 << 16  # one of the 16 sampled windows


def make_cache(root, weights=b"w"):
    """A GenieX model cache laid out as on the lab host: one GGUF, one QAIRT bundle.

    The GGUF is a first MiB of metadata and 2 MiB of `weights`, so two caches
    built with different weights are two quants that share their first MiB and
    their size -- as this host's Qwen3-4B quants share their first MiB.
    """
    gguf_dir = root / "unsloth" / "Qwen3-4B-GGUF"
    gguf_dir.mkdir(parents=True)
    gguf = b"GGUF" + b"\0" * (MIB - 4) + (weights * 2 * MIB)[: 2 * MIB]
    (gguf_dir / "Qwen3-4B-Q4_0.gguf").write_bytes(gguf)
    (gguf_dir / "geniex.json").write_text(
        json.dumps(
            {
                "Name": "unsloth/Qwen3-4B-GGUF",
                "PluginId": "llama_cpp",
                "ModelFile": {
                    "Q4_0": {"Name": "Qwen3-4B-Q4_0.gguf", "Size": len(gguf)},
                    "Q2_K": {"Name": "Qwen3-4B-Q2_K.gguf", "Size": 1},
                },
                "MMProjFile": {"Name": "", "Downloaded": False, "Size": 0},
                "ExtraFiles": [],
            }
        )
    )
    bundle = root / "qualcomm" / "Qwen3-4B-Instruct-2507"
    bundle.mkdir(parents=True)
    config = json.dumps(
        {
            "dialog": {
                "context": {"size": 4096},
                "sampler": SAMPLER,
                "engine": {
                    "backend": {"extensions": "htp_backend_ext_config.json"},
                    "model": {
                        "binary": {"ctx-bins": ["part1_of_2.bin", "part2_of_2.bin"]}
                    },
                },
            }
        }
    ).encode()
    (bundle / "genie_config.json").write_bytes(config)
    (bundle / "htp_backend_ext_config.json").write_text('{"perf_profile": "burst"}')
    (bundle / "metadata.json").write_text(
        json.dumps({"precision": "w4a16", "tool_versions": {"qairt": "2.45.0.260326"}})
    )
    for part in ("part1_of_2.bin", "part2_of_2.bin"):
        (bundle / part).write_bytes(part.encode() * 100)
    (bundle / "geniex.json").write_text(
        json.dumps(
            {
                "Name": "qualcomm/Qwen3-4B-Instruct-2507",
                "PluginId": "qairt",
                "ModelFile": {"W4A16": {"Name": "part1_of_2.bin", "Size": 1400}},
                "ExtraFiles": [
                    {"Name": "part2_of_2.bin", "Size": 1400},
                    {"Name": "genie_config.json", "Size": len(config)},
                ],
            }
        )
    )
    return root


def cache_state(root):
    return sorted(
        (str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*")
    )


class TestModelFiles:
    """PROV-1: WHICH files a model id resolved to, not only which build served it."""

    def test_a_gguf_is_named_by_size_and_a_hash_of_its_first_mib(self, tmp_path):
        cache = make_cache(tmp_path / "c")
        mf = geniex_model_files(GGUF_ID, [str(cache)])
        assert mf["error"] is None and mf["plugin"] == "llama_cpp"
        assert mf["variant"] == "Q4_0" and len(mf["files"]) == 1
        f = mf["files"][0]
        data = (cache / "unsloth" / "Qwen3-4B-GGUF" / "Qwen3-4B-Q4_0.gguf").read_bytes()
        assert f["name"] == "Qwen3-4B-Q4_0.gguf" and f["size"] == len(data)
        assert f["head_sha256"] == hashlib.sha256(data[:MIB]).hexdigest()
        assert f["head_bytes"] == MIB and f["size_matches_manifest"] is True
        # The documented recipe, so a report's hash can be checked by hand.
        span = len(data) - WINDOW
        sample = b"".join(data[span * i // 15 :][:WINDOW] for i in range(16))
        assert f["sampled_sha256"] == hashlib.sha256(sample).hexdigest()

    def test_two_quants_share_a_head_and_differ_in_the_sample(self, tmp_path):
        # Measured on this host: Qwen3-4B Q2_K, Q3_K_M, Q4_0 and UD-IQ3_XXS
        # have one first MiB (general.* and the vocabulary); only the size and
        # the weights tell them apart, and the size alone not at equal size.
        a = geniex_model_files(GGUF_ID, [str(make_cache(tmp_path / "a", b"q4"))])
        b = geniex_model_files(GGUF_ID, [str(make_cache(tmp_path / "b", b"Q2"))])
        (fa,), (fb,) = a["files"], b["files"]
        assert fa["size"] == fb["size"] and fa["head_sha256"] == fb["head_sha256"]
        assert fa["sampled_sha256"] != fb["sampled_sha256"]
        assert model_files_notes({"model_files": a}, {"model_files": b})

    def test_an_edit_between_the_windows_is_unseen(self, tmp_path):
        # Documented limit: a sample, not a content hash.
        cache = make_cache(tmp_path / "c")
        gguf = cache / "unsloth" / "Qwen3-4B-GGUF" / "Qwen3-4B-Q4_0.gguf"
        before = geniex_model_files(GGUF_ID, [str(cache)])["files"][0]
        data = bytearray(gguf.read_bytes())
        span = len(data) - WINDOW
        gap = next(o + WINDOW for o in (span * i // 15 for i in range(16)) if o > MIB)
        data[gap] ^= 0xFF
        gguf.write_bytes(bytes(data))
        after = geniex_model_files(GGUF_ID, [str(cache)])["files"][0]
        assert after["sampled_sha256"] == before["sampled_sha256"]

    def test_the_variant_matches_ignoring_case(self, tmp_path):
        cache = make_cache(tmp_path / "c")
        mf = geniex_model_files("unsloth/Qwen3-4B-GGUF:q4_0", [str(cache)])
        assert mf["variant"] == "Q4_0" and mf["files"]

    def test_an_id_naming_no_variant_of_several_is_not_guessed(self, tmp_path):
        cache = make_cache(tmp_path / "c")
        mf = geniex_model_files("unsloth/Qwen3-4B-GGUF", [str(cache)])
        assert mf["files"] == [] and "no variant" in mf["error"]
        assert "Q2_K" in mf["error"] and "Q4_0" in mf["error"]

    def test_a_qairt_bundle_records_its_config_hash_and_sampler(self, tmp_path):
        cache = make_cache(tmp_path / "c")
        mf = geniex_model_files(QAIRT_ID, [str(cache)])
        bundle = cache / "qualcomm" / "Qwen3-4B-Instruct-2507"
        config = (bundle / "genie_config.json").read_bytes()
        assert mf["error"] is None and mf["plugin"] == "qairt"
        assert [f["name"] for f in mf["files"]] == ["part1_of_2.bin", "part2_of_2.bin"]
        gc = mf["genie_config"]
        assert gc["sha256"] == hashlib.sha256(config).hexdigest()
        assert gc["sampler"] == SAMPLER and gc["context_size"] == 4096
        assert gc["size_matches_manifest"] is True
        ext = (bundle / "htp_backend_ext_config.json").read_bytes()
        assert mf["backend_extensions"]["sha256"] == hashlib.sha256(ext).hexdigest()
        assert mf["bundle_qairt"] == "2.45.0.260326" and mf["precision"] == "w4a16"

    def test_a_config_edited_in_place_no_longer_matches_the_manifest(self, tmp_path):
        cache = make_cache(tmp_path / "c")
        config = cache / "qualcomm" / "Qwen3-4B-Instruct-2507" / "genie_config.json"
        config.write_bytes(config.read_bytes() + b"\n")
        mf = geniex_model_files(QAIRT_ID, [str(cache)])
        assert mf["genie_config"]["size_matches_manifest"] is False

    def test_a_bundle_config_naming_no_weights_is_an_error(self, tmp_path):
        cache = make_cache(tmp_path / "c")
        config = cache / "qualcomm" / "Qwen3-4B-Instruct-2507" / "genie_config.json"
        config.write_text("{truncated")
        mf = geniex_model_files(QAIRT_ID, [str(cache)])
        assert mf["files"] == [] and "no ctx-bins" in mf["error"]
        assert mf["genie_config"]["sha256"]  # the broken file is still named

    def test_the_cache_is_never_written(self, tmp_path):
        cache = make_cache(tmp_path / "c")
        before = cache_state(cache)
        geniex_model_files(GGUF_ID, [str(cache)])
        geniex_model_files(QAIRT_ID, [str(cache)])
        geniex_model_files("unsloth/Qwen3-4B-GGUF", [str(cache)])
        assert cache_state(cache) == before

    def test_a_gap_is_an_error_never_an_exception(self, tmp_path):
        cache = make_cache(tmp_path / "c")
        assert geniex_model_files(None) is None
        assert (
            "no geniex.json" in geniex_model_files("nope/x:Q4_0", [str(cache)])["error"]
        )
        assert geniex_model_files("not-an-id", [str(cache)])["error"]
        manifest = cache / "unsloth" / "Qwen3-4B-GGUF" / "geniex.json"
        manifest.write_text(json.dumps({"ModelFile": {"Q4_0": "not an object"}}))
        assert "AttributeError" in geniex_model_files(GGUF_ID, [str(cache)])["error"]

    def test_from_wsl_the_windows_cache_is_searched_first(self, tmp_path, monkeypatch):
        # The documented topology serves the lanes from Windows; a Linux-side
        # cache of the same id is a different install.
        make_cache(tmp_path / "home" / ".cache" / "geniex" / "models")
        windows = make_cache(tmp_path / "mnt-c" / "u" / ".cache" / "geniex" / "models")
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(bench_provenance.glob, "glob", lambda p: [str(windows)])
        mf = geniex_model_files(GGUF_ID)
        assert mf["cache_dir"].startswith(str(windows))

    def test_on_windows_the_users_own_cache_is_the_one(self, tmp_path, monkeypatch):
        home = make_cache(tmp_path / "home" / ".cache" / "geniex" / "models")
        other = make_cache(tmp_path / "mnt-c" / "u" / ".cache" / "geniex" / "models")
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(bench_provenance.glob, "glob", lambda p: [str(other)])
        mf = geniex_model_files(GGUF_ID)
        assert mf["cache_dir"].startswith(str(home))

    def test_the_lane_process_resolves_the_model_it_serves(self, tmp_path, monkeypatch):
        from orchestrant.benchmark import hostload

        home = make_cache(tmp_path / "home" / ".cache" / "geniex" / "models")
        # A process this host can see reads this host's cache, even where a
        # Windows one is reachable through /mnt/c.
        other = make_cache(tmp_path / "mnt-c" / "u" / ".cache" / "geniex" / "models")
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(bench_provenance.glob, "glob", lambda p: [str(other)])

        class GeniexLane:
            available, reason = True, None

            def __init__(self, url):
                pass

            def info(self):
                return {"pid": 7, "exe": "/opt/geniex", "cmdline": ["geniex", "serve"]}

        monkeypatch.setattr(hostload, "LaneProcess", GeniexLane)
        monkeypatch.setattr(bench_provenance, "_geniex_version", lambda exe: {})
        info = _REAL_RUNTIME_INFO("http://127.0.0.1:18184", GGUF_ID)
        assert info["verified"] is True and info["model_files"]["model"] == GGUF_ID
        assert info["model_files"]["files"][0]["name"] == "Qwen3-4B-Q4_0.gguf"
        assert info["model_files"]["cache_dir"].startswith(str(home))

    def test_the_installed_binary_guess_names_files_and_drivers(
        self, tmp_path, monkeypatch
    ):
        # WSL2 without a lane-runtime file, the usual case: nothing is
        # verified, and the model files are still the ones the id resolves to.
        from orchestrant.benchmark import hostload

        make_cache(tmp_path / "home" / ".cache" / "geniex" / "models")
        monkeypatch.setattr(hostload, "LaneProcess", InvisibleLane)
        for name, value in (
            ("_ollama_version", None),
            ("_server_models", [GGUF_ID]),
            ("_serves_geniex_root", True),
            ("_geniex_version", {"cli": "v0.7.0"}),
        ):
            monkeypatch.setattr(bench_provenance, name, lambda *a, v=value, **k: v)
        monkeypatch.setattr(bench_provenance, "_installed_geniex", lambda: "geniex")
        info = _REAL_RUNTIME_INFO("http://127.0.0.1:18184", GGUF_ID)
        assert info["verified"] is False and info["cli"] == "v0.7.0"
        assert info["model_files"]["files"][0]["name"] == "Qwen3-4B-Q4_0.gguf"
        assert info["drivers"] == FAKE_DRIVERS


def fake_winreg(classes):
    """A winreg stand-in over {class guid: {subkey: {value: data} | an OSError}}."""

    class Key:
        def __init__(self, node):
            self.node = node

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def open_key(parent, sub):
        if parent == "HKLM":
            node = classes.get(sub.rsplit("\\", 1)[-1])
        else:
            assert sub.isdigit(), f"opened non-device key {sub!r}"
            node = parent.node[sub]
        if node is None:
            raise FileNotFoundError(sub)
        if isinstance(node, OSError):
            raise node
        return Key(node)

    def query_value(key, name):
        if name not in key.node:
            raise FileNotFoundError(name)
        return key.node[name], 1

    return types.SimpleNamespace(
        HKEY_LOCAL_MACHINE="HKLM",
        OpenKey=open_key,
        QueryInfoKey=lambda key: (len(key.node), 0, 0),
        EnumKey=lambda key, i: list(key.node)[i],
        QueryValueEx=query_value,
    )


NPU_CLASS = "{f01a9d53-3ff6-48d2-9f97-c8a7004be10c}"
GPU_CLASS = "{4d36e968-e325-11ce-bfc1-08002be10318}"
HEXAGON = {
    "DriverDesc": "Snapdragon(R) X - X126100 - Qualcomm(R) Hexagon(TM) NPU",
    "DriverVersion": "30.0.220.3000",
    "DriverDate": "1-8-2026",
    "ProviderName": "Qualcomm Technologies, Inc.",
    "InfPath": "oem95.inf",
}
ADRENO = {
    "DriverDesc": "Qualcomm(R) Adreno(TM) X1-45 GPU",
    "DriverVersion": "31.0.170.0",
    "ProviderName": "Qualcomm Incorporated",
}
REMOTE_DISPLAY = {
    "DriverDesc": "Microsoft Remote Display Adapter",
    "DriverVersion": "10.0.26100.8328",
    "ProviderName": "Microsoft",
}


class TestDriverVersions:
    """PROV-1: Windows Update moves the NPU and GPU drivers under an unchanged build."""

    def test_reads_the_npu_and_gpu_drivers_from_the_registry(self, monkeypatch):
        registry = fake_winreg(
            {
                NPU_CLASS: {
                    "0000": HEXAGON,
                    "0001": PermissionError("access denied"),
                    "Configuration": {},
                },
                GPU_CLASS: {"0000": ADRENO, "0001": REMOTE_DISPLAY},
            }
        )
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setitem(sys.modules, "winreg", registry)
        d = _REAL_DRIVER_VERSIONS()
        assert d["reason"] is None and d["source"].startswith("HKLM")
        assert [r["version"] for r in d["npu"]] == ["30.0.220.3000"]
        assert d["npu"][0]["inf"] == "oem95.inf"
        # The remote display adapter is Microsoft's, never a lane's; a value
        # the INF did not set is a null, not a missing key.
        assert [r["name"] for r in d["gpu"]] == [ADRENO["DriverDesc"]]
        assert d["gpu"][0]["date"] is None

    def test_an_unreadable_class_is_a_reason_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setitem(
            sys.modules, "winreg", fake_winreg({NPU_CLASS: {"0000": HEXAGON}})
        )
        d = _REAL_DRIVER_VERSIONS()
        assert d["npu"][0]["version"] == "30.0.220.3000"
        assert d["gpu"] is None and d["reason"].startswith("gpu:")

    def test_off_windows_they_are_null_with_the_reason(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        d = _REAL_DRIVER_VERSIONS()
        assert d["npu"] is None and d["gpu"] is None
        assert bench_provenance.LANE_RUNTIMES_ENV in d["reason"]


NPU_URL = "http://127.0.0.1:18181"
NPU_RUNTIME = {
    "server": "geniex",
    "cli": "v0.7.0",
    "serve_args": ["serve", "--compute", "npu", "--host", "127.0.0.1:18181"],
    "verified": True,
    "source": "lane process pid 372: geniex.exe --version",
    "model_files": {"model": QAIRT_ID, "files": [{"name": "part1_of_4.bin"}]},
    "drivers": {"npu": [{"version": "30.0.220.3000"}]},
}


def write_snapshot(path, captured=None, lanes=None):
    doc = {
        "schema_version": 1,
        "kind": "lane_runtimes",
        "captured_utc": (captured or datetime.now(UTC)).isoformat(),
        "host": "summy-server",
        "lanes": lanes
        or [
            {
                "lane": "geniex-npu",
                "base_url": NPU_URL,
                "model": QAIRT_ID,
                "runtime": NPU_RUNTIME,
            }
        ],
    }
    path.write_text(json.dumps(doc))
    return str(path)


class InvisibleLane:
    """What WSL2 sees of a Windows-side lane: nothing listening on its port."""

    available, reason = False, "no local process listens on port 18181"

    def __init__(self, url):
        pass


class TestLaneRuntimeFile:
    """OPS-7: from WSL2 the lane process is invisible; the host's own view is not."""

    def test_it_stands_in_for_an_invisible_lane_process(self, tmp_path, monkeypatch):
        from orchestrant.benchmark import hostload

        monkeypatch.setenv(
            bench_provenance.LANE_RUNTIMES_ENV, write_snapshot(tmp_path / "rt.json")
        )
        monkeypatch.setattr(hostload, "LaneProcess", InvisibleLane)

        def never(*a, **k):
            raise AssertionError("a snapshot hit must not probe the lane")

        monkeypatch.setattr(bench_provenance, "_ollama_version", never)
        # Mirrored networking: WSL2 names the lane localhost, the host 127.0.0.1.
        info = _REAL_RUNTIME_INFO("http://localhost:18181", QAIRT_ID)
        assert info["cli"] == "v0.7.0" and info["verified"] is True
        assert info["serve_args"] == NPU_RUNTIME["serve_args"]
        assert info["source"].startswith("lane-runtime file ")
        assert (
            info["snapshot"]["lane"] == "geniex-npu" and not info["snapshot"]["stale"]
        )
        assert info["drivers"] == NPU_RUNTIME["drivers"]
        assert info["model_files"] == NPU_RUNTIME["model_files"]

    def test_a_stale_snapshot_keeps_its_data_but_loses_verified(self, tmp_path):
        path = write_snapshot(
            tmp_path / "rt.json", datetime.now(UTC) - timedelta(days=2)
        )
        info = load_lane_runtime(NPU_URL, path=path)
        assert info["cli"] == "v0.7.0" and info["verified"] is False
        assert info["snapshot"]["stale"] is True and info["snapshot"]["age_s"] > 86400

    def test_an_upgrade_since_the_snapshot_loses_verified(self, tmp_path, monkeypatch):
        # v0.6.1 -> v0.7.0 was one session: a fresh snapshot of the old build
        # must not vouch for the restarted lanes.
        path = write_snapshot(tmp_path / "rt.json")
        monkeypatch.setattr(bench_provenance, "_installed_geniex", lambda: "geniex")
        installed = {"cli": "v0.7.0"}
        monkeypatch.setattr(bench_provenance, "_geniex_version", lambda e: installed)
        info = load_lane_runtime(NPU_URL, path=path)
        assert info["verified"] is True
        assert info["snapshot"]["installed_cli_mismatch"] is None
        installed = {"cli": "v0.7.1"}
        info = load_lane_runtime(NPU_URL, path=path)
        assert info["cli"] == "v0.7.0" and info["verified"] is False
        assert info["snapshot"]["installed_cli_mismatch"] == "v0.7.1"
        assert info["snapshot"]["stale"] is False

    def test_a_remote_url_never_matches_by_port(self, tmp_path):
        path = write_snapshot(tmp_path / "rt.json")
        assert load_lane_runtime("http://summy-server:18181", path=path) is None
        assert load_lane_runtime("http://127.0.0.1:18184", path=path) is None

    def test_an_ambiguous_port_matches_nothing(self, tmp_path):
        entry = {
            "lane": "a",
            "base_url": "http://127.0.0.1:18181",
            "runtime": NPU_RUNTIME,
        }
        twin = {**entry, "lane": "b", "base_url": "http://[::1]:18181"}
        path = write_snapshot(tmp_path / "rt.json", lanes=[entry, twin])
        assert load_lane_runtime("http://localhost:18181", path=path) is None
        assert load_lane_runtime(NPU_URL, path=path)["snapshot"]["lane"] == "a"

    def test_a_missing_or_broken_file_is_no_snapshot(self, tmp_path):
        assert load_lane_runtime(NPU_URL) is None  # nothing exported
        assert load_lane_runtime(NPU_URL, path=str(tmp_path / "absent.json")) is None
        (tmp_path / "bad.json").write_text("{not json")
        assert load_lane_runtime(NPU_URL, path=str(tmp_path / "bad.json")) is None
        bad_port = [{"base_url": "http://127.0.0.1:x", "runtime": NPU_RUNTIME}]
        path = write_snapshot(tmp_path / "p.json", lanes=bad_port)
        assert load_lane_runtime(NPU_URL, path=path) is None

    def test_an_entry_the_host_could_not_attribute_is_no_evidence(
        self, tmp_path, monkeypatch
    ):
        # lane_runtimes() records {"error": ...} for a lane it could not read.
        # Standing in for the runtime, that skipped the probes after it and
        # named no server at all.
        from orchestrant.benchmark import hostload

        failed = {"lane": "x", "base_url": NPU_URL, "runtime": {"error": "OSError"}}
        path = write_snapshot(tmp_path / "rt.json", lanes=[failed])
        monkeypatch.setenv(bench_provenance.LANE_RUNTIMES_ENV, path)
        monkeypatch.setattr(hostload, "LaneProcess", InvisibleLane)
        monkeypatch.setattr(bench_provenance, "_ollama_version", lambda *a, **k: "0.9")
        assert load_lane_runtime(NPU_URL) is None
        info = _REAL_RUNTIME_INFO(NPU_URL, QAIRT_ID)
        assert info["server"] == "ollama" and info["source"] == "/api/version"

    def test_another_model_than_the_snapshots_is_resolved_again(self, tmp_path):
        make_cache(tmp_path / "home" / ".cache" / "geniex" / "models")
        path = write_snapshot(tmp_path / "rt.json")
        info = load_lane_runtime(NPU_URL, path=path, model=GGUF_ID)
        assert info["model_files"]["model"] == GGUF_ID
        assert info["model_files"]["files"][0]["name"] == "Qwen3-4B-Q4_0.gguf"

    def test_written_on_the_host_and_read_back(self, tmp_path, monkeypatch):
        seen = []

        def runtime(url, model=None):
            seen.append((url, model))
            return dict(NPU_RUNTIME)

        monkeypatch.setattr(bench_provenance, "runtime_info", runtime)
        out = tmp_path / "rt.json"
        doc = write_lane_runtimes(str(out), {"geniex-npu": (NPU_URL, QAIRT_ID)})
        assert seen == [(NPU_URL, QAIRT_ID)] and doc["kind"] == "lane_runtimes"
        assert not (tmp_path / "rt.json.tmp").exists()
        info = load_lane_runtime(NPU_URL, path=str(out), model=QAIRT_ID)
        assert info["cli"] == "v0.7.0" and info["verified"] is True
        assert info["snapshot"]["host"] == doc["host"]

    def test_a_lane_whose_runtime_raises_is_an_error_entry(self, monkeypatch):
        def boom(url, model=None):
            raise OSError("psutil gave up")

        monkeypatch.setattr(bench_provenance, "runtime_info", boom)
        got = bench_provenance.lane_runtimes({"x": ("http://h:1", "m")})
        assert "OSError" in got["x"]["error"]

    def test_the_command_writes_the_file(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(
            bench_provenance, "runtime_info", lambda url, model=None: dict(NPU_RUNTIME)
        )
        out = tmp_path / "rt.json"
        spec = f"geniex-npu={NPU_URL},model={QAIRT_ID}"
        assert bench_provenance.main([spec, "--output", str(out)]) == 0
        doc = json.loads(out.read_text())
        assert doc["lanes"][0]["lane"] == "geniex-npu"
        assert doc["lanes"][0]["model"] == QAIRT_ID
        assert "geniex v0.7.0" in capsys.readouterr().out


class TestModelFilesNotes:
    def test_a_replaced_file_behind_the_same_id_is_named(self):
        old = {
            "model_files": {"model": GGUF_ID, "files": [{"name": "a.gguf", "size": 1}]}
        }
        new = {
            "model_files": {"model": GGUF_ID, "files": [{"name": "a.gguf", "size": 2}]}
        }
        notes = model_files_notes(old, new)
        assert len(notes) == 1 and "MODEL FILES CHANGED" in notes[0]
        assert "a.gguf" in notes[0]
        assert model_files_notes(old, old) == []

    def test_an_edited_bundle_config_names_the_sampler(self):
        def rt(sha, temp):
            return {
                "model_files": {
                    "model": QAIRT_ID,
                    "files": [{"name": "part1_of_4.bin", "size": 9}],
                    "genie_config": {"sha256": sha, "sampler": {"temp": temp}},
                }
            }

        notes = model_files_notes(rt("aa", 0.8), rt("bb", 0.0))
        assert len(notes) == 1 and "genie_config.json changed" in notes[0]
        assert "0.8" in notes[0]

    def test_different_ids_or_unrecorded_files_say_nothing(self):
        a = {"model_files": {"model": GGUF_ID, "files": [{"name": "a", "size": 1}]}}
        b = {"model_files": {"model": QAIRT_ID, "files": [{"name": "b", "size": 1}]}}
        assert model_files_notes(a, b) == []
        assert model_files_notes(a, {"model_files": None}) == []
        assert model_files_notes(None, a) == []

    def test_a_file_one_side_could_not_read_is_not_a_change(self):
        # A lane may hold its weights open: that report has a gap, not new
        # weights. A file listed on one side only is a change.
        read = {"name": "part1_of_4.bin", "size": 9, "sampled_sha256": "s"}
        locked = {"name": "part1_of_4.bin", "error": "PermissionError: WinError 32"}
        extra = {"name": "part2_of_4.bin", "size": 9, "sampled_sha256": "t"}

        def rt(*files):
            return {"model_files": {"model": QAIRT_ID, "files": list(files)}}

        assert model_files_notes(rt(read), rt(locked)) == []
        assert model_files_notes(rt(locked), rt(read)) == []
        (note,) = model_files_notes(rt(read), rt(read, extra))
        assert "part2_of_4.bin" in note and "part1_of_4.bin" not in note

    def test_an_edited_extensions_file_is_named(self):
        def rt(sha):
            return {
                "model_files": {
                    "model": QAIRT_ID,
                    "files": [{"name": "part1_of_4.bin", "size": 9}],
                    "backend_extensions": {"sha256": sha},
                }
            }

        (note,) = model_files_notes(rt("aa"), rt("bb"))
        assert "extensions file changed" in note and "perf profile" in note
        assert model_files_notes(rt("aa"), rt(None)) == []


_REAL_DRIVER_VERSIONS = bench_provenance.driver_versions


class TestBusyLanes:
    def test_a_probe_false_backend_is_never_asked(self, monkeypatch):
        # backends.json: probe:false marks a paid host, where a discovery
        # request costs money. busy_lanes() used to ask it on every report.
        from orchestrant.benchmark import openai_api

        registry = {
            "paid": {"base_url": "https://paid.example", "probe": False},
            "lane": {"base_url": "http://127.0.0.1:18181"},
        }
        monkeypatch.setattr(
            openai_api, "load_backends", lambda path=None: (registry, "t")
        )
        asked = []

        class Up:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(url, timeout=None):
            asked.append(url)
            return Up()

        monkeypatch.setattr(bench_provenance.urllib.request, "urlopen", fake_urlopen)
        assert _REAL_BUSY_LANES() == ["lane"]
        assert asked == ["http://127.0.0.1:18181/v1/models"]


# Captured at import, before the autouse fixture stubs them for every other test.
_REAL_RUNTIME_INFO = bench_provenance.runtime_info
_REAL_BUSY_LANES = bench_provenance.busy_lanes


class TestTemperatureAndSeed:
    """Whether a lane samples at T=0 decides how a --repeats 1 flip may be
    read. It is measured once and recorded, not rediscovered per run.
    """

    def test_defaults_are_explicit_nulls(self):
        p = collect()
        assert p["temperature"] is None and p["seed"] is None
        assert p["determinism_probe"] is None

    def test_values_are_recorded(self):
        probe = {"deterministic": True, "requests": 2}
        p = collect(temperature=0, seed=42, determinism=probe)
        assert (p["temperature"], p["seed"], p["determinism_probe"]) == (0, 42, probe)

    def test_positional_callers_still_work(self):
        p = collect(None, ("provenance.py",), {"lane": "npu"})
        assert p["lane"] == "npu" and p["tool_sha256"]

    def test_a_temperature_change_is_flagged_on_compare(self):
        old = collect(temperature=0, extra={"git_dirty": False})
        new = collect(temperature=0.7, extra={"git_dirty": False})
        assert any("temperature differs" in n for n in compare(old, new))
        assert compare(old, old) == []


class TestFingerprintLineEndings:
    def test_crlf_and_lf_checkouts_hash_the_same(self, tmp_path, monkeypatch):
        # core.autocrlf: the same commit is CRLF on the Windows host and LF in
        # WSL or CI, and every compare across them cried BENCHMARK SOURCE CHANGED.
        from orchestrant.benchmark import provenance

        (tmp_path / "a.py").write_bytes(b"x = 1\r\ny = 2\r\n")
        (tmp_path / "b.py").write_bytes(b"x = 1\ny = 2\n")
        monkeypatch.setattr(provenance, "__file__", str(tmp_path / "provenance.py"))
        assert provenance.tool_fingerprint("a.py") == provenance.tool_fingerprint(
            "b.py"
        )


class TestSourceChangedDuringRun:
    def test_a_mid_run_edit_is_recorded_and_named(self):
        from orchestrant.benchmark.provenance import collect, compare

        p = collect(tool_files=("stats.py",), tool_sha256_at_start="0" * 16)
        assert p["source_changed_during_run"] is True
        assert p["tool_sha256_at_start"] == "0" * 16
        notes = compare(p, {**p, "source_changed_during_run": False})
        assert any("WHILE it ran" in n for n in notes)

    def test_an_unchanged_source_adds_nothing(self):
        from orchestrant.benchmark.provenance import collect, tool_fingerprint

        sha = tool_fingerprint("stats.py")
        p = collect(tool_files=("stats.py",), tool_sha256_at_start=sha)
        assert "source_changed_during_run" not in p


class TestDeterminismProbe:
    @staticmethod
    def _post(replies):
        calls = []

        def post(url, payload):
            calls.append((url, payload))
            return {"choices": [{"message": {"content": replies[len(calls) - 1]}}]}

        return post, calls

    def test_two_identical_answers_are_deterministic(self):
        post, calls = self._post(["ready", "ok", "ready"])
        r = determinism_probe("http://h:1", "m", post)
        assert r["deterministic"] is True and r["error"] is None
        assert len(calls) == 3 and calls[0] == calls[2]
        assert calls[0][0] == "http://h:1/v1/chat/completions"
        assert calls[0][1]["temperature"] == 0 and calls[0][1]["model"] == "m"

    def test_the_draws_are_never_back_to_back(self):
        # GenieX answers an identical follow-up along a cache path that changes
        # the reply (llama.cpp: stale first token; QAIRT: a different sentence),
        # so two back-to-back draws measured that path, not the sampler.
        post, calls = self._post(["a", "ok", "a"])
        r = determinism_probe("http://h:1", "m", post)
        spacer = calls[1][1]
        assert (
            spacer["max_tokens"] == 1 and spacer["messages"] != calls[0][1]["messages"]
        )
        assert r["spacer"] is True

    def test_two_different_answers_are_not(self):
        post, _ = self._post(["ready", "ok", "Ready!"])
        r = determinism_probe("http://h:1", "m", post)
        assert r["deterministic"] is False
        assert r["output_sha256"][0] != r["output_sha256"][1]

    def test_a_failed_probe_is_null_not_an_exception(self):
        def post(url, payload):
            raise OSError("connection refused")

        r = determinism_probe("http://h:1", "m", post)
        assert r["deterministic"] is None and "OSError" in r["error"]

    def test_the_probe_leaves_the_model_real_choices(self):
        # "Reply with the single word: ready" has almost no entropy, so a
        # SAMPLING lane repeated it verbatim and was recorded deterministic
        # (GenieX v0.6.1 QAIRT lane, 2026-09-24). The probe must ask for enough
        # open-ended text that sampling shows.
        post, calls = self._post(["a", "ok", "a"])
        determinism_probe("http://h:1", "m", post)
        body = calls[0][1]
        assert body["max_tokens"] >= 32
        assert "single word" not in body["messages"][0]["content"]

    def test_a_reply_without_content_is_a_failed_probe(self):
        r = determinism_probe("http://h:1", "m", lambda u, p: {"choices": []})
        assert r["deterministic"] is None

    def test_known_deterministic_needs_a_positive_probe(self):
        assert known_deterministic({"determinism_probe": {"deterministic": True}})
        assert not known_deterministic({"determinism_probe": {"deterministic": False}})
        assert not known_deterministic({"determinism_probe": {"deterministic": None}})
        assert not known_deterministic({}) and not known_deterministic(None)
