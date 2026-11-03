"""Tests for which model files a GenieX id resolved to and which drivers ran it."""

import hashlib
import json
import pathlib
import sys
import types

import pytest

from orchestrant.benchmark import served_models
from orchestrant.benchmark.provenance import model_files_notes
from orchestrant.benchmark.served_models import driver_versions, geniex_model_files

from .geniex_cache import (
    GGUF_ID,
    MIB,
    QAIRT_ID,
    SAMPLER,
    WINDOW,
    cache_state,
    make_cache,
)


@pytest.fixture(autouse=True)
def _no_machine_caches(monkeypatch, tmp_path):
    # Neither this machine's model caches nor a Windows one under /mnt may answer.
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(served_models.glob, "glob", lambda pattern: [])


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
        # Quants of one model share their first MiB and can share their size; only the weights differ.
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
        # The documented topology serves the lanes from Windows; a Linux-side cache is another install.
        make_cache(tmp_path / "home" / ".cache" / "geniex" / "models")
        windows = make_cache(tmp_path / "mnt-c" / "u" / ".cache" / "geniex" / "models")
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(served_models.glob, "glob", lambda p: [str(windows)])
        mf = geniex_model_files(GGUF_ID)
        assert mf["cache_dir"].startswith(str(windows))

    def test_on_windows_the_users_own_cache_is_the_one(self, tmp_path, monkeypatch):
        home = make_cache(tmp_path / "home" / ".cache" / "geniex" / "models")
        other = make_cache(tmp_path / "mnt-c" / "u" / ".cache" / "geniex" / "models")
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(served_models.glob, "glob", lambda p: [str(other)])
        mf = geniex_model_files(GGUF_ID)
        assert mf["cache_dir"].startswith(str(home))


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
        d = driver_versions()
        assert d["reason"] is None and d["source"].startswith("HKLM")
        assert [r["version"] for r in d["npu"]] == ["30.0.220.3000"]
        assert d["npu"][0]["inf"] == "oem95.inf"
        # The remote display adapter is never a lane's; a value the INF did not set is null, not missing.
        assert [r["name"] for r in d["gpu"]] == [ADRENO["DriverDesc"]]
        assert d["gpu"][0]["date"] is None

    def test_an_unreadable_class_is_a_reason_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setitem(
            sys.modules, "winreg", fake_winreg({NPU_CLASS: {"0000": HEXAGON}})
        )
        d = driver_versions()
        assert d["npu"][0]["version"] == "30.0.220.3000"
        assert d["gpu"] is None and d["reason"].startswith("gpu:")

    def test_off_windows_they_are_null_with_the_reason(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        d = driver_versions()
        assert d["npu"] is None and d["gpu"] is None
        assert served_models.LANE_RUNTIMES_ENV in d["reason"]
