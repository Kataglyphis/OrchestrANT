"""The packages' third-party inventory and launcher-icon checks, on fixture archives."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest


if TYPE_CHECKING:
    from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
ELF = bytes((0x7F,)) + b"ELF fixture"


def _load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations through sys.modules, so the module is registered before it runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


notices = _load(ROOT / "scripts" / "third_party_notices.py")
icon = _load(ROOT / "scripts" / "check_launcher_icon.py")
pe_fixture = _load(ROOT / "scripts" / "windows" / "tests" / "new_icon_pe.py")

SITE = "runtime/lib/python3.15/site-packages"
NATIVE_TOML = """
[[component]]
name = "Fixture runtime"
license = "MIT"
source = "https://example.invalid/runtime"
files = ["runtime/lib/libfixture.so*"]
text = ["runtime/RUNTIME-LICENSE"]

[[component]]
name = "Unused"
license = "MIT"
source = "https://example.invalid/unused"
files = ["runtime/lib/libunused*"]
text_url = "https://example.invalid/unused/LICENSE"

[[component]]
name = "Fixture DirectML"
license = "MIT"
source = "https://example.invalid/directml"
files = ["runtime/DirectML.dll"]
text_url = "https://example.invalid/directml/LICENSE"

[[python_text]]
name = "bare"
files = ["licenses/bare-LICENSE"]
"""


def _dist(name: str, version: str, *, text: bool = True) -> dict[str, bytes]:
    info = f"{SITE}/{name}-{version}.dist-info"
    record = [f"{name}/__init__.py,,", f"{name}/_native.so,,"]
    files = {
        f"{info}/METADATA": f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\nLicense-Expression: MIT\n".encode(),
        f"{SITE}/{name}/__init__.py": b"",
        f"{SITE}/{name}/_native.so": ELF,
    }
    if text:
        record.append(f"{name}-{version}.dist-info/licenses/LICENSE,,")
        files[f"{info}/licenses/LICENSE"] = b"MIT text\n"
    files[f"{info}/RECORD"] = "\n".join(record).encode()
    return files


def _members(
    *, extra: dict[str, bytes] | None = None, drop: tuple[str, ...] = ()
) -> dict[str, bytes]:
    members = {
        **_dist("alpha", "1.0"),
        **_dist("bare", "2.0", text=False),
        **_dist("orchestrant", "0.0.1"),
        "runtime/lib/libfixture.so.1": ELF,
        "runtime/RUNTIME-LICENSE": b"runtime text\n",
        "licenses/bare-LICENSE": b"bare text\n",
        "bin/yolo-monitor": b"#!/bin/sh\n",
    }
    members.update(extra or {})
    for name in drop:
        members.pop(name)
    return members


def _tar(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(f"orchestrant-0.0.1-linux-x86_64/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(f"orchestrant-0.0.1-windows-x64/{name}", data)
    return path


@pytest.fixture
def native_toml(tmp_path: Path) -> Path:
    """The curated native components for the fixture packages."""
    path = tmp_path / "native.toml"
    path.write_text(NATIVE_TOML, encoding="utf-8")
    return path


def _notice(tmp_path: Path, native_toml: Path, members: dict[str, bytes]) -> bytes:
    built = _tar(tmp_path / "pre-linux-x86_64.tar.gz", members)
    components, supplied = notices.load_components(native_toml)
    return notices.generate(
        [notices.read_package(built)], components, supplied
    ).encode()


def _shipped(
    tmp_path: Path, native_toml: Path, members: dict[str, bytes] | None = None
) -> Path:
    """A tar.gz package whose root carries the notice generated from its own contents."""
    members = members if members is not None else _members()
    notice = _notice(tmp_path, native_toml, members)
    return _tar(
        tmp_path / "orchestrant-0.0.1-linux-x86_64.tar.gz",
        {**members, notices.NOTICE_NAME: notice},
    )


def _problems(path: Path) -> list[str]:
    return notices.check(notices.read_package(path))


def test_generated_notice_lists_every_component_and_text(
    tmp_path: Path, native_toml: Path
) -> None:
    """Distributions, supplied texts and covered binaries appear; the project and unused rows do not."""
    text = _notice(tmp_path, native_toml, _members()).decode()
    assert (
        f"- alpha==1.0 | MIT | linux-x86_64\n    linux-x86_64: {SITE}/alpha-1.0.dist-info/licenses/LICENSE"
        in text
    )
    assert "    linux-x86_64: licenses/bare-LICENSE" in text
    assert "- Fixture runtime | MIT | linux-x86_64" in text
    assert "    linux-x86_64: runtime/RUNTIME-LICENSE" in text
    assert "orchestrant==" not in text
    assert "Unused" not in text


def test_windows_nvidia_native_inventory_requires_the_original_sdk_texts() -> None:
    """The observed CUDA/cuDNN closure is covered without accepting a new major DLL."""
    components, supplied = notices.load_components(
        ROOT / "packaging/third-party-native.toml"
    )
    directory = "runtime/Lib/site-packages/cv2/bin"
    cuda = [
        "cublas64_13.dll",
        "cublasLt64_13.dll",
        "cufft64_12.dll",
        "nppc64_13.dll",
        "nppial64_13.dll",
    ]
    cudnn = ["cudnn64_9.dll"]
    native = {f"{directory}/{name}" for name in cuda + cudnn}
    licenses = {
        "licenses/nvidia-cuda-13.4-LICENSE.txt",
        "licenses/nvidia-cudnn-9.27.0.42-LICENSE.txt",
    }
    package = notices.Package(
        platform="windows-x64-directml", files=native | licenses, native=native
    )
    text = notices.generate([package], components, supplied)
    assert "NVIDIA CUDA 13.4 runtime libraries" in text
    assert "NVIDIA cuDNN 9.27.0.42 runtime" in text
    assert all(len(notices.covering(path, components)) == 1 for path in native)
    assert not notices.covering(f"{directory}/cublas64_14.dll", components)
    components, supplied = notices.load_components(
        ROOT / "packaging/third-party-native.toml"
    )
    package.files.remove("licenses/nvidia-cudnn-9.27.0.42-LICENSE.txt")
    with pytest.raises(notices.NoticeError, match="missing required text"):
        notices.generate([package], components, supplied)


def test_check_accepts_a_package_matching_its_notice(
    tmp_path: Path, native_toml: Path
) -> None:
    """A package and the notice generated from it agree."""
    assert _problems(_shipped(tmp_path, native_toml)) == []


def test_check_accepts_a_windows_zip(tmp_path: Path, native_toml: Path) -> None:
    """The zip reader and platform label work like the tar ones."""
    members = {
        k.replace("runtime/lib/python3.15/", "runtime/Lib/"): v
        for k, v in _members().items()
    }
    members.pop("runtime/lib/libfixture.so.1")
    members.pop("runtime/RUNTIME-LICENSE")
    built = _zip(tmp_path / "pre-windows-x64.zip", members)
    components, supplied = notices.load_components(native_toml)
    notice = notices.generate([notices.read_package(built)], components, supplied)
    package = _zip(
        tmp_path / "orchestrant-0.0.1-windows-x64.zip",
        {**members, notices.NOTICE_NAME: notice.encode()},
    )
    assert "| windows-x64" in notice
    assert _problems(package) == []


@pytest.mark.parametrize(
    ("filename", "label"),
    [
        ("orchestrant-0.0.1-linux-x86_64.tar.gz", "linux-x86_64"),
        ("orchestrant-gpu-amd-0.0.1-linux-x86_64.tar.gz", "linux-x86_64-amd"),
        ("orchestrant-gpu-nvidia-0.0.1-linux-x86_64.tar.gz", "linux-x86_64-nvidia"),
        ("orchestrant-gpu-nvidia-0.0.1-linux-aarch64.tar.gz", "linux-aarch64-nvidia"),
        ("orchestrant-directml-0.0.1-windows-x64.zip", "windows-x64-directml"),
        ("orchestrant-directml-amd-0.0.1-windows-x64.zip", "windows-x64-directml-amd"),
        (
            "orchestrant-directml-nvidia-0.0.1-windows-x64.zip",
            "windows-x64-directml-nvidia",
        ),
        ("orchestrant-0.0.1-windows-arm64.zip", "windows-arm64"),
    ],
)
def test_inventory_labels_distinguish_package_variants(
    filename: str, label: str
) -> None:
    """The actual GPU archive names do not collide with their CPU platform labels."""
    assert notices.platform_of(Path(filename)) == label


@pytest.mark.parametrize(
    "filename",
    [
        "orchestrant-gpu-amd-0.0.1-linux-aarch64.tar.gz",
        "orchestrant-gpu-nvidia-0.0.1-windows-arm64.zip",
        "orchestrant-directml-0.0.1-windows-arm64.zip",
        "orchestrant-directml-amd-0.0.1-windows-arm64.zip",
        "orchestrant-directml-nvidia-0.0.1-windows-arm64.zip",
        "orchestrant-directml-intel-0.0.1-windows-x64.zip",
        "orchestrant-directml-amd-nvidia-0.0.1-windows-x64.zip",
        "orchestrant-gpu-unknown-0.0.1-linux-x86_64.tar.gz",
    ],
)
def test_inventory_rejects_unsupported_gpu_names(filename: str) -> None:
    """Unsupported devices and architectures cannot be mislabeled as CPU."""
    with pytest.raises(notices.NoticeError, match=r"unsupported|require"):
        notices.platform_of(Path(filename))


def test_notice_checks_cpu_amd_and_directml_independently(
    tmp_path: Path, native_toml: Path
) -> None:
    """A common notice can describe different distributions and native components per variant."""
    cpu = _members(extra=_dist("onnxruntime-dnnl", "1.31"))
    amd = _members(
        extra={**_dist("onnxruntime-migraphx", "1.30"), **_dist("pip", "26.0")}
    )
    windows = {
        key.replace("runtime/lib/python3.15/", "runtime/Lib/"): value
        for key, value in _members().items()
        if key not in {"runtime/lib/libfixture.so.1", "runtime/RUNTIME-LICENSE"}
    }
    directml = {**windows, "runtime/DirectML.dll": b"MZ fixture"}
    paths = [
        _tar(tmp_path / "orchestrant-0.0.1-linux-x86_64.tar.gz", cpu),
        _tar(tmp_path / "orchestrant-gpu-amd-0.0.1-linux-x86_64.tar.gz", amd),
        _zip(tmp_path / "orchestrant-0.0.1-windows-x64.zip", windows),
        _zip(tmp_path / "orchestrant-directml-0.0.1-windows-x64.zip", directml),
    ]
    components, supplied = notices.load_components(native_toml)
    notice = notices.generate(
        [notices.read_package(path) for path in paths], components, supplied
    )
    assert "linux-x86_64-amd" in notice
    assert "windows-x64-directml" in notice
    assert "Fixture DirectML | MIT | windows-x64-directml" in notice
    for path, members in zip(paths, (cpu, amd, windows, directml), strict=True):
        writer = _zip if path.suffix == ".zip" else _tar
        writer(path, {**members, notices.NOTICE_NAME: notice.encode()})
        assert _problems(path) == []

    # A mislabeled package or a missing DirectML binary must not pass another variant's inventory.
    _tar(paths[1], {**cpu, notices.NOTICE_NAME: notice.encode()})
    assert any("onnxruntime-migraphx" in problem for problem in _problems(paths[1]))
    _zip(paths[3], {**windows, notices.NOTICE_NAME: notice.encode()})
    assert any("Fixture DirectML" in problem for problem in _problems(paths[3]))


def test_duplicate_variant_label_requires_independent_vendor_checks(
    tmp_path: Path, native_toml: Path
) -> None:
    """Two vendor builds of DirectML share a label but cannot overwrite each other at generation."""
    nvidia = tmp_path / "nvidia"
    amd = tmp_path / "amd"
    nvidia.mkdir()
    amd.mkdir()
    filename = "orchestrant-directml-0.0.1-windows-x64.zip"
    first = _zip(nvidia / filename, _members())
    second = _zip(amd / filename, _members(extra=_dist("different", "1.0")))
    components, supplied = notices.load_components(native_toml)
    with pytest.raises(notices.NoticeError, match="duplicate package inventory label"):
        notices.generate(
            [notices.read_package(first), notices.read_package(second)],
            components,
            supplied,
        )
    components, supplied = notices.load_components(native_toml)
    notice = notices.generate([notices.read_package(first)], components, supplied)
    _zip(first, {**_members(), notices.NOTICE_NAME: notice.encode()})
    _zip(
        second,
        {
            **_members(extra=_dist("different", "1.0")),
            notices.NOTICE_NAME: notice.encode(),
        },
    )
    assert _problems(first) == []
    assert any(
        "different 1.0 is not in the notice" in problem for problem in _problems(second)
    )


def test_windows_gpu_vendor_inventories_are_distinct(
    tmp_path: Path, native_toml: Path
) -> None:
    """Both GPU images can contribute different runtime bytes to one exact notice."""
    common = {
        key.replace("runtime/lib/python3.15/", "runtime/Lib/"): value
        for key, value in _members().items()
        if key not in {"runtime/lib/libfixture.so.1", "runtime/RUNTIME-LICENSE"}
    }
    amd = {**common, "runtime/DirectML.dll": b"MZ fixture"}
    nvidia_dist = {
        key.replace("runtime/lib/python3.15/", "runtime/Lib/"): value
        for key, value in _dist("nvidia-extra", "1.0").items()
    }
    nvidia = {**amd, **nvidia_dist}
    paths = [
        _zip(tmp_path / "orchestrant-directml-amd-0.0.1-windows-x64.zip", amd),
        _zip(tmp_path / "orchestrant-directml-nvidia-0.0.1-windows-x64.zip", nvidia),
    ]
    components, supplied = notices.load_components(native_toml)
    notice = notices.generate(
        [notices.read_package(p) for p in paths], components, supplied
    )
    for path, members in zip(paths, (amd, nvidia), strict=True):
        _zip(path, {**members, notices.NOTICE_NAME: notice.encode()})
        assert _problems(path) == []
    _zip(paths[0], {**nvidia, notices.NOTICE_NAME: notice.encode()})
    assert any(
        "nvidia-extra 1.0 is not in the notice" in p for p in _problems(paths[0])
    )
    _zip(paths[1], {**amd, notices.NOTICE_NAME: notice.encode()})
    assert any("notice lists nvidia-extra 1.0" in p for p in _problems(paths[1]))


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"extra": _dist("gamma", "3.0")}, "gamma 3.0 is not in the notice"),
        (
            {"extra": {"runtime/lib/libstray.so": ELF}},
            "no listed native component covers runtime/lib/libstray.so",
        ),
        (
            {"extra": {f"{SITE}/stray/_unowned.so": ELF}},
            f"covers {SITE}/stray/_unowned.so",
        ),
        (
            {"drop": ("licenses/bare-LICENSE",)},
            "bare's text licenses/bare-LICENSE is missing",
        ),
        (
            {"drop": ("runtime/RUNTIME-LICENSE",)},
            "Fixture runtime's text runtime/RUNTIME-LICENSE is missing",
        ),
        (
            {"drop": ("runtime/lib/libfixture.so.1",)},
            "the notice lists Fixture runtime, which the package lacks",
        ),
    ],
)
def test_check_rejects_a_package_its_notice_does_not_describe(
    tmp_path: Path, native_toml: Path, change: dict, expected: str
) -> None:
    """Every component the package gains or loses after the notice was written is a failure."""
    notice = _notice(tmp_path, native_toml, _members())
    members = {**_members(**change), notices.NOTICE_NAME: notice}
    problems = _problems(
        _tar(tmp_path / "orchestrant-0.0.1-linux-x86_64.tar.gz", members)
    )
    assert any(expected in problem for problem in problems), problems


def test_check_rejects_a_dropped_wheel_text(tmp_path: Path, native_toml: Path) -> None:
    """A distribution whose own license file disappears no longer matches the notice."""
    notice = _notice(tmp_path, native_toml, _members())
    members = _members(drop=(f"{SITE}/alpha-1.0.dist-info/licenses/LICENSE",))
    problems = _problems(
        _tar(
            tmp_path / "orchestrant-0.0.1-linux-x86_64.tar.gz",
            {**members, notices.NOTICE_NAME: notice},
        )
    )
    assert any("alpha's text" in problem for problem in problems), problems


def test_check_rejects_a_package_without_a_notice(tmp_path: Path) -> None:
    """The notice must be in the package, not only in the repository."""
    problems = _problems(
        _tar(tmp_path / "orchestrant-0.0.1-linux-x86_64.tar.gz", _members())
    )
    assert problems == [
        f"linux-x86_64: the package ships no {notices.NOTICE_NAME} at its root"
    ]


def test_check_reports_a_malformed_notice(tmp_path: Path) -> None:
    """A notice without its sections is a finding, not a crash."""
    members = {**_members(), notices.NOTICE_NAME: b"not a notice\n"}
    problems = _problems(
        _tar(tmp_path / "orchestrant-0.0.1-linux-x86_64.tar.gz", members)
    )
    assert len(problems) == 1
    assert "no distribution or native-component section" in problems[0]


def test_generate_refuses_a_wheel_without_text_or_supplied_text(
    tmp_path: Path, native_toml: Path
) -> None:
    """A distribution with no license text anywhere stops generation."""
    built = _tar(
        tmp_path / "pre-linux-x86_64.tar.gz",
        _members(extra=_dist("gamma", "3.0", text=False)),
    )
    components, supplied = notices.load_components(native_toml)
    with pytest.raises(notices.NoticeError, match=r"gamma 3\.0 ships no license text"):
        notices.generate([notices.read_package(built)], components, supplied)


def test_generate_refuses_an_uncovered_binary(
    tmp_path: Path, native_toml: Path
) -> None:
    """Generation cannot leave a native binary out."""
    built = _tar(
        tmp_path / "pre-linux-x86_64.tar.gz",
        _members(extra={"runtime/bin/python3.15.bin": ELF}),
    )
    components, supplied = notices.load_components(native_toml)
    with pytest.raises(
        notices.NoticeError,
        match=r"no native component covers runtime/bin/python3\.15\.bin",
    ):
        notices.generate([notices.read_package(built)], components, supplied)


def test_glob_star_stays_inside_one_directory() -> None:
    """``*.exe`` names root launchers, not every executable below them."""
    assert notices.glob_match("yolo-monitor.exe", "*.exe")
    assert not notices.glob_match("runtime/python.exe", "*.exe")


def test_package_names_must_carry_a_platform(tmp_path: Path) -> None:
    """A package whose platform is unknown cannot be inventoried."""
    with pytest.raises(notices.NoticeError, match="not a"):
        notices.read_package(_tar(tmp_path / "orchestrant.tar.gz", _members()))


def test_tracked_notice_ships_with_every_package() -> None:
    """app.json copies the notice and each supplied text into the bundle root."""
    data = json.loads((ROOT / "packaging" / "app.json").read_text(encoding="utf-8"))[
        "data"
    ]
    shipped = {(item["from"], item["to"]) for item in data}
    assert ("packaging/THIRD-PARTY-NOTICES.txt", notices.NOTICE_NAME) in shipped
    for text in (ROOT / "packaging" / "licenses").iterdir():
        assert (f"packaging/licenses/{text.name}", f"licenses/{text.name}") in shipped
    for text in (ROOT / "packaging" / "native-licenses").iterdir():
        assert (
            f"packaging/native-licenses/{text.name}",
            f"licenses/{text.name}",
        ) in shipped


def test_standalone_python_native_licenses_are_inventoried(tmp_path: Path) -> None:
    """The observed fallback's native libraries each carry their original texts."""
    libraries = (
        "runtime/lib/libtcl9.0.so",
        "runtime/lib/libtcl9tk9.0.so",
        "runtime/lib/itcl4.3.5/libtcl9itcl4.3.5.so",
        "runtime/lib/thread3.0.4/libtcl9thread3.0.4.so",
        "runtime/lib/libpcre2-8.so.0",
    )
    components, supplied = notices.load_components(
        ROOT / "packaging" / "third-party-native.toml"
    )
    members = dict.fromkeys(libraries, ELF)
    for path in libraries:
        owners = notices.covering(path, components)
        assert len(owners) == 1
        for license_path in owners[0].text:
            members[license_path] = (
                ROOT / "packaging" / "native-licenses" / Path(license_path).name
            ).read_bytes()
    built = _tar(tmp_path / "pre-linux-x86_64.tar.gz", members)
    notice = notices.generate([notices.read_package(built)], components, supplied)
    for path in members:
        if path.startswith("licenses/"):
            assert path in notice
    shipped = _tar(
        tmp_path / "orchestrant-0.0.1-linux-x86_64.tar.gz",
        {**members, notices.NOTICE_NAME: notice.encode()},
    )
    assert _problems(shipped) == []
    removed = "licenses/tcl-9.0.3-LICENSE.txt"
    del members[removed]
    missing = _tar(
        tmp_path / "missing-linux-x86_64.tar.gz",
        {**members, notices.NOTICE_NAME: notice.encode()},
    )
    assert any(removed in problem for problem in _problems(missing))
    del members["licenses/itcl-4.3.5-DOC-LICENSE.txt"]
    components, supplied = notices.load_components(
        ROOT / "packaging" / "third-party-native.toml"
    )
    with pytest.raises(notices.NoticeError, match=r"itcl-4\.3\.5-DOC-LICENSE\.txt"):
        notices.generate(
            [
                notices.read_package(
                    _tar(tmp_path / "incomplete-linux-x86_64.tar.gz", members)
                )
            ],
            components,
            supplied,
        )


def test_tracked_notice_parses_and_names_supplied_texts() -> None:
    """The tracked notice is well formed and points at every text this repo supplies."""
    text = (ROOT / "packaging" / "THIRD-PARTY-NOTICES.txt").read_text(encoding="utf-8")
    dists, components = notices.parse(text)
    assert dists
    assert components
    referenced = {
        path for dist in dists for paths in dist.texts.values() for path in paths
    }
    for supplied in (ROOT / "packaging" / "licenses").iterdir():
        assert f"licenses/{supplied.name}" in referenced


def _icon_zip(tmp_path: Path, launchers: dict[str, bytes]) -> Path:
    path = tmp_path / "orchestrant-0.0.1-windows-x64.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in launchers.items():
            archive.writestr(f"orchestrant-0.0.1-windows-x64/{name}", data)
        archive.writestr(
            "orchestrant-0.0.1-windows-x64/runtime/python.exe",
            pe_fixture.icon_pe(b"other"),
        )
    return path


def test_launcher_icon_accepts_the_logo() -> None:
    """The PE reader finds the logo the hub embeds verbatim."""
    logo = (ROOT / "images" / "logo.png").read_bytes()
    assert icon.icon_images(pe_fixture.icon_pe(logo)) == [logo]


def test_launcher_icon_check_passes_and_fails(tmp_path: Path) -> None:
    """Every root launcher must embed the logo; deeper executables are not launchers."""
    logo = (ROOT / "images" / "logo.png").read_bytes()
    good = _icon_zip(tmp_path, {"yolo-monitor.exe": pe_fixture.icon_pe(logo)})
    assert icon.check(good, logo) == []
    bad = _icon_zip(
        tmp_path,
        {
            "yolo-monitor.exe": pe_fixture.icon_pe(logo),
            "orchestrant-bench.exe": pe_fixture.icon_pe(b"x"),
        },
    )
    assert [p.split(":")[0] for p in icon.check(bad, logo)] == [
        "orchestrant-0.0.1-windows-x64/orchestrant-bench.exe"
    ]
    assert icon.check(_icon_zip(tmp_path, {}), logo) == [
        f"{good.name}: no launcher EXE at the bundle root"
    ]
    broken = _icon_zip(tmp_path, {"yolo-monitor.exe": b"MZ not a PE"})
    assert "unreadable PE" in icon.check(broken, logo)[0]
