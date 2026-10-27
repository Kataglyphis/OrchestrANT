#!/usr/bin/env python3
"""Write a minimal PE32+ image whose only resource is one RT_ICON; a fixture, never a runnable program."""

from __future__ import annotations

import struct
import sys
from pathlib import Path


def icon_pe(image: bytes) -> bytes:
    """A PE whose .rsrc holds ``image`` as RT_ICON 1, language 0x409."""
    rva, raw = 0x1000, 0x200
    tree = b"".join(
        [
            struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1)
            + struct.pack("<II", 3, 0x80000000 | 24),
            struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1)
            + struct.pack("<II", 1, 0x80000000 | 48),
            struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", 0x409, 72),
            struct.pack("<IIII", rva + 88, len(image), 0, 0),
        ]
    )
    resources = tree + image
    dos = b"MZ" + bytes(0x3A) + struct.pack("<I", 0x40)
    coff = b"PE\0\0" + struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, 240, 0x22)
    optional = bytearray(240)
    struct.pack_into("<H", optional, 0, 0x20B)
    struct.pack_into("<II", optional, 112 + 8 * 2, rva, len(resources))
    section = (
        b".rsrc\0\0\0"
        + struct.pack("<IIII", len(resources), rva, len(resources), raw)
        + bytes(16)
    )
    header = dos + coff + bytes(optional) + section
    return header + bytes(raw - len(header)) + resources


if __name__ == "__main__":
    Path(sys.argv[2]).write_bytes(icon_pe(Path(sys.argv[1]).read_bytes()))
