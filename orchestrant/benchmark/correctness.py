"""The speed runner's correctness probe (LB1): its prompts, their kinds, its verdict.

Speed metrics alone cannot tell a working model from a broken one: a model
emitting fluent nonsense scores EXCELLENT tokens/sec. GenieX's i-quant kernels
did exactly that (v0.5.0 and v0.6.1 alike: blank lines and dots,
' majorityathersyre...'), rated a good run by every throughput metric (see
third_party/ANTfrastructure/docs/geniex-local-ai-setup.md). So: prompts whose
answers can be CHECKED, not eyeballed.

Two kinds of item, because a wrong answer means two different things:

  integrity   an answer any working instruct model of this size gets. A miss
              is what a broken kernel or a bad quant looks like, and only
              these decide OK / DEGRADED / BROKEN, the --correctness-only exit
              code and upgrade_check's speed-step verdict.
  capability  tokenisation, trick reasoning, trivia. A miss is the model's.
              The 2026-09-24 campaign (benchmark_results/2026-09-24-roadmap,
              *speed-answer.json) measured it on healthy lanes: the Qwen3
              instruct 4B and Qwen2.5-Coder-7B miss only the strawberry count,
              Llama-3.2-3B also the 5-machines puzzle and 9.9 vs 9.11,
              Phi-4-mini the strawberry count, 9.9 vs 9.11 and Canberra
              (Sydney); the thinking Qwen3-4B and Qwen3-8B get all six. Every
              one of them answered both arithmetic items. Recorded and printed
              apart, never a kernel verdict -- but on ONE model a capability
              item that moved is still news (bench_compare lists it): Qwen3-4B
              at Q2_K lost two reasoning items where Q4_0 lost none.

The four integrity items after the first two are UNVERIFIED: chosen to be one
step each (a borrow, a pattern in the prompt, a fact with no famous wrong
answer, a word copied back out of the context), but no model had been asked
them when they were added (2026-09-25). The first --correctness run on each
lane is their check.
"""

import re
from typing import NamedTuple


INTEGRITY = "integrity"
CAPABILITY = "capability"
KINDS = (INTEGRITY, CAPABILITY)
NO_RESULT = "NO RESULT"
COUNT_FIELDS = ("score", "total", "wrong", "truncated", "errors")
# Items record the first 60 characters of their prompt: the key that ties an
# old report's item to its kind here, and bench_compare's case key.
PREVIEW = 60


class Probe(NamedTuple):
    kind: str
    prompt: str
    accepted: list[str]


# Matching is case-insensitive on the FINAL answer only (anything after
# </think> is stripped first) and anchored on word boundaries, so "3" does not
# match inside "13". Each entry's first accepted form is the recorded one.
CORRECTNESS_PROBES = [
    # Deliberately a SMALL multiplication. An earlier version used 847*293,
    # which a healthy 4B could not finish inside 4000 tokens of thinking -- so
    # the probe reported INCONCLUSIVE on a perfectly good model. A check that
    # cries wolf on healthy models is a check people learn to ignore.
    Probe(INTEGRITY, "What is 23 * 17? Reply with only the number.", ["391"]),
    # Trivia with a famous wrong answer: Phi-4-mini says Sydney.
    Probe(
        CAPABILITY,
        "What is the capital of Australia? Reply with only the city name.",
        ["canberra"],
    ),
    # Tokenisation: no non-thinking model on 2026-09-24 counted it, and one
    # Q4_0 file answered 5 on the GenieX CPU lane and 4 on its GPU lane.
    Probe(
        CAPABILITY,
        "How many times does the letter 'r' appear in the word strawberry? "
        "Reply with only the digit.",
        ["3"],
    ),
    # Trick reasoning: Llama-3.2-3B says 25.
    Probe(
        CAPABILITY,
        "If 5 machines make 5 widgets in 5 minutes, how many minutes do 100 "
        "machines need to make 100 widgets? Reply with only the number.",
        ["5"],
    ),
    Probe(INTEGRITY, "What is 17 squared? Reply with only the number.", ["289"]),
    # Trick comparison: Llama-3.2-3B and Phi-4-mini say 9.11.
    Probe(
        CAPABILITY,
        "Which number is larger, 9.11 or 9.9? Reply with only the number.",
        ["9.9"],
    ),
    # Unverified (module docstring). Each asks for a bare answer, so a
    # non-thinking model spends a few tokens on it.
    Probe(INTEGRITY, "What is 100 minus 37? Reply with only the number.", ["63"]),
    Probe(
        INTEGRITY,
        "What number comes next: 2, 4, 6, 8? Reply with only the number.",
        ["10"],
    ),
    Probe(
        INTEGRITY,
        "How many days are in one week? Reply with only the number.",
        ["7", "seven"],
    ),
    Probe(
        INTEGRITY,
        "Remember the word pelican. Which word did I ask you to remember? "
        "Reply with only the word.",
        ["pelican"],
    ),
]

_KIND_BY_PREVIEW = {p.prompt[:PREVIEW]: p.kind for p in CORRECTNESS_PROBES}
_CUT_PREVIEW = "<truncated inside <think>, raise --correctness-max-tokens>"


def _answer_matches(content, accepted):
    """Does the model's FINAL answer contain one of the accepted strings?

    Two deliberate choices, both learned from a probe that scored false
    positives: strip any <think> block first (a reasoning model often states
    and then discards a wrong intermediate value), and anchor on word
    boundaries so "3" does not match inside "13" or "0.31".
    """
    # A reasoning model that never CLOSED its <think> block ran out of budget
    # before answering. Searching the thinking text would score a discarded
    # intermediate value as a correct answer -- the exact false positive this
    # function exists to prevent. No final answer means not correct.
    if "<think>" in content and "</think>" not in content:
        return False

    answer = content.split("</think>")[-1] if "</think>" in content else content
    # Bias to the end: the final answer is what counts, not a mid-stream aside.
    answer = answer[-400:].lower().replace(",", "").replace("*", "")
    for exp in accepted:
        # Trailing rule: a sentence-ending "." must NOT break the match
        # ("248,171." -> "248171."), but ".<digit>" must, so "3" does not
        # match inside "3.5". Leading rule blocks "13" and "0.31".
        if re.search(rf"(?<![\w.]){re.escape(exp.lower())}(?!\w)(?!\.\d)", answer):
            return True
    return False


def graded_item(probe, content):
    """One answered probe, as the report's `items` record it."""
    truncated = "<think>" in content and "</think>" not in content
    answer = content.split("</think>")[-1].strip()
    return {
        "prompt": probe.prompt[:PREVIEW],
        "kind": probe.kind,
        "expected": probe.accepted[0],
        "answer_preview": _CUT_PREVIEW if truncated else answer[:80],
        "truncated": truncated,
        "correct": _answer_matches(content, probe.accepted),
    }


def errored_item(probe, error):
    """One probe the endpoint never answered."""
    return {
        "prompt": probe.prompt[:PREVIEW],
        "kind": probe.kind,
        "expected": probe.accepted[0],
        "error": str(error)[:120],
        "correct": False,
    }


def counts(items):
    """score/total/wrong/truncated/errors over `items`.

    Truncation is NOT incorrectness. A model cut off mid-thought did not
    answer wrongly -- we failed to measure it. Conflating the two makes a
    healthy model look degraded, which is the fastest way to teach someone
    to ignore this check. Count them apart and say which happened.
    """
    errors = sum(1 for i in items if "error" in i)
    return {
        "score": sum(1 for i in items if i.get("correct")),
        "total": len(items),
        "wrong": sum(
            1
            for i in items
            if not i.get("correct") and not i.get("truncated") and "error" not in i
        ),
        "truncated": sum(1 for i in items if i.get("truncated")),
        "errors": errors,
    }


def verdict(gate):
    """OK / INCONCLUSIVE / DEGRADED / BROKEN over integrity counts.

    NO RESULT when not one integrity answer came back.
    """
    if not gate or gate.get("total", 0) <= gate.get("errors", 0):
        return NO_RESULT
    if not gate.get("wrong"):
        return "INCONCLUSIVE" if gate.get("truncated") else "OK"
    return "BROKEN" if gate["wrong"] >= gate["total"] / 2 else "DEGRADED"


def summarise(items):
    """The report's correctness block, or None when every request errored.

    A superset of the block before kinds: score/total/wrong/truncated/errors
    still count EVERY item (the viewer and older readers sum them), and
    `integrity`, `capability` and `verdict` say which answers decide.
    """
    if all("error" in i for i in items):
        return None
    block = {**counts(items), "items": items}
    for kind in KINDS:
        group = [i for i in items if i.get("kind") == kind]
        if group:
            block[kind] = counts(group)
    block["verdict"] = verdict(block.get(INTEGRITY))
    return block


def kind_of(item):
    """An item's recorded kind, else its prompt's kind in today's table.

    An item the table no longer has counts as integrity: every answer did
    before kinds existed.
    """
    return item.get("kind") or _KIND_BY_PREVIEW.get(item.get("prompt"), INTEGRITY)


def by_kind(block):
    """{kind: counts} for a correctness block of any age; None without one.

    A block that recorded its kinds is read as written. An older one is split
    by prompt: the kind belongs to the question, not to the run, and the six
    prompts every tracked report asked are unchanged. A block with no items
    at all is judged whole, as before.
    """
    if not block:
        return None
    if INTEGRITY in block:
        return {kind: block[kind] for kind in KINDS if kind in block}
    items = block.get("items") or []
    if not items:
        return {INTEGRITY: {field: block.get(field, 0) for field in COUNT_FIELDS}}
    groups = {}
    for item in items:
        groups.setdefault(kind_of(item), []).append(item)
    return {kind: counts(group) for kind, group in groups.items()}


def integrity(block):
    """The counts the verdict is taken over, or None when there are none."""
    return (by_kind(block) or {}).get(INTEGRITY)


def outcomes(block, kind):
    """{prompt preview: correct} for the MEASURED items of `kind`.

    Truncated and errored items were not measured, so they are left out.
    """
    return {
        item.get("prompt"): bool(item.get("correct"))
        for item in (block or {}).get("items") or []
        if kind_of(item) == kind and "error" not in item and not item.get("truncated")
    }


def annotate(block):
    """`block` plus the kinds and the verdict an older report never recorded.

    For the viewer, which cannot import this table: the manifest carries each
    item's kind and both kinds' counts, so an old report's banner judges the
    same answers a new one does. A block that recorded them is returned as is.
    """
    kinds = by_kind(block)
    if kinds is None or INTEGRITY in block:
        return block
    items = [{**item, "kind": kind_of(item)} for item in block.get("items") or []]
    return {
        **block,
        **kinds,
        "items": items,
        "verdict": verdict(kinds.get(INTEGRITY)),
    }


def exit_code(block):
    """--correctness-only's exit status, over the integrity items only.

    1: unreachable, or an integrity answer wrong -- act on it. 2: an integrity
    answer cut off -- re-run with a bigger budget. 0 otherwise, capability
    misses included: they are the model's, not the lane's.
    """
    return {"OK": 0, "INCONCLUSIVE": 2}.get(verdict(integrity(block)), 1)


def _notes(block, gate, skill):
    """The NOTE lines under the item list."""
    lines = []
    truncated = block.get("truncated", 0)
    if truncated:
        lines += [
            f"    NOTE: {truncated} probe(s) ran out of tokens before answering. That is a",
            "          MEASUREMENT limit, not a model fault -- raise --correctness-max-tokens.",
        ]
    if gate.get("wrong"):
        lines += [
            "    NOTE: a wrong INTEGRITY answer means broken kernels or an over-aggressive",
            "          quant, not a slow model. Check the GGUF tensor types before tuning speed.",
        ]
    elif skill and skill.get("wrong"):
        lines += [
            "    NOTE: capability misses (tokenisation, trick reasoning, trivia) are the",
            "          model's: compare with its own earlier report, not with a full score.",
        ]
    return lines


def print_correctness(block):
    """Render the LB1 probe. A model can be fast and wrong; show both."""
    print()
    kinds = by_kind(block) or {}
    gate = kinds.get(INTEGRITY)
    if gate is None:
        print("  Correctness probe: NO RESULT -- endpoint unreachable")
        return
    cut = f", {gate['truncated']} truncated" if gate.get("truncated") else ""
    print(
        f"  Correctness probe: integrity {gate['score']}/{gate['total']} correct"
        f"{cut}  [{verdict(gate)}]"
    )
    skill = kinds.get(CAPABILITY)
    if skill:
        print(
            f"    capability: {skill['score']}/{skill['total']} -- not a kernel verdict"
        )
    for item in block.get("items") or []:
        kind = kind_of(item)
        if "error" in item:
            print(f"    ERR  {kind:<10} {item['prompt'][:42]:<42} {item['error'][:40]}")
            continue
        mark = "ok " if item["correct"] else "XX "
        got = item.get("answer_preview", "")[:44]
        print(f"    {mark}  {kind:<10} expected={item['expected']:<9} got={got!r}")
    for line in _notes(block, gate, skill):
        print(line)
