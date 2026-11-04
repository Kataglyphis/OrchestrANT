"""Answer a question from retrieved chunks through the chain GenAI: fenced as data, its citations checked."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from orchestrant.documents.extract import detect_language
from orchestrant.genai.engine import GenaiError, GenerationOptions
from orchestrant.genai.text_tasks import (
    LANGUAGES,
    object_schema,
    parse_answer,
    required_text,
)
from orchestrant.genai.untrusted import (
    DATA_RULE,
    fence,
    neutralize,
    reads_like_orders,
)


if TYPE_CHECKING:
    from collections.abc import Sequence

    from orchestrant.documents.index import Hit
    from orchestrant.genai.text_tasks import TaskEngine

_RULES = {
    "en": "Answer the question from the numbered sources only, and cite the numbers you used. Reply with JSON only.",
    "de": "Beantworte die Frage nur aus den nummerierten Quellen und nenne die benutzten Nummern. Antworte nur mit JSON.",
}
_MIN_EXCERPT = 40


@dataclass(frozen=True)
class AskLimits:
    """The bounds of one answer."""

    max_new_tokens: int = 512
    max_chars: int = 800
    language: str = "auto"
    include_suspect: bool = False


@dataclass
class Answered:
    """The answer, the sources the model cited, every source it was given, and any cut to fit the context."""

    answer: str | None
    reason: str = ""
    language: str = ""
    cited: list[dict[str, Any]] = field(default_factory=list)
    retrieved: list[dict[str, Any]] = field(default_factory=list)
    withheld: list[dict[str, Any]] = field(default_factory=list)
    excerpt_chars: int = 0
    tokens: list[int] = field(default_factory=list)

    def report(self) -> dict[str, Any]:
        """Return the JSON a command prints."""
        return {
            "answer": self.answer,
            "reason": self.reason,
            "language": self.language,
            "cited": self.cited,
            "retrieved": self.retrieved,
            "withheld": self.withheld,
            "excerpt_chars": self.excerpt_chars,
            "tokens": self.tokens,
        }


def source_row(number: int, hit: Hit) -> dict[str, Any]:
    """Return one source as the report shows it."""
    return {"n": number, "path": hit.path, "page": hit.page, "chunk": hit.ordinal}


def no_sources(withheld: Sequence[Hit] = ()) -> Answered:
    """Return the answer when nothing usable matched: no model is asked, so nothing is made up."""
    rows = [source_row(n, hit) for n, hit in enumerate(withheld, start=1)]
    return Answered(None, reason="no_sources", withheld=rows)


def question_language(question: str, hits: Sequence[Hit], requested: str) -> str:
    """Return the language to answer in: asked, else the question's, else the sources', else English."""
    if requested in LANGUAGES:
        return requested
    sources = " ".join(hit.text for hit in hits)
    return detect_language(question) or detect_language(sources) or "en"


def build_messages(
    engine: TaskEngine,
    question: str,
    hits: Sequence[Hit],
    excerpt: int,
    language: str,
) -> list[dict[str, Any]]:
    """Return the prompt: fixed rules as the system, the question and fenced sources as the user."""
    tokens = engine.control_tokens
    block = "\n".join(
        f"[{n}] {hit.locator()}\n{hit.text[:excerpt]}"
        for n, hit in enumerate(hits, start=1)
    )
    return [
        {"role": "system", "content": f"{_RULES[language]} {DATA_RULE[language]}"},
        {
            "role": "user",
            "content": f"{neutralize(question, tokens)}\n{fence(block, tokens)}",
        },
    ]


def fit_sources(
    engine: TaskEngine,
    question: str,
    hits: Sequence[Hit],
    budget: int,
    language: str,
) -> tuple[Sequence[Hit], int]:
    """Return the sources and excerpt length that fit: excerpts halve first, then the weakest source leaves."""
    limit = engine.context_tokens
    longest = max(len(hit.text) for hit in hits)
    if limit is None:
        return hits, longest
    kept, excerpt = hits, longest
    while kept:
        used = engine.prompt_tokens(
            build_messages(engine, question, kept, excerpt, language)
        )
        if used + budget <= limit:
            return kept, excerpt
        if excerpt > _MIN_EXCERPT:
            excerpt = max(excerpt // 2, _MIN_EXCERPT)
        else:
            kept, excerpt = kept[:-1], longest
    msg = f"not one source fits the model's context of {limit} beside max_new_tokens {budget}"
    raise GenaiError(msg, kind="context_length_exceeded")


def _source_schema(count: int, max_chars: int) -> str:
    return object_schema(
        {
            "answer": {"type": "string", "maxLength": max_chars},
            "sources": {
                "type": "array",
                "maxItems": count,
                "items": {"enum": list(range(1, count + 1))},
            },
        }
    )


def _checked_citations(
    value: dict[str, Any], kept: Sequence[Hit]
) -> list[dict[str, Any]]:
    """Return the cited sources; a number the prompt never offered is an error, not a citation."""
    numbers = value.get("sources")
    if not isinstance(numbers, list):
        msg = "the answer's 'sources' is not a list"
        raise GenaiError(msg, kind="invalid_answer")
    cited = []
    for number in dict.fromkeys(numbers):
        if not isinstance(number, int) or not 1 <= number <= len(kept):
            msg = f"the answer cites {number!r}; it was given {len(kept)} sources"
            raise GenaiError(msg, kind="invalid_answer")
        cited.append(source_row(number, kept[number - 1]))
    return cited


def answer_from(
    engine: TaskEngine,
    question: str,
    hits: Sequence[Hit],
    limits: AskLimits | None = None,
) -> Answered:
    """Ask the model about ``hits``; the citations name real sources, whether they support the answer is unverified."""
    limits = limits or AskLimits()
    suspect = (
        [] if limits.include_suspect else [h for h in hits if reads_like_orders(h.text)]
    )
    hits = [hit for hit in hits if hit not in suspect]
    if not hits:
        return no_sources(suspect)
    language = question_language(question, hits, limits.language)
    budget = limits.max_new_tokens
    kept, excerpt = fit_sources(engine, question, hits, budget, language)
    result = engine.generate(
        build_messages(engine, question, kept, excerpt, language),
        GenerationOptions(
            max_new_tokens=budget,
            json_schema=_source_schema(len(kept), limits.max_chars),
        ),
    )
    value = parse_answer(result)
    return Answered(
        required_text(value, "answer"),
        language=language,
        cited=_checked_citations(value, kept),
        retrieved=[source_row(n, hit) for n, hit in enumerate(kept, start=1)],
        withheld=[source_row(n, hit) for n, hit in enumerate(suspect, start=1)],
        excerpt_chars=excerpt,
        tokens=result.tokens,
    )
