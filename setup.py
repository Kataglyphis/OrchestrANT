"""Packaging setup helpers for optional Cython builds."""

import logging
import os
import runpy
import shutil
import sys
from pathlib import Path

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext
from setuptools.command.build_py import build_py


try:
    from wheel.bdist_wheel import bdist_wheel as _bdist_wheel
except Exception:
    _bdist_wheel = None

rewrite_wheel = runpy.run_path(
    str(Path(__file__).parent / "build_support/kataglyphis_wheel.py")
)["rewrite_wheel"]

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


class StripWheel(_bdist_wheel if _bdist_wheel is not None else object):
    """Build the wheel, then drop its source files and rewrite RECORD so it stays valid."""

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
        """Delegate the archive rewrite to the shared, sdist-bundled helper."""
        rewrite_wheel(wheel_path, self.exclude_suffixes)


class ClangBuildExt(build_ext):
    """Under Windows, bend the compiler to clang-cl."""

    def build_extension(self, ext: Extension) -> None:
        """Build extension with clang-cl overrides on Windows."""
        if self.compiler.compiler_type == "msvc":
            # The first compile() would initialize() and reset cc to cl.exe, so the first extension built with MSVC cl.
            if not getattr(self.compiler, "initialized", True):
                self.compiler.initialize(self.plat_name)

            # Keep compiler set to clang-cl
            if hasattr(self.compiler, "cc"):
                self.compiler.cc = "clang-cl"

            # IMPORTANT: Do NOT set the linker to clang-cl!
            if hasattr(self.compiler, "linker_so"):
                self.compiler.linker_so = "link.exe"  # or "lld-link.exe"
            if hasattr(self.compiler, "linker"):
                self.compiler.linker = "link.exe"  # or "lld-link.exe"

        super().build_extension(ext)


class CleanBuildPy(build_py):
    """Remove stale build outputs before copying package files."""

    def run(self) -> None:
        """Ensure build/lib does not leak files from earlier builds."""
        build_lib = Path(self.build_lib)
        if build_lib.exists():
            shutil.rmtree(build_lib)
        super().run()


dist_name = "OrchestrANT"
package_dir = "orchestrant"
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
        # In VS's x64_arm64 environment (the arm64 cross lane), clang-cl compiles for the host unless told the target.
        if os.environ.get("VSCMD_ARG_TGT_ARCH", "").lower() == "arm64":
            extra_compile_args.append("--target=aarch64-pc-windows-msvc")
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
    cmds = {"build_ext": ClangBuildExt, "build_py": CleanBuildPy}
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
                    # Else each module re-enables the GIL on 3.14t; the 3.14t legs test this code free-threaded.
                    "freethreading_compatible": True,
                },
            ),
            "cmdclass": cmds,  # {"build_ext": ClangBuildExt},
            "package_data": {"": ["*.c", "*.so", "*.pyd"]},
        }
    )
else:
    setup_kwargs.update({"cmdclass": {"build_py": CleanBuildPy}})

setup(**setup_kwargs)
