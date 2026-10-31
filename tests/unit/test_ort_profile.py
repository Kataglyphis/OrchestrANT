"""Profile only actual model Node placement on the selected bundled provider."""

# ruff: noqa: PT009, PT027 -- unittest keeps this fixture runnable without pytest in build images

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orchestrant.smoke import ort_profile


class FakeOrt:
    """Exercise real profile parsing without requiring a GPU or an ORT installation."""

    def __init__(
        self, event_provider: str, *, active: bool = True, operator: str = "Conv"
    ) -> None:
        """Record whether an injected provider is active and where its nodes ran."""
        self.event_provider = event_provider
        self.operator = operator
        self.active = active
        self.available = list(ort_profile.PROVIDERS.values())
        self.selected = None
        self.selections = []
        self.did_run = False
        self.disabled_fallback = False
        self.initial_fallback = None
        self.__file__ = "fixture-onnxruntime.py"
        self.__version__ = "fixture"

    def get_available_providers(self) -> list[str]:
        """Return the provider names exposed to the session builder."""
        return self.available

    def SessionOptions(self) -> SimpleNamespace:  # noqa: N802
        """Provide the options object consumed by the profiler."""
        return SimpleNamespace(enable_profiling=False, profile_file_prefix="")

    def InferenceSession(  # noqa: N802
        self,
        model: str,
        *,
        sess_options: SimpleNamespace,
        providers: list,
        enable_fallback: bool = True,
    ) -> FakeSession:
        """Record selected providers and create the session fixture."""
        self.initial_fallback = enable_fallback
        self.selected = providers
        self.selections.append(providers)
        return FakeSession(self, model, sess_options)


class FakeSession:
    """A session that emits a controlled ORT profile."""

    def __init__(self, ort: FakeOrt, model: str, options: SimpleNamespace) -> None:
        """Keep the session's profile destination and simulated runtime."""
        self.ort = ort
        self.model = model
        self.options = options

    def disable_fallback(self) -> None:
        """Record that runtime fallback was disabled."""
        self.ort.disabled_fallback = True

    def get_providers(self) -> list[str]:
        """Return either activated providers or CPU alone."""
        return (
            self.ort.get_available_providers()
            if self.ort.active
            else ["CPUExecutionProvider"]
        )

    def get_inputs(self) -> list[SimpleNamespace]:
        """Describe the packaged model's image input."""
        return [
            SimpleNamespace(name="images", type="tensor(float)", shape=[1, 3, 640, 640])
        ]

    def run(self, _: object, inputs: dict) -> None:
        """Check the input tensor reaches inference."""
        assert inputs == {"images": ([1, 3, 640, 640], "float32")}
        self.ort.did_run = True

    def end_profiling(self) -> str:
        """Write the recorded provider as a Node event."""
        path = Path(self.options.profile_file_prefix + ".json")
        path.write_text(
            json.dumps(
                [
                    {"cat": "Session", "args": {"provider": "CUDAExecutionProvider"}},
                    {
                        "cat": "Node",
                        "args": {
                            "provider": self.ort.event_provider,
                            "op_name": self.ort.operator,
                        },
                    },
                ]
            ),
            encoding="utf-8",
        )
        return str(path)


class OrtProfileTest(unittest.TestCase):
    """Validate model profiling without relying on GPU hardware."""

    def setUp(self) -> None:
        """Create the minimal NumPy fixture used by the model."""
        self.numpy = SimpleNamespace(
            float32="float32", zeros=lambda shape, dtype: (shape, dtype)
        )

    def run_profile(
        self,
        fake: FakeOrt,
        model: Path,
        device: str,
        gpu: int,
        *,
        session_device: str | None = None,
    ) -> dict:
        """Exercise the real production session selector with a fake ORT."""
        path = Path(__file__).resolve().parents[2] / "orchestrant/yolo/providers.py"
        spec = importlib.util.spec_from_file_location("profile_provider_fixture", path)
        assert spec
        assert spec.loader
        providers = importlib.util.module_from_spec(spec)
        logger = SimpleNamespace(warning=lambda *_args: None)
        with patch.dict(
            sys.modules, {"onnxruntime": fake, "loguru": SimpleNamespace(logger=logger)}
        ):
            spec.loader.exec_module(providers)
            with patch.dict(sys.modules, {"orchestrant.yolo.providers": providers}):
                return ort_profile.profile_model(
                    fake, self.numpy, model, device, gpu, session_device=session_device
                )

    def test_explicit_devices_run_and_report_node_placement(self) -> None:
        """Each explicit provider reaches inference and is counted."""
        for device, provider in ort_profile.PROVIDERS.items():
            with self.subTest(device=device):
                fake = FakeOrt(provider)
                report = self.run_profile(fake, Path("model.onnx"), device, 2)
                self.assertEqual(report["providers"], {provider: 1})
                self.assertTrue(fake.did_run and fake.disabled_fallback)
                self.assertFalse(fake.initial_fallback)
                selected = fake.selected[0]
                expected_options = {"device_id": 2}
                if device == "cuda":
                    expected_options["arena_extend_strategy"] = "kNextPowerOfTwo"
                self.assertEqual(
                    selected,
                    provider if device == "cpu" else (provider, expected_options),
                )

    def test_cpu_fallback_is_not_gpu_evidence(self) -> None:
        """CPU node events cannot satisfy either GPU request."""
        for device in ("cuda", "migraphx"):
            with (
                self.subTest(device=device),
                self.assertRaisesRegex(RuntimeError, "No model operators"),
            ):
                self.run_profile(
                    FakeOrt("CPUExecutionProvider"), Path("model"), device, 0
                )

    def test_bundle_default_profiles_the_production_selector(self) -> None:
        """Require GPU nodes in both explicit and the monitor's default sessions."""
        path = Path(__file__).resolve().parents[2] / "orchestrant/yolo/providers.py"
        for device, provider in (
            ("cuda", "CUDAExecutionProvider"),
            ("migraphx", "MIGraphXExecutionProvider"),
        ):
            with self.subTest(device=device):
                fake = FakeOrt(provider)
                fake.available = [provider, "CPUExecutionProvider"]
                spec = importlib.util.spec_from_file_location(
                    "profile_default_fixture", path
                )
                assert spec
                assert spec.loader
                providers = importlib.util.module_from_spec(spec)
                modules = {
                    "numpy": self.numpy,
                    "onnxruntime": fake,
                    "loguru": SimpleNamespace(
                        logger=SimpleNamespace(warning=lambda *_: None)
                    ),
                }
                with patch.dict(sys.modules, modules):
                    spec.loader.exec_module(providers)
                    with (
                        patch.dict(
                            sys.modules, {"orchestrant.yolo.providers": providers}
                        ),
                        patch.object(
                            ort_profile,
                            "bundle_paths",
                            return_value=(Path("runtime"), Path("model")),
                        ),
                    ):
                        report = ort_profile.run_bundle_profile(
                            device, 2, verify_default=True
                        )
                self.assertEqual(report["default_device"], "auto")
                self.assertEqual(report["default_device_id"], 0)
                self.assertEqual(report["default_providers"], {provider: 1})
                self.assertEqual(len(fake.selections), 2)
                self.assertEqual(fake.selections[0][0][1]["device_id"], 2)
                self.assertEqual(fake.selections[1][0][1]["device_id"], 0)

    def test_default_auto_cannot_pass_with_only_cpu_model_nodes(self) -> None:
        """An activated GPU that executes no model nodes is not acceleration."""
        for device, provider in (
            ("cuda", "CUDAExecutionProvider"),
            ("migraphx", "MIGraphXExecutionProvider"),
        ):
            fake = FakeOrt("CPUExecutionProvider")
            fake.available = [provider, "CPUExecutionProvider"]
            with (
                self.subTest(device=device),
                self.assertRaisesRegex(RuntimeError, "No model operators"),
            ):
                self.run_profile(fake, Path("model"), device, 0, session_device="auto")

    def test_default_auto_rejects_a_different_gpu_provider(self) -> None:
        """A CUDA trace cannot certify the AMD candidate on a mixed-provider host."""
        fake = FakeOrt("CUDAExecutionProvider")
        with self.assertRaisesRegex(RuntimeError, "No model operators"):
            self.run_profile(fake, Path("model"), "migraphx", 0, session_device="auto")
        self.assertEqual(fake.selected[0][0], "CUDAExecutionProvider")

    def test_provider_activation_and_index_are_required(self) -> None:
        """Reject missing session providers and invalid indices."""
        with self.assertRaisesRegex(RuntimeError, "did not activate"):
            self.run_profile(
                FakeOrt("CPUExecutionProvider", active=False), Path("model"), "cuda", 0
            )
        with self.assertRaisesRegex(ValueError, "non-negative"):
            self.run_profile(FakeOrt("CPUExecutionProvider"), Path("model"), "cpu", -1)

    def test_profile_parser_ignores_non_node_events(self) -> None:
        """Session metadata cannot stand in for model nodes."""
        events = [
            {"cat": "Session", "args": {"provider": "CUDAExecutionProvider"}},
            None,
            {"cat": "Node", "args": {}},
            {
                "cat": "Node",
                "args": {"provider": "CPUExecutionProvider", "op_name": "Conv"},
            },
        ]
        self.assertEqual(
            dict(ort_profile.operator_counts(events)), {"CPUExecutionProvider": 1}
        )
        with self.assertRaises(TypeError):
            ort_profile.operator_counts({})

    def test_transfer_only_gpu_events_do_not_prove_acceleration(self) -> None:
        """ORT-inserted host/device copies are not model computation."""
        events = [
            {
                "cat": "Node",
                "args": {
                    "provider": "CUDAExecutionProvider",
                    "op_name": "MemcpyFromHost",
                },
            },
            {
                "cat": "Node",
                "args": {
                    "provider": "CUDAExecutionProvider",
                    "op_name": "MemcpyToHost",
                },
            },
            {"cat": "Node", "args": {"provider": "MIGraphXExecutionProvider"}},
            {
                "cat": "Node",
                "args": {"provider": "DmlExecutionProvider", "op_name": ""},
            },
            {
                "cat": "Node",
                "args": {"provider": "CPUExecutionProvider", "op_name": "Conv"},
            },
        ]
        self.assertEqual(
            dict(ort_profile.operator_counts(events)), {"CPUExecutionProvider": 1}
        )
        for device, provider in (
            ("cuda", "CUDAExecutionProvider"),
            ("migraphx", "MIGraphXExecutionProvider"),
        ):
            with (
                self.subTest(device=device),
                self.assertRaisesRegex(RuntimeError, "No model operators"),
            ):
                self.run_profile(
                    FakeOrt(provider, operator="MemcpyFromHost"),
                    Path("model"),
                    device,
                    0,
                )

    def test_requires_bundle_runtime_and_model(self) -> None:
        """Reject a host runtime or absent model instead of accepting a checkout."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / "runtime"
            data = root / "share/orchestrant"
            model = data / "models" / ort_profile.MODEL_NAME
            model.parent.mkdir(parents=True)
            model.write_bytes(b"fixture")
            module = runtime / "lib/site-packages/onnxruntime/__init__.py"
            with (
                patch("sys.prefix", str(runtime)),
                patch.dict(os.environ, {"ORCHESTRANT_DATA_DIR": str(data)}),
            ):
                self.assertEqual(
                    ort_profile.bundle_paths(str(module)),
                    (runtime.resolve(), model.resolve()),
                )
                with self.assertRaisesRegex(RuntimeError, "outside the bundle"):
                    ort_profile.bundle_paths(str(root / "system-ort.py"))
                model.unlink()
                with self.assertRaisesRegex(RuntimeError, "model is missing"):
                    ort_profile.bundle_paths(str(module))


if __name__ == "__main__":
    unittest.main()
