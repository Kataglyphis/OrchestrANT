"""Keep self-hosted GPU profiles isolated from earlier downloaded artifacts."""

import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ProfileDirectories(unittest.TestCase):
    """Check that profiling cannot consume an older run's artifact."""

    def test_download_and_profile_use_the_current_attempt(self) -> None:
        """The downloaded and consumed paths isolate both runs and attempts."""
        for filename, job_name, reference in (
            ("linux-gpu-inventory.yml", "hardware-profile", '"$CANDIDATE_DIR"'),
            ("windows-directml.yml", "gpu-profile", "$env:CANDIDATE_DIR"),
        ):
            text = (ROOT / ".github/workflows" / filename).read_text(encoding="utf-8")
            job = text.split(f"\n  {job_name}:\n", 1)[1]
            destination = re.search(r"(?m)^\s+path: (.+)$", job).group(1)
            used = re.search(r"(?m)^\s+CANDIDATE_DIR: (.+)$", job).group(1)
            assert destination == used
            assert reference in job
            for key in ("github.run_id", "github.run_attempt", "matrix.vendor"):
                assert "${{ " + key + " }}" in destination
            if filename.startswith("linux"):
                assert "${{ matrix.arch }}" in destination
            with tempfile.TemporaryDirectory() as temp:

                def path(
                    run: str, attempt: str, vendor: str, template: str = destination
                ) -> Path:
                    return Path(
                        template.replace("${{ runner.temp }}", temp)
                        .replace("${{ github.run_id }}", run)
                        .replace("${{ github.run_attempt }}", attempt)
                        .replace("${{ matrix.vendor }}", vendor)
                        .replace("${{ matrix.arch }}", "x64")
                    )

                stale, previous, current = (
                    path("1", "1", "amd"),
                    path("2", "1", "amd"),
                    path("2", "2", "amd"),
                )
                for directory in (stale, previous, current):
                    directory.mkdir()
                (stale / "old.zip").touch()
                (previous / "previous.zip").touch()
                (current / "current.zip").touch()
                assert [p.name for p in current.glob("*.zip")] == ["current.zip"]
                assert current != path("2", "2", "nvidia")


if __name__ == "__main__":
    unittest.main()
