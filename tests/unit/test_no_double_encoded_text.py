"""No source file carries UTF-8 text decoded as Latin-1 and encoded again, as the GPU log's degree sign once did."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
# "Â" or "Ã" followed by a second C2/C3 lead byte: one UTF-8 sign that went through Latin-1 once more.
DOUBLE_ENCODED = re.compile(rb"\xc3[\x82\x83]\xc2[\x80-\xbf]")


def test_no_source_file_holds_double_encoded_text() -> None:
    """Scan every text source for a sign that went through Latin-1 twice."""
    found = []
    for folder in ("orchestrant", "benchmarks", "frontend", "scripts"):
        for path in sorted((ROOT / folder).rglob("*")):
            if (
                path.suffix not in {".py", ".md", ".toml", ".json", ".sh"}
                or not path.is_file()
            ):
                continue
            for number, line in enumerate(path.read_bytes().splitlines(), start=1):
                if DOUBLE_ENCODED.search(line):
                    found.append(f"{path.relative_to(ROOT)}:{number}")
    assert not found, "double-encoded text: " + ", ".join(found)


def test_the_gpu_line_logs_a_plain_degree_sign() -> None:
    """The GPU temperature reads 41°C, not with a stray character before the sign."""
    source = (ROOT / "orchestrant/yolo/resource_log.py").read_text(encoding="utf-8")
    assert "Temp: {:.0f}°C" in source
