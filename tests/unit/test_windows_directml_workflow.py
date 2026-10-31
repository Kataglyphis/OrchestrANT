"""Check scoped Windows GPU candidates, their images and inventory isolation."""

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/windows-directml.yml"
SELECTOR = ROOT / "scripts/linux/select-gpu-inventory.py"
DOWNLOAD = (
    "name: orchestrant-windows-x64-directml-${{ matrix.vendor }}-candidate"
    "-attempt-${{ github.run_attempt }}${{ needs.image-prefix.outputs.suffix }}"
)


def run_selector(selection: str) -> subprocess.CompletedProcess[str]:
    """Run the fixed Python selector with explicit test arguments."""
    return subprocess.run(  # noqa: S603 -- fixed executable and arguments, no shell
        [sys.executable, "-I", str(SELECTOR), "windows", selection],
        capture_output=True,
        text=True,
        check=False,
    )


def valid_workflow(text: str) -> bool:
    """Require scoped artifacts and vendor-isolated GPU checks."""
    required = (
        "  workflow_dispatch:\n    inputs:",
        "  workflow_call:\n    inputs:",
        "    secrets:\n      GHCR_PAT:\n        required: false",
        "options: [all, nvidia, amd]",
        "GPU_VENDOR: ${{ inputs.gpu-vendor }}",
        "GPU_INVOCATION_SCOPE: ${{ inputs.invocation-scope }}",
        "scripts/linux/select-gpu-inventory.py windows",
        "# GPU_INVOCATION_SCOPE_API=1",
        "# PYTHON_ORT_PROVIDER_WHEEL_API=1",
        "# PACKAGE_ORT_PROVIDER_SET_API=1",
        "# PACKAGE_BINARY_WHEEL_API=1",
        "third_party/ANTfrastructure/linux/scripts/verify_package_binary.py",
        "third_party/ANTfrastructure/windows/scripts/diagnostics/Test-NvidiaOpenCvRuntime.ps1",
        'image="$(bash third_party/ANTfrastructure/linux/scripts/ci-image-ref.sh --windows)"',
        'prefix="${image%:*}"',
        "group: orchestrant-windows-candidate-${{ github.ref }}-${{ github.workflow }}-${{ matrix.vendor }}${{ needs.image-prefix.outputs.suffix }}",
        "group: orchestrant-windows-profile-${{ github.ref }}-${{ github.workflow }}-${{ matrix.vendor }}${{ needs.image-prefix.outputs.suffix }}",
        "RootDirectoryId",
        "needs: [image-prefix, inventory-export-test]",
        "needs: [image-prefix, candidate]",
        "needs: [image-prefix, candidate, licences]",
        "needs.candidate.result == 'success' || needs.candidate.result == 'failure'",
        "image: ${{ format('{0}:{1}', needs.image-prefix.outputs.prefix, matrix.tag) }}",
        "artifact-name: orchestrant-windows-x64-directml-${{ matrix.vendor }}-${{ inputs.inventory-only && 'inventory-only' || 'candidate' }}-attempt-${{ github.run_attempt }}${{ needs.image-prefix.outputs.suffix }}",
        "Build-DirectML-{0}-{1}.ps1', matrix.vendor, inputs.inventory-only && 'inventory' || 'candidate'",
        "overwrite-artifact: false",
        "${{ inputs.inventory-only && './dist/directml-inventory/inventory/'",
        "if: ${{ inputs.verify-gpu && !inputs.inventory-only }}",
        "${{ matrix.vendor }}${{ needs.image-prefix.outputs.suffix }}",
    )
    return (
        text.count("default: all") == 2
        and text.count("default: default") == 2
        and text.count("matrix: ${{ fromJSON(needs.image-prefix.outputs.matrix) }}")
        == 3
        and text.count(DOWNLOAD) == 2
        and "\nconcurrency:\n" not in text
        and all(value in text for value in required)
    )


class WorkflowTest(unittest.TestCase):
    """Check Windows vendor routing without running an application build."""

    def test_registry_rows_and_unsupported_targets(self) -> None:
        """Only the two registry's x64 GPU images may be selected."""
        expected = {
            "nvidia": ("nvidia", "winamd64-nvidia", "directml-gpu-nvidia"),
            "amd": ("amd", "winamd64-rocm", "directml-gpu-amd"),
        }
        rows = []
        for selection, identity in expected.items():
            result = run_selector(selection)
            assert result.returncode == 0, result.stderr
            selected = json.loads(result.stdout)["include"]
            assert len(selected) == 1
            assert (
                tuple(selected[0][key] for key in ("vendor", "tag", "gpu-runner"))
                == identity
            )
            rows.extend(selected)
        result = run_selector("all")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["include"] == rows
        for invalid in ("", "amd-arm64", "nvidia-arm64", "all\nname=bad"):
            result = run_selector(invalid)
            assert result.returncode != 0, invalid
            assert result.stdout == ""

    def test_workflow_and_mutations(self) -> None:
        """Reject a workflow that loses scope or inventory isolation."""
        original = WORKFLOW.read_text(encoding="utf-8")
        assert valid_workflow(original)
        for old, new in (
            ("  workflow_call:", "  workflow_disabled:"),
            ("# GPU_INVOCATION_SCOPE_API=1", "# OLD_SCOPE_API"),
            (
                "needs.candidate.result == 'success' || needs.candidate.result == 'failure'",
                "needs.candidate.result == 'success'",
            ),
            (
                DOWNLOAD,
                DOWNLOAD.replace("${{ needs.image-prefix.outputs.suffix }}", ""),
            ),
            ("overwrite-artifact: false", "overwrite-artifact: true"),
            ("inputs.verify-gpu && !inputs.inventory-only", "inputs.verify-gpu"),
            ("needs: [image-prefix, inventory-export-test]", "needs: []"),
            ("# PYTHON_ORT_PROVIDER_WHEEL_API=1", "# missing-api"),
            ("# PACKAGE_ORT_PROVIDER_SET_API=1", "# missing-api"),
            ("# PACKAGE_BINARY_WHEEL_API=1", "# missing-api"),
            ("RootDirectoryId", "missing-root"),
            ('prefix="${image%:*}"', 'prefix="other"'),
            ("'inventory-only' || 'candidate'", "'candidate' || 'candidate'"),
        ):
            with self.subTest(old=old):
                assert original != original.replace(old, new, 1)
                assert not valid_workflow(original.replace(old, new, 1))

    def test_each_matrix_entry_calls_the_matching_vendor(self) -> None:
        """Each declared wrapper passes its own vendor and inventory mode."""
        for vendor in ("amd", "nvidia"):
            for mode in ("inventory", "candidate"):
                script = ROOT / f"scripts/windows/Build-DirectML-{vendor}-{mode}.ps1"
                content = script.read_text(encoding="utf-8")
                assert f"-Vendor {vendor}" in content
                assert ("-InventoryOnly" in content) == (mode == "inventory")
        builder = (ROOT / "scripts/windows/Build-DirectML.ps1").read_text(
            encoding="utf-8"
        )
        assert "[ValidateSet('amd', 'nvidia')]" in builder
        assert "orchestrant-directml-$Vendor" in builder


if __name__ == "__main__":
    unittest.main()
