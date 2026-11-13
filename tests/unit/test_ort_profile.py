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
from typing import TYPE_CHECKING
from unittest.mock import patch

# Loaded here, not first inside patch.dict(sys.modules): that would unload it, and a C extension loads once per process.
import numpy as np  # noqa: F401

from orchestrant.smoke import ort_profile
from orchestrant.yolo import plugin_ep


if TYPE_CHECKING:
    from types import ModuleType


MODULE = "libmigraphx-ep.so"
MIGRAPHX = "MIGraphX"


class FakeOptions(SimpleNamespace):
    """Session options that record the device a plugin provider was appended with."""

    def add_provider_for_devices(self, devices: list, options: dict) -> None:
        """Record the appended devices as provider entries."""
        self.appended = [(device.ep_name, dict(options)) for device in devices]


class FakeOrt:
    """Exercise real profile parsing without requiring a GPU or an ORT installation."""

    def __init__(
        self,
        event_provider: str,
        *,
        active: bool = True,
        operator: str = "Conv",
        home: Path = Path("no-runtime"),
    ) -> None:
        """Record whether an injected provider is active and where its nodes ran."""
        self.event_provider = event_provider
        self.operator = operator
        self.active = active
        self.available = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        self.registered: list[tuple[str, str]] = []
        self.selected: list = []
        self.selections = []
        self.provider_arguments: list = []
        self.did_run = False
        self.disabled_fallback = False
        self.initial_fallback = None
        self.__file__ = str(home / "onnxruntime" / "__init__.py")
        self.__version__ = "fixture"

    def get_available_providers(self) -> list[str]:
        """Return the compile-time provider names, which include no plugin."""
        return self.available

    def register_execution_provider_library(self, name: str, path: str) -> None:
        """Record a plugin registration."""
        self.registered.append((name, path))

    def get_ep_devices(self) -> list[SimpleNamespace]:
        """Offer three devices for each registered plugin."""
        return [
            SimpleNamespace(ep_name=name, index=index)
            for name, _ in self.registered
            for index in range(3)
        ]

    def SessionOptions(self) -> FakeOptions:  # noqa: N802
        """Provide the options object consumed by the profiler."""
        return FakeOptions(enable_profiling=False, profile_file_prefix="", appended=[])

    def InferenceSession(  # noqa: N802
        self,
        model: str,
        *,
        sess_options: FakeOptions,
        providers: list | None = None,
        enable_fallback: bool = True,
    ) -> FakeSession:
        """Record the providers named or appended and create the session fixture."""
        self.initial_fallback = enable_fallback
        self.provider_arguments.append(providers)
        self.selected = sess_options.appended if providers is None else providers
        self.selections.append(self.selected)
        return FakeSession(self, model, sess_options)


class FakeSession:
    """A session that emits a controlled ORT profile."""

    def __init__(self, ort: FakeOrt, model: str, options: FakeOptions) -> None:
        """Keep the session's profile destination and simulated runtime."""
        self.ort = ort
        self.model = model
        self.options = options
        self.selected = ort.selected

    def disable_fallback(self) -> None:
        """Record that runtime fallback was disabled."""
        self.ort.disabled_fallback = True

    def get_providers(self) -> list[str]:
        """Return the selected providers and the CPU, or the CPU alone."""
        names = [p[0] if isinstance(p, tuple) else p for p in self.selected]
        active = names if self.ort.active else []
        return [*dict.fromkeys([*active, "CPUExecutionProvider"])]

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
        """Create the NumPy fixture, a runtime directory with the plugin module, and an empty registration record."""
        self.numpy = SimpleNamespace(
            float32="float32", zeros=lambda shape, dtype: (shape, dtype)
        )
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name).resolve() / "runtime"
        self.module = self.home / "onnxruntime" / "capi" / MODULE
        self.module.parent.mkdir(parents=True)
        self.module.write_bytes(b"stand-in")
        for patcher in (
            patch.dict(os.environ, {"ORT_AMDGPU_EP_ROOT": ""}),
            patch.object(plugin_ep, "_registered", set()),
            patch.object(plugin_ep, "_failures", {}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake(self, event_provider: str, **options: object) -> FakeOrt:
        """A fake ONNX Runtime that lives in the runtime directory."""
        return FakeOrt(event_provider, home=self.home, **options)

    def load_providers(self, fake: FakeOrt, name: str) -> ModuleType:
        """Load the real production session selector over a fake ORT."""
        path = Path(__file__).resolve().parents[2] / "orchestrant/yolo/providers.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec
        assert spec.loader
        providers = importlib.util.module_from_spec(spec)
        logger = SimpleNamespace(warning=lambda *_args: None)
        with patch.dict(
            sys.modules, {"onnxruntime": fake, "loguru": SimpleNamespace(logger=logger)}
        ):
            spec.loader.exec_module(providers)
        providers.require_kernel = lambda _gpu: None
        providers.require_answer = lambda _gpu: None
        return providers

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
        providers = self.load_providers(fake, "profile_provider_fixture")
        with patch.dict(sys.modules, {"orchestrant.yolo.providers": providers}):
            return ort_profile.profile_model(
                fake, self.numpy, model, device, gpu, session_device=session_device
            )

    def test_explicit_devices_run_and_report_node_placement(self) -> None:
        """Each explicit provider reaches inference and is counted."""
        for device, provider in ort_profile.PROVIDERS.items():
            with self.subTest(device=device):
                fake = self.fake(provider)
                report = self.run_profile(fake, Path("model.onnx"), device, 2)
                self.assertEqual(report["providers"], {provider: 1})
                self.assertTrue(fake.did_run and fake.disabled_fallback)
                self.assertFalse(fake.initial_fallback)
                expected = {
                    "cpu": [provider],
                    "cuda": [
                        (
                            provider,
                            {
                                "device_id": 2,
                                "arena_extend_strategy": "kNextPowerOfTwo",
                            },
                        )
                    ],
                    "migraphx": [(provider, {"device_id": "2"})],
                }
                self.assertEqual(fake.selected, expected[device])

    def test_migraphx_is_the_registered_plugin_and_takes_no_provider_list(self) -> None:
        """The profile names MIGraphX, registers the module beside the runtime and appends one device."""
        self.assertEqual(ort_profile.PROVIDERS["migraphx"], MIGRAPHX)
        fake = self.fake(MIGRAPHX)
        self.run_profile(fake, Path("model.onnx"), "migraphx", 1)
        self.assertEqual(fake.registered, [(MIGRAPHX, str(self.module))])
        self.assertEqual(fake.provider_arguments, [None])
        self.assertNotIn(MIGRAPHX, fake.get_available_providers())

    def test_migraphx_without_its_module_names_the_places_searched(self) -> None:
        """The available list is no place to look for a plugin, so a missing module is the error to report."""
        self.module.unlink()
        for session_device in (None, "auto"):
            fake = self.fake(MIGRAPHX)
            with (
                self.subTest(session_device=session_device),
                self.assertRaisesRegex(RuntimeError, "MIGraphX was not found") as error,
            ):
                self.run_profile(
                    fake, Path("model"), "migraphx", 0, session_device=session_device
                )
            self.assertIn(str(self.module), str(error.exception))
            self.assertEqual(fake.selections, [])

    def test_nodes_under_the_old_provider_name_are_not_migraphx_evidence(self) -> None:
        """The in-tree provider is gone: a trace that names it proves nothing about the plugin."""
        fake = self.fake("MIGraphXExecutionProvider")
        with self.assertRaisesRegex(RuntimeError, "No model operators ran on MIGraphX"):
            self.run_profile(fake, Path("model"), "migraphx", 0)

    def test_cpu_fallback_is_not_gpu_evidence(self) -> None:
        """CPU node events cannot satisfy either GPU request."""
        for device in ("cuda", "migraphx"):
            with (
                self.subTest(device=device),
                self.assertRaisesRegex(RuntimeError, "No model operators"),
            ):
                self.run_profile(
                    self.fake("CPUExecutionProvider"), Path("model"), device, 0
                )

    def bundle_profile(self, fake: FakeOrt, device: str, runtime: Path) -> dict:
        """Profile through the bundle entry point, with ``runtime`` as the bundle's Python runtime."""
        providers = self.load_providers(fake, "profile_default_fixture")
        modules = {
            "numpy": self.numpy,
            "onnxruntime": fake,
            "orchestrant.yolo.providers": providers,
        }
        with (
            patch.dict(sys.modules, modules),
            patch.object(
                ort_profile, "bundle_paths", return_value=(runtime, Path("model"))
            ),
        ):
            return ort_profile.run_bundle_profile(device, 2, verify_default=True)

    def test_bundle_default_profiles_the_production_selector(self) -> None:
        """Require GPU nodes in both explicit and the monitor's default sessions."""
        for device, provider, first, default in (
            ("cuda", "CUDAExecutionProvider", 2, 0),
            ("migraphx", MIGRAPHX, "2", "0"),
        ):
            with self.subTest(device=device):
                fake = self.fake(provider)
                fake.available = [
                    name
                    for name in fake.available
                    if name in {provider, "CPUExecutionProvider"}
                ]
                report = self.bundle_profile(fake, device, self.home)
                self.assertEqual(report["default_device"], "auto")
                self.assertEqual(report["default_device_id"], 0)
                self.assertEqual(report["default_providers"], {provider: 1})
                self.assertEqual(len(fake.selections), 2)
                self.assertEqual(fake.selections[0][0][1]["device_id"], first)
                self.assertEqual(fake.selections[1][0][1]["device_id"], default)
                self.assertEqual(
                    report.get("plugin_module"),
                    str(self.module) if device == "migraphx" else None,
                )

    def test_a_bundle_profile_refuses_a_plugin_module_outside_the_bundle(self) -> None:
        """Inside the image the root variable names the image's module; that would not prove the package."""
        outside = self.home.parent / "image"
        (outside / "lib").mkdir(parents=True)
        (outside / "lib" / MODULE).write_bytes(b"the image's module")
        fake = self.fake(MIGRAPHX)
        with (
            patch.dict(os.environ, {"ORT_AMDGPU_EP_ROOT": str(outside)}),
            self.assertRaisesRegex(RuntimeError, "outside the bundle") as error,
        ):
            self.bundle_profile(fake, "migraphx", self.home)
        self.assertIn(str(outside / "lib" / MODULE), str(error.exception))
        self.assertIn("unset ORT_AMDGPU_EP_ROOT", str(error.exception))
        self.assertEqual((fake.registered, fake.selections), ([], []))

    def test_default_auto_cannot_pass_with_only_cpu_model_nodes(self) -> None:
        """An activated GPU that executes no model nodes is not acceleration."""
        for device, available in (
            ("cuda", ["CUDAExecutionProvider", "CPUExecutionProvider"]),
            ("migraphx", ["CPUExecutionProvider"]),
        ):
            fake = self.fake("CPUExecutionProvider")
            fake.available = available
            with (
                self.subTest(device=device),
                self.assertRaisesRegex(RuntimeError, "No model operators"),
            ):
                self.run_profile(fake, Path("model"), device, 0, session_device="auto")

    def test_default_auto_rejects_a_different_gpu_provider(self) -> None:
        """A CUDA session cannot certify the AMD candidate on a mixed-provider host."""
        fake = self.fake("CUDAExecutionProvider")
        with self.assertRaisesRegex(RuntimeError, "did not activate MIGraphX"):
            self.run_profile(fake, Path("model"), "migraphx", 0, session_device="auto")
        self.assertEqual(fake.selected[0][0], "CUDAExecutionProvider")

    def test_provider_activation_and_index_are_required(self) -> None:
        """Reject missing session providers and invalid indices."""
        with self.assertRaisesRegex(RuntimeError, "did not activate"):
            self.run_profile(
                self.fake("CPUExecutionProvider", active=False),
                Path("model"),
                "cuda",
                0,
            )
        with self.assertRaisesRegex(ValueError, "non-negative"):
            self.run_profile(
                self.fake("CPUExecutionProvider"), Path("model"), "cpu", -1
            )

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
            {"cat": "Node", "args": {"provider": MIGRAPHX}},
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
            ("migraphx", MIGRAPHX),
        ):
            with (
                self.subTest(device=device),
                self.assertRaisesRegex(RuntimeError, "No model operators"),
            ):
                self.run_profile(
                    self.fake(provider, operator="MemcpyFromHost"),
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
            model = data / "models" / ort_profile.default_model_name()
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
