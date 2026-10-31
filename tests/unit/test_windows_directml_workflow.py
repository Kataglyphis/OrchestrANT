"""Check the Windows DirectML workflow's image routing and inventory labels."""

import re
import unittest
from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github/workflows/windows-directml.yml"
)
EXPECTED = {("nvidia", "winamd64-nvidia"), ("amd", "winamd64-rocm")}


def valid_workflow(text: str) -> bool:
    """Require both x64 images to come from the hub's registry prefix."""
    matrix = re.search(r"(?ms)^      matrix:\n(.*?)(?=^    uses:)", text)
    if not matrix:
        return False
    rows = re.findall(
        r"(?m)^          - vendor: (\S+)\n            tag: (\S+)$",
        matrix.group(1),
    )
    if (
        len(rows) != len(EXPECTED)
        or set(rows) != EXPECTED
        or matrix.group(1).count("- vendor:") != len(EXPECTED)
    ):
        return False
    required = (
        'image="$(bash third_party/ANTfrastructure/linux/scripts/ci-image-ref.sh --windows)"',
        'prefix="${image%:*}"',
        "# PYTHON_ORT_PROVIDER_WHEEL_API=1",
        "# PACKAGE_ORT_PROVIDER_SET_API=1",
        "# PACKAGE_BINARY_WHEEL_API=1",
        "third_party/ANTfrastructure/linux/scripts/verify_package_binary.py",
        "RootDirectoryId",
        "needs: [image-prefix, inventory-export-test]",
        "image: ${{ format('{0}:{1}', needs.image-prefix.outputs.prefix, matrix.tag) }}",
        "orchestrant-windows-x64-directml-${{ matrix.vendor }}-",
        "Build-DirectML-{0}-{1}.ps1', matrix.vendor, inputs.inventory-only && 'inventory' || 'candidate'",
        "overwrite-artifact: true",
        "${{ inputs.inventory-only && 'inventory-only' || 'candidate' }}",
        "${{ inputs.inventory-only && './dist/directml-inventory/inventory/'",
        "  workflow_call:\n    inputs:\n      inventory-only:\n        type: boolean\n        default: false\n      verify-gpu:\n        type: boolean\n        default: false\n    secrets:\n      GHCR_PAT:\n        required: false",
        "group: windows-directml-${{ github.ref }}-${{ github.workflow }}",
        "if: ${{ inputs.verify-gpu && !inputs.inventory-only }}",
    )
    return all(part in text for part in required)


class WorkflowTest(unittest.TestCase):
    """Prove the image matrix and its inventory isolation are checked."""

    def test_supported_candidates_only(self) -> None:
        """The current workflow selects the pinned registry and both x64 images."""
        assert valid_workflow(WORKFLOW.read_text(encoding="utf-8"))

    def test_mutations_do_not_pass(self) -> None:
        """Changed vendor tags, prefix wiring and inventory labels fail."""
        original = WORKFLOW.read_text(encoding="utf-8")
        mutations = (
            original.replace("winamd64-nvidia", "winarm64-nvidia"),
            original.replace("  workflow_call:", "  workflow_disabled:"),
            original.replace(
                "    secrets:\n      GHCR_PAT:", "    secrets:\n      WRONG_PAT:", 1
            ),
            original.replace(
                "${{ github.ref }}-${{ github.workflow }}", "${{ github.ref }}", 1
            ),
            original.replace(
                "inputs.verify-gpu && !inputs.inventory-only", "inputs.verify-gpu"
            ),
            original.replace("winamd64-rocm", "winamd64-nvidia"),
            original.replace(
                "matrix.vendor, inputs.inventory-only", "'amd', inputs.inventory-only"
            ),
            original.replace('prefix="${image%:*}"', 'prefix="other"'),
            original.replace("# PYTHON_ORT_PROVIDER_WHEEL_API=1", "# missing-api"),
            original.replace("# PACKAGE_ORT_PROVIDER_SET_API=1", "# missing-api"),
            original.replace("# PACKAGE_BINARY_WHEEL_API=1", "# missing-api"),
            original.replace(
                "third_party/ANTfrastructure/linux/scripts/verify_package_binary.py",
                "other-verifier",
            ),
            original.replace("RootDirectoryId", "missing-root"),
            original.replace(
                "needs: [image-prefix, inventory-export-test]", "needs: []"
            ),
            original.replace("needs.image-prefix.outputs.prefix", "'other'"),
            original.replace("overwrite-artifact: true", "overwrite-artifact: false"),
            original.replace(
                "'inventory-only' || 'candidate'", "'candidate' || 'candidate'"
            ),
        )
        for mutant in mutations:
            with self.subTest(mutant=mutant):
                assert not valid_workflow(mutant)

    def test_each_matrix_entry_calls_the_matching_vendor(self) -> None:
        """The selected image and inventory label are carried to the package builder."""
        root = WORKFLOW.parents[2]
        for vendor in ("amd", "nvidia"):
            for mode in ("inventory", "candidate"):
                script = root / f"scripts/windows/Build-DirectML-{vendor}-{mode}.ps1"
                expected = f"-Vendor {vendor}"
                content = script.read_text(encoding="utf-8")
                assert expected in content
                assert ("-InventoryOnly" in content) == (mode == "inventory")
        builder = (root / "scripts/windows/Build-DirectML.ps1").read_text(
            encoding="utf-8"
        )
        assert "[ValidateSet('amd', 'nvidia')]" in builder
        assert "orchestrant-directml-$Vendor" in builder


if __name__ == "__main__":
    unittest.main()
