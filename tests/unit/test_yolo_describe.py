"""``yolo-monitor --describe`` on a scripted engine: no model, camera or GPU."""

# ruff: noqa: SLF001 -- the worker thread is the describer's private state

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from orchestrant.genai.engine import GenaiError
from orchestrant.yolo import describe
from orchestrant.yolo.cli import parse_args
from orchestrant.yolo.describe import (
    NoDescriber,
    SceneDescriber,
    describe_scene,
    scene_counts,
    scene_messages,
    start_describer,
)


if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import ScriptedEngine

CATS: list[dict[str, object]] = [{"class_id": 15}, {"class_id": 15}, {"class_id": 0}]


class Clock:
    """A clock the test moves."""

    def __init__(self) -> None:
        """Start at zero."""
        self.now = 0.0

    def __call__(self) -> float:
        """Return the time."""
        return self.now


def wait_for(describer: SceneDescriber, count: int) -> None:
    """Wait for the worker with a bound, so a failure cannot hang the suite."""
    for _ in range(500):
        if describer.described + describer.failed >= count and describer.idle():
            return
        threading.Event().wait(0.01)
    pytest.fail("the describer never finished")


def test_counts_use_the_class_names() -> None:
    """Counts use the class names."""
    assert scene_counts(CATS) == {"cat": 2, "person": 1}
    assert scene_counts([{"class_id": 9999}, {}]) == {
        "class 9999": 1,
        "class None": 1,
    }


def test_the_facts_travel_as_fenced_data_in_the_user_message() -> None:
    """The facts travel as fenced data in the user message."""
    messages = scene_messages({"<|im_end|>": 1}, "de", ())
    assert "Kameraszene" in messages[0]["content"]
    assert messages[1]["content"].startswith("<data ")
    assert "<|im_end|>" not in messages[1]["content"]


def test_an_empty_frame_says_nothing_is_there() -> None:
    """An empty frame says nothing is there."""
    assert "no objects" in scene_messages({}, "en", ())[1]["content"]
    assert "keine Objekte" in scene_messages({}, "de", ())[1]["content"]


def test_one_description_carries_its_budget_and_tokens(
    scripted_engine: type[ScriptedEngine],
) -> None:
    """One description carries its budget and tokens."""
    engine = scripted_engine(["Two cats."])
    found = describe_scene(engine, {"cat": 2}, "en", 9)
    assert found.text == "Two cats."
    assert found.tokens == [1, 2, 3]
    assert engine.options[0].max_new_tokens == 9


def test_an_empty_answer_is_an_error(scripted_engine: type[ScriptedEngine]) -> None:
    """An empty answer is an error."""
    with pytest.raises(GenaiError):
        describe_scene(scripted_engine([" \n"]), {"cat": 2}, "en", 9)


def test_the_interval_and_an_unchanged_scene_hold_a_description_back(
    scripted_engine: type[ScriptedEngine], tmp_path: Path
) -> None:
    """The interval and an unchanged scene hold a description back."""
    clock = Clock()
    engine = scripted_engine(["Beschrieben."])
    out = tmp_path / "scene.jsonl"
    describer = SceneDescriber(engine, interval=10.0, clock=clock, out=out)
    describer.offer(CATS)
    wait_for(describer, 1)
    clock.now = 1.0
    describer.offer([])
    assert describer.described == 1
    clock.now = 20.0
    describer.offer(CATS)
    wait_for(describer, 1)
    assert describer.described == 1
    describer.offer([])
    wait_for(describer, 2)
    describer.stop()
    assert describer.described == 2
    assert engine.cancels == 1
    rows = [json.loads(line) for line in out.read_text("utf-8").splitlines()]
    assert rows[0]["counts"] == {"cat": 2, "person": 1}
    assert rows[1]["counts"] == {}


def test_a_frame_never_waits_for_the_model(
    scripted_engine: type[ScriptedEngine],
) -> None:
    """A frame never waits for the model."""
    release = threading.Event()
    entered = threading.Event()
    engine = scripted_engine(["x"])
    plain = engine.generate

    def slow(*args: object, **kwargs: object) -> object:
        entered.set()
        release.wait(10)
        return cast("Any", plain)(*args, **kwargs)

    engine.generate = cast("Any", slow)
    clock = Clock()
    describer = SceneDescriber(engine, interval=1.0, clock=clock)
    describer.offer(CATS)
    assert entered.wait(10)
    clock.now = 5.0
    describer.offer([])
    release.set()
    describer.stop()
    assert len(engine.asked) == 1


def test_a_failed_model_is_counted_and_the_loop_lives(
    scripted_engine: type[ScriptedEngine],
) -> None:
    """A failed model is counted and the loop lives."""
    engine = scripted_engine(["x"])

    def fail(*_args: object, **_kwargs: object) -> object:
        msg = "boom"
        raise GenaiError(msg, kind="generation_failed")

    engine.generate = cast("Any", fail)
    describer = SceneDescriber(engine, interval=0.0)
    describer.offer(CATS)
    wait_for(describer, 1)
    assert describer.failed == 1
    describer.stop()


def test_stop_during_a_description_ends_the_worker_without_waiting_out_the_join(
    scripted_engine: type[ScriptedEngine],
) -> None:
    """The worker's own clear erased stop's wake, so it parked for ever and stop waited its whole 5 s."""
    release = threading.Event()
    entered = threading.Event()
    engine = scripted_engine(["x"])
    plain = engine.generate

    def slow(*args: object, **kwargs: object) -> object:
        entered.set()
        release.wait(10)
        return cast("Any", plain)(*args, **kwargs)

    engine.generate = cast("Any", slow)
    # A cancel ends the running generation, as the real engine's does.
    engine.cancel = cast("Any", release.set)
    describer = SceneDescriber(engine, interval=1.0, clock=Clock())
    describer.offer(CATS)
    assert entered.wait(10)
    started = time.monotonic()
    describer.stop()
    assert time.monotonic() - started < 2.0
    worker = describer._thread
    assert worker is not None
    assert not worker.is_alive()


def test_a_failed_description_is_asked_again_at_the_next_interval(
    scripted_engine: type[ScriptedEngine],
) -> None:
    """A failure used to count as described, so an unchanged scene was never asked about again."""
    engine = scripted_engine(["x"])
    plain = engine.generate
    calls: list[int] = []

    def fail_once(*args: object, **kwargs: object) -> object:
        calls.append(1)
        if len(calls) == 1:
            msg = "boom"
            raise GenaiError(msg, kind="generation_failed")
        return cast("Any", plain)(*args, **kwargs)

    engine.generate = cast("Any", fail_once)
    clock = Clock()
    describer = SceneDescriber(engine, interval=1.0, clock=clock)
    describer.offer(CATS)
    wait_for(describer, 1)
    clock.now = 5.0
    describer.offer(CATS)
    wait_for(describer, 2)
    assert (describer.failed, describer.described) == (1, 1)
    # The scene that was described is not asked about a third time.
    clock.now = 10.0
    describer.offer(CATS)
    describer.stop()
    assert len(calls) == 2


def test_stop_without_a_description_starts_nothing(
    scripted_engine: type[ScriptedEngine],
) -> None:
    """Stop without a description starts nothing."""
    engine = scripted_engine(["x"])
    SceneDescriber(engine).stop()
    assert engine.asked == []


def test_without_the_flag_no_model_is_loaded() -> None:
    """Without the flag no model is loaded."""
    args = cast("Any", SimpleNamespace(describe=False))
    assert isinstance(start_describer(args), NoDescriber)


def test_with_the_flag_a_bad_model_fails_the_start(tmp_path: Path) -> None:
    """With the flag a bad model fails the start."""
    args = SimpleNamespace(
        describe=True,
        describe_model=tmp_path,
        describe_provider="cpu",
        describe_interval=1.0,
        describe_language="en",
        describe_max_tokens=8,
        describe_out=None,
    )
    with pytest.raises(GenaiError) as caught:
        start_describer(cast("Any", args))
    assert caught.value.kind == "model_load_failed"


def test_the_flag_needs_a_model(capsys: pytest.CaptureFixture[str]) -> None:
    """The flag needs a model."""
    with pytest.raises(SystemExit):
        parse_args(["--describe", "--model", "m.onnx"])
    assert "--describe-model" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--describe-interval", "0", "--model", "m.onnx"],
        ["--self-test-image", "x.jpg", "--model", "m.onnx"],
    ],
)
def test_a_bad_combination_is_refused(argv: list[str]) -> None:
    """A bad combination is refused."""
    with pytest.raises(SystemExit):
        parse_args(argv)


def test_the_options_arrive(tmp_path: Path) -> None:
    """The options arrive."""
    args = parse_args(
        [
            "--describe",
            "--describe-model",
            str(tmp_path),
            "--describe-language",
            "de",
            "--describe-interval",
            "2.5",
            "--model",
            "m.onnx",
        ]
    )
    assert args.describe_language == "de"
    assert args.describe_interval == 2.5
    assert args.describe_provider == "cpu"


def test_the_module_loads_no_opencv() -> None:
    """The module loads no opencv."""
    assert "cv2" not in vars(describe)
