"""``yolo-monitor --describe``: a sentence about the scene from a language model, beside the detection loop and never in its way."""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger

from orchestrant.genai.engine import GenaiEngine, GenaiError, GenerationOptions
from orchestrant.genai.untrusted import DATA_RULE, fence
from orchestrant.yolo.core.constants import CLASS_NAMES


if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable, Collection, Sequence
    from pathlib import Path

    from orchestrant.genai.engine import GenerationResult

_ASK = {
    "en": "Describe the camera scene in one short sentence, using only these detected objects.",
    "de": "Beschreibe die Kameraszene in einem kurzen Satz und nutze nur diese erkannten Objekte.",
}
_NOTHING = {"en": "no objects", "de": "keine Objekte"}
_JOIN_SECONDS = 5.0


class DescribeEngine(Protocol):
    """What the describer needs from an engine, so the tests can hand it a fake."""

    @property
    def control_tokens(self) -> Collection[str]:
        """Return the literal strings the tokenizer turns into control ids."""

    def generate(
        self,
        messages: Sequence[dict[str, Any]],
        options: GenerationOptions | None = None,
    ) -> GenerationResult:
        """Generate one answer."""

    def cancel(self) -> None:
        """End the running generation."""


def _class_name(class_id: object) -> str:
    if isinstance(class_id, int) and 0 <= class_id < len(CLASS_NAMES):
        return CLASS_NAMES[class_id]
    return f"class {class_id}"


def scene_counts(detections: Sequence[dict[str, object]]) -> dict[str, int]:
    """Return how many objects of each class a frame holds, sorted by name."""
    counts: Counter[str] = Counter()
    for detection in detections:
        counts[_class_name(detection.get("class_id"))] += 1
    return dict(sorted(counts.items()))


def scene_messages(
    counts: dict[str, int], language: str, control_tokens: Collection[str]
) -> list[dict[str, Any]]:
    """Return the prompt: the counts travel as data, since a custom model's names come from outside this package."""
    facts = ", ".join(f"{n} {name}" for name, n in counts.items())
    return [
        {"role": "system", "content": f"{_ASK[language]} {DATA_RULE[language]}"},
        {
            "role": "user",
            "content": fence(facts or _NOTHING[language], control_tokens),
        },
    ]


@dataclass
class SceneDescription:
    """One sentence, the counts it was asked about, and the token ids that let a run be compared."""

    counts: dict[str, int]
    text: str
    tokens: list[int]
    finish_reason: str

    def report(self) -> dict[str, Any]:
        """Return the JSON of one description."""
        return {
            "counts": self.counts,
            "text": self.text,
            "tokens": self.tokens,
            "finish_reason": self.finish_reason,
        }


def describe_scene(
    engine: DescribeEngine, counts: dict[str, int], language: str, max_new_tokens: int
) -> SceneDescription:
    """Generate one description; an empty one is an error, never a silent gap."""
    result = engine.generate(
        scene_messages(counts, language, engine.control_tokens),
        GenerationOptions(max_new_tokens=max_new_tokens, stop=("\n",)),
    )
    if not result.text.strip():
        msg = "the model gave an empty description"
        raise GenaiError(msg, kind="generation_failed")
    return SceneDescription(counts, result.text, result.tokens, result.finish_reason)


class NoDescriber:
    """The loop's describer without ``--describe``."""

    def offer(self, detections: Sequence[dict[str, object]]) -> None:
        """Ignore the frame."""

    def stop(self) -> None:
        """Nothing runs."""


class SceneDescriber:
    """Describes a changed scene at most once per interval, in its own thread; a frame never waits for it."""

    def __init__(
        self,
        engine: DescribeEngine,
        *,
        interval: float = 30.0,
        language: str = "en",
        max_new_tokens: int = 64,
        out: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Describe through ``engine``; ``out`` also gets one JSON line per description."""
        self._engine = engine
        self._interval = interval
        self._language = language
        self._budget = max_new_tokens
        self._out = out
        self._clock = clock
        self._next_at = clock()
        self._last: dict[str, int] | None = None
        self._pending: dict[str, int] | None = None
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self.described = 0
        self.failed = 0

    def offer(self, detections: Sequence[dict[str, object]]) -> None:
        """Hand over this frame's counts when a description is due; returns at once either way."""
        if self._clock() < self._next_at or not self.idle():
            return
        counts = scene_counts(detections)
        if counts == self._last:
            return
        self._next_at = self._clock() + self._interval
        self._last = counts
        self._pending = counts
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name="yolo-describe", daemon=True
            )
            self._thread.start()
        self._wake.set()

    def idle(self) -> bool:
        """Return whether no description is waiting or running."""
        return not self._wake.is_set()

    def _run(self) -> None:
        while self._wake.wait() and not self._stopping.is_set():
            counts = self._pending
            if counts is not None:
                self.describe_once(counts)
            self._wake.clear()

    def describe_once(self, counts: dict[str, int]) -> None:
        """Describe once; a failed model is logged and counted, and the camera keeps running."""
        try:
            description = describe_scene(
                self._engine, counts, self._language, self._budget
            )
            if self._out is not None:
                with self._out.open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps(description.report(), ensure_ascii=False) + "\n"
                    )
        except (GenaiError, OSError) as exc:
            self.failed += 1
            logger.error("Scene description failed: {}", exc)
            return
        self.described += 1
        logger.info("Scene {}: {}", counts, description.text)

    def stop(self) -> None:
        """End a running description and the thread, then report the totals."""
        self._stopping.set()
        self._engine.cancel()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(_JOIN_SECONDS)
        logger.info(
            "Scene descriptions: {} written, {} failed", self.described, self.failed
        )


def open_engine(args: argparse.Namespace) -> GenaiEngine:
    """Load the language model the command line names; a bad one stops the start-up rather than vanish later."""
    return GenaiEngine(args.describe_model, args.describe_provider)


def start_describer(args: argparse.Namespace) -> SceneDescriber | NoDescriber:
    """Return the describer the command line asks for; its thread starts with the first description due."""
    if not args.describe:
        return NoDescriber()
    return SceneDescriber(
        open_engine(args),
        interval=args.describe_interval,
        language=args.describe_language,
        max_new_tokens=args.describe_max_tokens,
        out=args.describe_out,
    )


def self_test_report(
    args: argparse.Namespace, detections: Sequence[dict[str, object]]
) -> dict[str, Any]:
    """Return one description of the self-test's frame."""
    description = describe_scene(
        open_engine(args),
        scene_counts(detections),
        args.describe_language,
        args.describe_max_tokens,
    )
    return description.report()
