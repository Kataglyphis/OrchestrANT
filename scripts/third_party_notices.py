#!/usr/bin/env python3
"""Write or check THIRD-PARTY-NOTICES.txt, the open-source inventory every package ships.

``generate`` reads built packages (the Linux ``.tar.gz`` and Windows ``.zip`` of each
platform) and writes ``packaging/THIRD-PARTY-NOTICES.txt``: every Python distribution
with its license and the in-package paths of its own license texts, then every native
component outside ``site-packages`` from ``packaging/third-party-native.toml``.
``check`` proves one built package against the notice it ships: no distribution,
license text or native binary may be missing from it, and nothing listed may be absent.
CPU and GPU package variants have separate inventory labels. Packages with the same
label must be checked independently against the generated notice.
"""

from __future__ import annotations

import argparse
import email.message
import email.parser
import fnmatch
import posixpath
import re
import sys
import tarfile
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import IO


REPO = Path(__file__).resolve().parents[1]
NOTICE = REPO / "packaging" / "THIRD-PARTY-NOTICES.txt"
NATIVE = REPO / "packaging" / "third-party-native.toml"
NOTICE_NAME = "THIRD-PARTY-NOTICES.txt"
PROJECT = "orchestrant"
PLATFORM_RE = re.compile(
    r"-(linux-x86_64|linux-aarch64|windows-x64|windows-arm64)\.(tar\.gz|zip)$"
)
# A binary is native by its ELF or PE header, not its name: Linux executables have no suffix.
ELF_MAGIC = bytes((0x7F,)) + b"ELF"
PE_MAGIC = b"MZ"
TEXT_STEMS = ("LICEN", "COPYING", "NOTICE", "COPYRIGHT", "AUTHORS", "THIRDPARTYNOTICES")
TEXT_SUFFIXES = ("", ".txt", ".md", ".rst")
PYTHON_HEADER = "Python distributions"
NATIVE_HEADER = "Native components outside site-packages"
LIMIT = 16 * 1024 * 1024


class NoticeError(RuntimeError):
    """A package or notice is unreadable, or they disagree."""


@dataclass
class Package:
    """The paths of one built package, relative to its bundle root, and the files the notice needs."""

    platform: str
    files: set[str] = field(default_factory=set)
    contents: dict[str, bytes] = field(default_factory=dict)
    native: set[str] = field(default_factory=set)


@dataclass
class Dist:
    """One Python distribution and its license texts per platform."""

    name: str
    version: str
    license: str
    texts: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Component:
    """One native component: its license, source and the package paths it covers."""

    name: str
    license: str
    source: str
    files: list[str]
    text: list[str]
    text_url: str = ""
    platforms: dict[str, list[str]] = field(default_factory=dict)
    require_all_texts: bool = False


def platform_of(path: Path) -> str:
    """The archive's CPU platform or explicit GPU variant inventory label."""
    match = PLATFORM_RE.search(path.name)
    if not match:
        msg = f"{path.name}: not a <id>-<version>-<linux-x86_64|linux-aarch64|windows-x64|windows-arm64>.<tar.gz|zip> package"
        raise NoticeError(msg)
    platform = match.group(1)
    if path.name.startswith("orchestrant-gpu-"):
        variants = {
            "orchestrant-gpu-amd-": ("amd", {"linux-x86_64"}),
            "orchestrant-gpu-nvidia-": ("nvidia", {"linux-x86_64", "linux-aarch64"}),
        }
        for prefix, (variant, supported) in variants.items():
            if (
                path.name.startswith(prefix)
                and platform in supported
                and match.group(2) == "tar.gz"
            ):
                return f"{platform}-{variant}"
        msg = f"{path.name}: unsupported GPU package variant or platform"
        raise NoticeError(msg)
    if path.name.startswith("orchestrant-directml-"):
        if platform != "windows-x64" or match.group(2) != "zip":
            msg = f"{path.name}: DirectML packages require Windows x64"
            raise NoticeError(msg)
        for vendor in ("amd", "nvidia"):
            if re.match(rf"orchestrant-directml-{vendor}-\d", path.name):
                return f"{platform}-directml-{vendor}"
        if re.match(r"orchestrant-directml-\d", path.name):
            return f"{platform}-directml"
        msg = f"{path.name}: unsupported DirectML package variant"
        raise NoticeError(msg)
    return platform


def needed(name: str) -> bool:
    """Only the dist-info metadata and the shipped notice are read, never the payload."""
    return name == NOTICE_NAME or (
        ".dist-info/" in name and name.rsplit("/", 1)[-1] in {"METADATA", "RECORD"}
    )


def strip_root(name: str) -> str | None:
    """Drop the archive's single top directory; the bundle root is what packages share."""
    parts = PurePosixPath(name.replace("\\", "/")).parts
    if len(parts) < 2 or ".." in parts:
        return None
    return "/".join(parts[1:])


def _add_member(
    package: Package, archive: str, name: str, stream: IO[bytes] | None
) -> None:
    """Record one member, keeping its bytes when the notice needs them and its header otherwise."""
    relative = strip_root(name)
    if relative is None:
        return
    package.files.add(relative)
    if stream is None:
        return
    if needed(relative):
        data = stream.read(LIMIT + 1)
        if len(data) > LIMIT:
            msg = f"{archive}: {relative} exceeds {LIMIT} bytes"
            raise NoticeError(msg)
        package.contents[relative] = data
        return
    header = stream.read(4)
    if header == ELF_MAGIC or header.startswith(PE_MAGIC):
        package.native.add(relative)


def read_package(path: Path) -> Package:
    """Stream a tar.gz or zip package once, reading only magic bytes and the notice's inputs."""
    package = Package(platform_of(path))
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            for info in (i for i in archive.infolist() if not i.is_dir()):
                with archive.open(info) as stream:
                    _add_member(package, path.name, info.filename, stream)
        return package
    with tarfile.open(path, "r|*") as archive:
        for member in archive:
            if member.isfile():
                _add_member(
                    package, path.name, member.name, archive.extractfile(member)
                )
            elif member.issym():
                _add_member(package, path.name, member.name, None)
    return package


def license_of(metadata: email.message.Message) -> str:
    """SPDX expression first, then a short License field, then trove classifiers."""
    expression = (metadata.get("License-Expression") or "").strip()
    if expression:
        return expression
    declared = (metadata.get("License") or "").strip()
    if (
        declared
        and "\n" not in declared
        and len(declared) <= 80
        and declared.upper() != "UNKNOWN"
    ):
        return declared
    classifiers = [
        c.split("::")[-1].strip()
        for c in metadata.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    return ", ".join(classifiers) or "see its license text"


def is_text(entry: str) -> bool:
    """A RECORD entry that is a license or notice text rather than code."""
    name = PurePosixPath(entry).name
    stem, dot, suffix = name.partition(".")
    plain = stem.upper().replace("-", "").replace("_", "")
    return plain.startswith(TEXT_STEMS) and (
        f"{dot}{suffix}".lower() in TEXT_SUFFIXES or "." not in name
    )


def canonical(name: str) -> str:
    """PEP 503 normalization, so dist-info spellings compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


def package_dists(
    package: Package,
) -> dict[tuple[str, str], tuple[str, str, list[str]]]:
    """Each bundled distribution but the project: (display name, license, text paths)."""
    found: dict[tuple[str, str], tuple[str, str, list[str]]] = {}
    parser = email.parser.Parser()
    for path in sorted(
        p for p in package.contents if p.endswith(".dist-info/METADATA")
    ):
        dist_info = path.rsplit("/", 1)[0]
        site = dist_info.rsplit("/", 1)[0]
        metadata = parser.parsestr(package.contents[path].decode("utf-8", "replace"))
        name, version = metadata["Name"], metadata["Version"]
        if canonical(name) == PROJECT:
            continue
        record = package.contents.get(f"{dist_info}/RECORD", b"").decode(
            "utf-8", "replace"
        )
        entries = (line.split(",", 1)[0] for line in record.splitlines())
        texts = sorted(
            {f"{site}/{e}" for e in entries if e and ".." not in e and is_text(e)}
            & package.files
        )
        found[(canonical(name), version)] = (name, license_of(metadata), texts)
    return found


def owned_files(package: Package) -> set[str]:
    """Every path a bundled distribution's RECORD claims."""
    owned: set[str] = set()
    for path, data in package.contents.items():
        if path.endswith(".dist-info/RECORD"):
            site = path.rsplit("/", 2)[0]
            for line in data.decode("utf-8", "replace").splitlines():
                entry = line.split(",", 1)[0]
                if entry:
                    owned.add(posixpath.normpath(f"{site}/{entry}"))
    return owned


def native_files(package: Package) -> list[str]:
    """ELF and PE files outside site-packages, or inside it but claimed by no distribution."""
    owned = owned_files(package)
    return sorted(
        p for p in package.native if "site-packages/" not in p or p not in owned
    )


def load_components(
    path: Path = NATIVE,
) -> tuple[list[Component], dict[str, list[str]]]:
    """The curated native components, and the texts this repo ships for wheels that carry none."""
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    components = [
        Component(
            r["name"],
            r["license"],
            r["source"],
            r["files"],
            r.get("text", []),
            r.get("text_url", ""),
            require_all_texts=r.get("require_all_texts", False),
        )
        for r in config["component"]
    ]
    supplied = {canonical(r["name"]): r["files"] for r in config.get("python_text", [])}
    return components, supplied


def glob_match(path: str, pattern: str) -> bool:
    """Shell-style match whose ``*`` stays inside one directory, so ``*.exe`` is the root only."""
    return path.count("/") == pattern.count("/") and fnmatch.fnmatchcase(path, pattern)


def covering(path: str, components: list[Component]) -> list[Component]:
    """The components whose file globs name ``path``."""
    return [c for c in components if any(glob_match(path, g) for g in c.files)]


def _add_dists(
    package: Package,
    dists: dict[tuple[str, str], Dist],
    supplied: dict[str, list[str]],
    problems: list[str],
) -> None:
    """Merge one package's distributions; supplied texts stand in where a wheel ships none."""
    for key, (name, license_id, texts) in package_dists(package).items():
        dist = dists.setdefault(key, Dist(name, key[1], license_id))
        # packaging/app.json ships the supplied texts, so packages built before them lack the paths.
        dist.texts[package.platform] = texts or supplied.get(key[0], [])
        if not dist.texts[package.platform]:
            problems.append(
                f"{package.platform}: {name} {key[1]} ships no license text; add a python_text row"
            )


def _add_components(
    package: Package, components: list[Component], problems: list[str]
) -> None:
    """Mark the components whose binaries one package carries, with their shipped texts."""
    for path in native_files(package):
        owners = covering(path, components)
        if not owners:
            problems.append(f"{package.platform}: no native component covers {path}")
        for owner in owners:
            owner.platforms.setdefault(package.platform, [])
    for component in (c for c in components if package.platform in c.platforms):
        texts = sorted(
            p for p in package.files if any(glob_match(p, g) for g in component.text)
        )
        component.platforms[package.platform] = texts
        if component.require_all_texts:
            problems.extend(
                f"{package.platform}: {component.name} is missing required text {pattern}"
                for pattern in component.text
                if not any(glob_match(path, pattern) for path in package.files)
            )
        if not texts and not component.text_url:
            problems.append(
                f"{package.platform}: {component.name} has neither a shipped text nor a text_url"
            )


def generate(
    packages: list[Package], components: list[Component], supplied: dict[str, list[str]]
) -> str:
    """The notice for these packages; fails on a binary or distribution it cannot account for."""
    platforms = [package.platform for package in packages]
    if len(platforms) != len(set(platforms)):
        msg = "duplicate package inventory label; check each same-label package separately"
        raise NoticeError(msg)
    dists: dict[tuple[str, str], Dist] = {}
    problems: list[str] = []
    for package in packages:
        _add_dists(package, dists, supplied, problems)
        _add_components(package, components, problems)
    if problems:
        raise NoticeError("; ".join(problems))
    return render(
        sorted(dists.values(), key=lambda d: (canonical(d.name), d.version)),
        [c for c in components if c.platforms],
    )


def render(dists: list[Dist], components: list[Component]) -> str:
    """The notice text; ``parse`` reads back exactly what this writes."""
    out = [
        "OrchestrANT third-party notices",
        "",
        "OrchestrANT is MIT-licensed; its text is LICENSE beside this file.",
        "Each component below names its license and where its original texts are inside this package.",
        "Generated by scripts/third_party_notices.py from built packages; do not edit.",
        "",
        PYTHON_HEADER,
        "=" * len(PYTHON_HEADER),
    ]
    for dist in dists:
        out.append(
            f"- {dist.name}=={dist.version} | {dist.license} | {', '.join(sorted(dist.texts))}"
        )
        out += [
            f"    {platform}: {'; '.join(texts)}"
            for platform, texts in sorted(dist.texts.items())
        ]
    out += ["", NATIVE_HEADER, "=" * len(NATIVE_HEADER)]
    for component in components:
        out.append(
            f"- {component.name} | {component.license} | {', '.join(sorted(component.platforms))}"
        )
        out.append(f"    source: {component.source}")
        out.append(f"    files: {'; '.join(component.files)}")
        if component.text_url:
            out.append(f"    license text: {component.text_url}")
        out += [
            f"    {platform}: {'; '.join(texts)}"
            for platform, texts in sorted(component.platforms.items())
            if texts
        ]
    return "\n".join(out) + "\n"


def parse(text: str) -> tuple[list[Dist], list[Component]]:
    """The distributions and components a rendered notice records."""
    if PYTHON_HEADER not in text or NATIVE_HEADER not in text:
        msg = "the notice has no distribution or native-component section"
        raise NoticeError(msg)
    python_part, native_part = text.split(PYTHON_HEADER, 1)[1].split(NATIVE_HEADER, 1)
    dists: list[Dist] = []
    for line in python_part.splitlines():
        if line.startswith("- "):
            spec, license_id, _ = line[2:].split(" | ", 2)
            name, version = spec.split("==", 1)
            dists.append(Dist(name, version, license_id))
        elif line.startswith("    ") and dists:
            platform, _, paths = line.strip().partition(": ")
            dists[-1].texts[platform] = [p for p in paths.split("; ") if p]
    components: list[Component] = []
    for line in native_part.splitlines():
        if line.startswith("- "):
            name, license_id, platforms = line[2:].split(" | ", 2)
            components.append(
                Component(
                    name,
                    license_id,
                    "",
                    [],
                    [],
                    "",
                    {p: [] for p in platforms.split(", ")},
                )
            )
        elif line.startswith("    ") and components:
            key, _, value = line.strip().partition(": ")
            if key == "files":
                components[-1].files = value.split("; ")
            elif key in components[-1].platforms:
                components[-1].platforms[key] = value.split("; ")
    return dists, components


def _check_dists(package: Package, dists: list[Dist]) -> list[str]:
    """Distributions and their texts the package and its notice disagree on."""
    platform = package.platform
    listed = {(canonical(d.name), d.version): d for d in dists if platform in d.texts}
    actual = package_dists(package)
    problems = [
        f"{platform}: {name} {key[1]} is not in the notice"
        for key, (name, _, _) in sorted(actual.items())
        if key not in listed
    ]
    problems += [
        f"{platform}: the notice lists {d.name} {d.version}, which the package lacks"
        for key, d in sorted(listed.items())
        if key not in actual
    ]
    for key, dist in sorted(listed.items()):
        own = actual[key][2] if key in actual else []
        if own and dist.texts[platform] != own:
            problems.append(
                f"{platform}: license texts of {dist.name} {dist.version} differ: notice {dist.texts[platform]}, package {own}"
            )
        problems += [
            f"{platform}: {dist.name}'s text {p} is missing"
            for p in dist.texts[platform]
            if p not in package.files
        ]
    return problems


def _check_components(package: Package, components: list[Component]) -> list[str]:
    """Native binaries and component texts the package and its notice disagree on."""
    platform = package.platform
    here = [c for c in components if platform in c.platforms]
    natives = native_files(package)
    problems = [
        f"{platform}: no listed native component covers {path}"
        for path in natives
        if not covering(path, here)
    ]
    for component in here:
        if not any(covering(p, [component]) for p in natives):
            problems.append(
                f"{platform}: the notice lists {component.name}, which the package lacks"
            )
        problems += [
            f"{platform}: {component.name}'s text {p} is missing"
            for p in component.platforms[platform]
            if p not in package.files
        ]
    return problems


def check(package: Package) -> list[str]:
    """Differences between a built package and the notice it ships; empty when they agree."""
    if NOTICE_NAME not in package.contents:
        return [f"{package.platform}: the package ships no {NOTICE_NAME} at its root"]
    try:
        dists, components = parse(package.contents[NOTICE_NAME].decode("utf-8"))
    except (NoticeError, ValueError, UnicodeDecodeError) as error:
        return [f"{package.platform}: the shipped {NOTICE_NAME} is malformed: {error}"]
    return _check_dists(package, dists) + _check_components(package, components)


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("mode", choices=("generate", "check"))
    parser.add_argument(
        "packages", nargs="+", type=Path, help="built .tar.gz/.zip packages"
    )
    parser.add_argument("--output", type=Path, default=NOTICE)
    parser.add_argument("--native", type=Path, default=NATIVE)
    args = parser.parse_args(argv)
    try:
        packages = [read_package(p) for p in args.packages]
        if args.mode == "generate":
            components, supplied = load_components(args.native)
            args.output.write_text(
                generate(packages, components, supplied), encoding="utf-8", newline="\n"
            )
            return 0
        problems = [problem for package in packages for problem in check(package)]
    except (NoticeError, OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        print(f"third-party notices: {error}", file=sys.stderr)  # noqa: T201
        return 1
    for problem in problems:
        print(f"third-party notices: {problem}", file=sys.stderr)  # noqa: T201
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
