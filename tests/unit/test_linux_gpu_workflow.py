"""Check scoped Linux GPU candidates and their requested registry matrix."""

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/linux-gpu-inventory.yml"
SELECTOR = ROOT / "scripts/linux/select-gpu-inventory.py"
ARTIFACT = (
    "unverified-gpu-${{ matrix.vendor }}-packages-linux-${{ matrix.arch }}"
    "-attempt-${{ github.run_attempt }}${{ needs.preflight.outputs.suffix }}"
)


def run_selector(selection: str) -> subprocess.CompletedProcess[str]:
    """Run the fixed Python selector with explicit test arguments."""
    return subprocess.run(  # noqa: S603 -- fixed executable and arguments, no shell
        [sys.executable, "-I", str(SELECTOR), "linux", selection],
        capture_output=True,
        text=True,
        check=False,
    )


def valid_workflow(text: str) -> bool:
    """Check the producer, clean-host and hardware paths share a validated scope."""
    required = (
        "  workflow_dispatch:\n    inputs:",
        "  workflow_call:\n    inputs:",
        "    secrets:\n      GHCR_PAT:\n        required: false",
        "options: [all, nvidia-x64, nvidia-arm64, amd-x64]",
        "GPU_TUPLE: ${{ inputs.gpu-tuple }}",
        "GPU_INVOCATION_SCOPE: ${{ inputs.invocation-scope }}",
        "scripts/linux/select-gpu-inventory.py linux",
        "# GPU_INVOCATION_SCOPE_API=1",
        "# PYTHON_GPU_INVOCATION_SCOPE_API=1",
        "gpu-invocation-scope: ${{ needs.preflight.outputs.scope }}",
        "gpu-attempt-artifacts: true",
        "group: orchestrant-linux-inventory-${{ github.ref }}-${{ github.workflow }}-${{ matrix.vendor }}-${{ matrix.arch }}${{ needs.preflight.outputs.suffix }}",
        "group: orchestrant-linux-profile-${{ github.ref }}-${{ github.workflow }}-${{ matrix.vendor }}-${{ matrix.arch }}${{ needs.preflight.outputs.suffix }}",
        "host-gpu-runtime: true",
        "uses: Kataglyphis/ANTfrastructure/.github/workflows/python-ci-linux.yml@develop",
        "arches: ${{ matrix.arch }}",
        "gpu-artifact-label: ${{ matrix.vendor }}",
        "test-python-versions: pinned\n",
        "free-threaded-wheel: 'off'",
        "deploy-docs: false",
        "app-python-runtime: ${{ matrix.vendor == 'amd' && 'standalone' || 'image' }}",
        "container-image: ${{ format('{0}:{1}', needs.preflight.outputs.image-repository, matrix.image-tag) }}",
        "needs: [preflight, inventory]",
        "needs.inventory.result == 'success' || needs.inventory.result == 'failure'",
        "needs: [preflight, inventory, clean-host]",
        "if: ${{ inputs.verify-gpu }}",
        "deb-command: yolo-monitor --self-test --device cpu",
        "uses: Kataglyphis/ANTfrastructure/.github/actions/clean-host-package-check@develop",
        "subdir: packages",
        "deb: '*.deb'",
        "tarball: '*.tar.gz'",
        "appimage: '*.AppImage'",
        "scan-elves: 'false'",
        "tarball-command: ./bin/yolo-monitor --self-test --device cpu",
        "appimage-command: '\"$APPIMAGE\" --self-test --device cpu'",
        "bash scripts/linux/profile-gpu-candidate.sh",
        "PYTHON_APP_GPU_FINISHED_BINARY_API=1",
        "PYTHON_APP_HOST_GPU_RUNTIME_API=1",
        "PYTHON_APP_PYTHON_RUNTIME_API=1",
        "PYTHON_APP_OPTIONAL_TENSORRT_HOST_RUNTIME_API=1",
        "PYTHON_APP_GPU_PACKAGE_API=1",
        "ORT_PYTHON_GPU_HOST_IMPORTS_API=1",
        "ELF_NEEDED_API=1",
    )
    return (
        text.count("default: all") == 2
        and text.count("default: default") == 2
        and text.count("matrix: ${{ fromJSON(needs.preflight.outputs.matrix) }}") == 3
        and text.count(ARTIFACT) == 2
        and text.count("${{ matrix.arch }}${{ needs.preflight.outputs.suffix }}") >= 2
        and "\nconcurrency:\n" not in text
        and all(value in text for value in required)
    )


class WorkflowTest(unittest.TestCase):
    """Check the matrix contract without running a GPU build."""

    def test_supported_registry_rows_and_invalid_selections(self) -> None:
        """The selector emits only the supported registry image tuples."""
        expected = {
            "nvidia-x64": (
                "nvidia",
                "x64",
                "latest-nvidia",
                "ubuntu-26.04",
                "gpu-nvidia-linux-x64",
            ),
            "nvidia-arm64": (
                "nvidia",
                "arm64",
                "latest-nvidia",
                "ubuntu-26.04-arm",
                "gpu-nvidia-linux-arm64",
            ),
            "amd-x64": (
                "amd",
                "x64",
                "latest-rocm",
                "ubuntu-26.04",
                "gpu-amd-linux-x64",
            ),
        }
        all_rows = []
        for selection, identity in expected.items():
            result = run_selector(selection)
            assert result.returncode == 0, result.stderr
            rows = json.loads(result.stdout)["include"]
            assert len(rows) == 1
            row = rows[0]
            assert (
                tuple(
                    row[key]
                    for key in ("vendor", "arch", "image-tag", "runner", "gpu-runner")
                )
                == identity
            )
            all_rows.append(row)
        result = run_selector("all")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["include"] == all_rows
        for invalid in ("", "amd-arm64", "nvidia-x86", "all\nname=bad"):
            result = run_selector(invalid)
            assert result.returncode != 0, invalid
            assert result.stdout == ""

    def test_workflow_and_mutations(self) -> None:
        """Reject a workflow that drops scope, selected rows, or CPU fallback."""
        original = WORKFLOW.read_text(encoding="utf-8")
        assert valid_workflow(original)
        for old, new in (
            ("  workflow_call:", "  workflow_disabled:"),
            (
                "gpu-invocation-scope: ${{ needs.preflight.outputs.scope }}",
                "gpu-invocation-scope: default",
            ),
            ("# GPU_INVOCATION_SCOPE_API=1", "# OLD_SCOPE_API"),
            ("# PYTHON_GPU_INVOCATION_SCOPE_API=1", "# OLD_UPLOAD_API"),
            (
                "needs.inventory.result == 'success' || needs.inventory.result == 'failure'",
                "needs.inventory.result == 'success'",
            ),
            (ARTIFACT, ARTIFACT.replace("${{ needs.preflight.outputs.suffix }}", "")),
            ("--self-test --device cpu", "--self-test --device auto"),
            ("PYTHON_APP_GPU_PACKAGE_API=1", "OLD_API"),
            ("PYTHON_APP_PYTHON_RUNTIME_API=1", "OLD_API"),
            ("PYTHON_APP_OPTIONAL_TENSORRT_HOST_RUNTIME_API=1", "OLD_API"),
            ("ORT_PYTHON_GPU_HOST_IMPORTS_API=1", "OLD_API"),
            ("ELF_NEEDED_API=1", "OLD_API"),
            ("gpu-artifact-label: ${{ matrix.vendor }}", "gpu-artifact-label: cpu"),
            ("deploy-docs: false", "deploy-docs: true"),
            ("free-threaded-wheel: 'off'", "free-threaded-wheel: auto"),
            ("subdir: packages", "subdir: other"),
            ("appimage: '*.AppImage'", "appimage: ''"),
        ):
            with self.subTest(old=old):
                assert old != new
                assert original != original.replace(old, new, 1)
                assert not valid_workflow(original.replace(old, new, 1))


if __name__ == "__main__":
    unittest.main()
