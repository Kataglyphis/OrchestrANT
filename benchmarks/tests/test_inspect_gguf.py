"""Unit tests for the GGUF inspector (LB6).

Builds tiny synthetic GGUF files in a temp dir -- no model download, no
network. The point is the verdict logic: this tool exists to separate "the
weights are fine, the kernel is broken" from "this file is dangerous", and a
wrong verdict here would send someone down the same multi-hour wrong path the
tool was written to prevent.
"""

import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspect_gguf import inspect  # noqa: E402

# ggml type ids used below
F32, Q4_0, Q3_K, Q4_K, IQ4_XS, IQ3_S, IQ3_XXS, IQ2_S = 0, 2, 11, 12, 23, 21, 18, 22


def write_gguf(path, tensor_types, metadata=None):
    """Write a minimal but structurally valid GGUF file.

    Layout: magic, version, tensor_count, kv_count, [kv...], [tensor_info...].
    Only string metadata values are emitted (type 8), which is all the
    inspector reads for its report.
    """
    metadata = metadata or {}
    with open(path, "wb") as f:
        f.write(b"GGUF")
        f.write(struct.pack("<I", 3))  # version
        f.write(struct.pack("<Q", len(tensor_types)))  # tensor count
        f.write(struct.pack("<Q", len(metadata)))  # kv count
        for key, value in metadata.items():
            kb = key.encode()
            f.write(struct.pack("<Q", len(kb)) + kb)
            f.write(struct.pack("<I", 8))  # value type: string
            vb = str(value).encode()
            f.write(struct.pack("<Q", len(vb)) + vb)
        for i, ttype in enumerate(tensor_types):
            nb = f"blk.{i}.weight".encode()
            f.write(struct.pack("<Q", len(nb)) + nb)
            f.write(struct.pack("<I", 1))  # n_dims
            f.write(struct.pack("<Q", 4096))  # shape[0]
            f.write(struct.pack("<I", ttype))  # ggml type
            f.write(struct.pack("<Q", 0))  # offset
    return path


class TestVerdicts:
    def test_pure_legacy_quant_is_ok(self, tmp_path):
        p = write_gguf(tmp_path / "q4_0.gguf", [F32] * 10 + [Q4_0] * 40)
        r = inspect(str(p))
        assert r["verdict"] == "OK"
        assert r["risky_types"] == {}

    def test_three_bit_k_quant_is_ok(self, tmp_path):
        # The crucial distinction: 3-bit K-quants work fine. Bit width is NOT
        # what breaks; the i-quant tensor type is.
        p = write_gguf(tmp_path / "q3_k_m.gguf", [F32] * 10 + [Q3_K] * 30 + [Q4_K] * 20)
        assert inspect(str(p))["verdict"] == "OK"

    def test_iq4_is_not_flagged(self, tmp_path):
        # IQ4_XS / IQ4_NL were measured working on the runtime that breaks on
        # sub-4-bit i-quants, so they must not be flagged.
        p = write_gguf(tmp_path / "iq4.gguf", [F32] * 10 + [IQ4_XS] * 40)
        assert inspect(str(p))["verdict"] == "OK"

    def test_iquant_dominated_is_risky(self, tmp_path):
        p = write_gguf(
            tmp_path / "iq3.gguf",
            [F32] * 10 + [IQ3_XXS] * 30 + [IQ2_S] * 10 + [IQ3_S] * 5,
        )
        r = inspect(str(p))
        assert r["verdict"] == "RISKY"
        assert r["risky_share"] > 0.9

    def test_handful_of_iquant_tensors_is_likely_ok(self, tmp_path):
        # Mirrors a real working file: 27B UD-Q4_K_M carries 4 IQ3_S tensors
        # among ~500 quantised ones and runs fine.
        p = write_gguf(
            tmp_path / "mixed.gguf",
            [F32] * 10 + [Q4_K] * 200 + [IQ4_XS] * 100 + [IQ3_S] * 4,
        )
        r = inspect(str(p))
        assert r["verdict"] == "LIKELY OK"
        assert 0 < r["risky_share"] < 0.05

    def test_metadata_and_counts(self, tmp_path):
        p = write_gguf(
            tmp_path / "meta.gguf",
            [F32, Q4_0],
            {"general.architecture": "qwen3", "general.name": "Test"},
        )
        r = inspect(str(p))
        assert r["metadata"]["general.architecture"] == "qwen3"
        assert r["tensor_count"] == 2
        assert r["tensor_types"] == {"F32": 1, "Q4_0": 1}

    def test_rejects_non_gguf(self, tmp_path):
        bad = tmp_path / "not.gguf"
        bad.write_bytes(b"NOPE" + b"\x00" * 64)
        with pytest.raises(ValueError, match="not a GGUF"):
            inspect(str(bad))
