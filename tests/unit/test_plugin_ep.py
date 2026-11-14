"""The plugin execution provider: where its module is looked for, how it is registered, and what a failure says."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


# providers and monitor import ONNX Runtime and OpenCV, which the free-threaded 3.14t leg does not install.
pytest.importorskip("cv2", reason="OpenCV has no free-threaded wheel")
pytest.importorskip("onnxruntime", reason="the chain ONNX Runtime is GIL-only")

import onnxruntime as ort

from orchestrant.yolo import monitor, plugin_ep, providers, self_test
from orchestrant.yolo.cli import parse_args
from orchestrant.yolo.core import detector as detector_module


MIGRAPHX = plugin_ep.PLUGINS["migraphx"]
MODULE = "libmigraphx-ep.so"


class Refused(Exception):  # noqa: N818 -- stands in for onnxruntime's Fail, which is no RuntimeError either
    """What the fake runtime raises where ONNX Runtime raises its Fail."""


class Options:
    """Session options that record each device selection."""

    def __init__(self) -> None:
        """Start with no device selected."""
        self.appended: list[tuple[list[Any], dict[str, str]]] = []
        self.intra_op_num_threads = 0
        self.enable_profiling = False
        self.profile_file_prefix = ""

    def add_provider_for_devices(
        self, devices: list[Any], options: dict[str, str]
    ) -> None:
        """Record one selection: the devices and the options passed with them."""
        self.appended.append((list(devices), dict(options)))


class Runtime:
    """An ONNX Runtime that keeps registrations as the real one does: once per name, for the process."""

    def __init__(self, home: Path, *, offers: int = 1, refuses: str = "") -> None:
        """Live under ``home``; each registered name offers ``offers`` devices, unless registering is refused."""
        self.__file__ = str(home / "onnxruntime" / "__init__.py")
        self.__version__ = "fake"
        self.offers = offers
        self.refuses = refuses
        self.registered: dict[str, str] = {}
        self.calls: list[str] = []
        self.sessions: list[dict[str, Any]] = []
        self.active = True
        self.SessionOptions = Options

    def register_execution_provider_library(self, name: str, path: str) -> None:
        """Register once per name; a second registration fails, as in ONNX Runtime."""
        self.calls.append(f"register {name} {path}")
        if self.refuses:
            raise Refused(self.refuses)
        if name in self.registered:
            message = f"EP library is already registered under {name}"
            raise Refused(message)
        self.registered[name] = path

    def get_ep_devices(self) -> list[SimpleNamespace]:
        """List the CPU provider's device, then the devices of every registered name."""
        devices = [SimpleNamespace(ep_name="CPUExecutionProvider", index=0)]
        for name in self.registered:
            devices += [
                SimpleNamespace(ep_name=name, index=index)
                for index in range(self.offers)
            ]
        return devices

    def get_available_providers(self) -> list[str]:
        """Name no plugin: the real list names none before its registration, and nothing may rely on it after."""
        return ["CPUExecutionProvider"]

    def InferenceSession(self, model: str, **kwargs: Any) -> SimpleNamespace:  # noqa: N802, ANN401 -- ONNX Runtime's own name and options
        """Record the call and report the appended devices' providers, then the CPU."""
        self.calls.append("session")
        self.sessions.append({"model": model, **kwargs})
        options = kwargs.get("sess_options")
        chosen = [
            device.ep_name
            for devices, _ in getattr(options, "appended", [])
            for device in devices
        ]
        named = [
            p[0] if isinstance(p, tuple) else p for p in kwargs.get("providers", [])
        ]
        active = [*chosen, *named] if self.active else []
        listed = [*dict.fromkeys([*active, "CPUExecutionProvider"])]
        return SimpleNamespace(
            get_providers=lambda: listed,
            disable_fallback=lambda: self.calls.append("no fallback"),
        )


def _module(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / MODULE
    path.write_bytes(b"stand-in")
    return path


@pytest.fixture(autouse=True)
def _a_new_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test sees the record of a process that has registered nothing, and no root variable."""
    monkeypatch.setattr(plugin_ep, "_registered", set())
    monkeypatch.setattr(plugin_ep, "_failures", {})
    monkeypatch.delenv(MIGRAPHX.root_variable, raising=False)
    monkeypatch.setattr(providers, "require_answer", lambda _gpu: None)


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Runtime:
    """A fake runtime in place of the one providers.py loaded, with the module beside it."""
    fake = Runtime(tmp_path)
    _module(tmp_path / "onnxruntime" / "capi")
    monkeypatch.setattr(providers, "ort", fake)
    return fake


def test_the_table_holds_the_name_the_root_variable_and_the_module_file() -> None:
    """The hub's contract fixes all three; a name ending in .virtual would switch on virtual devices."""
    assert plugin_ep.Plugin("MIGraphX", "ORT_AMDGPU_EP_ROOT", MODULE) == MIGRAPHX
    assert plugin_ep.by_name("MIGraphX") is MIGRAPHX
    assert plugin_ep.by_name("MIGraphXExecutionProvider") is None
    assert plugin_ep.by_name("CUDAExecutionProvider") is None
    assert not any(row.name.endswith(".virtual") for row in plugin_ep.PLUGINS.values())


def test_the_root_is_searched_first_then_the_place_beside_onnxruntime(
    tmp_path: Path,
) -> None:
    """The image names its module through the variable; a package carries it beside the runtime."""
    fake = Runtime(tmp_path)
    beside = tmp_path / "onnxruntime" / "capi" / MODULE
    root = tmp_path / "root"
    environ = {MIGRAPHX.root_variable: str(root)}
    assert plugin_ep.candidates(MIGRAPHX, fake, environ) == [
        root / "lib" / MODULE,
        beside,
    ]
    assert plugin_ep.find_module(MIGRAPHX, fake, environ) is None
    _module(beside.parent)
    assert plugin_ep.find_module(MIGRAPHX, fake, environ) == beside
    _module(root / "lib")
    assert plugin_ep.find_module(MIGRAPHX, fake, environ) == root / "lib" / MODULE
    assert plugin_ep.find_module(MIGRAPHX, fake, {}) == beside


@pytest.mark.parametrize("environ", [{}, {"ORT_AMDGPU_EP_ROOT": ""}])
def test_an_empty_or_unset_root_is_no_place_to_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environ: dict[str, str]
) -> None:
    """Every image but the rocm one sets the variable to nothing: lib/ under the working directory is not it."""
    monkeypatch.chdir(tmp_path)
    _module(tmp_path / "lib")
    fake = Runtime(tmp_path / "site")
    beside = tmp_path / "site" / "onnxruntime" / "capi" / MODULE
    assert plugin_ep.candidates(MIGRAPHX, fake, environ) == [beside]
    assert plugin_ep.find_module(MIGRAPHX, fake, environ) is None


def test_a_root_whose_directory_is_empty_falls_back_to_the_packaged_module(
    tmp_path: Path,
) -> None:
    """A set variable is not a module: the file decides."""
    fake = Runtime(tmp_path)
    beside = _module(tmp_path / "onnxruntime" / "capi")
    (tmp_path / "root" / "lib").mkdir(parents=True)
    environ = {MIGRAPHX.root_variable: str(tmp_path / "root")}
    assert plugin_ep.require_module(MIGRAPHX, fake, environ) == beside


def test_a_root_that_may_not_be_read_holds_no_module_and_auto_goes_on(
    tmp_path: Path, runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before Python 3.14 is_file raises there: the packaged module is taken, and without one auto runs on the CPU."""
    locked = tmp_path / "locked"
    is_file = Path.is_file

    def refused(path: Path, **options: bool) -> bool:
        if locked in path.parents:
            raise PermissionError(13, "Permission denied", str(path))
        return is_file(path, **options)

    monkeypatch.setattr(Path, "is_file", refused)
    monkeypatch.setenv(MIGRAPHX.root_variable, str(locked))
    beside = tmp_path / "onnxruntime" / "capi" / MODULE
    assert plugin_ep.find_module(MIGRAPHX, runtime) == beside
    beside.unlink()
    session = providers.create_session("model.onnx", 0, "auto")
    assert session.get_providers() == ["CPUExecutionProvider"]
    with pytest.raises(
        plugin_ep.PluginUnavailableError, match="MIGraphX was not found"
    ) as error:
        providers.create_session("model.onnx", 0, "migraphx")
    assert f"{locked / 'lib' / MODULE} or {beside} " in str(error.value)


@pytest.mark.parametrize(
    ("environ", "state"),
    [
        ({}, "ORT_AMDGPU_EP_ROOT is not set"),
        ({"ORT_AMDGPU_EP_ROOT": ""}, "ORT_AMDGPU_EP_ROOT is ''"),
        ({"ORT_AMDGPU_EP_ROOT": "/image/root"}, "ORT_AMDGPU_EP_ROOT is '/image/root'"),
    ],
)
def test_a_missing_module_names_every_place_and_the_variable(
    tmp_path: Path, environ: dict[str, str], state: str
) -> None:
    """The error says what was looked for and where, never only that something is unavailable."""
    fake = Runtime(tmp_path)
    with pytest.raises(plugin_ep.PluginUnavailableError) as error:
        plugin_ep.require_module(MIGRAPHX, fake, environ)
    text = str(error.value)
    assert text.startswith(f"MIGraphX was not found: no {MODULE} at ")
    assert str(tmp_path / "onnxruntime" / "capi" / MODULE) in text
    assert state in text
    if environ.get("ORT_AMDGPU_EP_ROOT"):
        assert f"{Path('/image/root') / 'lib' / MODULE} or " in text


def test_only_the_device_id_is_passed_on() -> None:
    """AMD's module fails on a key it does not list; the old memory limit is such a key."""
    assert plugin_ep.device_options(2) == {"device_id": "2"}


def test_a_session_gets_exactly_one_device_of_the_plugin_and_its_index(
    tmp_path: Path,
) -> None:
    """The second card: the second device of the name, with device_id 1 and no other option."""
    fake = Runtime(tmp_path, offers=2)
    module = _module(tmp_path / "onnxruntime" / "capi")
    fake.registered["Other"] = "elsewhere"
    options = Options()
    plugin_ep.append(MIGRAPHX, fake, options, 1, {})
    assert fake.calls == [f"register MIGraphX {module}"]
    ((devices, passed),) = options.appended
    assert [(device.ep_name, device.index) for device in devices] == [("MIGraphX", 1)]
    assert passed == {"device_id": "1"}


def test_a_second_session_does_not_register_the_name_again(tmp_path: Path) -> None:
    """ONNX Runtime refuses a second registration of a name, so the first is remembered."""
    fake = Runtime(tmp_path)
    _module(tmp_path / "onnxruntime" / "capi")
    first, second = Options(), Options()
    plugin_ep.append(MIGRAPHX, fake, first, 0, {})
    plugin_ep.append(MIGRAPHX, fake, second, 0, {})
    assert len(fake.calls) == 1
    assert len(first.appended) == len(second.appended) == 1
    assert plugin_ep.report() == {
        "registered_plugin_eps": ["MIGraphX"],
        "plugin_ep_errors": {},
    }


def test_a_module_that_offers_no_device_is_still_registered_only_once(
    tmp_path: Path,
) -> None:
    """Without a device nothing shows that the name is taken: only the record stops a second registration."""
    fake = Runtime(tmp_path, offers=0)
    _module(tmp_path / "onnxruntime" / "capi")
    for _ in range(2):
        with pytest.raises(
            plugin_ep.PluginUnavailableError, match=r"offers 0 device\(s\)"
        ):
            plugin_ep.append(MIGRAPHX, fake, Options(), 0, {})
    assert len(fake.calls) == 1
    assert plugin_ep.report()["registered_plugin_eps"] == ["MIGraphX"]


def test_a_name_another_library_registered_is_used_and_not_registered_again(
    tmp_path: Path,
) -> None:
    """The runtime is one per process: a device of the name may be there before this module looks."""
    fake = Runtime(tmp_path)
    _module(tmp_path / "onnxruntime" / "capi")
    fake.registered["MIGraphX"] = "registered by another library"
    options = Options()
    plugin_ep.append(MIGRAPHX, fake, options, 0, {})
    assert fake.calls == []
    assert options.appended[0][0][0].ep_name == "MIGraphX"


def test_a_failed_registration_names_the_module_and_the_reason_and_is_tried_again(
    tmp_path: Path,
) -> None:
    """ONNX Runtime rolls a failed registration back, so nothing is remembered but the reason."""
    fake = Runtime(tmp_path, refuses="HIP error 100")
    module = _module(tmp_path / "onnxruntime" / "capi")
    options = Options()
    with pytest.raises(plugin_ep.PluginUnavailableError) as error:
        plugin_ep.append(MIGRAPHX, fake, options, 0, {})
    text = f"MIGraphX failed to register {module}: HIP error 100"
    assert str(error.value) == text
    assert isinstance(error.value.__cause__, Refused)
    assert options.appended == []
    assert plugin_ep.report() == {
        "registered_plugin_eps": [],
        "plugin_ep_errors": {"MIGraphX": text},
    }
    fake.refuses = ""
    plugin_ep.append(MIGRAPHX, fake, options, 0, {})
    assert plugin_ep.report() == {
        "registered_plugin_eps": ["MIGraphX"],
        "plugin_ep_errors": {},
    }


@pytest.mark.parametrize(("offers", "gpu"), [(0, 0), (1, 1), (2, -1)])
def test_a_device_the_plugin_does_not_offer_is_an_error_with_the_count(
    tmp_path: Path, offers: int, gpu: int
) -> None:
    """A module can register and offer nothing; an empty or wrong selection never reaches a session."""
    fake = Runtime(tmp_path, offers=offers)
    _module(tmp_path / "onnxruntime" / "capi")
    options = Options()
    with pytest.raises(
        plugin_ep.PluginUnavailableError, match=rf"offers {offers} device\(s\)"
    ) as error:
        plugin_ep.append(MIGRAPHX, fake, options, gpu, {})
    assert f"no device {gpu}" in str(error.value)
    assert options.appended == []
    assert plugin_ep.report()["plugin_ep_errors"] == {"MIGraphX": str(error.value)}


def test_another_plugin_is_one_more_row(
    tmp_path: Path, runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name, a root variable and a module file are all a second plugin needs to be selected and loaded."""
    row = plugin_ep.Plugin("OtherPlugin", "OTHER_EP_ROOT", "libother-ep.so")
    monkeypatch.setitem(plugin_ep.PLUGINS, "other", row)
    (tmp_path / "other" / "lib").mkdir(parents=True)
    module = tmp_path / "other" / "lib" / "libother-ep.so"
    module.write_bytes(b"stand-in")
    monkeypatch.setenv("OTHER_EP_ROOT", str(tmp_path / "other"))
    assert [p[0] for p in providers.select_providers(0)[:-1]] == [
        "MIGraphX",
        "OtherPlugin",
    ]
    session = providers.create_session("model.onnx", 0, "other")
    assert session.get_providers() == ["OtherPlugin", "CPUExecutionProvider"]
    assert runtime.calls == [
        f"register OtherPlugin {module}",
        "session",
        "no fallback",
    ]


def test_the_device_names_map_migraphx_to_the_registration_name(
    runtime: Runtime,
) -> None:
    """Devices, sessions and profiles say MIGraphX; the user-facing device stays migraphx."""
    assert providers.select_providers(2, "migraphx") == [("MIGraphX", {"device_id": 2})]
    assert providers.select_providers(2) == [
        ("MIGraphX", {"device_id": 2}),
        "CPUExecutionProvider",
    ]
    assert runtime.calls == []


def test_a_named_migraphx_without_a_module_fails_before_the_probe_and_the_session(
    tmp_path: Path, runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No module is an error that names the places; no child is forked and nothing runs on the CPU."""
    probed: list[int] = []
    monkeypatch.setattr(providers, "require_answer", probed.append)
    beside = tmp_path / "onnxruntime" / "capi" / MODULE
    beside.unlink()
    with pytest.raises(RuntimeError, match="MIGraphX was not found") as error:
        providers.create_session("model.onnx", 0, "migraphx")
    assert str(beside) in str(error.value)
    assert probed == []
    assert runtime.calls == []


def test_auto_does_not_try_a_plugin_whose_module_is_absent(
    tmp_path: Path, runtime: Runtime
) -> None:
    """A CPU package has no module: auto goes to the CPU without a registration or a warning."""
    (tmp_path / "onnxruntime" / "capi" / MODULE).unlink()
    session = providers.create_session("model.onnx", 0, "auto")
    assert session.get_providers() == ["CPUExecutionProvider"]
    assert runtime.calls == ["session"]
    assert runtime.sessions[0]["providers"] == ["CPUExecutionProvider"]
    assert plugin_ep.report() == {"registered_plugin_eps": [], "plugin_ep_errors": {}}


def test_the_probe_runs_before_the_registration_and_the_session_takes_no_provider_list(
    tmp_path: Path, runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AMD's registration calls HIP; a providers= argument would replace the appended device."""
    monkeypatch.setattr(
        providers, "require_answer", lambda gpu: runtime.calls.append(f"probe {gpu}")
    )
    module = tmp_path / "onnxruntime" / "capi" / MODULE
    runtime.offers = 2
    session = providers.create_session("model.onnx", 1, "migraphx", intra_op_threads=3)
    assert runtime.calls[0] == "probe 1"
    assert runtime.calls[1:] == [
        f"register MIGraphX {module}",
        "session",
        "no fallback",
    ]
    assert session.get_providers() == ["MIGraphX", "CPUExecutionProvider"]
    (created,) = runtime.sessions
    assert set(created) == {"model", "sess_options", "enable_fallback"}
    assert created["enable_fallback"] is False
    assert created["sess_options"].intra_op_num_threads == 3
    ((devices, passed),) = created["sess_options"].appended
    assert len(devices) == 1
    assert passed == {"device_id": "1"}


def test_each_plugin_session_gets_new_options(runtime: Runtime) -> None:
    """A provider appended twice to one options object fails the session, as do another provider's ep.* entries."""
    providers.create_session("model.onnx", 0, "migraphx", intra_op_threads=0)
    providers.create_session("model.onnx", 0, "migraphx", profile_prefix="trace")
    first, second = (created["sess_options"] for created in runtime.sessions)
    assert first is not second
    assert len(first.appended) == len(second.appended) == 1
    assert second.enable_profiling is True
    assert second.profile_file_prefix == "trace"


def test_a_named_migraphx_that_cannot_register_fails_with_the_reason(
    tmp_path: Path, runtime: Runtime
) -> None:
    """A machine without a card: the request fails and names the module and what ONNX Runtime said."""
    runtime.refuses = "hipGetDeviceCount failed: 100"
    module = tmp_path / "onnxruntime" / "capi" / MODULE
    with pytest.raises(RuntimeError) as error:
        providers.create_session("model.onnx", 0, "migraphx")
    assert str(error.value) == (
        f"MIGraphX failed to register {module}: hipGetDeviceCount failed: 100"
    )
    assert "session" not in runtime.calls


def test_a_named_migraphx_the_session_does_not_activate_is_an_error(
    runtime: Runtime,
) -> None:
    """A session that lists only the CPU is not what was asked for."""
    runtime.active = False
    with pytest.raises(RuntimeError, match="did not activate MIGraphX"):
        providers.create_session("model.onnx", 0, "migraphx")


@pytest.mark.parametrize("offers", [0, 1])
def test_auto_goes_on_to_the_cpu_when_the_plugin_cannot_be_had(
    runtime: Runtime, offers: int
) -> None:
    """A failed registration and a module that offers no device both mean: not available here."""
    runtime.offers = offers
    runtime.refuses = "" if offers == 0 else "no AMD device"
    session = providers.create_session("model.onnx", 0, "auto")
    assert session.get_providers() == ["CPUExecutionProvider"]
    assert runtime.calls[-1] == "session"
    assert runtime.sessions[-1]["providers"] == ["CPUExecutionProvider"]
    assert list(plugin_ep.report()["plugin_ep_errors"]) == ["MIGraphX"]


def test_auto_takes_the_plugin_when_it_registers(runtime: Runtime) -> None:
    """The module on disk, a registration and a device: auto stops at MIGraphX."""
    session = providers.create_session("model.onnx", 0, "auto")
    assert session.get_providers() == ["MIGraphX", "CPUExecutionProvider"]
    assert "providers" not in runtime.sessions[0]


def test_the_self_test_reports_what_was_registered_and_why_not(
    runtime: Runtime,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The hub's bundle gate reads registered_plugin_eps; a failed registration is reported, not hidden."""
    model_input = SimpleNamespace(name="x", shape=[1, 3, 32, 32])

    def detector(_args: object, model: str) -> SimpleNamespace:
        session = providers.create_session(model, 0, "auto")
        session.get_inputs = lambda: [model_input]
        session.get_modelmeta = lambda: SimpleNamespace(custom_metadata_map={})
        session.run = lambda _outputs, _feeds: []
        return session

    monkeypatch.setattr(self_test, "session_for", detector)
    monkeypatch.setattr(detector_module, "postprocess", lambda *_a, **_k: ([], None))
    args = parse_args(["--self-test", "--model", __file__])
    runtime.refuses = "HIP error 100"
    assert monitor.run_self_test(args) == 0
    failed = json.loads(capsys.readouterr().out)
    assert failed["ok"] is True
    assert failed["providers"] == ["CPUExecutionProvider"]
    assert failed["registered_plugin_eps"] == []
    assert "HIP error 100" in failed["plugin_ep_errors"]["MIGraphX"]
    assert Path(failed["onnxruntime_module"]).name == "onnxruntime"
    runtime.refuses = ""
    assert monitor.run_self_test(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["registered_plugin_eps"] == ["MIGraphX"]
    assert report["plugin_ep_errors"] == {}
    assert report["providers"] == ["MIGraphX", "CPUExecutionProvider"]
    assert report["available_providers"] == ["CPUExecutionProvider"]


def test_a_named_request_the_self_test_cannot_load_is_a_failed_report(
    runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    """--device migraphx on a machine without a card: exit 1 and the reason in the JSON."""
    runtime.refuses = "HIP error 100"
    args = parse_args(["--self-test", "--model", __file__, "--device", "migraphx"])
    assert monitor.run_self_test(args) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["device"] == "migraphx"
    assert "MIGraphX failed to register" in report["error"]
    assert "HIP error 100" in report["error"]


CHILD = """
import json, os, sys
import numpy as np
import onnxruntime as ort
from orchestrant.yolo import cuda_probe, plugin_ep, providers

def attempt(device):
    try:
        session = providers.create_session(cuda_probe.ONE_KERNEL, 0, device, intra_op_threads=1)
    except Exception as error:
        return {"error": str(error), "type": type(error).__name__}
    value = session.run(None, {"x": np.array([-1.0], dtype=np.float32)})[0]
    entry = session.get_session_options().get_session_config_entry
    return {"providers": session.get_providers(), "value": float(np.asarray(value).item()),
            "device_id": entry("ep.cpuexecutionprovider.device_id") if os.environ.get("STAND_IN") else None}

# The copy to a GPU has its own tests; a card of this machine that hangs must not decide these.
providers.require_answer = lambda _gpu: None
if os.environ.get("STAND_IN"):
    plugin_ep.PLUGINS["migraphx"] = plugin_ep.Plugin(os.environ["STAND_IN"], "ORT_AMDGPU_EP_ROOT", "libmigraphx-ep.so")
# The place beside the library this process loaded, found without the code under test.
beside = os.path.join(os.path.realpath(os.path.dirname(ort.capi.onnxruntime_pybind11_state.__file__)), "libmigraphx-ep.so")
results = {device: attempt(device) for device in sys.argv[1:]}
print(json.dumps({"results": results, "beside": beside, "available": ort.get_available_providers(), **plugin_ep.report()}))
"""
PLUGIN_API = all(
    hasattr(ort, name)
    for name in ("register_execution_provider_library", "get_ep_devices")
) and hasattr(ort.SessionOptions, "add_provider_for_devices")
needs_plugin_api = pytest.mark.skipif(
    not PLUGIN_API or sys.platform != "linux",
    reason="AMD's module is a Linux module, and the runtime must have the plugin provider calls",
)


def _child(devices: list[str], root: str | None, stand_in: str = "") -> dict[str, Any]:
    """Run the real ONNX Runtime in a process of its own: a registration lasts for the process."""
    environ = {
        key: value
        for key, value in os.environ.items()
        if key not in {"ORT_AMDGPU_EP_ROOT", "STAND_IN"}
    }
    environ["PYTHONPATH"] = os.pathsep.join(path for path in sys.path if path)
    if root is not None:
        environ["ORT_AMDGPU_EP_ROOT"] = root
    if stand_in:
        environ["STAND_IN"] = stand_in
    done = subprocess.run(  # noqa: S603 -- this interpreter, argv built here
        [sys.executable, "-c", CHILD, *devices],
        capture_output=True,
        text=True,
        env=environ,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.splitlines()[-1])


@needs_plugin_api
@pytest.mark.parametrize("root", [None, ""])
def test_the_real_runtime_without_a_module_fails_migraphx_and_runs_auto_on_the_cpu(
    root: str | None,
) -> None:
    """No module: the named request names the place beside the loaded runtime, and auto computes on the CPU."""
    answer = _child(["migraphx", "auto"], root)
    named, auto = answer["results"]["migraphx"], answer["results"]["auto"]
    assert named["type"] == "PluginUnavailableError"
    assert f"no {MODULE} at {answer['beside']} " in named["error"]
    assert auto == {
        "providers": ["CPUExecutionProvider"],
        "value": 0.0,
        "device_id": None,
    }
    assert answer["registered_plugin_eps"] == []
    assert answer["plugin_ep_errors"] == {}
    assert "MIGraphX" not in answer["available"]


@needs_plugin_api
def test_the_real_runtime_reports_a_module_it_cannot_load_and_auto_goes_on(
    tmp_path: Path,
) -> None:
    """A module that is no library: ONNX Runtime's own reason reaches the error, and nothing is remembered."""
    module = _module(tmp_path / "lib")
    answer = _child(["migraphx", "auto", "migraphx"], str(tmp_path))
    named, auto = answer["results"]["migraphx"], answer["results"]["auto"]
    assert named["type"] == "PluginUnavailableError"
    assert named["error"].startswith(f"MIGraphX failed to register {module}: ")
    assert "Failed to load library" in named["error"]
    assert auto["providers"] == ["CPUExecutionProvider"]
    assert auto["value"] == 0.0
    assert answer["registered_plugin_eps"] == []
    assert answer["plugin_ep_errors"] == {"MIGraphX": named["error"]}


@needs_plugin_api
def test_the_real_runtime_takes_one_appended_device_with_its_device_id(
    tmp_path: Path,
) -> None:
    """The CPU provider's own device stands in for a plugin's: the append, the session and the run are real."""
    _module(tmp_path / "lib")
    answer = _child(["migraphx", "auto"], str(tmp_path), "CPUExecutionProvider")
    expected = {"providers": ["CPUExecutionProvider"], "value": 0.0, "device_id": "0"}
    assert answer["results"] == {"migraphx": expected, "auto": expected}
    assert answer["registered_plugin_eps"] == ["CPUExecutionProvider"]
    assert answer["plugin_ep_errors"] == {}


QNN_MODULE = (
    Path(os.environ.get("ORT_QNN_EP_ROOT") or "/nonexistent")
    / "lib"
    / "libonnxruntime_providers_qnn.so"
)


@needs_plugin_api
@pytest.mark.skipif(
    not QNN_MODULE.is_file(),
    reason="needs a real plugin module: the arm64 image's Qualcomm module is the only one without a card",
)
def test_a_real_plugin_module_registers_once_under_migraphx_and_offers_no_device_here(
    tmp_path: Path,
) -> None:
    """Qualcomm's module stands in for AMD's: it registers under the name and has no device without its hardware."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / MODULE).symlink_to(QNN_MODULE)
    answer = _child(["migraphx", "auto", "migraphx"], str(tmp_path))
    named, auto = answer["results"]["migraphx"], answer["results"]["auto"]
    assert named["type"] == "PluginUnavailableError"
    assert "MIGraphX offers 0 device(s)" in named["error"]
    assert auto["providers"] == ["CPUExecutionProvider"]
    assert answer["registered_plugin_eps"] == ["MIGraphX"]
    assert answer["plugin_ep_errors"] == {"MIGraphX": named["error"]}
    assert "MIGraphX" not in answer["available"]
