"""Tests for the wheel smoke checks' contract and failure semantics."""

from __future__ import annotations

import sys

import pytest

from orchestr_ant_ion.smoke import checks


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
        monkeypatch.setitem(sys.modules, "onnxruntime_genai", None)
        result = checks.check_onnxruntime_genai()
        assert not result.ok
        assert result.optional

    def test_missing_tvm_is_optional(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An absent tvm wheel reports an optional (warning) failure."""
        monkeypatch.setitem(sys.modules, "tvm", None)
        result = checks.check_tvm()
        assert not result.ok
        assert result.optional

    def test_missing_litert_is_optional(self) -> None:
        """The litert check never hard-fails on absence (existing contract)."""
        result = checks.check_litert()
        if not result.ok:
            assert result.optional
