"""Check the reusable Linux GPU inventory matrix and its shared CI inputs."""

import re
import unittest
from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github/workflows/linux-gpu-inventory.yml"
)
EXPECTED = {
    ("nvidia", "x64", "latest-nvidia"),
    ("nvidia", "arm64", "latest-nvidia"),
    ("amd", "x64", "latest-rocm"),
}


def _valid_inventory_matrix(text: str) -> bool:
    matrix = re.search(r"(?ms)^      matrix:\n(.*?)(?=^    uses:)", text)
    if not matrix:
        return False
    rows = re.findall(
        r"(?m)^          - vendor: (\S+)\n"
        r"            arch: (\S+)\n"
        r"            image-tag: (\S+)$",
        matrix.group(1),
    )
    return (
        len(rows) == len(EXPECTED)
        and set(rows) == EXPECTED
        and matrix.group(1).count("- vendor:") == len(EXPECTED)
    )


def _valid_clean_host(text: str) -> bool:
    clean = re.search(r"(?ms)^  clean-host:\n(.*?)(?=^  hardware-profile:)", text)
    if not clean:
        return False
    clean_rows = re.findall(
        r"(?m)^          - vendor: (\S+)\n"
        r"            arch: (\S+)\n"
        r"            runner: (\S+)$",
        clean.group(1),
    )
    clean_required = (
        "uses: Kataglyphis/ANTfrastructure/.github/actions/clean-host-package-check@develop",
        "artifact: unverified-gpu-${{ matrix.vendor }}-packages-linux-${{ matrix.arch }}",
        "subdir: packages",
        "deb: '*.deb'",
        "deb-command: yolo-monitor --self-test --device cpu",
        "tarball: '*.tar.gz'",
        "tarball-command: ./bin/yolo-monitor --self-test --device cpu",
        "appimage: '*.AppImage'",
        "appimage-command: '\"$APPIMAGE\" --self-test --device cpu'",
        "scan-elves: 'false'",
    )
    return (
        set(clean_rows)
        == {
            ("nvidia", "x64", "ubuntu-26.04"),
            ("nvidia", "arm64", "ubuntu-26.04-arm"),
            ("amd", "x64", "ubuntu-26.04"),
        }
        and len(clean_rows) == 3
        and all(part in clean.group(1) for part in clean_required)
    )


def _valid_hardware(text: str) -> bool:
    hardware_job = re.search(r"(?ms)^  hardware-profile:\n(.*)\Z", text)
    if not hardware_job:
        return False
    hardware = re.findall(
        r"(?m)^          - vendor: (\S+)\n"
        r"            arch: (\S+)\n"
        r"            runner: (\S+)$",
        hardware_job.group(1),
    )
    return (
        set(hardware)
        == {
            ("nvidia", "x64", "gpu-nvidia-linux-x64"),
            ("nvidia", "arm64", "gpu-nvidia-linux-arm64"),
            ("amd", "x64", "gpu-amd-linux-x64"),
        }
        and len(hardware) == 3
    )


def valid_workflow(text: str) -> bool:
    """Require the supported matrix and the shared GPU packaging inputs."""
    event = re.search(r"(?ms)^on:\n(.*?)(?=^\S)", text)
    if not event or not event.group(1).lstrip().startswith("workflow_dispatch:\n"):
        return False
    required = (
        "uses: Kataglyphis/ANTfrastructure/.github/workflows/python-ci-linux.yml@develop",
        "arches: ${{ matrix.arch }}",
        "container-image: ${{ format('{0}:{1}', needs.preflight.outputs.image-repository, matrix.image-tag) }}",
        "host-gpu-runtime: true",
        "app-python-runtime: ${{ matrix.vendor == 'amd' && 'standalone' || 'image' }}",
        "gpu-artifact-label: ${{ matrix.vendor }}",
        "test-python-versions: pinned\n",
        "free-threaded-wheel: 'off'",
        "deploy-docs: false",
        "needs: preflight",
        "PYTHON_APP_HOST_GPU_RUNTIME_API=1",
        "PYTHON_APP_PYTHON_RUNTIME_API=1",
        "PYTHON_APP_OPTIONAL_TENSORRT_HOST_RUNTIME_API=1",
        "ORT_PYTHON_GPU_HOST_IMPORTS_API=1",
        "ELF_NEEDED_API=1",
        "PYTHON_APP_GPU_PACKAGE_API=1",
        "name: unverified-gpu-${{ matrix.vendor }}-packages-linux-${{ matrix.arch }}",
        "bash scripts/linux/profile-gpu-candidate.sh",
        "if: ${{ inputs.verify-gpu }}",
        "needs: [inventory, clean-host]",
        "  workflow_call:\n    inputs:\n      verify-gpu:\n        type: boolean\n        default: false\n    secrets:\n      GHCR_PAT:\n        required: false",
        "group: linux-gpu-inventory-${{ github.ref }}-${{ github.workflow }}",
    )
    return (
        _valid_inventory_matrix(text)
        and _valid_clean_host(text)
        and _valid_hardware(text)
        and all(part in text for part in required)
    )


class WorkflowTest(unittest.TestCase):
    """Reject unsupported architectures and mislabeled candidate workflows."""

    def test_supported_candidates_only(self) -> None:
        """The actual workflow covers exactly the requested GPU matrix."""
        assert valid_workflow(WORKFLOW.read_text(encoding="utf-8"))

    def test_mutations_do_not_pass(self) -> None:
        """Changed rows, labels, triggers and preflight markers fail."""
        original = WORKFLOW.read_text(encoding="utf-8")
        mutations = (
            original.replace("arch: x64", "arch: arm64", 1),
            original.replace(
                "    uses: Kataglyphis/ANTfrastructure",
                "          - vendor: other\n    uses: Kataglyphis/ANTfrastructure",
            ),
            original.replace("image-tag: latest-rocm", "image-tag: latest-nvidia"),
            original.replace("host-gpu-runtime: true", "host-gpu-runtime: false"),
            original.replace("matrix.vendor == 'amd'", "matrix.vendor == 'nvidia'"),
            original.replace("PYTHON_APP_PYTHON_RUNTIME_API=1", "OLD_RUNTIME_API"),
            original.replace(
                "gpu-artifact-label: ${{ matrix.vendor }}", "gpu-artifact-label: cpu"
            ),
            original.replace("deploy-docs: false", "deploy-docs: true"),
            original.replace(
                "test-python-versions: pinned", "test-python-versions: pinnedt"
            ),
            original.replace("free-threaded-wheel: 'off'", "free-threaded-wheel: auto"),
            original.replace("  workflow_dispatch:", "  push:"),
            original.replace("  workflow_call:", "  workflow_disabled:"),
            original.replace(
                "    secrets:\n      GHCR_PAT:", "    secrets:\n      WRONG_PAT:", 1
            ),
            original.replace(
                "${{ github.ref }}-${{ github.workflow }}", "${{ github.ref }}", 1
            ),
            original.replace("PYTHON_APP_GPU_PACKAGE_API=1", "OLD_API"),
            original.replace(
                "PYTHON_APP_OPTIONAL_TENSORRT_HOST_RUNTIME_API=1", "OLD_TENSORRT_API"
            ),
            original.replace("ORT_PYTHON_GPU_HOST_IMPORTS_API=1", "OLD_IMPORTS_API"),
            original.replace("ELF_NEEDED_API=1", "OLD_ELF_API"),
            original.replace("runner: ubuntu-26.04-arm", "runner: ubuntu-26.04", 1),
            original.replace(
                "--self-test --device cpu", "--self-test --device auto", 1
            ),
            original.replace("subdir: packages", "subdir: other", 1),
            original.replace("appimage: '*.AppImage'", "appimage: ''", 1),
            original.replace("needs: [inventory, clean-host]", "needs: inventory"),
        )
        for mutant in mutations:
            with self.subTest(mutant=mutant):
                assert not valid_workflow(mutant)


if __name__ == "__main__":
    unittest.main()
