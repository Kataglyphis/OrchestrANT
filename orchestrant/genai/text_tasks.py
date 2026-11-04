"""Classification, entities, summaries and translation, German and English, each answer bound by a JSON schema."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from orchestrant.documents.extract import detect_language
from orchestrant.genai.engine import GenaiError, GenerationOptions
from orchestrant.genai.untrusted import (
    DATA_RULE,
    fence,
    neutralize,
    reads_like_orders,
)


if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

    from orchestrant.genai.engine import GenerationResult

LANGUAGES = ("de", "en")
ENTITY_TYPES = ("person", "organization", "location", "date", "money", "other")
# Greedy decoding of a tiny model loops on whitespace without this; every answer stays one compact line.
_COMPACT = {"x-guidance": {"whitespace_flexible": False}}
_JSON_ONLY = {"en": "Reply with JSON only.", "de": "Antworte nur mit JSON."}
PROMPTS = {
    "classify": {
        "en": "Pick the one label that fits the text. Labels: {labels}.",
        "de": "Wähle für den Text genau ein Label. Labels: {labels}.",
    },
    "entities": {
        "en": "List the named entities exactly as the text writes them.",
        "de": "Nenne die benannten Entitäten im genauen Wortlaut des Textes.",
    },
    "summarize": {
        "en": "Summarize the text in English.",
        "de": "Fasse den Text auf Deutsch zusammen.",
    },
    "translate": {
        "en": "Translate the text into English.",
        "de": "Übersetze den Text ins Deutsche.",
    },
}
# No tokenizer spends a token on fewer characters than this, so a longer document cannot fit.
_MAX_CHARS_PER_TOKEN = 16
_FIT_STEPS = 12


class TaskEngine(Protocol):
    """What the tasks need from an engine, so the tests can hand them a fake."""

    @property
    def control_tokens(self) -> Collection[str]:
        """Return the literal strings the tokenizer turns into control ids."""

    @property
    def context_tokens(self) -> int | None:
        """Return the model's context, or None when it names none."""

    def prompt_tokens(self, messages: Sequence[dict[str, Any]]) -> int:
        """Return how many tokens these messages take."""

    def generate(
        self,
        messages: Sequence[dict[str, Any]],
        options: GenerationOptions | None = None,
    ) -> GenerationResult:
        """Generate one answer."""


@dataclass
class TaskAnswer:
    """One task's checked value, the language it ran in, and whether the document had to be cut to fit."""

    value: dict[str, Any]
    language: str
    language_source: str
    truncated: bool
    suspect: bool
    result: GenerationResult

    def report(self) -> dict[str, Any]:
        """Return the JSON a command prints; tokens let a run be compared with another."""
        return {
            **self.value,
            "language": self.language,
            "language_source": self.language_source,
            "truncated": self.truncated,
            "suspect_instructions": self.suspect,
            "finish_reason": self.result.finish_reason,
            "tokens": self.result.tokens,
        }


@dataclass
class TaskLimits:
    """The bounds of one answer; ``max_new_tokens`` must hold the JSON around ``max_chars``."""

    max_new_tokens: int = 512
    max_chars: int = 400
    max_entities: int = 16


def resolve_language(text: str, requested: str) -> tuple[str, str]:
    """Return the language to work in and where it came from: ``requested``, ``detected`` or ``fallback``."""
    if requested in LANGUAGES:
        return requested, "requested"
    if requested != "auto":
        msg = f"language {requested!r} is not one of {[*LANGUAGES, 'auto']}"
        raise GenaiError(msg, kind="invalid_options")
    detected = detect_language(text)
    return (detected, "detected") if detected else ("en", "fallback")


def object_schema(properties: dict[str, Any]) -> str:
    """Return the JSON text of a closed object whose every property is required."""
    return json.dumps(
        {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
            **_COMPACT,
        }
    )


def fit_text(
    engine: TaskEngine,
    build: Callable[[str], list[dict[str, Any]]],
    text: str,
    budget: int,
) -> str:
    """Return the longest head of ``text`` that fits the context beside ``budget``; the caller compares it to report a cut."""
    limit = engine.context_tokens
    if limit is None:
        return text
    room = limit - budget - engine.prompt_tokens(build(""))
    if room <= 0:
        msg = f"the prompt and max_new_tokens {budget} leave no room in the model's context of {limit}"
        raise GenaiError(msg, kind="context_length_exceeded")
    kept = text[: room * _MAX_CHARS_PER_TOKEN]
    for _ in range(_FIT_STEPS):
        used = engine.prompt_tokens(build(kept)) + budget
        if used <= limit:
            return kept
        over = used - limit
        kept = kept[: max(len(kept) - max(over, len(kept) // 8), 0)]
    msg = f"no head of the text fits the model's context of {limit}"
    raise GenaiError(msg, kind="context_length_exceeded")


def parse_answer(result: GenerationResult) -> dict[str, Any]:
    """Return the answer's JSON object; a cut, cancelled or malformed answer is an error, never a value."""
    if result.finish_reason == "cancelled":
        msg = "the answer was cancelled"
        raise GenaiError(msg, kind="cancelled")
    if result.finish_reason == "length":
        msg = f"the answer hit its budget of {len(result.tokens)} tokens before the schema closed"
        raise GenaiError(msg, kind="answer_truncated")
    try:
        value = json.loads(result.text)
    except ValueError as exc:
        msg = f"the answer is not JSON: {exc}"
        raise GenaiError(msg, kind="invalid_answer") from exc
    if not isinstance(value, dict):
        msg = "the answer is not a JSON object"
        raise GenaiError(msg, kind="invalid_answer")
    return value


def required_text(value: dict[str, Any], key: str) -> str:
    """Return the string ``key`` holds, or fail: a model without guidance can break the schema."""
    found = value.get(key)
    if not isinstance(found, str):
        msg = f"the answer's {key!r} is not a string"
        raise GenaiError(msg, kind="invalid_answer")
    return found


def _normal(text: str) -> str:
    return " ".join(text.split()).casefold()


def checked_entities(value: dict[str, Any], text: str) -> dict[str, Any]:
    """Split the answer's entities into those the text really contains and those a model made up."""
    items = value.get("entities")
    if not isinstance(items, list):
        msg = "the answer's 'entities' is not a list"
        raise GenaiError(msg, kind="invalid_answer")
    haystack = _normal(text)
    found: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    for item in items:
        name = required_text(item, "text") if isinstance(item, dict) else None
        if name is None or item.get("type") not in ENTITY_TYPES:
            msg = f"the answer's entity {item!r} has no text and known type"
            raise GenaiError(msg, kind="invalid_answer")
        entity = {"text": name, "type": item["type"]}
        target = found if name.strip() and _normal(name) in haystack else rejected
        if entity not in target:
            target.append(entity)
    return {"entities": found, "rejected_entities": rejected}


def check_labels(labels: Sequence[str]) -> None:
    """Refuse a label set no answer could be checked against."""
    if len(labels) < 2 or len(set(labels)) != len(labels):
        msg = f"classify needs at least two distinct labels, not {list(labels)}"
        raise GenaiError(msg, kind="invalid_options")
    if not all(isinstance(label, str) and label.strip() for label in labels):
        msg = "every label must be non-empty text"
        raise GenaiError(msg, kind="invalid_options")


class TextTasks:
    """The four tasks over one engine; each document goes into the user message, fenced and neutralized."""

    def __init__(self, engine: TaskEngine, limits: TaskLimits | None = None) -> None:
        """Answer through ``engine``, within ``limits``."""
        self.engine = engine
        self.limits = limits or TaskLimits()

    def _ask(
        self, system: str, language: str, text: str, schema: str
    ) -> tuple[dict[str, Any], str, GenerationResult]:
        if not text.strip():
            msg = "the text is empty"
            raise GenaiError(msg, kind="invalid_options")
        rules = f"{system} {DATA_RULE[language]} {_JSON_ONLY[language]}"
        tokens = self.engine.control_tokens

        def build(body: str) -> list[dict[str, Any]]:
            return [
                {"role": "system", "content": rules},
                {"role": "user", "content": fence(body, tokens)},
            ]

        budget = self.limits.max_new_tokens
        kept = fit_text(self.engine, build, text, budget)
        options = GenerationOptions(max_new_tokens=budget, json_schema=schema)
        result = self.engine.generate(build(kept), options)
        return parse_answer(result), kept, result

    @staticmethod
    def _answer(
        value: dict[str, Any],
        text: str,
        kept: str,
        lang: str,
        source: str,
        result: GenerationResult,
    ) -> TaskAnswer:
        suspect = reads_like_orders(text)
        return TaskAnswer(value, lang, source, kept != text, suspect, result)

    def classify(
        self, text: str, labels: Sequence[str], language: str = "auto"
    ) -> TaskAnswer:
        """Return the one of ``labels`` the model picks; the schema and a check hold it to the set."""
        check_labels(labels)
        lang, source = resolve_language(text, language)
        shown = ", ".join(neutralize(label) for label in labels)
        system = PROMPTS["classify"][lang].format(labels=shown)
        schema = object_schema({"label": {"enum": list(labels)}})
        value, kept, result = self._ask(system, lang, text, schema)
        if value.get("label") not in labels:
            msg = f"the answer's label {value.get('label')!r} is not in {list(labels)}"
            raise GenaiError(msg, kind="invalid_answer")
        return self._answer({"label": value["label"]}, text, kept, lang, source, result)

    def entities(self, text: str, language: str = "auto") -> TaskAnswer:
        """Return the entities the model names; one the kept text does not contain lands in ``rejected_entities``."""
        lang, source = resolve_language(text, language)
        item = object_item(self.limits.max_chars)
        schema = object_schema(
            {
                "entities": {
                    "type": "array",
                    "maxItems": self.limits.max_entities,
                    "items": item,
                }
            }
        )
        system = PROMPTS["entities"][lang]
        value, kept, result = self._ask(system, lang, text, schema)
        checked = checked_entities(value, kept)
        return self._answer(checked, text, kept, lang, source, result)

    def _prose(
        self, task: str, key: str, text: str, lang: str, source: str
    ) -> TaskAnswer:
        schema = object_schema(
            {key: {"type": "string", "maxLength": self.limits.max_chars}}
        )
        value, kept, result = self._ask(PROMPTS[task][lang], lang, text, schema)
        answer = {key: required_text(value, key)}
        return self._answer(answer, text, kept, lang, source, result)

    def summarize(self, text: str, language: str = "auto") -> TaskAnswer:
        """Return a summary in ``language``; a document over the context is summarized from its head and says so."""
        lang, source = resolve_language(text, language)
        return self._prose("summarize", "summary", text, lang, source)

    def translate(self, text: str, target: str) -> TaskAnswer:
        """Return ``text`` in ``target``, ``de`` or ``en``; ``language`` in the report is that target."""
        if target not in LANGUAGES:
            msg = f"target {target!r} is not one of {list(LANGUAGES)}"
            raise GenaiError(msg, kind="invalid_options")
        return self._prose("translate", "translation", text, target, "requested")


def object_item(max_chars: int) -> dict[str, Any]:
    """Return the schema of one entity."""
    return {
        "type": "object",
        "properties": {
            "text": {"type": "string", "maxLength": max_chars},
            "type": {"enum": list(ENTITY_TYPES)},
        },
        "required": ["text", "type"],
        "additionalProperties": False,
    }
