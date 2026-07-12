"""Wheel smoke checks for the installed ML runtime.

These checks exercise the packaged wheels (torch, torchvision, onnxruntime,
opencv, pillow, ai-edge-litert) with real computation so a cross-built or
emulated container image can prove its ML stack actually works, not merely
imports. Run the suite with ``python -m orchestr_ant_ion.smoke``.
"""

from orchestr_ant_ion.smoke.checks import CheckResult, run_all


__all__ = ["CheckResult", "run_all"]
