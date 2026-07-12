"""Command-line entry point: ``python -m orchestr_ant_ion.smoke``.

Runs the wheel smoke checks (see :mod:`orchestr_ant_ion.smoke.checks`) and prints
a report. Exits non-zero when any non-skipped check fails, so it can gate a
container build, a CI job, or a post-install verification step.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from typing import TYPE_CHECKING

from orchestr_ant_ion.smoke.checks import run_all


if TYPE_CHECKING:
    from orchestr_ant_ion.smoke.checks import CheckResult


def _status(result: CheckResult) -> str:
    """Return the four-letter status token for a result."""
    if result.ok:
        return "PASS"
    return "WARN" if result.optional else "FAIL"


def _format_line(result: CheckResult) -> str:
    """Render one result as an aligned report line."""
    return f"  [{_status(result):<4}] {result.name:<16} {result.detail}"


def main(argv: list[str] | None = None) -> int:
    """Run the wheel smoke suite and return a process exit code.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv``).

    Returns:
        ``0`` when every non-skipped check passed, otherwise ``1``.
    """
    parser = argparse.ArgumentParser(
        prog="python -m orchestr_ant_ion.smoke",
        description="Exercise the installed ML wheels (torch, torchvision, "
        "onnxruntime, opencv, pillow, litert) with real work.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of the text report",
    )
    args = parser.parse_args(argv)

    results = run_all()
    failures = [r for r in results if not r.ok and not r.optional]
    warnings = [r for r in results if not r.ok and r.optional]

    if args.json:
        payload = {
            "ok": not failures,
            "results": [dataclasses.asdict(r) for r in results],
        }
        print(json.dumps(payload, indent=2))
    else:
        print("=== Orchestr-ANT-ion wheel smoke ===")
        for result in results:
            print(_format_line(result))
        passed = sum(1 for r in results if r.ok)
        print(
            f"=== {passed}/{len(results)} ok, "
            f"{len(failures)} failure(s), {len(warnings)} warning(s) ==="
        )

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
