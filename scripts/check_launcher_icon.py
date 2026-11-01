#!/usr/bin/env python3
"""Check that every launcher EXE at a Windows ZIP package's bundle root embeds the project logo.

The hub wraps the PNG verbatim as the icon resource, so one RT_ICON entry must equal its bytes.
This reads PE resources only; it does not check the MSI shortcut or the MSIX logos.
"""

from __future__ import annotations

import argparse
import struct
import sys
import zipfile
from pathlib import Path, PurePosixPath


RT_ICON = 3
RESOURCE_DIRECTORY = 2
SUBDIRECTORY = 0x80000000


class PeError(ValueError):
    """The file is not a PE image this reader understands."""


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _sections(data: bytes) -> tuple[int, list[tuple[int, int, int]]]:
    """The resource directory's RVA and each section's (RVA, size, file offset)."""
    if data[:2] != b"MZ":
        msg = "no MZ header"
        raise PeError(msg)
    pe = _u32(data, 0x3C)
    if data[pe : pe + 4] != b"PE\0\0":
        msg = "no PE signature"
        raise PeError(msg)
    count, optional_size = _u16(data, pe + 6), _u16(data, pe + 20)
    optional = pe + 24
    directories = optional + {0x10B: 96, 0x20B: 112}.get(
        _u16(data, optional), -optional - 1
    )
    if directories < 0:
        msg = "unknown optional header"
        raise PeError(msg)
    rva = _u32(data, directories + 8 * RESOURCE_DIRECTORY)
    table = optional + optional_size
    sections = [
        (
            _u32(data, s + 12),
            max(_u32(data, s + 8), _u32(data, s + 16)),
            _u32(data, s + 20),
        )
        for s in range(table, table + 40 * count, 40)
    ]
    return rva, sections


def _offset(rva: int, sections: list[tuple[int, int, int]]) -> int:
    for start, size, raw in sections:
        if start <= rva < start + size:
            return raw + rva - start
    msg = f"RVA {rva:#x} lies in no section"
    raise PeError(msg)


def icon_images(data: bytes) -> list[bytes]:
    """The raw RT_ICON resources of a PE image."""
    rva, sections = _sections(data)
    if not rva:
        return []
    base = _offset(rva, sections)

    def entries(directory: int) -> list[tuple[int, int]]:
        total = _u16(data, base + directory + 12) + _u16(data, base + directory + 14)
        return [
            (_u32(data, e), _u32(data, e + 4))
            for e in range(base + directory + 16, base + directory + 16 + 8 * total, 8)
        ]

    def leaves(directory: int) -> list[bytes]:
        found: list[bytes] = []
        for _, target in entries(directory):
            if target & SUBDIRECTORY:
                found += leaves(target & ~SUBDIRECTORY)
            else:
                start = _offset(_u32(data, base + target), sections)
                found.append(data[start : start + _u32(data, base + target + 4)])
        return found

    return [
        image
        for key, target in entries(0)
        if key == RT_ICON and target & SUBDIRECTORY
        for image in leaves(target & ~SUBDIRECTORY)
    ]


def check(package: Path, logo: bytes) -> list[str]:
    """One problem per root launcher that lacks the logo; an archive without launchers is one too."""
    problems: list[str] = []
    with zipfile.ZipFile(package) as archive:
        launchers = [
            n
            for n in archive.namelist()
            if len(PurePosixPath(n).parts) == 2 and n.lower().endswith(".exe")
        ]
        if not launchers:
            return [f"{package.name}: no launcher EXE at the bundle root"]
        for name in launchers:
            try:
                images = icon_images(archive.read(name))
            except (PeError, struct.error) as error:
                problems.append(f"{name}: unreadable PE: {error}")
                continue
            if logo not in images:
                problems.append(
                    f"{name}: no icon resource equals the project logo ({len(images)} icon image(s))"
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path, help="the Windows .zip package")
    parser.add_argument("logo", type=Path, help="the PNG the launchers must embed")
    args = parser.parse_args(argv)
    try:
        problems = check(args.package, args.logo.read_bytes())
    except (OSError, zipfile.BadZipFile) as error:
        problems = [str(error)]
    for problem in problems:
        print(f"launcher icon: {problem}", file=sys.stderr)  # noqa: T201
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
