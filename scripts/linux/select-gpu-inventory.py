#!/usr/bin/env python3
"""Select OrchestrANT's supported registry GPU candidate rows."""

import argparse
import json
import sys


LINUX = {
    "nvidia-x64": {
        "vendor": "nvidia",
        "arch": "x64",
        "image-tag": "latest-nvidia",
        "runner": "ubuntu-26.04",
        "gpu-runner": "gpu-nvidia-linux-x64",
    },
    "nvidia-arm64": {
        "vendor": "nvidia",
        "arch": "arm64",
        "image-tag": "latest-nvidia",
        "runner": "ubuntu-26.04-arm",
        "gpu-runner": "gpu-nvidia-linux-arm64",
    },
    "amd-x64": {
        "vendor": "amd",
        "arch": "x64",
        "image-tag": "latest-rocm",
        "runner": "ubuntu-26.04",
        "gpu-runner": "gpu-amd-linux-x64",
    },
}
WINDOWS = {
    "nvidia": {
        "vendor": "nvidia",
        "tag": "winamd64-nvidia",
        "gpu-runner": "directml-gpu-nvidia",
    },
    "amd": {
        "vendor": "amd",
        "tag": "winamd64-rocm",
        "gpu-runner": "directml-gpu-amd",
    },
}


def main() -> None:
    """Print only the requested supported rows as compact Actions matrix JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", choices=("linux", "windows"))
    parser.add_argument("selection")
    args = parser.parse_args()
    rows = LINUX if args.platform == "linux" else WINDOWS
    if args.selection != "all" and args.selection not in rows:
        parser.error(f"unsupported {args.platform} GPU selection")
    selected = (
        list(rows.values()) if args.selection == "all" else [rows[args.selection]]
    )
    sys.stdout.write(json.dumps({"include": selected}, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
