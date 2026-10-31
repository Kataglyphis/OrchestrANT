"""Exercise the packaged detector diagnostic without requiring GPU libraries."""

# ruff: noqa: PT009, PT027 -- unittest runs without pytest in build images

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/diagnostics/detector_parity.py"
SPEC = importlib.util.spec_from_file_location("detector_parity_diagnostic", SCRIPT)
assert SPEC
assert SPEC.loader
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)


class DetectorParityDiagnosticTest(unittest.TestCase):
    """The same session must perform both production pipeline calls."""

    def test_gpu_index_is_non_negative(self) -> None:
        """Explicit device selection must reject invalid provider indices."""
        self.assertEqual(diagnostic.gpu_index("0"), 0)
        self.assertEqual(diagnostic.gpu_index("1"), 1)
        for value in ("-1", "invalid", "1.5"):
            with (
                self.subTest(value=value),
                self.assertRaises(argparse.ArgumentTypeError),
            ):
                diagnostic.gpu_index(value)

    def test_two_runs_are_copied_and_postprocessed_independently(self) -> None:
        """Repeat inference, passing both outputs to the production postprocessor."""
        session = Mock()
        session.get_inputs.return_value = [
            SimpleNamespace(name="images", shape=[1, 3, 640, 640])
        ]
        session.run.side_effect = [["first"], ["second"]]
        pre = SimpleNamespace(
            infer_input_size=Mock(return_value=(640, 640)),
            wants_stretch=Mock(return_value=True),
            preprocess=Mock(return_value=("blob", (1.0, 1.0), 0, 0)),
        )
        post = SimpleNamespace(
            DecodeConfig=Mock(return_value="configuration"),
            postprocess=Mock(
                side_effect=[
                    ([{"class_id": 15, "score": 0.9, "bbox": [1, 2, 3, 4]}], None),
                    ([{"class_id": 15, "score": 0.8, "bbox": [5, 6, 7, 8]}], None),
                ]
            ),
        )
        numpy = SimpleNamespace(
            array=Mock(side_effect=lambda value, copy: (value, copy))
        )
        actual = diagnostic.capture(
            session, SimpleNamespace(shape=(100, 200, 3)), numpy, pre, post
        )
        self.assertEqual(session.run.call_count, 2)
        self.assertEqual(post.postprocess.call_args_list[0].args[0], [("first", True)])
        self.assertEqual(post.postprocess.call_args_list[1].args[0], [("second", True)])
        self.assertEqual(
            actual[0],
            {
                "schema_version": 1,
                "width": 200,
                "height": 100,
                "detections": [
                    {"class": 15, "score": 0.9, "box": [1.0, 2.0, 3.0, 4.0]}
                ],
            },
        )
        self.assertEqual(actual[1]["detections"][0]["score"], 0.8)
        self.assertEqual(post.DecodeConfig.call_args.kwargs["conf_threshold"], 0.25)
        json.dumps(actual, allow_nan=False)

    def test_invalid_or_classification_results_fail(self) -> None:
        """Never emit detector results for classification or a failed repeat."""
        session = Mock()
        session.get_inputs.return_value = [
            SimpleNamespace(name="x", shape=[1, 3, 4, 4])
        ]
        session.run.return_value = ["out"]
        pre = SimpleNamespace(
            infer_input_size=Mock(return_value=(4, 4)),
            wants_stretch=Mock(return_value=False),
            preprocess=Mock(return_value=("blob", 1, 0, 0)),
        )
        post = SimpleNamespace(
            DecodeConfig=Mock(), postprocess=Mock(return_value=([], {"class": 1}))
        )
        with self.assertRaises(ValueError):
            diagnostic.capture(
                session,
                SimpleNamespace(shape=(5, 5, 3)),
                SimpleNamespace(array=Mock()),
                pre,
                post,
            )
        post.postprocess.side_effect = [
            ([{"class_id": 15, "score": 0.9, "bbox": [1, 1, 2, 2]}], None),
            ([], None),
        ]
        with self.assertRaises(ValueError):
            diagnostic.capture(
                session,
                SimpleNamespace(shape=(5, 5, 3)),
                SimpleNamespace(array=Mock()),
                pre,
                post,
            )

    def test_changed_input_is_rejected(self) -> None:
        """A wrong fixture or model must fail before inference."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "image.jpg"
            path.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                diagnostic.check_hash(path, diagnostic.FIXTURE_SHA256)
            with self.assertRaises(ValueError):
                diagnostic.check_hash(path / "missing", "0" * 64)

    def test_gpu_and_loader_overrides_are_rejected(self) -> None:
        """Profiling may not silently use inherited provider or loader tuning."""
        for name in (
            "ORT_DYLIB_PATH",
            "MIGRAPHX_SKIP_BENCHMARKING",
            "LD_PRELOAD",
            "PYTHONPATH",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                diagnostic.check_environment({name: "poison"})
        diagnostic.check_environment(
            {"ORCHESTRANT_DATA_DIR": "/bundle/share/orchestrant"}
        )


if __name__ == "__main__":
    unittest.main()
