"""Tests for the wheel smoke checks' contract and failure semantics."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

from orchestrant.smoke import checks


if TYPE_CHECKING:
    import pytest


def _uninstall(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Make `name` and its submodules fail to import, as an absent wheel does."""

    def find_spec(fullname: str, *_: object) -> None:
        if fullname == name or fullname.startswith(f"{name}."):
            msg = f"No module named {fullname!r}"
            raise ModuleNotFoundError(msg, name=fullname)

    # Not a None in sys.modules: Cython's compiled `import` hands that None back as the module.
    for loaded in [m for m in sys.modules if m == name or m.startswith(f"{name}.")]:
        monkeypatch.delitem(sys.modules, loaded)
    finder = SimpleNamespace(find_spec=find_spec)
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])


class TestCheckContract:
    """Every check returns a CheckResult and never raises."""

    def test_run_all_returns_results_for_every_check(self) -> None:
        """The suite yields one well-formed result per registered check."""
        results = checks.run_all()
        assert len(results) == len(checks.ALL_CHECKS)
        for result in results:
            assert isinstance(result, checks.CheckResult)
            assert result.name
            assert isinstance(result.detail, str)


class TestLaneDependentWheels:
    """Wheels that only some images ship must WARN when absent, not gate-fail."""

    def test_missing_onnxruntime_genai_is_optional(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An absent genai wheel reports an optional (warning) failure."""
        _uninstall(monkeypatch, "onnxruntime_genai")
        result = checks.check_onnxruntime_genai()
        assert not result.ok
        assert result.optional

    def test_missing_tvm_is_optional(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An absent tvm wheel reports an optional (warning) failure."""
        _uninstall(monkeypatch, "tvm")
        result = checks.check_tvm()
        assert not result.ok
        assert result.optional

    def test_missing_pyav_is_optional(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An absent PyAV wheel reports an optional (warning) failure."""
        _uninstall(monkeypatch, "av")
        result = checks.check_pyav()
        assert not result.ok
        assert result.optional

    def test_missing_iree_is_optional(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Absent IREE wheels report an optional (warning) failure."""
        _uninstall(monkeypatch, "iree.compiler.tools")
        result = checks.check_iree()
        assert not result.ok
        assert result.optional

    def test_missing_litert_is_optional(self) -> None:
        """The litert check never hard-fails on absence (existing contract)."""
        result = checks.check_litert()
        if not result.ok:
            assert result.optional
