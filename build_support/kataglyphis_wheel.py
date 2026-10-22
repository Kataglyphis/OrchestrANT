# Copyright (c) 2026 Kataglyphis
# SPDX-License-Identifier: MIT
"""Rewrite a binary wheel atomically, preserving metadata and rebuilding RECORD."""

# ruff: noqa: INP001
import base64
import copy
import csv
import hashlib
import io
import os
import tempfile
import zipfile
from pathlib import Path


def rewrite_wheel(
    wheel_path: str | os.PathLike[str], exclude_suffixes: tuple[str, ...]
) -> None:
    """Remove excluded sources and signatures, then compress and hash every retained file."""
    wheel_path = Path(wheel_path)
    descriptor, temporary = tempfile.mkstemp(suffix=".whl", dir=wheel_path.parent)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(wheel_path) as source:
            records = [
                info.filename
                for info in source.infolist()
                if info.filename.endswith(".dist-info/RECORD")
            ]
            if len(records) != 1:
                message = "a wheel must contain exactly one .dist-info/RECORD"
                raise ValueError(message)
            record_name = records[0]
            info_prefix = record_name.removesuffix("RECORD")
            record = io.StringIO(newline="")
            writer = csv.writer(record, lineterminator="\n")
            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED
            ) as target:
                for info in source.infolist():
                    name = info.filename
                    if (
                        name.endswith("/")
                        or name == record_name
                        or name.endswith(exclude_suffixes)
                    ):
                        continue
                    if name.startswith(info_prefix) and name.lower().endswith(
                        (".jws", ".asc", ".sig")
                    ):
                        continue
                    data = source.read(info)
                    output_info = copy.copy(info)
                    output_info.compress_type = zipfile.ZIP_DEFLATED
                    target.writestr(output_info, data)
                    digest = (
                        base64.urlsafe_b64encode(hashlib.sha256(data).digest())
                        .rstrip(b"=")
                        .decode()
                    )
                    writer.writerow([name, "sha256=" + digest, len(data)])
                writer.writerow([record_name, "", ""])
                info = zipfile.ZipInfo(record_name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                target.writestr(info, record.getvalue().encode("utf-8"))
        Path(temporary).replace(wheel_path)
    finally:
        Path(temporary).unlink(missing_ok=True)
