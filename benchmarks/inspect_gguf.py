#!/usr/bin/env python3
"""Inspect a GGUF file: metadata + tensor quantisation types (LB6).

Why this exists
---------------
A benchmark cannot tell you *why* a model is broken -- and a broken model is
often FAST, so throughput numbers look great while the output is nonsense.
What actually diagnosed one such case was the tensor-type histogram: files
dominated by sub-4-bit **i-quants** (IQ3_S, IQ3_XXS, IQ2_*, IQ1_*) produced
garbage on GenieX (v0.5.0 llama.cpp 873e5d8 AND v0.6.1 llama.cpp 0eadefe,
re-verified 2026-09-05), on both its CPU and GPU lanes, while plain K-quants
at the same bit width (Q3_K_M) and IQ4_XS were fine. See
docs/geniex-local-ai-setup.md.

Reads only the file header, so it is instant even on a 16 GB model.

Usage:
    python3 inspect_gguf.py model.gguf [more.gguf ...]
    python3 inspect_gguf.py --json model.gguf
"""

import argparse
import collections
import json
import struct
import sys

# ggml_type -> name, from ggml.h
GGML_TYPES = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    6: "Q5_0",
    7: "Q5_1",
    8: "Q8_0",
    9: "Q8_1",
    10: "Q2_K",
    11: "Q3_K",
    12: "Q4_K",
    13: "Q5_K",
    14: "Q6_K",
    15: "Q8_K",
    16: "IQ2_XXS",
    17: "IQ2_XS",
    18: "IQ3_XXS",
    19: "IQ1_S",
    20: "IQ4_NL",
    21: "IQ3_S",
    22: "IQ2_S",
    23: "IQ4_XS",
    24: "I8",
    25: "I16",
    26: "I32",
    27: "I64",
    28: "F64",
    29: "IQ1_M",
    30: "BF16",
}

# i-quants at 3 bits and below. IQ4_XS / IQ4_NL are deliberately NOT here:
# they were measured working on the same runtime that breaks on these.
RISKY_TYPES = {"IQ3_S", "IQ3_XXS", "IQ2_XXS", "IQ2_XS", "IQ2_S", "IQ1_S", "IQ1_M"}

INTERESTING_KV = (
    "general.architecture",
    "general.name",
    "general.file_type",
    "general.quantization_version",
    "general.size_label",
    "quantize.imatrix.file",
    "quantize.imatrix.entries_count",
    "quantize.imatrix.chunks_count",
)


def _read(f, fmt):
    return struct.unpack(fmt, f.read(struct.calcsize(fmt)))[0]


def _read_str(f):
    return f.read(_read(f, "<Q")).decode("utf-8", "replace")


def _read_value(f, vtype):
    if vtype == 8:
        return _read_str(f)
    if vtype == 9:  # array
        elem_type = _read(f, "<I")
        count = _read(f, "<Q")
        values = [_read_value(f, elem_type) for _ in range(count)]
        return values[:3] + [f"...({count} total)"] if count > 3 else values
    scalar = {
        0: "<B",
        1: "<b",
        2: "<H",
        3: "<h",
        4: "<I",
        5: "<i",
        6: "<f",
        7: "<?",
        10: "<Q",
        11: "<q",
        12: "<d",
    }
    return _read(f, scalar[vtype])


def inspect(path):
    """Return {metadata, tensor_types, verdict} for one GGUF file."""
    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            raise ValueError(f"{path}: not a GGUF file (bad magic)")
        version = _read(f, "<I")
        n_tensors = _read(f, "<Q")
        n_kv = _read(f, "<Q")

        metadata = {}
        for _ in range(n_kv):
            key = _read_str(f)
            metadata[key] = _read_value(f, _read(f, "<I"))

        types = collections.Counter()
        for _ in range(n_tensors):
            _read_str(f)  # tensor name
            n_dims = _read(f, "<I")
            for _ in range(n_dims):
                _read(f, "<Q")  # shape
            types[GGML_TYPES.get(_read(f, "<I"), "UNKNOWN")] += 1
            _read(f, "<Q")  # offset

    risky = {t: c for t, c in types.items() if t in RISKY_TYPES}
    risky_count = sum(risky.values())
    quantised = sum(c for t, c in types.items() if t not in ("F32", "F16", "BF16"))
    share = risky_count / quantised if quantised else 0.0

    if risky_count == 0:
        verdict, note = "OK", "no sub-4-bit i-quant tensors"
    elif share < 0.05:
        verdict, note = (
            "LIKELY OK",
            f"only {risky_count} sub-4-bit i-quant tensors "
            f"({share:.1%}) — a working file measured 4",
        )
    else:
        verdict, note = (
            "RISKY",
            f"{risky_count} sub-4-bit i-quant tensors ({share:.1%}) — "
            "this pattern produced garbage output on GenieX v0.5.0 and v0.6.1",
        )

    return {
        "file": path,
        "gguf_version": version,
        "tensor_count": n_tensors,
        "metadata": {k: v for k, v in metadata.items() if k in INTERESTING_KV},
        "tensor_types": dict(types.most_common()),
        "risky_types": risky,
        "risky_share": round(share, 4),
        "verdict": verdict,
        "note": note,
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("files", nargs="+", help="GGUF file(s) to inspect")
    ap.add_argument("--json", action="store_true", help="Machine-readable output")
    args = ap.parse_args()

    reports, failed = [], False
    for path in args.files:
        try:
            reports.append(inspect(path))
        except Exception as e:
            failed = True
            if args.json:
                reports.append({"file": path, "error": str(e)})
            else:
                print(f"  ERROR  {path}: {e}", file=sys.stderr)

    if args.json:
        print(json.dumps(reports, indent=2))
    else:
        for r in reports:
            if "error" in r:
                continue
            print(f"\n=== {r['file']}")
            print(f"  GGUF v{r['gguf_version']}, {r['tensor_count']} tensors")
            for k, v in r["metadata"].items():
                print(f"  {k} = {v}")
            print(f"  tensor types: {r['tensor_types']}")
            print(f"  VERDICT: {r['verdict']} — {r['note']}")
        print()

    # Non-zero exit on a RISKY file so a pull script can gate on it.
    if failed or any(r.get("verdict") == "RISKY" for r in reports):
        sys.exit(1)


if __name__ == "__main__":
    main()
