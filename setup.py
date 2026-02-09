"""Packaging setup helpers for optional Cython builds."""

import base64
import hashlib
import logging
import os
import platform
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext


try:
    from wheel.bdist_wheel import bdist_wheel as _bdist_wheel
except Exception:
    _bdist_wheel = None

LOGGER = logging.getLogger(__name__)

# Accept several truthy values for CYTHONIZE (so "True", True, "1", "true" all work)
CYTHONIZE_RAW = os.getenv("CYTHONIZE", "0")
CYTHONIZE = str(CYTHONIZE_RAW).strip().lower() in ("1", "true", "yes", "on")

if CYTHONIZE:
    from Cython.Build import cythonize

# Compiler-Env will be set based on OS
if sys.platform == "win32":
    os.environ["CC"] = "clang-cl"
    os.environ["CXX"] = "clang-cl"
    os.environ["DISTUTILS_USE_SDK"] = "1"
else:
    os.environ.pop("DISTUTILS_USE_SDK", None)
    # I really like clang
    os.environ.setdefault("CC", "clang")
    os.environ.setdefault("CXX", "clang")


def _locate_dist_info(
    zin_infos: list[zipfile.ZipInfo],
) -> tuple[str, str]:
    dist_info_record = next(
        (zi.filename for zi in zin_infos if zi.filename.endswith(".dist-info/RECORD")),
        None,
    )
    if dist_info_record is None:
        dist_info_dir = next(
            (zi.filename for zi in zin_infos if zi.filename.endswith(".dist-info/")),
            None,
        )
        if dist_info_dir is None:
            message = "Could not locate .dist-info directory inside wheel"
            raise RuntimeError(message)
        dist_info_record = dist_info_dir + "RECORD"
    else:
        dist_info_dir = dist_info_record.rsplit("/", 1)[0] + "/"
    return dist_info_dir, dist_info_record


def _should_keep_entry(
    name: str,
    dist_info_record: str,
    dist_info_dir: str,
    exclude_suffixes: tuple[str, ...],
) -> bool:
    if name.endswith("/"):
        return False
    if name == dist_info_record:
        return False
    if name.startswith(dist_info_dir) and name.lower().endswith(
        (".jws", ".asc", ".sig")
    ):
        return False
    return not any(name.endswith(suf) for suf in exclude_suffixes)


class StripWheel(_bdist_wheel if _bdist_wheel is not None else object):
    """Build the wheel then rewrite it to exclude source files (.py, .pyc, .c, etc.).

    and rebuild the .dist-info/RECORD so the wheel remains valid.

    - Use ZipInfo objects and preserve file metadata where possible.
    - Skip directory entries and signature files (RECORD.jws, .asc, .sig, .jws)
    - Correctly compute sha256 and sizes for binary files and write a valid RECORD
    - Avoid writing RECORD into itself when computing hashes
    - Replace the original wheel atomically
    """

    exclude_suffixes = (".py", ".pyc", ".pyo", ".c", ".h", ".pxd", ".pyi")

    def run(self) -> None:
        """Build the wheel and strip source files from the archive."""
        if _bdist_wheel is not None:
            super().run()
        else:
            # fallback: let setuptools create dist/ wheel via other commands
            message = "wheel bdist_wheel not available; install 'wheel' package"
            raise RuntimeError(message)

        dist_dir = Path(getattr(self, "dist_dir", "dist"))
        # find the newly created wheel(s)
        for path in dist_dir.iterdir():
            if path.suffix != ".whl":
                continue
            self._strip_wheel(path)

    def _strip_wheel(self, wheel_path: Path) -> None:
        dirname = wheel_path.parent
        tmpfd, tmpname = tempfile.mkstemp(suffix=".whl", dir=str(dirname))
        os.close(tmpfd)

        try:
            with zipfile.ZipFile(wheel_path, "r") as zin:
                zin_infos = zin.infolist()

                dist_info_dir, dist_info_record = _locate_dist_info(zin_infos)

                kept_infos = []  # list of (ZipInfo, data)

                # Determine which files to keep
                for zi in zin_infos:
                    name = zi.filename
                    if not _should_keep_entry(
                        name,
                        dist_info_record,
                        dist_info_dir,
                        self.exclude_suffixes,
                    ):
                        continue
                    # keep everything else
                    data = zin.read(name)
                    kept_infos.append((zi, data))

            # Write kept files into new wheel and compute RECORD entries
            record_lines = []
            with zipfile.ZipFile(
                tmpname, "w", compression=zipfile.ZIP_DEFLATED
            ) as zout:
                for zi, data in kept_infos:
                    # preserve original ZipInfo metadata where possible
                    new_zi = zipfile.ZipInfo(filename=zi.filename)
                    # copy date_time and external_attr to preserve timestamps and permissions
                    new_zi.date_time = zi.date_time
                    new_zi.external_attr = zi.external_attr
                    new_zi.compress_type = zipfile.ZIP_DEFLATED

                    # write entry
                    zout.writestr(new_zi, data)

                    # compute hash and size for RECORD
                    h = hashlib.sha256(data).digest()
                    b64 = base64.urlsafe_b64encode(h).rstrip(b"=").decode("ascii")
                    size = str(len(data))
                    record_lines.append(f"{zi.filename},sha256={b64},{size}")

                # Add the new RECORD file with entries computed above.
                # RECORD itself has an empty hash and size.
                record_content = "\n".join(
                    [*record_lines, f"{dist_info_dir}RECORD,,"]
                ).encode("utf-8")

                # create ZipInfo for RECORD and set reasonable permissions
                record_zi = zipfile.ZipInfo(filename=dist_info_dir + "RECORD")
                record_zi.date_time = (1980, 1, 1, 0, 0, 0)
                # set rw-r--r-- permissions
                record_zi.external_attr = (0o644 & 0xFFFF) << 16
                zout.writestr(record_zi, record_content)

            # replace original wheel with the stripped one
            shutil.move(tmpname, wheel_path)
            LOGGER.info("Stripped wheel written: %s", wheel_path)
        finally:
            # cleanup tmp file if it still exists
            try:
                tmp_path = Path(tmpname)
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception as exc:
                LOGGER.debug("Failed to clean temp wheel %s: %s", tmpname, exc)


class ClangBuildExt(build_ext):
    """Under Windows, bend the compiler to clang-cl.

    Open source >>> closed source
    """

    def build_extension(self, ext: Extension) -> None:
        """Build extension with clang-cl overrides on Windows."""
        if self.compiler.compiler_type == "msvc":
            original_spawn = self.compiler.spawn

            def clang_spawn(cmd: list[str]) -> object:
                LOGGER.debug("clang_spawn invoked")
                if not cmd:
                    return original_spawn(cmd)

                exe = cmd[0].strip('"')  # remove surrounding quotes if any
                name = Path(exe).name.lower()

                if name in {"cl.exe", "cl"}:
                    cmd[0] = "clang-cl"
                    LOGGER.debug("Using clang-cl compiler: %s", " ".join(cmd))
                elif name in {"link.exe", "link"}:
                    cmd[0] = "lld-link.exe"
                    LOGGER.debug("Using lld-link linker: %s", " ".join(cmd))

                return original_spawn(cmd)

            self.compiler.spawn = clang_spawn

            # Compiler auf clang-cl lassen
            if hasattr(self.compiler, "cc"):
                self.compiler.cc = "clang-cl"

            # WICHTIG: Linker NICHT auf clang-cl setzen!
            if hasattr(self.compiler, "linker_so"):
                self.compiler.linker_so = "link.exe"  # oder "lld-link.exe"
            if hasattr(self.compiler, "linker"):
                self.compiler.linker = "link.exe"  # oder "lld-link.exe"

        super().build_extension(ext)


frontend_deps = ["wxPython", "reflex"]

if sys.platform != "win32":
    frontend_deps.append("dearpygui @ git+https://github.com/hoffstadt/DearPyGui.git@v2.1.1")
else:
    frontend_deps.append("dearpygui==2.1.1")

pytorch_cpu_deps: list[str] = []
if platform.machine() == "riscv64":
    pytorch_cpu_deps.append(
        "torch @ git+https://github.com/pytorch/pytorch.git@v2.10.0"
    )
    pytorch_cpu_deps.append(
        "torchvision @ git+https://github.com/pytorch/vision.git@v0.25.0"
    )
    pytorch_cpu_deps.append(
        "torchaudio @ git+https://github.com/pytorch/audio.git@v2.10.0"
    )

dist_name = "Orchestr-ANT-ion"
package_dir = "orchestr_ant_ion"
version = Path("VERSION.txt").read_text().strip()


def list_py_files(package_dir: str | Path) -> list[str]:
    """Return Python source files under the package directory."""
    root = Path(package_dir)
    return [str(path) for path in root.rglob("*.py")]


py_files = list_py_files(package_dir)

extensions = []
if CYTHONIZE:
    if sys.platform == "win32":
        extra_compile_args = ["/O2", "/MD"]
        extra_link_args = ["/OPT:REF", "/OPT:ICF", "/LTCG:OFF"]
    else:
        extra_compile_args = ["-O3", "-flto", "-fvisibility=hidden"]
        extra_link_args = ["-flto"]

    extensions = [
        Extension(
            py_file.replace(os.path.sep, ".")[:-3],  # + "_compiled",
            [py_file],
            extra_compile_args=extra_compile_args,
            extra_link_args=extra_link_args,
        )
        for py_file in py_files
    ]

setup_kwargs = {"name": dist_name, "version": version, "zip_safe": False}

if CYTHONIZE:
    # merge cmdclasses
    cmds = {"build_ext": ClangBuildExt}
    if _bdist_wheel is not None:
        cmds["bdist_wheel"] = StripWheel
    setup_kwargs.update(
        {
            "ext_modules": cythonize(
                extensions,
                compiler_directives={
                    "language_level": "3",
                    "emit_code_comments": False,
                    "linetrace": False,
                    "embedsignature": False,
                    "binding": False,
                    "profile": False,
                    "annotation_typing": False,
                    "initializedcheck": False,
                    "warn.undeclared": False,
                    "infer_types": False,
                },
            ),
            "cmdclass": cmds,  # {"build_ext": ClangBuildExt},
            "package_data": {"": ["*.c", "*.so", "*.pyd"]},
        }
    )
else:
    setup_kwargs.update({"packages": [package_dir], "include_package_data": True})

setup_kwargs["extras_require"] = {
    "frontend": frontend_deps,
    "pytorch-cpu": pytorch_cpu_deps,
}

setup(**setup_kwargs)
