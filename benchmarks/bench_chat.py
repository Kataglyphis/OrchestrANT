#!/usr/bin/env python3
"""Measure chat quality: does the model do what a chat user asked?

The chat recommendation rested on speed plus six trivia probes. A model can
recite Canberra and still ignore "at most 20 words", wrap its JSON in prose,
forget turn 1 by turn 4, or miss a fact three thousand tokens into a document.
This asks about thirty cases in four families, every one graded by code:

  * instruction following: word, sentence and bullet limits, an answer in
    German, no Markdown, capitals only, a fixed sign-off, a banned word;
  * JSON-only replies, validated against a JSON schema (a minimal validator
    for the subset the cases use; jsonschema is not a dependency) and then
    against the values the prompt dictates;
  * multi-turn: a fact and a rule from turn 1, a correction in turn 2;
  * document QA over a generated ~1k, ~3.5k and ~8k-token document: three
    facts each, at 15/50/85 % depth, each with a decoy elsewhere. ~3.5k fits
    the NPU lane's 4096-token context; ~8k does not, and a lane that refuses
    it records OVERFLOW rows, not graded -- bench_coding's rule.

Thinking is stripped before grading (answers.split_answer), so a `<think>`
that never closed is no answer; a reply the budget cut is a CUT row, excluded
and counted, as in bench_coding. Repeats of one case are separated by
client.spacer. `tool_sha256` fingerprints this file, where the cases and
graders live, and determinism.py, whose probe sets bench_compare's strict mode
-- beside a control, compare_suspect.py too, which recounts the other rows.

Usage:
    python3 bench_chat.py --backend geniex-npu
    python3 bench_chat.py --compare candidates.json --repeats 3 --output chat.json
    python3 bench_chat.py --backend geniex-npu --category instruction --category json
"""

import argparse
import hashlib
import json
import os
import random
import re
import statistics
import sys
import time
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Standalone runs of these scripts (they are not a package) need the repo
# root on sys.path; the request path lives in orchestrant.benchmark.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from orchestrant.benchmark import client as bench_cli
from orchestrant.benchmark.answers import accounting, from_body, split_answer
from orchestrant.benchmark.stats import format_score, tiers

# determinism.py too: the probe this tool runs sets bench_compare's strict mode.
TOOL_FILES = (os.path.abspath(__file__), "determinism.py")
# Room for a thinking model to finish: on 2026-09-24 the CPU lane left six of
# nine speed-runner replies inside <think> at 256 tokens; at 2048 all five
# short prompts answered (only the code prompts and a blog post were cut).
DEFAULT_MAX_TOKENS = 2048

# ── text measures ────────────────────────────────────────────────────────────

_WORD = re.compile(r"\w+(?:['’-]\w+)*")
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"'”)\]]*\s+")
# A full stop that ends an abbreviation, not a sentence: "measures temperature,
# e.g. of air" read as two sentences failed a correct one-sentence reply.
_ABBREVIATION = re.compile(
    r"\b(?:e\.g|i\.e|cf|vs|approx|ca|Dr|Mr|Mrs|Ms|St|z\.B|d\.h|bzw)\.$", re.I
)


def words(text):
    """Words, a hyphenated or apostrophised one counted once."""
    return _WORD.findall(text)


def sentences(text):
    merged = []
    for piece in _SENTENCE_END.split(text.strip()):
        if merged and _ABBREVIATION.search(merged[-1]):
            merged[-1] += " " + piece
        else:
            merged.append(piece)
    return [s for s in merged if _WORD.search(s)]


def paragraphs(text):
    """Blocks between blank lines; single-newline blocks if there is only one."""
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    if len(blocks) == 1:
        blocks = [line for line in blocks[0].splitlines() if line.strip()]
    return blocks


# Function words only, and disjoint: a hit is a vote for one language.
_GERMAN = frozenset({
    "der", "die", "das", "den", "dem", "des", "und", "ist", "nicht", "mit",  # codespell:ignore
    "von", "zu", "im", "auf", "für", "ein", "eine", "einen", "sich", "sie",  # codespell:ignore
    "es", "sind", "wird", "auch", "als", "bei", "oder", "aber",  # codespell:ignore
})  # fmt: skip
_ENGLISH = frozenset({
    "the", "and", "is", "are", "of", "to", "that", "for", "with", "on", "as",
    "this", "was", "be", "it", "its", "which", "or", "but", "has", "have",
})  # fmt: skip


def german_votes(text):
    """(German, English) function-word counts."""
    lowered = [w.lower() for w in words(text)]
    return sum(w in _GERMAN for w in lowered), sum(w in _ENGLISH for w in lowered)


_MARKDOWN = (
    ("a heading", re.compile(r"^\s{0,3}#{1,6}\s", re.M)),
    ("a list item", re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s", re.M)),
    ("bold", re.compile(r"\*\*|__")),
    ("italics", re.compile(r"(?<![\w*])\*(?!\s)[^*\n]+(?<!\s)\*(?![\w*])")),
    ("code", re.compile(r"`")),
    ("a quote block", re.compile(r"^\s*>", re.M)),
    ("a link", re.compile(r"\[[^\]\n]+\]\([^)\n]+\)")),
    ("a table", re.compile(r"^\s*\|.*\|\s*$", re.M)),
)


def markdown_found(text):
    """The first kind of Markdown in `text`, or None."""
    return next((name for name, rx in _MARKDOWN if rx.search(text)), None)


def mentions(text, needle):
    """`needle` as a whole word, any case; a number not inside a longer one.

    "16" is found in "the 16th": an answer that says the 16th named it.
    """
    lead = r"(?<!\d)" if needle[0].isdigit() else r"(?<!\w)"
    tail = r"(?!\d)" if needle[-1].isdigit() else r"(?!\w)"
    return re.search(lead + re.escape(needle) + tail, text, re.I) is not None


# ── a JSON Schema subset ─────────────────────────────────────────────────────

SCHEMA_KEYWORDS = frozenset({
    "type", "enum", "const", "properties", "required", "additionalProperties",
    "items", "minItems", "maxItems", "minimum", "maximum", "minLength", "maxLength",
})  # fmt: skip
_JSON_TYPES = {"string": str, "boolean": bool, "null": type(None), "object": dict, "array": list}  # fmt: skip
# keyword -> (the values it applies to, whether it holds, how a failure reads)
_LIMITS = {
    "minItems": (list, lambda v, n: len(v) >= n, "fewer items than"),
    "maxItems": (list, lambda v, n: len(v) <= n, "more items than"),
    "minLength": (str, lambda v, n: len(v) >= n, "shorter than"),
    "maxLength": (str, lambda v, n: len(v) <= n, "longer than"),
    "minimum": ((int, float), lambda v, n: v >= n, "below"),
    "maximum": ((int, float), lambda v, n: v <= n, "above"),
}


def _is_type(value, name):
    # bool is an int in Python and never a JSON number; 3.0 is a JSON integer.
    if isinstance(value, bool) and name in ("integer", "number"):
        return False
    if name == "integer":
        return isinstance(value, int) or (
            isinstance(value, float) and value.is_integer()
        )
    if name == "number":
        return isinstance(value, (int, float))
    return isinstance(value, _JSON_TYPES[name])


def _same(a, b):
    """JSON equality: true is not 1."""
    return a == b and isinstance(a, bool) == isinstance(b, bool)


def _limit_errors(value, schema, path):
    return [
        f"{path}: {reads} {schema[key]}"
        for key, (kind, holds, reads) in _LIMITS.items()
        if key in schema
        and isinstance(value, kind)
        and not isinstance(value, bool)
        and not holds(value, schema[key])
    ]


def _object_errors(value, schema, path):
    props = schema.get("properties", {})
    extra = schema.get("additionalProperties", True)
    if not isinstance(extra, bool):
        raise TypeError(f"{path}: additionalProperties must be true or false here")
    required = schema.get("required", ())
    errors = [f"{path}: missing {k!r}" for k in required if k not in value]
    errors += [f"{path}: unexpected {k!r}" for k in value if not (extra or k in props)]
    for key in (k for k in props if k in value):
        errors += validate(value[key], props[key], f"{path}.{key}")
    return errors


def validate(value, schema, path="$"):
    """Errors of `value` against `schema`, [] when it conforms.

    Only SCHEMA_KEYWORDS are implemented, and anything else raises: a case
    must not rely on a keyword this would silently ignore.
    """
    unknown = set(schema) - SCHEMA_KEYWORDS
    if unknown:
        raise ValueError(f"{path}: schema keyword(s) {sorted(unknown)} not implemented")
    names = schema.get("type") or []
    names = [names] if isinstance(names, str) else names
    if names and not any(_is_type(value, n) for n in names):
        return [f"{path} is {type(value).__name__}, not {'/'.join(names)}"]
    errors = _limit_errors(value, schema, path)
    if "enum" in schema and not any(_same(value, e) for e in schema["enum"]):
        errors.append(f"{path}={value!r} is not one of {schema['enum']}")
    if "const" in schema and not _same(value, schema["const"]):
        errors.append(f"{path}={value!r} is not {schema['const']!r}")
    if isinstance(value, dict):
        errors += _object_errors(value, schema, path)
    items = value if isinstance(value, list) and "items" in schema else ()
    for i, item in enumerate(items):
        errors += validate(item, schema["items"], f"{path}[{i}]")
    return errors


_FENCE = re.compile(r"^```[\w-]*\s*\n(.*?)\n?```$", re.DOTALL)


def parse_json_reply(answer):
    """(value, None), or (None, why): the WHOLE answer must be JSON.

    Valid JSON inside a fence is named as such: every JSON prompt here
    forbids one, and a client's json.loads fails on it.
    """
    text = answer.strip()
    try:
        return json.loads(text), None
    except ValueError as e:
        problem = f"not JSON: {e}"[:120]
    fence = _FENCE.match(text)
    if not fence:
        return None, problem
    try:
        json.loads(fence.group(1))
    except ValueError:
        return None, "not JSON, even inside its code fence"
    return None, "valid JSON, but inside a code fence"


def _canonical(value, unordered):
    """`value` with every list sorted, when order does not matter."""
    if isinstance(value, dict):
        return {k: _canonical(v, unordered) for k, v in value.items()}
    if not isinstance(value, list):
        return value
    items = [_canonical(v, unordered) for v in value]
    if not unordered:
        return items
    return sorted(items, key=lambda v: json.dumps(v, sort_keys=True))


def _value_problem(value, spec):
    """Why a schema-valid value is still wrong, or None."""
    loose = spec.get("unordered", False)
    if "equals" in spec and _canonical(value, loose) != _canonical(
        spec["equals"], loose
    ):
        return f"schema-valid, but {json.dumps(value)[:80]} is not the expected value"
    names = [str(v).strip().lower() for v in value] if isinstance(value, list) else []
    wrong = [n for n in names if n not in spec.get("each_in", names)]
    if wrong:
        return f"not an accepted value: {wrong[:3]}"
    if spec.get("distinct") and len(set(names)) != len(names):
        return f"a value repeats: {names}"
    return None


# ── graders: (answer, argument) -> (ok, detail) ──────────────────────────────


def _count(n, bounds, unit):
    lo, hi = bounds
    wanted = f"exactly {lo}" if lo == hi else f"{lo}-{hi}"
    return lo <= n <= hi, f"{n} {unit}, wanted {wanted}"


def _check_bullets(answer, bounds):
    lines = [line.strip() for line in answer.strip().splitlines() if line.strip()]
    stray = [line for line in lines if not line.startswith("- ")]
    if stray:
        return False, f"{len(stray)} line(s) not a '- ' bullet: {stray[0][:40]!r}"
    return _count(len(lines), bounds, "bullets")


def _check_german(answer, _):
    german, english = german_votes(answer)
    ok = german >= 2 and german > 2 * english
    return ok, f"German/English function words {german}/{english}"


def _check_upper(answer, _):
    letters = [c for c in answer if c.isalpha()]
    lower = sum(c.islower() for c in letters)
    return bool(letters) and not lower, f"{lower} lowercase letter(s)"


def _check_contains(answer, items):
    for item in items:
        alternatives = (item,) if isinstance(item, str) else item
        if not any(mentions(answer, a) for a in alternatives):
            return False, f"does not say {' / '.join(alternatives)!r}"
    return True, "ok"


def _check_forbid(answer, items):
    hit = next((w for w in items if mentions(answer, w)), None)
    return hit is None, f"says {hit!r}"


def _check_json(answer, spec):
    value, problem = parse_json_reply(answer)
    if problem is None:
        errors = validate(value, spec["schema"])
        problem = "schema: " + "; ".join(errors[:3]) if errors else None
    if problem is None:
        problem = _value_problem(value, spec)
    return problem is None, problem or "ok"


GRADERS = {
    "words": lambda a, b: _count(len(words(a)), b, "words"),
    "sentences": lambda a, b: _count(len(sentences(a)), b, "sentences"),
    "paragraphs": lambda a, b: _count(len(paragraphs(a)), b, "paragraphs"),
    "bullets": _check_bullets,
    "german": _check_german,
    "no_markdown": lambda a, _: (not markdown_found(a), f"uses {markdown_found(a)}"),
    "upper": _check_upper,
    "contains": _check_contains,
    "forbid": _check_forbid,
    "ends": lambda a, rx: (bool(re.search(rx + r"\s*$", a)), f"ends {a[-30:]!r}"),
    "json": _check_json,
}


def grade(answer, checks):
    """(passed, detail): every check must hold; the first that fails is named."""
    answer = answer.strip()
    if not answer:
        return False, "no answer"
    for kind, argument in checks:
        ok, detail = GRADERS[kind](answer, argument)
        if not ok:
            return False, f"{kind}: {detail}"
    return True, "ok"


# ── the cases ────────────────────────────────────────────────────────────────


def _case(name, prompt, *checks):
    return {"name": name, "prompt": prompt, "checks": list(checks)}


# A bare "Over and out" ended correctly and named no planet: it passed.
_PLANETS = ("Mercury", "Venus", "Earth", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune")  # fmt: skip


INSTRUCTION_CASES = [
    _case("words_at_most_20", "In 20 words or fewer, explain why the sky looks blue.", ("words", (3, 20))),
    _case("words_exactly_5", "Describe the ocean in exactly five words. Reply with those five words and nothing else.", ("words", (5, 5))),
    _case("one_sentence", "In one sentence: what does a thermometer measure?", ("sentences", (1, 1)), ("contains", ["temperature"])),
    _case("three_sentences", "Describe a bicycle in exactly three sentences.", ("sentences", (3, 3))),
    _case("bullets_exactly_4", "List exactly four fruits. Put each on its own line starting with '- '. Write nothing else.", ("bullets", (4, 4))),
    _case("bullets_at_most_3", "Give at most three short tips for sleeping better. Put each tip on its own line starting with '- '. No introduction and no closing remark.", ("bullets", (1, 3))),
    _case("answer_in_german", "Answer in German, in two sentences: what is the capital of France, and what is it famous for?", ("german", None), ("contains", ["Paris"])),
    _case("translate_to_german", "Translate into German and reply with the translation only: The cat is sleeping on the warm windowsill.", ("german", None), ("contains", ["Katze", ("schläft", "schlaeft")]), ("forbid", ["cat", "sleeping"])),
    _case("plain_text_paragraphs", "Explain what a hash table is in two short paragraphs of plain text. Do not use Markdown: no headings, lists, bold, italics, code spans or code blocks.", ("no_markdown", None), ("paragraphs", (2, 2))),
    _case("plain_text_flag", "Name the three colours of the German flag in one plain sentence, without Markdown and without a list.", ("no_markdown", None), ("sentences", (1, 1)), ("contains", ["black", "red", ("gold", "yellow")])),
    _case("capitals_only", "Answer in capital letters only, with no lowercase letters at all: which planet is the largest in our solar system?", ("upper", None), ("contains", ["JUPITER"])),
    _case("fixed_sign_off", "Name one planet of our solar system, then end your reply with exactly these words: Over and out", ("contains", [_PLANETS]), ("ends", r"Over and out[.!]?")),
    _case("avoid_a_word", "Describe the moon in two sentences without using the word 'the' anywhere.", ("forbid", ["the"]), ("words", (8, 80))),
]  # fmt: skip


def _obj(**props):
    """An object schema: exactly these keys, all of them required."""
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}  # fmt: skip


_STR, _INT, _BOOL = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
_JSON_ONLY = " Reply with the JSON only: no prose and no code fence."
EU_CAPITALS = frozenset({
    "vienna", "brussels", "sofia", "zagreb", "nicosia", "prague", "copenhagen",
    "tallinn", "helsinki", "paris", "berlin", "athens", "budapest", "dublin",
    "rome", "riga", "vilnius", "luxembourg", "luxembourg city", "valletta",
    "amsterdam", "warsaw", "lisbon", "bucharest", "bratislava", "ljubljana",
    "madrid", "stockholm",
})  # fmt: skip


def _json_case(name, prompt, schema, **expect):
    return _case(name, prompt + _JSON_ONLY, ("json", {"schema": schema, **expect}))


JSON_CASES = [
    _json_case(
        "json_people",
        'Extract every person and their age from this text as a JSON array of objects with the keys "name" and "age": Mara is 31 and her brother Tobias is 27.',
        {"type": "array", "minItems": 2, "maxItems": 2, "items": _obj(name=_STR, age=_INT)},
        equals=[{"name": "Mara", "age": 31}, {"name": "Tobias", "age": 27}], unordered=True,
    ),
    _json_case(
        "json_config",
        "Write a JSON object for this server configuration, with the keys host, port, debug and workers: it listens on localhost port 8080, debugging is off, and it runs 4 workers.",
        _obj(host=_STR, port={"type": "integer", "minimum": 1, "maximum": 65535}, debug=_BOOL, workers={"type": "integer", "minimum": 1}),
        equals={"host": "localhost", "port": 8080, "debug": False, "workers": 4},
    ),
    _json_case(
        "json_sentiment",
        'Classify each review as "positive", "negative" or "neutral", as a JSON object {"reviews": [{"id": 1, "sentiment": "..."}, ...]} in id order. (1) "Absolutely loved it, would buy again." (2) "Broke after one day, a total waste of money." (3) "It arrived on a Tuesday."',
        _obj(reviews={"type": "array", "minItems": 3, "maxItems": 3, "items": _obj(id=_INT, sentiment={"type": "string", "enum": ["positive", "negative", "neutral"]})}),
        equals={"reviews": [{"id": 1, "sentiment": "positive"}, {"id": 2, "sentiment": "negative"}, {"id": 3, "sentiment": "neutral"}]},
    ),
    _json_case(
        "json_order_total",
        'Order "A-17" contains 3 notebooks (sku "NB-1") at 2.50 each and 1 pen (sku "PN-4") at 10.00. Write it as a JSON object with "id", "items" (a list of objects with "sku" and "qty") and "total" (a number: the order total).',
        _obj(id=_STR, items={"type": "array", "minItems": 1, "items": _obj(sku=_STR, qty={"type": "integer", "minimum": 1})}, total={"type": "number"}),
        equals={"id": "A-17", "items": [{"sku": "NB-1", "qty": 3}, {"sku": "PN-4", "qty": 1}], "total": 17.5}, unordered=True,
    ),
    _json_case(
        "json_booleans",
        'Answer each question with true or false, as a JSON object {"q1": ..., "q2": ..., "q3": ...}. q1: Is 17 a prime number? q2: Is Paris the capital of Germany? q3: Does pure water freeze at 0 degrees Celsius at sea level?',
        _obj(q1=_BOOL, q2=_BOOL, q3=_BOOL),
        equals={"q1": True, "q2": False, "q3": True},
    ),
    _json_case(
        "json_capitals",
        "List exactly three capital cities of European Union member states, in English, as a JSON array of strings.",
        {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "string", "minLength": 1}},
        each_in=EU_CAPITALS, distinct=True,
    ),
    _json_case(
        "json_escaping",
        'Return a JSON object with one key, "quote", whose value is exactly the text between the angle brackets: <She said "hi" and left a \\ behind.>',
        _obj(quote=_STR),
        equals={"quote": 'She said "hi" and left a \\ behind.'},
    ),
    _json_case(
        "json_nullable",
        'Turn this record into a JSON object with the keys "name", "phone" and "city", using null for anything unknown: name Ana Ruiz, phone unknown, city Porto.',
        _obj(name=_STR, phone={"type": ["string", "null"]}, city=_STR),
        equals={"name": "Ana Ruiz", "phone": None, "city": "Porto"},
    ),
]  # fmt: skip


def _turns(*texts):
    """Alternating user/assistant messages, starting and ending with the user."""
    roles = ("user", "assistant")
    return [{"role": roles[i % 2], "content": text} for i, text in enumerate(texts)]


MULTI_CASES = [
    {
        "name": "recall_turn_one",
        "messages": _turns(
            "Hi! My name is Liesel and my favourite number is 47. Please keep both in mind.",
            "Nice to meet you, Liesel! I will remember that your favourite number is 47.",
            "Can you suggest a name for a grey cat?",
            "How about Smokey? It suits a grey cat.",
            "And one for a black dog?",
            "Maybe Shadow.",
            "What is my favourite number plus 3, and what is my name? One short sentence.",
        ),
        "checks": [("contains", ["50", "Liesel"])],
    },
    {
        "name": "rule_from_turn_one",
        "messages": _turns(
            "For the rest of this conversation, answer every question with a single word.",
            "Understood.",
            "What colour is the sky on a clear day?",
            "Blue.",
            "What is the opposite of hot?",
        ),
        "checks": [("words", (1, 1)), ("contains", ["cold"])],
    },
    {
        "name": "correction_in_turn_two",
        "messages": _turns(
            "I am booking a flight to Lisbon on the 14th of May.",
            "Great: a flight to Lisbon on 14 May. Anything else?",
            "Correction: the flight is on the 16th of May, not the 14th.",
            "Thanks, noted: 16 May.",
            "On which day of May is my flight? Reply with the number only.",
        ),
        "checks": [("contains", ["16"]), ("forbid", ["14"])],
    },
]  # fmt: skip

# ── document QA ──────────────────────────────────────────────────────────────

DOC_SIZES = {1000: "1k", 3500: "3.5k", 8000: "8k"}
# Built to this share of the nominal size by approx_tokens, so the ~3.5k
# document plus its question stays inside a 4096-token context. Counted with
# the NPU bundle's own tokenizer.json (Qwen3-4B-Instruct-2507, 2026-09-24),
# the three prompts are 953-962, 3142-3151 and 7071-7080 tokens with the
# lane's default system turn: the estimate runs ~9 % high on this text, and
# the ~3.5k prompt leaves ~950 of the 4096 for the reply.
DOC_FILL = 0.95
_DOC_TITLE = "Field notes of the Varde valley works"
_DOC_PLACES = ("north pump station", "harbour office", "glass depot", "river lab", "old mill", "signal tower", "south greenhouse", "cold store", "print shop", "ferry landing", "bakery annex", "tool shed")  # fmt: skip
_DOC_PEOPLE = ("Irma Varga", "Tomas Lind", "Priya Raman", "Oskar Brandt", "Mei Tanaka", "Luca Ferri", "Ada Okafor", "Nils Hagen", "Rosa Medina", "Emil Sauer", "Hana Novak", "Farid Aziz")  # fmt: skip
_DOC_ITEMS = ("valves", "crates", "filters", "cables", "lamps", "seed trays", "ledgers", "batteries", "ropes", "jars")  # fmt: skip
_DOC_LINES = (
    "On day {d} {p} inspected the {a} and counted {n} {i}.",
    "{p} moved {n} {i} from the {a} to the {b} before noon.",
    "The {a} asked for more {i}, and {p} promised a delivery by day {d}.",
    "Rain on day {d} soaked {n} {i} at the {a}; {p} logged the damage.",
    "{p} and {q} spent the afternoon sorting {i} at the {a}.",
    "According to {p}, nothing unusual happened at the {a} on day {d}.",
    "{p} repaired {n} of the {i} at the {a} and sent the rest to the {b}.",
    "The weekly meeting at the {a} was short; {p} read out the list of {i}.",
)
# (key, fact, decoy, question, the part of a value an answer must name).
# Filler never mentions an archive, the observatory, the boathouse or a
# lantern, and its numbers stay under 31: each answer is in the text once.
_FACTS = (
    ("door_code", "The door code for the west archive is {}.", "The door code for the east archive is {}.", "What is the door code for the west archive?", str),
    ("spare_key", "The only spare key to the observatory is held by {}.", "The spare key to the boathouse is held by {}.", "Who holds the spare key to the observatory?", lambda name: name.split()[-1]),
    ("lanterns", "The final stocktake found exactly {} brass lanterns in the lighthouse store.", "An earlier, incomplete count had listed {} brass lanterns in the lighthouse store.", "According to the final stocktake, how many brass lanterns are in the lighthouse store?", str),
)  # fmt: skip
# Per size, (value, decoy) for each fact in _FACTS order.
DOC_FACTS = {
    1000: (("cobalt heron", "amber falcon"), ("Wilma Stroud", "Kasper Olin"), (41, 36)),
    3500: (("silver otter", "copper finch"), ("Greta Holm", "Dario Mendes"), (73, 68)),
    8000: (
        ("violet marten", "ochre plover"),
        ("Idris Calloway", "Maren Voss"),
        (128, 112),
    ),
}
# (fact, decoy) depth as a share of the document; the superseded lantern
# count comes before the final one, as it would in a real log.
_DEPTHS = ((0.15, 0.7), (0.5, 0.25), (0.85, 0.4))
_DOC_PROMPT = (
    "Read the document below, then answer the question after it.\n\n"
    "=== DOCUMENT ===\n{doc}\n=== END OF DOCUMENT ===\n\n"
    "Question: {question}\nAnswer with the answer only, no explanation."
)


def approx_tokens(text):
    """A tokenizer-free size: 1.3 per word, one per digit and per mark.

    Digits count singly because Qwen's tokenizer splits numbers into them.
    An estimate only -- every row records the lane's own prompt_tokens.
    """
    letters = len(re.findall(r"[^\W\d_]+", text))
    digits = sum(c.isdigit() for c in text)
    return round(1.3 * letters + digits + len(re.findall(r"[^\w\s]", text)))


def _filler_sentence(rng):
    p, q = rng.sample(_DOC_PEOPLE, 2)
    a, b = rng.sample(_DOC_PLACES, 2)
    i, d, n = rng.choice(_DOC_ITEMS), rng.randint(1, 30), rng.randint(2, 19)
    return rng.choice(_DOC_LINES).format(p=p, q=q, a=a, b=b, i=i, d=d, n=n)


def document(nominal):
    """The same document for a size on every run: seeded filler, placed facts."""
    rng = random.Random(nominal)  # nosec B311  # noqa: S311 -- fixed filler, not a secret
    placed = []
    for fact, (value, wrong), depths in zip(
        _FACTS, DOC_FACTS[nominal], _DEPTHS, strict=True
    ):
        placed += [
            (depths[0], fact[1].format(value)),
            (depths[1], fact[2].format(wrong)),
        ]
    budget = DOC_FILL * nominal - approx_tokens(_DOC_TITLE)
    budget -= sum(approx_tokens(sentence) for _, sentence in placed)
    lines, used = [], 0
    while used < budget:
        lines.append(_filler_sentence(rng))
        used += approx_tokens(lines[-1])
    for depth, sentence in sorted(placed, reverse=True):
        lines.insert(round(depth * len(lines)), sentence)
    blocks = [" ".join(lines[i : i + 5]) for i in range(0, len(lines), 5)]
    return f"{_DOC_TITLE}\n\n" + "\n\n".join(blocks)


def doc_cases():
    cases = []
    for nominal, label in DOC_SIZES.items():
        text = document(nominal)
        for fact, (value, wrong) in zip(_FACTS, DOC_FACTS[nominal], strict=True):
            key, question, named = fact[0], fact[3], fact[4]
            prompt = _DOC_PROMPT.format(doc=text, question=question)
            checks = (("contains", [named(value)]), ("forbid", [named(wrong)]))
            case = _case(f"doc_{label}_{key}", prompt, *checks)
            cases.append(dict(case, category=f"doc_{label}", doc_tokens=nominal))
    return cases


def _tagged(cases, category):
    return [dict(case, category=category) for case in cases]


CASES = (
    _tagged(INSTRUCTION_CASES, "instruction")
    + _tagged(JSON_CASES, "json")
    + _tagged(MULTI_CASES, "multiturn")
    + doc_cases()
)

# ── the run ──────────────────────────────────────────────────────────────────

# bench_coding's rule for a 4xx that says the prompt did not fit; it cannot be
# imported from there, because bench_coding needs the POSIX-only `resource`.
_OVERFLOW_BODY = re.compile(
    r"context|too (?:long|many tokens)|(?:context|prompt|input)[^.]{0,40}exceed"
    r"|max(?:imum)?_? ?(?:tokens|length)|(?:prompt|input|request) (?:is )?too",
    re.I,
)
_NOT_OVERFLOW_BODY = re.compile(r"rate.?limit|quota|billing", re.I)


def overflow_reason(exc):
    """The body of a 4xx refusing a prompt too long for the context, or None."""
    if not isinstance(exc, urllib.error.HTTPError) or exc.code == 429:
        return None
    if not 400 <= exc.code < 500:
        return None
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:
        body = ""
    if _NOT_OVERFLOW_BODY.search(body) or not _OVERFLOW_BODY.search(body):
        return None
    return f"HTTP {exc.code}: {body[:160]}"


def ask(base_url, model, messages, max_tokens, entry=None, timeout=900):
    """One non-streamed chat request -> (response body, seconds)."""
    body = {"model": model, "messages": messages, "max_tokens": max_tokens}
    body.update(temperature=0, stream=False)
    url, started = f"{base_url}/v1/chat/completions", time.monotonic()
    with bench_cli.post_json(url, body, entry=entry, timeout=timeout) as r:
        data = r.json()
    if not isinstance(data, dict):
        raise TypeError(f"reply is not a JSON object: {str(data)[:80]}")
    return data, time.monotonic() - started


def case_messages(case):
    return case.get("messages") or [{"role": "user", "content": case["prompt"]}]


def grade_reply(case, body, max_tokens):
    """The graded fields of one reply: thinking stripped, a cut not graded."""
    reply = from_body(body)
    usage = reply.usage or {}
    completion = usage.get("completion_tokens")
    answer = split_answer(reply.content, reply.reasoning)[1].strip()
    blob = json.dumps([reply.content, reply.reasoning]).encode()
    fields = {
        "finish_reason": reply.finish_reason,
        "answered": accounting(reply, completion, max_tokens)["answered"],
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": completion,
        "reply_sha256": hashlib.sha256(blob).hexdigest(),
        "answer_preview": answer[:160],
    }
    cut = reply.finish_reason == "length" or (
        reply.finish_reason is None and bool(completion) and completion >= max_tokens
    )
    if cut:
        detail = f"CUT at {completion} tokens (budget {max_tokens}) - not graded"
        return dict(fields, truncated=True, detail=detail)
    passed, detail = grade(answer, case["checks"])
    return dict(fields, passed=passed, detail=detail)


def run_case(base_url, model, case, attempt, max_tokens, entry=None):
    """One attempt at one case -> its result row."""
    row = {"case": case["name"], "attempt": attempt, "category": case["category"]}
    row.update(dict.fromkeys(("passed", "errored", "overflow", "truncated"), False))
    row.update({"wall_s": None, "doc_tokens": case.get("doc_tokens")})
    try:
        body, wall = ask(base_url, model, case_messages(case), max_tokens, entry)
    except Exception as e:
        overflow = overflow_reason(e)
        if overflow:  # the model never saw the case: unmeasured, and named
            return dict(
                row, overflow=True, detail=f"OVERFLOW ({overflow}) - not graded"
            )
        # A transport failure is ours, not the model's: out of the rate.
        return dict(
            row, errored=True, detail=f"request failed: {type(e).__name__}: {e}"[:300]
        )
    return dict(row, **grade_reply(case, body, max_tokens), wall_s=round(wall, 2))


def measured(row):
    return not (row["errored"] or row["overflow"] or row["truncated"])


def _verdict(row):
    flags = (("errored", "ERROR"), ("overflow", "OVERFLOW"), ("truncated", "CUT"))
    word = next((word for flag, word in flags if row[flag]), None)
    return word or ("PASS" if row["passed"] else "FAIL")


def category_counts(results):
    """Per category: attempts passed/total, rows not graded, cases passed/cases."""
    cats, outcomes = {}, {}
    for r in results:
        c = cats.setdefault(r["category"], dict.fromkeys(("passed", "total", "excluded", "cases_passed", "cases"), 0))  # fmt: skip
        if not measured(r):
            c["excluded"] += 1
            continue
        c["total"] += 1
        c["passed"] += int(bool(r["passed"]))
        outcomes.setdefault((r["category"], r["case"]), set()).add(bool(r["passed"]))
    for (category, _), seen in outcomes.items():
        cats[category]["cases"] += 1
        cats[category]["cases_passed"] += int(seen == {True})
    return dict(sorted(cats.items()))


def summarise(results, repeats):
    """The report's counts; determinism from the reply hashes, as bench_tools."""
    rows = [r for r in results if measured(r)]
    passed = sum(1 for r in rows if r["passed"])
    hashes, outcomes = {}, {}
    for r in rows:
        hashes.setdefault(r["case"], set()).add(r["reply_sha256"])
        outcomes.setdefault(r["case"], set()).add(bool(r["passed"]))
    same = repeats > 1 and bool(hashes) and all(len(v) == 1 for v in hashes.values())
    agreed = (
        repeats > 1 and bool(outcomes) and all(len(v) == 1 for v in outcomes.values())
    )
    # Repeats that agree are one observation of the case, not `repeats` of them.
    n, k = len(rows), passed
    if same or agreed:
        n, k = len(outcomes), sum(1 for v in outcomes.values() if v == {True})
    walls = [r["wall_s"] for r in rows]
    counts = {
        f: sum(1 for r in results if r[f]) for f in ("errored", "overflow", "truncated")
    }
    return {
        "passed": passed,
        "total": len(rows),
        **counts,
        "deterministic": same,
        "repeats_agreed": agreed,
        "effective_n": n,
        "effective_k": k,
        "categories": category_counts(results),
        "total_wall_s": round(sum(walls), 2),
        "wall_measured_s": round(sum(walls), 2),
        "avg_wall_s": round(sum(walls) / len(walls), 2) if walls else None,
        "median_wall_s": round(statistics.median(walls), 2) if walls else None,
        "stdev_wall_s": round(statistics.stdev(walls), 2) if len(walls) > 1 else None,
    }


_NOT_GRADED = (
    ("overflow", "OVERFLOW (did not fit the context)"),
    ("truncated", "CUT (no answer inside max_tokens)"),
    ("errored", "EXCLUDED (transport errors)"),
)


def print_summary(report):
    by_case = report["deterministic"] or report["repeats_agreed"]
    skipped = [f"{report[key]} {name}" for key, name in _NOT_GRADED if report[key]]
    score = format_score(report["effective_k"], report["effective_n"])
    line = f"    -> {score} {'cases' if by_case else 'attempts'}"
    line += f", not graded: {', '.join(skipped)}" if skipped else ""
    print(f"{line}, {report['total_wall_s']:.1f}s", flush=True)
    for name, c in report["categories"].items():
        k, n = (c["cases_passed"], c["cases"]) if by_case else (c["passed"], c["total"])
        extra = f"  (+{c['excluded']} not graded)" if c["excluded"] else ""
        print(f"       {name:12s} {format_score(k, n):>24s}{extra}", flush=True)
    if report["truncated"]:
        print(
            "       raise --max-tokens until nothing is CUT, then compare", flush=True
        )


def _print_row(row, suffix):
    wall = f"{row['wall_s']:6.2f}s" if row["wall_s"] is not None else "      -"
    detail = "" if row["passed"] else row.get("detail", "")[:70]
    print(
        f"    {row['case']:26s}{suffix} {_verdict(row):8s} {wall}  {detail}", flush=True
    )


def evaluate(
    base_url,
    model,
    label,
    repeats=1,
    max_tokens=DEFAULT_MAX_TOKENS,
    warmup=True,
    entry=None,
    backend=None,
    cases=None,
):
    """Every case against one endpoint -> one report row.

    `entry` is the backends.json entry (auth, headers, request_extra) and
    `backend` its registry name, kept so a ranking can find the control.
    """
    print(f"\n  === {label} ===", flush=True)
    if warmup:  # otherwise the first case carries the model's load time
        try:
            ready = case_messages({"prompt": "Reply with the single word: ready."})
            ask(base_url, model, ready, 16, entry)
        except Exception as e:
            print(f"    (warmup failed: {type(e).__name__})", flush=True)
    results = []
    for case in CASES if cases is None else cases:
        for attempt in range(repeats):
            if attempt:
                # Never an identical follow-up: GenieX answers one along a
                # cache path that changes the reply (client.spacer).
                bench_cli.spacer(base_url, model, entry)
            results.append(run_case(base_url, model, case, attempt, max_tokens, entry))
            _print_row(
                results[-1], f" [{attempt + 1}/{repeats}]" if repeats > 1 else ""
            )
    report = {
        "label": label,
        "model": model,
        "backend": backend,
        "max_tokens": max_tokens,
    }
    report.update(summarise(results, repeats), results=results)
    print_summary(report)
    return report


def print_ranking(reports, suspect):
    """Rate over measured cases, then coverage, then time; ties by the sign test."""
    from bench_compare import case_outcomes, is_control

    print("\n" + "=" * 70 + "\n  RANKING — chat cases passed, then time\n" + "=" * 70)
    if suspect:
        print(f"  SUSPECT (the control fails them too, excluded): {', '.join(suspect)}")
    contenders = [r for r in reports if not is_control(r)] or reports
    ranked = sorted(
        contenders,
        key=lambda x: (
            -x["passed"] / (x["total"] or 1),
            -x["total"],
            x["total_wall_s"],
        ),
    )
    tiered = tiers(ranked, case_outcomes)
    for i, group in enumerate(tiered):
        if i:
            print(f"  {'-' * 40} tier {i + 1} " + "-" * 20)
        for r in group:
            score = format_score(r["effective_k"], r["effective_n"])
            print(f"  {r['label'][:40]:40s} {score:>24s} {r['total_wall_s']:8.1f}s")
    if len(tiered) < len(ranked):
        print(
            "  rows within one tier are NOT separated by the paired sign test: a tie."
        )


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    for flag in ("--backend", "--base-url", "--model", "--label", "--output"):
        ap.add_argument(flag, default=None)
    ap.add_argument("--compare", default=None, help="a candidates JSON file")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help="per reply; a reply cut before its answer finished is CUT, not graded",
    )
    ap.add_argument(
        "--category",
        action="append",
        choices=sorted({c["category"] for c in CASES}),
        help="run only this category (repeatable); the report records which ran",
    )
    ap.add_argument("--no-warmup", action="store_true")
    return ap.parse_args(argv)


def _write(args, candidates, reports, cases, start, tool_files):
    # One copy of the probe for the lab: bench_tools' already hands the
    # provenance temperature, seed and the spaced two-draw determinism probe.
    from bench_tools import _determinism_extra

    extra = _determinism_extra(candidates)
    sizes = {label: approx_tokens(document(n)) for n, label in DOC_SIZES.items()}
    config = {
        "repeats": args.repeats,
        # Repeats of one case are separated by a throwaway request.
        "repeat_spacer": True,
        "warmup": not args.no_warmup,
        "max_tokens": args.max_tokens,
        "categories": sorted({c["category"] for c in cases}),
        "cases": len(cases),
        "doc_tokens_estimated": sizes,
        "backend_entry": {
            c["label"]: bench_cli.entry_config(c["entry"]) for c in candidates
        },
    }
    base_url = candidates[0]["base_url"] if candidates else None
    # `start` (run_start) names a source edited mid-run and records the load.
    bench_cli.write_report(
        args.output,
        "bench_chat",
        config,
        reports,
        base_url,
        tool_files,
        extra=extra,
        run_start=start,
    )
    print(f"  Report written to {args.output}")


def main(argv=None):
    bench_cli.utf8_stdio()
    args = parse_args(argv)
    from compare_suspect import is_control, mark_suspect_cases, suspect_tool_files
    from orchestrant.benchmark.openai_api import resolve_backend, resolve_backend_entry

    cases = [c for c in CASES if not args.category or c["category"] in args.category]
    candidates = bench_cli.candidate_rows(args, resolve_backend, resolve_backend_entry)
    tool_files = TOOL_FILES + suspect_tool_files(candidates)
    start = bench_cli.run_start(
        tool_files, candidates[0]["base_url"] if candidates else None
    )
    reports = [
        evaluate(
            c["base_url"],
            c["model"],
            c["label"],
            repeats=args.repeats,
            max_tokens=args.max_tokens,
            warmup=not args.no_warmup,
            entry=c["entry"],
            backend=c["backend"],
            cases=cases,
        )
        for c in candidates
    ]
    # A case the CONTROL endpoint also fails is evidence about the case.
    suspect = mark_suspect_cases(reports)
    for report in (r for r in reports if suspect and not is_control(r)):
        # That re-derives `categories` as a bare passed/total pair; this tool's
        # also count the rows nobody graded (a doc_8k OVERFLOW) and the cases.
        kept = [r for r in report["results"] if not r.get("suspect")]
        report["categories"] = category_counts(kept)
    if args.output:
        _write(args, candidates, reports, cases, start, tool_files)
    # Ranking last: it only prints, and must never cost a written report.
    if len(reports) > 1:
        print_ranking(reports, suspect)
    return 0


if __name__ == "__main__":
    sys.exit(main())
