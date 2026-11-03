"""A GenieX model cache as on the lab host, for the tests of what served a measurement."""

import json


GGUF_ID = "unsloth/Qwen3-4B-GGUF:Q4_0"
QAIRT_ID = "qualcomm/Qwen3-4B-Instruct-2507:W4A16"
SAMPLER = {"version": 1, "seed": 42, "temp": 0.8, "top-k": 40, "top-p": 0.95}
MIB = 1 << 20
WINDOW = 1 << 16  # one of the 16 sampled windows


def make_cache(root, weights=b"w"):
    """A GenieX model cache as on the lab host; caches with other `weights` are quants sharing first MiB and size."""
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
