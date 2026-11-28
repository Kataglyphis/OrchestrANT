"""The usable CPUs: the hub's cases (ANTfrastructure CON304) through usable_of, and the cgroup walk on fake trees."""

import json
from pathlib import Path

import pytest

from orchestrant import cpus


CONTRACT = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "third_party/ANTfrastructure/shared/contracts/usable-cpus.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=lambda case: case["name"])
def test_every_hub_case_gives_its_usable_count(case: dict) -> None:
    """Each case's affinity and cpu_max, own cgroup first, give the count the hub file expects."""
    assert CONTRACT["schema_version"] == 1, "a new schema needs a new reader"
    assert cpus.usable_of(case["affinity"], case["cpu_max"]) == case["expect"]["usable"]


def test_a_zero_period_sets_no_limit() -> None:
    """The kernel never writes one; read as no limit, as OxidANT's checked division does."""
    assert cpus.usable_of(4, ["100000 0"]) == 4


def _tree(tmp_path: Path, membership: str, limits: dict[str, str]) -> tuple[Path, Path]:
    """A cgroup root holding each relative directory's cpu.max, and a /proc/self/cgroup naming `membership`."""
    root = tmp_path / "cgroup"
    for relative, text in limits.items():
        directory = root / relative
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "cpu.max").write_text(text + "\n", encoding="utf-8")
    proc = tmp_path / "proc-self-cgroup"
    proc.write_text(membership, encoding="utf-8")
    return root, proc


def test_the_walk_reads_from_the_own_cgroup_up_to_the_root(tmp_path: Path) -> None:
    """Own first, the root last, None where a level has no cpu.max."""
    root, proc = _tree(
        tmp_path,
        "12:cpu:/v1\n0::/user.slice/app\n",
        {"user.slice/app": "max 100000", ".": "400000 100000"},
    )
    assert cpus.cgroup_cpu_max(root, proc) == ["max 100000", None, "400000 100000"]


def test_a_cgroup_outside_the_namespace_reads_only_its_root(tmp_path: Path) -> None:
    """A path through .. is not visible from here, so only the namespace's root counts."""
    root, proc = _tree(tmp_path, "0::/../../other\n", {".": "200000 100000"})
    assert cpus.cgroup_cpu_max(root, proc) == ["200000 100000"]


def test_no_cgroup_v2_line_and_no_proc_file_read_nothing(tmp_path: Path) -> None:
    """A v1-only membership or a platform without /proc gives an empty walk, so the mask decides."""
    root, proc = _tree(tmp_path, "4:cpu,cpuacct:/docker/1\n", {".": "100000 100000"})
    assert cpus.cgroup_cpu_max(root, proc) == []
    assert cpus.cgroup_cpu_max(root, tmp_path / "missing") == []


def test_the_process_has_at_least_one_usable_cpu() -> None:
    """Whatever the host, the count a thread rule divides is at least 1."""
    assert cpus.usable_cpus() >= 1
