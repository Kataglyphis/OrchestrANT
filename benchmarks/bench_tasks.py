#!/usr/bin/env python3
"""The extended coding task set.

Six tasks could not carry a ranking: at n=6 the smallest provable drop is
100% -> 17%, so the coding column of a full sweep separated nobody while the
27-case tool column separated the field cleanly. These 21 bring it to 27.

Each task ships its own REFERENCE and WRONG solutions beside it, not in the
test file. They are never sent to a model — the benchmark shows only `prompt`.
They live here so they cannot drift away from the task they describe, and
tests/test_bench_coding_tasks.py enforces both directions: the reference must
PASS (an unsolvable task makes every model look incapable while measuring
nothing) and the wrong solution must FAIL (weak tests pass wrong answers, which
is how `return sorted(a + b)` survived in the merge task).

Authored and adversarially verified 2026-08-31; every rule the tests check is
stated in the prompt, because a model cannot be marked wrong for a rule it was
never told.
"""

EXTENDED_TASKS = [
    {
        "name": "strings_normalize_tag",
        "kind": "spec-transcription",
        "lang": "python",
        "stdlib_only": True,
        "prompt": """You are writing the slug helper for a tagging system.

Write a Python function with this exact signature:
    def normalize_tag(label: str) -> str

Apply these steps in exactly this order:
1. Remove leading and trailing whitespace from `label`.
2. Convert the result to lowercase.
3. Replace every character that is not one of the 26 ASCII letters `a`-`z` or the 10 digits `0`-`9` with a single hyphen `-`. Spaces, underscores, punctuation and accented letters such as `e` with an acute accent all count as "not allowed", and each one of them becomes exactly one hyphen.
4. Collapse every run of two or more consecutive hyphens into a single hyphen.
5. Remove all leading and trailing hyphens from the result.
6. If the string is empty at this point, return the literal string "untitled". Otherwise return the string.

Example: normalize_tag("  Hello World  ") returns "hello-world".

Use only the Python standard library. Reply with the function in a single ```python code block and nothing else.""",
        "tests": """assert normalize_tag("  Hello World  ") == "hello-world"
assert normalize_tag("Python__Rocks!!") == "python-rocks"
assert normalize_tag("---a---b---") == "a-b"
assert normalize_tag("C++ 101") == "c-101"
assert normalize_tag("2024") == "2024"
assert normalize_tag("a") == "a"
assert normalize_tag("MiXeD   CaSe\\tTag") == "mixed-case-tag"
assert normalize_tag("Ünïcode") == "n-code"
assert normalize_tag("") == "untitled"
assert normalize_tag("   ") == "untitled"
assert normalize_tag("!!!") == "untitled"
assert normalize_tag("-") == "untitled\"""",
        "reference": """def normalize_tag(label: str) -> str:
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789"
    s = label.strip().lower()
    s = "".join(ch if ch in allowed else "-" for ch in s)
    while "--" in s:
        s = s.replace("--", "-")
    s = s.strip("-")
    return s if s else "untitled\"""",
        "wrong": """def normalize_tag(label: str) -> str:
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789"
    s = label.strip().lower()
    s = "".join(ch if ch in allowed else "-" for ch in s)
    while "--" in s:
        s = s.replace("--", "-")
    return s if s else "untitled\"""",
        "wrong_explanation": 'It never strips the leading/trailing hyphens produced in step 3, so "Python__Rocks!!" yields "python-rocks-" and "!!!" yields "-" instead of "untitled".',
    },
    {
        "name": "strings_split_steps",
        "kind": "spec-transcription",
        "lang": "python",
        "stdlib_only": True,
        "prompt": """A recipe app stores an instruction list as one string in which steps are separated by the exact three-character marker `-->` (hyphen, hyphen, greater-than sign).

Write a Python function with this exact signature:
    def split_steps(text: str) -> list[str]

Rules:
- Split `text` on every occurrence of the exact marker `-->`. Only that exact three-character sequence separates steps; sequences such as `->`, `- >` or `==>` are ordinary text and must not split anything.
- Clean each resulting piece: remove leading and trailing whitespace, and replace every run of whitespace inside it (spaces, tabs, newlines) with a single space.
- Discard any piece that is the empty string after cleaning.
- Return the surviving cleaned pieces as a list, in the order they appeared in `text`.
- If `text` contains no marker, it is a single piece and the same cleaning rules apply.
- If nothing survives, return an empty list. The function must never raise.

Example: split_steps("wash  --> dry --> fold") returns ["wash", "dry", "fold"].

Use only the Python standard library. Reply with the function in a single ```python code block and nothing else.""",
        "tests": """assert split_steps("wash --> dry --> fold") == ["wash", "dry", "fold"]
assert split_steps("a-->b") == ["a", "b"]
assert split_steps("only one") == ["only one"]
assert split_steps("") == []
assert split_steps("      ") == []
assert split_steps("-->") == []
assert split_steps("a -->  --> b") == ["a", "b"]
assert split_steps("-->x-->") == ["x"]
assert split_steps("hold\\tthe   line") == ["hold the line"]
assert split_steps("a - > b") == ["a - > b"]
assert split_steps("x---> y") == ["x-", "y"]
assert split_steps("  step one  -->\\n step two \\n") == ["step one", "step two"]""",
        "reference": """def split_steps(text: str) -> list[str]:
    out = []
    for piece in text.split("-->"):
        cleaned = " ".join(piece.split())
        if cleaned:
            out.append(cleaned)
    return out""",
        "wrong": """def split_steps(text: str) -> list[str]:
    return [" ".join(piece.split()) for piece in text.split("-->")]""",
        "wrong_explanation": 'It cleans every piece but never discards the empty ones, so "" returns [""] and "a -->  --> b" returns ["a", "", "b"].',
    },
    {
        "name": "strings_dominant_case",
        "kind": "spec-transcription",
        "lang": "python",
        "stdlib_only": True,
        "prompt": """Write a Python function with this exact signature:
    def dominant_case(words: list[str]) -> str

A "letter" is any character `c` for which `c.isalpha()` is True. For each string in `words`, look only at its letters (digits, spaces and punctuation never affect the classification) and let:
- has_upper = the string has at least one letter `c` with `c.isupper()` True
- has_lower = the string has at least one letter `c` with `c.islower()` True

Classify each string into exactly one bucket:
- If the string has no letters at all, ignore it completely; it counts toward nothing.
- Otherwise, if has_upper and has_lower are both True, it counts as "mixed".
- Otherwise, if has_upper is True, it counts as "upper".
- Otherwise, it counts as "lower".

Return the name of the bucket holding the most strings, as one of the strings "upper", "lower" or "mixed".
- Tie-break: if two or three buckets are tied for the largest count, return whichever of the tied buckets comes first in the fixed order "upper", "lower", "mixed".
- If every string was ignored, including when `words` is empty, return "none".

Example: dominant_case(["ABC", "abc"]) returns "upper", because the 1-1 tie is broken in favour of "upper".

Use only the Python standard library. Reply with the function in a single ```python code block and nothing else.""",
        "tests": """assert dominant_case([]) == "none"
assert dominant_case(["123", "!!!", "  "]) == "none"
assert dominant_case(["ABC", "abc"]) == "upper"
assert dominant_case(["abc", "Abc"]) == "lower"
assert dominant_case(["Abc", "aBc", "abc", "ABC"]) == "mixed"
assert dominant_case(["x"]) == "lower"
assert dominant_case(["A1!", "B2?"]) == "upper"
assert dominant_case(["7", "8", "hi"]) == "lower"
assert dominant_case(["hello world", "OK"]) == "upper"
assert dominant_case(["A", "b", "42"]) == "upper"
assert dominant_case(["McDonald", "iPhone", "PLAIN"]) == "mixed"
assert dominant_case(["ready", "set", "GO", "go"]) == "lower\"""",
        "reference": """def dominant_case(words: list[str]) -> str:
    counts = {"upper": 0, "lower": 0, "mixed": 0}
    for word in words:
        letters = [c for c in word if c.isalpha()]
        if not letters:
            continue
        has_upper = any(c.isupper() for c in letters)
        has_lower = any(c.islower() for c in letters)
        if has_upper and has_lower:
            counts["mixed"] += 1
        elif has_upper:
            counts["upper"] += 1
        else:
            counts["lower"] += 1
    best = max(counts.values())
    if best == 0:
        return "none"
    for name in ("upper", "lower", "mixed"):
        if counts[name] == best:
            return name""",
        "wrong": """def dominant_case(words: list[str]) -> str:
    counts = {"upper": 0, "lower": 0, "mixed": 0}
    for word in words:
        if word.isupper():
            counts["upper"] += 1
        elif word.islower():
            counts["lower"] += 1
        else:
            counts["mixed"] += 1
    if not words:
        return "none"
    best = max(counts.values())
    for name in ("upper", "lower", "mixed"):
        if counts[name] == best:
            return name""",
        "wrong_explanation": 'It relies on str.isupper()/str.islower(), which are both False for letterless strings, so those fall into "mixed" instead of being ignored (e.g. ["123", "!!!", "  "] returns "mixed" instead of "none").',
    },
    {
        "name": "lists_chunk_with_remainder_policy",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": """Write a Python function with this exact signature:
    def chunk_sequence(items: list, size: int, policy: str, fill=None) -> list

It splits `items` into consecutive chunks of length `size`, in order, and returns a list of lists.

Rules:
1. Chunks are taken left to right: the first chunk is the first `size` items, the second chunk the next `size` items, and so on. Every chunk in the result must be a list.
2. If `len(items)` is not an exact multiple of `size`, the final chunk is shorter than `size`. Only in that case does `policy` apply to it:
   - "keep": leave the short final chunk in the result unchanged.
   - "drop": remove the short final chunk from the result entirely.
   - "pad": append copies of `fill` to the short final chunk until its length equals `size`.
3. If the final chunk is already exactly `size` long, `policy` changes nothing: all three policies give the same result.
4. If `items` is empty, return an empty list, whatever the policy is.
5. If `size` is less than or equal to 0, raise ValueError.
6. If `policy` is not one of "keep", "drop", "pad", raise ValueError.
7. Do not modify `items`. The inner lists in the result must be new lists, so mutating them must not affect `items`.

Examples:
    chunk_sequence([1, 2, 3, 4, 5], 2, "keep") == [[1, 2], [3, 4], [5]]
    chunk_sequence([1, 2, 3, 4, 5], 2, "drop") == [[1, 2], [3, 4]]
    chunk_sequence([1, 2, 3, 4, 5], 2, "pad", 0) == [[1, 2], [3, 4], [5, 0]]

Reply with the function in a single ```python code block and nothing else.""",
        "tests": """assert chunk_sequence([1, 2, 3, 4, 5], 2, "keep") == [[1, 2], [3, 4], [5]]
assert chunk_sequence([1, 2, 3, 4, 5], 2, "drop") == [[1, 2], [3, 4]]
assert chunk_sequence([1, 2, 3, 4, 5], 2, "pad", 0) == [[1, 2], [3, 4], [5, 0]]
assert chunk_sequence([1, 2, 3, 4], 2, "drop") == [[1, 2], [3, 4]]
assert chunk_sequence([1, 2, 3, 4], 2, "pad", 0) == [[1, 2], [3, 4]]
assert chunk_sequence([], 3, "pad", 9) == []
assert chunk_sequence([], 3, "drop") == []
assert chunk_sequence([7], 4, "drop") == []
assert chunk_sequence([7], 4, "keep") == [[7]]
assert chunk_sequence([7], 4, "pad") == [[7, None, None, None]]
assert chunk_sequence(["a", "b", "c"], 1, "keep") == [["a"], ["b"], ["c"]]
assert chunk_sequence([-1, -2, -3], 5, "pad", -9) == [[-1, -2, -3, -9, -9]]
src = [1, 2, 3]
out = chunk_sequence(src, 2, "pad", 0)
out[0][0] = 99
assert src == [1, 2, 3]
try:
    chunk_sequence([1, 2], 0, "keep")
    raise AssertionError("expected ValueError")
except ValueError:
    pass
try:
    chunk_sequence([1, 2], -3, "keep")
    raise AssertionError("expected ValueError")
except ValueError:
    pass
try:
    chunk_sequence([1, 2], 2, "trim")
    raise AssertionError("expected ValueError")
except ValueError:
    pass""",
        "reference": """def chunk_sequence(items: list, size: int, policy: str, fill=None) -> list:
    if size <= 0:
        raise ValueError("size must be positive")
    if policy not in ("keep", "drop", "pad"):
        raise ValueError("unknown policy")
    chunks = [list(items[i:i + size]) for i in range(0, len(items), size)]
    if chunks and len(chunks[-1]) < size:
        if policy == "drop":
            chunks.pop()
        elif policy == "pad":
            chunks[-1] = chunks[-1] + [fill] * (size - len(chunks[-1]))
    return chunks""",
        "wrong": """def chunk_sequence(items: list, size: int, policy: str, fill=None) -> list:
    if size <= 0:
        raise ValueError("size must be positive")
    if policy not in ("keep", "drop", "pad"):
        raise ValueError("unknown policy")
    chunks = [list(items[i:i + size]) for i in range(0, len(items), size)]
    if chunks:
        if policy == "drop":
            chunks.pop()
        elif policy == "pad":
            chunks[-1] = chunks[-1] + [fill] * (size - len(chunks[-1]))
    return chunks""",
        "wrong_explanation": 'It applies the "drop" policy to the last chunk unconditionally, so it also deletes a final chunk that is already exactly `size` long.',
    },
    {
        "name": "lists_dedupe_keep_last_casefold",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": """Write a Python function with this exact signature:
    def dedupe_keep_last(words: list) -> list

It removes duplicate strings from `words` and returns a new list.

Rules:
1. Two strings count as duplicates when `a.lower() == b.lower()`. No other normalisation happens: leading and trailing whitespace is significant, so "hi " and "hi" are NOT duplicates. The empty string "" is an ordinary item.
2. For each group of duplicates, keep only the LAST occurrence in `words`, spelled exactly as it appears there. Earlier occurrences are dropped.
3. The kept items appear in the result in the same relative order they have in `words` (that is, ordered by the position of each group's last occurrence).
4. If `words` is empty, return an empty list.
5. If any element of `words` is not a `str`, raise TypeError. Check this for every element before producing any output, so a bad element anywhere causes the error.
6. Do not modify `words`; return a new list.

Examples:
    dedupe_keep_last(["a", "B", "A", "b"]) == ["A", "b"]
    dedupe_keep_last(["Cat", "cat", "CAT"]) == ["CAT"]
    dedupe_keep_last(["x", "y", "X"]) == ["y", "X"]

Reply with the function in a single ```python code block and nothing else.""",
        "tests": """assert dedupe_keep_last([]) == []
assert dedupe_keep_last(["a"]) == ["a"]
assert dedupe_keep_last(["a", "b", "c"]) == ["a", "b", "c"]
assert dedupe_keep_last(["a", "B", "A", "b"]) == ["A", "b"]
assert dedupe_keep_last(["x", "y", "X"]) == ["y", "X"]
assert dedupe_keep_last(["Cat", "cat", "CAT"]) == ["CAT"]
assert dedupe_keep_last(["hi ", "hi", "HI "]) == ["hi", "HI "]
assert dedupe_keep_last(["", "a", ""]) == ["a", ""]
assert dedupe_keep_last(["Zed", "zed"]) == ["zed"]
assert dedupe_keep_last(["one", "TWO", "two", "One", "three"]) == ["two", "One", "three"]
src = ["p", "P", "q"]
assert dedupe_keep_last(src) == ["P", "q"]
assert src == ["p", "P", "q"]
try:
    dedupe_keep_last(["a", 1])
    raise AssertionError("expected TypeError")
except TypeError:
    pass
try:
    dedupe_keep_last([None])
    raise AssertionError("expected TypeError")
except TypeError:
    pass""",
        "reference": """def dedupe_keep_last(words: list) -> list:
    for w in words:
        if not isinstance(w, str):
            raise TypeError("every item must be a str")
    last = {}
    for i, w in enumerate(words):
        last[w.lower()] = i
    keep = set(last.values())
    return [w for i, w in enumerate(words) if i in keep]""",
        "wrong": """def dedupe_keep_last(words: list) -> list:
    for w in words:
        if not isinstance(w, str):
            raise TypeError("every item must be a str")
    seen = set()
    out = []
    for w in words:
        key = w.lower()
        if key not in seen:
            seen.add(key)
            out.append(w)
    return out""",
        "wrong_explanation": "It keeps the first occurrence of each case-insensitive group instead of the last, so both the surviving spelling and the output order are wrong.",
    },
    {
        "name": "lists_merge_pairs_longest_value_wins",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": """Write a Python function with this exact signature:
    def merge_records(primary: list, secondary: list) -> list

Both arguments are lists of (key, value) pairs, where `key` and `value` are strings. The function merges them into one list of (key, value) tuples.

Rules:
1. Within a single input list, if the same key appears more than once, the value of the LAST pair for that key wins for that list. The key's position, however, is the position of its FIRST appearance in that list.
2. If a key appears in both lists (after applying rule 1 to each list separately), resolve the conflict by value length: the value with the greater `len(value)` wins. If the two values have equal length, the value from `primary` wins.
3. Output order: first every key from `primary`, in the order of its first appearance in `primary`; then every key that appears only in `secondary`, in the order of its first appearance in `secondary`.
4. If both lists are empty, return an empty list.
5. If any pair in either list does not have exactly 2 elements, raise ValueError.
6. Return a list of 2-element tuples.

Examples:
    merge_records([("a", "xx")], [("a", "yy")]) == [("a", "xx")]
    merge_records([("a", "x")], [("a", "yyy")]) == [("a", "yyy")]
    merge_records([("b", "1"), ("a", "22")], [("c", "3"), ("a", "4")]) == [("b", "1"), ("a", "22"), ("c", "3")]

Reply with the function in a single ```python code block and nothing else.""",
        "tests": """assert merge_records([], []) == []
assert merge_records([("a", "x")], []) == [("a", "x")]
assert merge_records([], [("a", "x")]) == [("a", "x")]
assert merge_records([("a", "xx")], [("a", "yy")]) == [("a", "xx")]
assert merge_records([("a", "x")], [("a", "yyy")]) == [("a", "yyy")]
assert merge_records([("a", "xxx")], [("a", "y")]) == [("a", "xxx")]
assert merge_records([("b", "1"), ("a", "22")], [("c", "3"), ("a", "4")]) == [("b", "1"), ("a", "22"), ("c", "3")]
assert merge_records([("a", "x"), ("b", "y"), ("a", "z")], []) == [("a", "z"), ("b", "y")]
assert merge_records([], [("k", "aa"), ("k", "b")]) == [("k", "b")]
assert merge_records([("a", "")], [("a", "")]) == [("a", "")]
assert merge_records([("a", "")], [("a", "z")]) == [("a", "z")]
assert merge_records([("m", "long"), ("n", "no")], [("n", "nope"), ("m", "lo")]) == [("m", "long"), ("n", "nope")]
try:
    merge_records([("a", "x", 1)], [])
    raise AssertionError("expected ValueError")
except ValueError:
    pass
try:
    merge_records([], [("a",)])
    raise AssertionError("expected ValueError")
except ValueError:
    pass""",
        "reference": """def merge_records(primary: list, secondary: list) -> list:
    for pair in list(primary) + list(secondary):
        if len(pair) != 2:
            raise ValueError("each pair must have exactly two elements")

    def collapse(pairs):
        order = []
        vals = {}
        for k, v in pairs:
            if k not in vals:
                order.append(k)
            vals[k] = v
        return order, vals

    o1, v1 = collapse(primary)
    o2, v2 = collapse(secondary)
    out = []
    for k in o1:
        v = v1[k]
        if k in v2 and len(v2[k]) > len(v):
            v = v2[k]
        out.append((k, v))
    for k in o2:
        if k not in v1:
            out.append((k, v2[k]))
    return out""",
        "wrong": """def merge_records(primary: list, secondary: list) -> list:
    merged = dict(primary)
    merged.update(secondary)
    return list(merged.items())""",
        "wrong_explanation": "It lets `secondary` overwrite `primary` unconditionally instead of applying the longest-value-wins conflict rule (and never validates pair length).",
    },
    {
        "name": "dicts_invert_to_groups",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": """Write a Python function with this exact signature:
    def invert_to_groups(mapping: dict) -> dict

It inverts a dictionary into a grouped index.

The input `mapping` has string keys and hashable values (for example ints or strings). Several keys may share the same value.

Rules:
- Return a NEW dictionary whose keys are the distinct values of `mapping`.
- Each of those keys maps to a list of every input key that had that value.
- Each list must be sorted in ascending order (normal Python string comparison).
- Every value in the returned dictionary must be a list, even when only one key had that value.
- An empty input dictionary returns an empty dictionary.
- Do not modify the input dictionary.

Reply with the function in a single ```python code block and nothing else.""",
        "tests": """assert invert_to_groups({}) == {}
assert invert_to_groups({"a": 1}) == {1: ["a"]}
assert invert_to_groups({"b": 1, "a": 1}) == {1: ["a", "b"]}
assert invert_to_groups({"x": 2, "y": 3, "z": 2}) == {2: ["x", "z"], 3: ["y"]}
assert invert_to_groups({"k": "red", "j": "red", "m": "blue"}) == {"red": ["j", "k"], "blue": ["m"]}
assert invert_to_groups({"c": 0, "a": -1, "b": -1}) == {0: ["c"], -1: ["a", "b"]}
assert invert_to_groups({"p": 7, "q": "7"}) == {7: ["p"], "7": ["q"]}
_src = {"n": 5, "m": 5}
_res = invert_to_groups(_src)
assert _src == {"n": 5, "m": 5}
assert isinstance(_res[5], list) and _res[5] == ["m", "n"]""",
        "reference": """def invert_to_groups(mapping: dict) -> dict:
    out = {}
    for key, value in mapping.items():
        out.setdefault(value, []).append(key)
    for value in out:
        out[value] = sorted(out[value])
    return out""",
        "wrong": """def invert_to_groups(mapping: dict) -> dict:
    return {value: key for key, value in mapping.items()}""",
        "wrong_explanation": "It performs a one-to-one inversion, so keys sharing a value overwrite each other and the results are bare keys instead of sorted lists.",
    },
    {
        "name": "dicts_merge_layers",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": """Write a Python function with this exact signature:
    def merge_layers(layers: list) -> dict

It merges a list of configuration dictionaries into one.

Rules:
- Process the layers in order, starting at index 0. Later layers take precedence over earlier ones.
- For each key/value pair in a layer: if the value is None, remove that key from the result if it is present (and do nothing if it is not). Otherwise set the key in the result to that value.
- Note that a key deleted by a None in one layer can be set again by a later layer.
- Values such as 0, "" and False are ordinary values and must be kept; only None deletes.
- Return a new dictionary. Do not modify any of the input dictionaries.
- An empty `layers` list returns an empty dictionary.
- If any element of `layers` is not a dict, raise TypeError. Check every element before merging anything.

Reply with the function in a single ```python code block and nothing else.""",
        "tests": """assert merge_layers([]) == {}
assert merge_layers([{}, {}]) == {}
assert merge_layers([{"a": 1}]) == {"a": 1}
assert merge_layers([{"a": 1}, {"a": 2}]) == {"a": 2}
assert merge_layers([{"a": 1, "b": 2}, {"b": 3, "c": 4}]) == {"a": 1, "b": 3, "c": 4}
assert merge_layers([{"a": 1, "b": 2}, {"a": None}]) == {"b": 2}
assert merge_layers([{"a": None}]) == {}
assert merge_layers([{"a": None}, {"a": 9}]) == {"a": 9}
assert merge_layers([{"a": 1}, {"a": None}, {"a": 5}, {"b": None}]) == {"a": 5}
assert merge_layers([{"a": 0}, {"b": ""}, {"c": False}]) == {"a": 0, "b": "", "c": False}
_first = {"a": 1}
_second = {"a": None, "z": 3}
assert merge_layers([_first, _second]) == {"z": 3}
assert _first == {"a": 1} and _second == {"a": None, "z": 3}
try:
    merge_layers([{"a": 1}, ["b", 2]])
    raise AssertionError("expected TypeError")
except TypeError:
    pass
try:
    merge_layers([None])
    raise AssertionError("expected TypeError")
except TypeError:
    pass""",
        "reference": """def merge_layers(layers: list) -> dict:
    for layer in layers:
        if not isinstance(layer, dict):
            raise TypeError("every layer must be a dict")
    result = {}
    for layer in layers:
        for key, value in layer.items():
            if value is None:
                result.pop(key, None)
            else:
                result[key] = value
    return result""",
        "wrong": """def merge_layers(layers: list) -> dict:
    result = {}
    for layer in layers:
        if not isinstance(layer, dict):
            raise TypeError("every layer must be a dict")
        result.update(layer)
    return result""",
        "wrong_explanation": "It uses dict.update for every layer, so a None value stores None instead of deleting the key.",
    },
    {
        "name": "dicts_top_tags",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": """Write a Python function with this exact signature:
    def top_tags(tags: list, n: int) -> list

It counts strings and returns the most frequent ones.

Rules:
- Count how many times each string appears in `tags`. Matching is exact and case-sensitive, so "A" and "a" are different tags.
- Return a list of (tag, count) tuples for the n most frequent tags.
- Order the result by count in descending order. Ties are broken by the tag itself in ascending alphabetical order (normal Python string comparison), NOT by order of first appearance.
- If n is greater than the number of distinct tags, return all of them.
- If n is 0, return an empty list.
- If n is negative, raise ValueError.
- An empty `tags` list returns an empty list (when n is not negative).

Reply with the function in a single ```python code block and nothing else.""",
        "tests": """assert top_tags([], 3) == []
assert top_tags(["a"], 1) == [("a", 1)]
assert top_tags(["a", "a", "b"], 5) == [("a", 2), ("b", 1)]
assert top_tags(["b", "a"], 2) == [("a", 1), ("b", 1)]
assert top_tags(["z", "y", "x"], 2) == [("x", 1), ("y", 1)]
assert top_tags(["dog", "cat", "dog", "cat", "emu"], 2) == [("cat", 2), ("dog", 2)]
assert top_tags(["dog", "cat", "dog", "cat", "emu"], 3) == [("cat", 2), ("dog", 2), ("emu", 1)]
assert top_tags(["A", "a", "A"], 2) == [("A", 2), ("a", 1)]
assert top_tags(["p", "q"], 0) == []
assert top_tags([], 0) == []
assert top_tags(["b", "b", "a", "a", "c"], 1) == [("a", 2)]
try:
    top_tags(["a"], -1)
    raise AssertionError("expected ValueError")
except ValueError:
    pass""",
        "reference": """def top_tags(tags: list, n: int) -> list:
    if n < 0:
        raise ValueError("n must not be negative")
    counts = {}
    for tag in tags:
        counts[tag] = counts.get(tag, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ordered[:n]""",
        "wrong": """def top_tags(tags: list, n: int) -> list:
    if n < 0:
        raise ValueError("n must not be negative")
    counts = {}
    for tag in tags:
        counts[tag] = counts.get(tag, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    return ordered[:n]""",
        "wrong_explanation": "It sorts only by count, so tied tags keep first-appearance order instead of being broken alphabetically.",
    },
    {
        "name": "validation_parse_seat_code",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": """An airline seat code is a short string like "12B": a row number written in ASCII digits, immediately followed by exactly one seat letter, with nothing else in the string.

Write a Python function with this exact signature:
    def validation_parse_seat_code(code: str) -> tuple[int, str]

The argument is always a str. Parse it according to these rules:

1. The last character of the string is the seat letter. It must be exactly one of the uppercase letters A, B, C, D, E, F. Lowercase letters are invalid.
2. Everything before the last character is the row. It must be one or more characters, each of which is an ASCII digit 0-9. No signs, no spaces, no dots, no other characters anywhere in the string.
3. The row must not have a leading zero: "0" alone would be allowed by this rule, but "012" or "07" is invalid.
4. The row's integer value must be between 1 and 60 inclusive.
5. If all rules hold, return the tuple (row, letter) where row is an int and letter is a one-character str.
6. If any rule is violated, raise ValueError. Do not raise any other exception type, and do not return None.

Examples: "1A" -> (1, "A"); "60F" -> (60, "F"); "7C" -> (7, "C"). Each of "", "A", "12", "012B", "0A", "61A", "12b", "12G", "12AB", "1 A" raises ValueError.

Reply with the function in a single ```python code block and nothing else.""",
        "tests": """def _bad(s):
    try:
        validation_parse_seat_code(s)
    except ValueError:
        return True
    except Exception:
        return False
    return False

assert validation_parse_seat_code("1A") == (1, "A")
assert validation_parse_seat_code("60F") == (60, "F")
assert validation_parse_seat_code("7C") == (7, "C")
assert validation_parse_seat_code("12B") == (12, "B")
assert isinstance(validation_parse_seat_code("3D"), tuple)
assert validation_parse_seat_code("3D")[1] == "D"
assert _bad("")
assert _bad("A")
assert _bad("12")
assert _bad("012B")
assert _bad("0A")
assert _bad("61A")
assert _bad("100A")
assert _bad("12b")
assert _bad("12G")
assert _bad("12AB")
assert _bad("1 A")
assert _bad("-1A")
assert _bad("1.5A")
assert _bad("١A")""",
        "reference": """def validation_parse_seat_code(code: str) -> tuple[int, str]:
    if len(code) < 2:
        raise ValueError("seat code too short")
    digits, letter = code[:-1], code[-1]
    if letter not in "ABCDEF":
        raise ValueError("bad seat letter")
    if any(c not in "0123456789" for c in digits):
        raise ValueError("row must be ASCII digits")
    if len(digits) > 1 and digits[0] == "0":
        raise ValueError("row has a leading zero")
    row = int(digits)
    if not 1 <= row <= 60:
        raise ValueError("row out of range")
    return (row, letter)""",
        "wrong": """def validation_parse_seat_code(code: str) -> tuple[int, str]:
    if len(code) < 2:
        raise ValueError("seat code too short")
    digits, letter = code[:-1], code[-1]
    if letter not in "ABCDEF":
        raise ValueError("bad seat letter")
    if not digits.isdigit():
        raise ValueError("row must be digits")
    row = int(digits)
    if not 1 <= row <= 60:
        raise ValueError("row out of range")
    return (row, letter)""",
        "wrong_explanation": 'It never checks the leading-zero rule, so "012B" returns (12, "B") instead of raising ValueError.',
    },
    {
        "name": "validation_parse_kv_pairs",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": """A tiny config format is a semicolon-separated list of key=value fields, for example "host=local;port=8080".

Write a Python function with this exact signature:
    def validation_parse_kv_pairs(text: str) -> dict

The argument is always a str. Parse it according to these rules:

1. If text is the empty string "", return an empty dict. This is the only case where an empty input is legal.
2. Otherwise split text on ";" into fields. Every field must contain exactly one "=" character; a field with zero or two or more "=" characters is invalid, and so is an empty field (which is what a leading, trailing, or doubled ";" produces).
3. In each field, the key is the part before "=" and the value is the part after it.
4. A key must be non-empty and every character in it must be a lowercase ASCII letter a-z or an underscore "_". Digits, uppercase letters, spaces and anything else are invalid.
5. A value must be non-empty and every character in it must be an ASCII letter (uppercase or lowercase), an ASCII digit 0-9, or one of ".", "-", "_". Spaces and anything else are invalid.
6. The same key must not appear twice: a repeated key is an error, not an overwrite.
7. Return a dict mapping each key to its value as a str. Do not convert values to numbers. Keys must be inserted in the order the fields appear in text.
8. If any rule is violated, raise ValueError. Do not raise any other exception type, and do not return None.

Examples: "" -> {}; "a=1;b=2" -> {"a": "1", "b": "2"}; "max_size=10.5" -> {"max_size": "10.5"}. Each of "a=1;a=2", "a=1;", ";a=1", "a", "a==1", "=1", "a=", "A=1", "a1=2", "a=1 2" raises ValueError.

Reply with the function in a single ```python code block and nothing else.""",
        "tests": """def _bad(s):
    try:
        validation_parse_kv_pairs(s)
    except ValueError:
        return True
    except Exception:
        return False
    return False

assert validation_parse_kv_pairs("") == {}
assert validation_parse_kv_pairs("host=local") == {"host": "local"}
assert validation_parse_kv_pairs("a=1;b=2") == {"a": "1", "b": "2"}
assert validation_parse_kv_pairs("max_size=10.5;mode=Fast-2") == {"max_size": "10.5", "mode": "Fast-2"}
assert validation_parse_kv_pairs("_x=_") == {"_x": "_"}
assert list(validation_parse_kv_pairs("b=1;a=2")) == ["b", "a"]
assert validation_parse_kv_pairs("port=8080")["port"] == "8080"
assert _bad("a=1;a=2")
assert _bad("a=1;b=2;a=3")
assert _bad("a=1;")
assert _bad(";a=1")
assert _bad("a")
assert _bad("a==1")
assert _bad("=1")
assert _bad("a=")
assert _bad("A=1")
assert _bad("a b=1")
assert _bad("a1=2")
assert _bad("a=1 2")
assert _bad("a=x;;b=y")
assert _bad(" ")
assert _bad("   ")
assert _bad("\t")""",
        "reference": """def validation_parse_kv_pairs(text: str) -> dict:
    KEY = set("abcdefghijklmnopqrstuvwxyz_")
    VAL = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_")
    result = {}
    if text == "":
        return result
    for field in text.split(";"):
        parts = field.split("=")
        if len(parts) != 2:
            raise ValueError("field must contain exactly one '='")
        key, value = parts
        if not key or any(c not in KEY for c in key):
            raise ValueError("bad key")
        if not value or any(c not in VAL for c in value):
            raise ValueError("bad value")
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result""",
        "wrong": """def validation_parse_kv_pairs(text: str) -> dict:
    KEY = set("abcdefghijklmnopqrstuvwxyz_")
    VAL = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_")
    result = {}
    if text == "":
        return result
    for field in text.split(";"):
        parts = field.split("=")
        if len(parts) != 2:
            raise ValueError("field must contain exactly one '='")
        key, value = parts
        if not key or any(c not in KEY for c in key):
            raise ValueError("bad key")
        if not value or any(c not in VAL for c in value):
            raise ValueError("bad value")
        result[key] = value
    return result""",
        "wrong_explanation": 'It lets a repeated key silently overwrite the earlier one, so "a=1;a=2" returns {"a": "2"} instead of raising ValueError.',
    },
    {
        "name": "validation_parse_range_spec",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": """A range spec is a comma-separated list of items where each item is either a single number or an inclusive range written "lo-hi", for example "9,1-3".

Write a Python function with this exact signature:
    def validation_parse_range_spec(spec: str) -> list

The argument is always a str. Parse it according to these rules:

1. The empty string "" is invalid.
2. Otherwise split spec on "," into fields. A field that contains no "-" is a single number. A field that contains "-" is a range and must contain exactly one "-", with a number on each side; the "-" is never a minus sign, so "-5" is invalid.
3. A number must be non-empty and every character in it must be an ASCII digit 0-9. No spaces, no signs, no dots.
4. A number must not have a leading zero, except that the single character "0" is itself a valid number. So "0" is fine but "007" is invalid.
5. Every number's integer value must be between 0 and 999 inclusive.
6. For a range "lo-hi", lo must be strictly less than hi. A range whose two endpoints are equal is invalid, and so is one where lo is greater than hi. A range expands to every integer from lo to hi, including both endpoints.
7. Build the result list by appending each field's value or expanded values in the order the fields appear in spec. Do not sort it.
8. No integer may appear more than once in the whole result, whether it came from a single number or from inside a range.
9. Return the list of ints. If any rule is violated, raise ValueError. Do not raise any other exception type, and do not return None.

Examples: "5" -> [5]; "3-7" -> [3, 4, 5, 6, 7]; "9,1-3" -> [9, 1, 2, 3]. Each of "", "5-5", "7-3", "1,1", "1-3,2", "007", "1000", "-5", "5-", "1-2-3", "1,", "1, 2" raises ValueError.

Reply with the function in a single ```python code block and nothing else.""",
        "tests": """def _bad(s):
    try:
        validation_parse_range_spec(s)
    except ValueError:
        return True
    except Exception:
        return False
    return False

assert validation_parse_range_spec("5") == [5]
assert validation_parse_range_spec("0") == [0]
assert validation_parse_range_spec("999") == [999]
assert validation_parse_range_spec("3-7") == [3, 4, 5, 6, 7]
assert validation_parse_range_spec("9,1-3") == [9, 1, 2, 3]
assert validation_parse_range_spec("10-11,4,0-1") == [10, 11, 4, 0, 1]
assert validation_parse_range_spec("998-999") == [998, 999]
assert _bad("")
assert _bad("5-5")
assert _bad("7-3")
assert _bad("1,1")
assert _bad("1-3,2")
assert _bad("1-3,3-4")
assert _bad("007")
assert _bad("1000")
assert _bad("1-1000")
assert _bad("-5")
assert _bad("5-")
assert _bad("1-2-3")
assert _bad("1,")
assert _bad(",1")
assert _bad("1, 2")
assert _bad("a")
assert _bad("1.5")
assert _bad("٣")""",
        "reference": """def validation_parse_range_spec(spec: str) -> list:
    def num(s):
        if not s or any(c not in "0123456789" for c in s):
            raise ValueError("bad number")
        if len(s) > 1 and s[0] == "0":
            raise ValueError("leading zero")
        v = int(s)
        if v > 999:
            raise ValueError("number out of range")
        return v

    if spec == "":
        raise ValueError("empty spec")
    out = []
    seen = set()
    for field in spec.split(","):
        if "-" in field:
            parts = field.split("-")
            if len(parts) != 2:
                raise ValueError("bad range")
            lo = num(parts[0])
            hi = num(parts[1])
            if lo >= hi:
                raise ValueError("lo must be strictly less than hi")
            values = list(range(lo, hi + 1))
        else:
            values = [num(field)]
        for v in values:
            if v in seen:
                raise ValueError("duplicate value")
            seen.add(v)
            out.append(v)
    return out""",
        "wrong": """def validation_parse_range_spec(spec: str) -> list:
    def num(s):
        if not s or any(c not in "0123456789" for c in s):
            raise ValueError("bad number")
        if len(s) > 1 and s[0] == "0":
            raise ValueError("leading zero")
        v = int(s)
        if v > 999:
            raise ValueError("number out of range")
        return v

    if spec == "":
        raise ValueError("empty spec")
    out = []
    seen = set()
    for field in spec.split(","):
        if "-" in field:
            parts = field.split("-")
            if len(parts) != 2:
                raise ValueError("bad range")
            lo = num(parts[0])
            hi = num(parts[1])
            if lo > hi:
                raise ValueError("lo must not exceed hi")
            values = list(range(lo, hi + 1))
        else:
            values = [num(field)]
        for v in values:
            if v in seen:
                raise ValueError("duplicate value")
            seen.add(v)
            out.append(v)
    return out""",
        "wrong_explanation": 'It uses lo > hi instead of lo >= hi, so the degenerate range "5-5" returns [5] instead of raising ValueError.',
    },
    {
        "name": "numbers_clamp_and_count",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": """Write a Python function with this exact signature:
    def clamp_and_count(values: list[int], low: int, high: int) -> tuple[list[int], int]

It clamps every number in `values` into the inclusive range [low, high] and reports how many numbers had to be changed.

Rules:
- Return a tuple `(clamped, changed)`. `clamped` is a NEW list with the same length and order as `values`; `changed` is an int.
- For each value v: if v < low use `low`; if v > high use `high`; otherwise keep v as it is.
- A value exactly equal to `low`, or exactly equal to `high`, is already inside the range: keep it and do NOT count it as changed.
- `changed` is the number of values that were replaced by `low` or by `high`.
- Do not modify the input list `values` in place.
- If low > high, raise ValueError. low == high is legal: every value not equal to that number becomes it and counts as changed.
- An empty `values` returns `([], 0)`.
- Values, `low` and `high` may be negative.

Reply with the function in a single ```python code block and nothing else.""",
        "tests": """assert clamp_and_count([], 0, 10) == ([], 0)
assert clamp_and_count([5], 0, 10) == ([5], 0)
assert clamp_and_count([0, 10], 0, 10) == ([0, 10], 0)
assert clamp_and_count([-3, 4, 99], 0, 10) == ([0, 4, 10], 2)
assert clamp_and_count([7, 7, 7], 1, 5) == ([5, 5, 5], 3)
assert clamp_and_count([-8, -2], -5, -1) == ([-5, -2], 1)
assert clamp_and_count([3, 9], 5, 5) == ([5, 5], 2)
assert clamp_and_count([1, 2, 3], -100, 100) == ([1, 2, 3], 0)
src = [-1, 50]
out = clamp_and_count(src, 0, 10)
assert out == ([0, 10], 2)
assert src == [-1, 50]
assert isinstance(out, tuple) and isinstance(out[0], list) and isinstance(out[1], int)
try:
    clamp_and_count([1, 2], 10, 0)
    raise AssertionError("expected ValueError")
except ValueError:
    pass""",
        "reference": """def clamp_and_count(values: list[int], low: int, high: int) -> tuple[list[int], int]:
    if low > high:
        raise ValueError("low must not be greater than high")
    out = []
    changed = 0
    for v in values:
        if v < low:
            out.append(low)
            changed += 1
        elif v > high:
            out.append(high)
            changed += 1
        else:
            out.append(v)
    return out, changed""",
        "wrong": """def clamp_and_count(values: list[int], low: int, high: int) -> tuple[list[int], int]:
    if low > high:
        raise ValueError("low must not be greater than high")
    out = []
    changed = 0
    for v in values:
        if v <= low:
            out.append(low)
            changed += 1
        elif v >= high:
            out.append(high)
            changed += 1
        else:
            out.append(v)
    return out, changed""",
        "wrong_explanation": "It uses <= and >= at the bounds, so values exactly equal to low or high are counted as changed even though they are inside the range and were never modified.",
    },
    {
        "name": "numbers_round_to_step",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": """Write a Python function with this exact signature:
    def round_to_step(value: int, step: int) -> int

It rounds an integer to the nearest multiple of a positive step size.

Rules:
- Return the multiple of `step` that is closest to `value`, as an int.
- Tie rule: when `value` lies exactly halfway between two multiples of `step`, return the LARGER multiple (the one nearer positive infinity). For example round_to_step(5, 10) == 10, round_to_step(-5, 10) == 0, and round_to_step(-15, 10) == -10.
- `value` may be negative, zero, or positive. round_to_step(0, step) == 0 for any valid step.
- If `value` is already a multiple of `step`, return `value` unchanged.
- If `step` is zero or negative, raise ValueError.
- The returned object must be an int, not a float.

Reply with the function in a single ```python code block and nothing else.""",
        "tests": """assert round_to_step(7, 5) == 5
assert round_to_step(8, 5) == 10
assert round_to_step(5, 10) == 10
assert round_to_step(-5, 10) == 0
assert round_to_step(-15, 10) == -10
assert round_to_step(-7, 5) == -5
assert round_to_step(-8, 5) == -10
assert round_to_step(0, 3) == 0
assert round_to_step(12, 1) == 12
assert round_to_step(-12, 4) == -12
assert round_to_step(1, 100) == 0
assert round_to_step(50, 100) == 100
assert round_to_step(-50, 100) == 0
assert isinstance(round_to_step(7, 5), int)
try:
    round_to_step(10, 0)
    raise AssertionError("expected ValueError")
except ValueError:
    pass
try:
    round_to_step(10, -5)
    raise AssertionError("expected ValueError")
except ValueError:
    pass""",
        "reference": """def round_to_step(value: int, step: int) -> int:
    if step <= 0:
        raise ValueError("step must be positive")
    q, r = divmod(value, step)
    if 2 * r >= step:
        q += 1
    return q * step""",
        "wrong": """def round_to_step(value: int, step: int) -> int:
    if step <= 0:
        raise ValueError("step must be positive")
    return int(round(value / step)) * step""",
        "wrong_explanation": "It delegates to Python's built-in round(), which breaks ties to the nearest even quotient instead of always toward positive infinity, so round_to_step(5, 10) returns 0 rather than 10.",
    },
    {
        "name": "numbers_speed_to_kmh",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": """Write a Python function with this exact signature:
    def speed_to_kmh(meters: float, seconds: float) -> str

It converts a distance in meters covered in a given number of seconds into a speed in kilometers per hour, returned as formatted text.

Rules:
- The speed in km/h is (meters / 1000) / (seconds / 3600), which is the same as meters * 3.6 / seconds.
- Return a string built as: the speed with EXACTLY two digits after the decimal point, then a single space, then the literal text km/h. For example a speed of 36 must produce "36.00 km/h" and a speed of 1 must produce "1.00 km/h".
- Trailing zeros after the decimal point must be kept, so "1.0 km/h" and "1 km/h" are both wrong.
- The two-decimal value is the ordinary fixed-point rounding of the computed speed, e.g. 37.578288... becomes "37.58 km/h".
- If `seconds` is zero or negative, raise ValueError.
- If `meters` is negative, raise ValueError.
- `meters` may be zero, which gives "0.00 km/h".

Reply with the function in a single ```python code block and nothing else.""",
        "tests": """assert speed_to_kmh(100, 10) == "36.00 km/h"
assert speed_to_kmh(0, 5) == "0.00 km/h"
assert speed_to_kmh(1000, 3600) == "1.00 km/h"
assert speed_to_kmh(100, 9.58) == "37.58 km/h"
assert speed_to_kmh(5, 2) == "9.00 km/h"
assert speed_to_kmh(1, 3600) == "0.00 km/h"
assert speed_to_kmh(2500, 300) == "30.00 km/h"
assert isinstance(speed_to_kmh(10, 1), str)
try:
    speed_to_kmh(100, 0)
    raise AssertionError("expected ValueError")
except ValueError:
    pass
try:
    speed_to_kmh(100, -2)
    raise AssertionError("expected ValueError")
except ValueError:
    pass
try:
    speed_to_kmh(-1, 10)
    raise AssertionError("expected ValueError")
except ValueError:
    pass""",
        "reference": """def speed_to_kmh(meters: float, seconds: float) -> str:
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    if meters < 0:
        raise ValueError("meters must not be negative")
    kmh = meters * 3.6 / seconds
    return f"{kmh:.2f} km/h\"""",
        "wrong": """def speed_to_kmh(meters: float, seconds: float) -> str:
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    if meters < 0:
        raise ValueError("meters must not be negative")
    kmh = meters * 3.6 / seconds
    return str(round(kmh, 2)) + " km/h\"""",
        "wrong_explanation": 'It builds the text with str(round(x, 2)) instead of fixed-point formatting, so trailing zeros are dropped and 36.0 renders as "36.0 km/h" rather than the required "36.00 km/h".',
    },
    {
        "name": "stateful_classify_ticket",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": """A support desk classifies a ticket by applying rules in a fixed order.

Write a Python function with this exact signature:
    def stateful_classify_ticket(age_days: int, severity: str, subscriber: bool) -> str

First validate the inputs, in this order:
1. If age_days is negative, raise ValueError.
2. If severity is not exactly one of "low", "medium", "high" (lowercase), raise ValueError.

Then apply the classification rules IN THIS ORDER and return the label of the FIRST rule that matches:
1. severity is "high" and subscriber is True -> "escalate"
2. age_days > 30 -> "archive"
3. severity is "high" -> "urgent"
4. subscriber is True and age_days >= 7 -> "review"
5. nothing above matched -> "queue"

The order is part of the specification: a ticket with age_days=40, severity="high", subscriber=True is "escalate" (rule 1 wins over rule 2), and age_days=30 is NOT greater than 30.

Reply with the function in a single ```python code block and nothing else.""",
        "tests": """assert stateful_classify_ticket(0, "low", False) == "queue"
assert stateful_classify_ticket(40, "high", True) == "escalate"
assert stateful_classify_ticket(40, "high", False) == "archive"
assert stateful_classify_ticket(31, "medium", True) == "archive"
assert stateful_classify_ticket(30, "high", False) == "urgent"
assert stateful_classify_ticket(30, "medium", True) == "review"
assert stateful_classify_ticket(7, "low", True) == "review"
assert stateful_classify_ticket(6, "low", True) == "queue"
assert stateful_classify_ticket(99, "low", False) == "archive"
assert stateful_classify_ticket(0, "high", True) == "escalate"
assert stateful_classify_ticket(100, "medium", False) == "archive"
assert stateful_classify_ticket(12, "medium", False) == "queue"
try:
    stateful_classify_ticket(-1, "low", False)
    raise AssertionError("expected ValueError for negative age_days")
except ValueError:
    pass
try:
    stateful_classify_ticket(5, "HIGH", True)
    raise AssertionError("expected ValueError for uppercase severity")
except ValueError:
    pass
try:
    stateful_classify_ticket(5, "critical", True)
    raise AssertionError("expected ValueError for unknown severity")
except ValueError:
    pass""",
        "reference": """def stateful_classify_ticket(age_days: int, severity: str, subscriber: bool) -> str:
    if age_days < 0:
        raise ValueError("age_days must not be negative")
    if severity not in ("low", "medium", "high"):
        raise ValueError("bad severity")
    if severity == "high" and subscriber:
        return "escalate"
    if age_days > 30:
        return "archive"
    if severity == "high":
        return "urgent"
    if subscriber and age_days >= 7:
        return "review"
    return "queue\"""",
        "wrong": """def stateful_classify_ticket(age_days: int, severity: str, subscriber: bool) -> str:
    if age_days < 0:
        raise ValueError("age_days must not be negative")
    if severity not in ("low", "medium", "high"):
        raise ValueError("bad severity")
    if severity == "high":
        return "escalate" if subscriber else "urgent"
    if age_days > 30:
        return "archive"
    if subscriber and age_days >= 7:
        return "review"
    return "queue\"""",
        "wrong_explanation": 'It groups both "high" severity outcomes into one branch, so rule 3 ("urgent") is effectively checked before rule 2, and an old non-subscriber high ticket (40, "high", False) returns "urgent" instead of "archive".',
    },
    {
        "name": "stateful_label_readings",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": """Write a Python function with this exact signature:
    def stateful_label_readings(readings: list[int]) -> list[str]

Return a new list holding one label per reading, in the same order as the input. Do not modify the input list.

For each reading r, apply these rules IN THIS ORDER and use the label of the FIRST rule that matches:
1. r < 0 -> "invalid"
2. r > 90 -> "critical"
3. r % 7 == 0 -> "check"
4. r % 2 == 0 -> "even"
5. nothing above matched -> "ok"

The order is part of the specification: 91 is divisible by 7 but it is labelled "critical" because rule 2 is checked before rule 3, and -7 is labelled "invalid" because rule 1 is checked first. Note that 0 % 7 == 0, so 0 is labelled "check". An empty input list returns an empty list.

Reply with the function in a single ```python code block and nothing else.""",
        "tests": """assert stateful_label_readings([]) == []
assert stateful_label_readings([0]) == ["check"]
assert stateful_label_readings([-3]) == ["invalid"]
assert stateful_label_readings([-7]) == ["invalid"]
assert stateful_label_readings([91]) == ["critical"]
assert stateful_label_readings([98]) == ["critical"]
assert stateful_label_readings([90]) == ["even"]
assert stateful_label_readings([89]) == ["ok"]
assert stateful_label_readings([7, 7]) == ["check", "check"]
assert stateful_label_readings([7, 14, 15, 2, 3]) == ["check", "check", "ok", "even", "ok"]
assert stateful_label_readings([100, -1, 0, 1, 2]) == ["critical", "invalid", "check", "ok", "even"]
_src = [5, -5, 91, 42]
_out = stateful_label_readings(_src)
assert _out == ["ok", "invalid", "critical", "check"]
assert _src == [5, -5, 91, 42]
assert len(stateful_label_readings([1, 2, 3, 4, 5, 6])) == 6""",
        "reference": """def stateful_label_readings(readings: list[int]) -> list[str]:
    out = []
    for r in readings:
        if r < 0:
            out.append("invalid")
        elif r > 90:
            out.append("critical")
        elif r % 7 == 0:
            out.append("check")
        elif r % 2 == 0:
            out.append("even")
        else:
            out.append("ok")
    return out""",
        "wrong": """def stateful_label_readings(readings: list[int]) -> list[str]:
    out = []
    for r in readings:
        if r < 0:
            out.append("invalid")
        elif r % 7 == 0:
            out.append("check")
        elif r > 90:
            out.append("critical")
        elif r % 2 == 0:
            out.append("even")
        else:
            out.append("ok")
    return out""",
        "wrong_explanation": 'It tests divisibility by 7 before the >90 range rule, so multiples of 7 above 90 (91, 98) are labelled "check" instead of "critical".',
    },
    {
        "name": "stateful_run_machine",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": """A tiny job machine starts in the state "idle" and processes a list of events from left to right.

Write a Python function with this exact signature:
    def stateful_run_machine(events: list[str]) -> str

For each event, apply these rules IN THIS ORDER and use the FIRST rule that matches:
1. If the event is not exactly one of "start", "pause", "resume", "stop", "tick" (lowercase), raise ValueError.
2. If the current state is "stopped", the state stays "stopped" ("stopped" is final; nothing restarts it).
3. If the event is "stop", the state becomes "stopped".
4. If the event is "start", the state becomes "running" (from any state reached at this point).
5. If the state is "running" and the event is "pause", the state becomes "paused".
6. If the state is "paused" and the event is "resume", the state becomes "running".
7. Otherwise the state is unchanged.

Return the state after all events have been processed. An empty event list returns "idle".

The order is part of the specification: rule 1 applies even when the state is already "stopped", and rule 2 applies before rules 3-6, so ["stop", "start"] ends in "stopped".

Reply with the function in a single ```python code block and nothing else.""",
        "tests": """assert stateful_run_machine([]) == "idle"
assert stateful_run_machine(["tick"]) == "idle"
assert stateful_run_machine(["pause"]) == "idle"
assert stateful_run_machine(["resume"]) == "idle"
assert stateful_run_machine(["start"]) == "running"
assert stateful_run_machine(["start", "pause"]) == "paused"
assert stateful_run_machine(["start", "pause", "resume"]) == "running"
assert stateful_run_machine(["start", "pause", "start"]) == "running"
assert stateful_run_machine(["start", "resume"]) == "running"
assert stateful_run_machine(["start", "start", "start"]) == "running"
assert stateful_run_machine(["stop"]) == "stopped"
assert stateful_run_machine(["stop", "start"]) == "stopped"
assert stateful_run_machine(["stop", "start", "resume", "pause"]) == "stopped"
assert stateful_run_machine(["start", "stop", "tick"]) == "stopped"
assert stateful_run_machine(["tick", "tick", "start", "tick", "pause"]) == "paused"
try:
    stateful_run_machine(["start", "bogus"])
    raise AssertionError("expected ValueError for unknown event")
except ValueError:
    pass
try:
    stateful_run_machine(["stop", "nope"])
    raise AssertionError("expected ValueError even after stopping")
except ValueError:
    pass
try:
    stateful_run_machine(["START"])
    raise AssertionError("expected ValueError for uppercase event")
except ValueError:
    pass""",
        "reference": """def stateful_run_machine(events: list[str]) -> str:
    valid = ("start", "pause", "resume", "stop", "tick")
    state = "idle"
    for e in events:
        if e not in valid:
            raise ValueError("unknown event: " + str(e))
        if state == "stopped":
            continue
        if e == "stop":
            state = "stopped"
        elif e == "start":
            state = "running"
        elif state == "running" and e == "pause":
            state = "paused"
        elif state == "paused" and e == "resume":
            state = "running"
    return state""",
        "wrong": """def stateful_run_machine(events: list[str]) -> str:
    valid = ("start", "pause", "resume", "stop", "tick")
    state = "idle"
    for e in events:
        if e not in valid:
            raise ValueError("unknown event: " + str(e))
        if e == "stop":
            state = "stopped"
        elif e == "start":
            state = "running"
        elif state == "running" and e == "pause":
            state = "paused"
        elif state == "paused" and e == "resume":
            state = "running"
    return state""",
        "wrong_explanation": 'It omits rule 2, so "stopped" is not final and a later "start" restarts the machine, making ["stop", "start"] return "running" instead of "stopped".',
    },
    {
        "name": "parsing_pipe_record",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": """You are parsing one line of a tiny record format into its fields.

Write a Python function with this exact signature:
    def parse_pipe_record(line: str) -> list[str]

Rules:
- Fields are separated by the `|` character.
- A backslash `\\` starts an escape sequence. Exactly three escapes are valid: `\\|` produces a literal `|` and does NOT separate fields; `\\\\` produces one literal backslash; `\\n` produces a newline character.
- A backslash followed by any other character must raise ValueError. A backslash as the very last character of the line (with nothing after it) must also raise ValueError.
- Do not strip anything: spaces inside or around a field are part of that field.
- Two separators in a row produce an empty field between them. The empty string as input returns a list holding one empty string: [""].
- Return the list of decoded fields, in order.

Reply with the function in a single ```python code block and nothing else.""",
        "tests": """assert parse_pipe_record("a|b|c") == ["a", "b", "c"]
assert parse_pipe_record("") == [""]
assert parse_pipe_record("|") == ["", ""]
assert parse_pipe_record("solo") == ["solo"]
assert parse_pipe_record(r"a\\|b|c") == ["a|b", "c"]
assert parse_pipe_record(r"a\\\\|b") == ["a\\\\", "b"]
assert parse_pipe_record(r"\\\\") == ["\\\\"]
assert parse_pipe_record(r"x\\ny") == ["x\\ny"]
assert parse_pipe_record(" a | b ") == [" a ", " b "]
assert parse_pipe_record(r"a\\|\\|b") == ["a||b"]
assert parse_pipe_record("a||b") == ["a", "", "b"]
try:
    parse_pipe_record("a\\\\")
    assert False, "expected ValueError for dangling backslash"
except ValueError:
    pass
try:
    parse_pipe_record(r"a\\t")
    assert False, "expected ValueError for unknown escape"
except ValueError:
    pass""",
        "reference": """def parse_pipe_record(line: str) -> list[str]:
    fields = []
    cur = []
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if c == '\\\\':
            if i + 1 >= n:
                raise ValueError("dangling escape")
            nxt = line[i + 1]
            if nxt == '|':
                cur.append('|')
            elif nxt == '\\\\':
                cur.append('\\\\')
            elif nxt == 'n':
                cur.append('\\n')
            else:
                raise ValueError("unknown escape")
            i += 2
        elif c == '|':
            fields.append(''.join(cur))
            cur = []
            i += 1
        else:
            cur.append(c)
            i += 1
    fields.append(''.join(cur))
    return fields""",
        "wrong": """def parse_pipe_record(line: str) -> list[str]:
    out = []
    for part in line.split('|'):
        out.append(part.replace('\\\\\\\\', '\\\\').replace('\\\\n', '\\n'))
    return out""",
        "wrong_explanation": "It splits on every `|` before handling escapes, so `\\|` still breaks a field in two, and it never raises on a dangling or unknown escape.",
    },
    {
        "name": "parsing_kv_config",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": """A one-line settings string looks like `name=alice; timeout = 30 ;path=/a=b`.

Write a Python function with this exact signature:
    def parse_kv_config(line: str) -> dict[str, str]

Rules:
- Segments are separated by `;`. There is no escaping at all: every `;` separates, so no key or value can contain `;`.
- Skip any segment that is empty or made only of whitespace.
- Every remaining segment must contain at least one `=`; if it does not, raise ValueError.
- Split a segment at its FIRST `=` only. Everything before it is the key; everything after it is the value, including any further `=` characters.
- Strip leading and trailing whitespace from the key and from the value (plain str.strip()). Whitespace inside a value is kept exactly as it is.
- After stripping, the key must not be empty; if it is, raise ValueError. An empty value is allowed.
- If the same key appears more than once, the last occurrence wins.
- Return the dict mapping key to value. An empty or whitespace-only line returns an empty dict.

Reply with the function in a single ```python code block and nothing else.""",
        "tests": """assert parse_kv_config("name=alice;age=30") == {"name": "alice", "age": "30"}
assert parse_kv_config("") == {}
assert parse_kv_config("   ") == {}
assert parse_kv_config(";;") == {}
assert parse_kv_config(" a = 1 ; ; b=2 ") == {"a": "1", "b": "2"}
assert parse_kv_config("path=/x=y") == {"path": "/x=y"}
assert parse_kv_config("k=") == {"k": ""}
assert parse_kv_config("k=   ") == {"k": ""}
assert parse_kv_config("k=1;k=2") == {"k": "2"}
assert parse_kv_config("m=hello  world") == {"m": "hello  world"}
assert parse_kv_config("only=one;") == {"only": "one"}
try:
    parse_kv_config("novalue")
    assert False, "expected ValueError for missing '='"
except ValueError:
    pass
try:
    parse_kv_config("a=1;novalue")
    assert False, "expected ValueError for missing '='"
except ValueError:
    pass
try:
    parse_kv_config(" = 5")
    assert False, "expected ValueError for empty key"
except ValueError:
    pass""",
        "reference": """def parse_kv_config(line: str) -> dict[str, str]:
    result = {}
    for seg in line.split(';'):
        if seg.strip() == '':
            continue
        if '=' not in seg:
            raise ValueError("segment has no '='")
        key, value = seg.split('=', 1)
        key = key.strip()
        value = value.strip()
        if key == '':
            raise ValueError("empty key")
        result[key] = value
    return result""",
        "wrong": """def parse_kv_config(line: str) -> dict[str, str]:
    result = {}
    for seg in line.split(';'):
        if not seg.strip():
            continue
        parts = seg.split('=')
        if len(parts) < 2:
            raise ValueError("segment has no '='")
        key = parts[0].strip()
        value = parts[1].strip()
        if not key:
            raise ValueError("empty key")
        result[key] = value
    return result""",
        "wrong_explanation": "It splits a segment on every `=` instead of only the first one, so a value containing `=` is truncated at the second separator (`path=/x=y` yields `/x`).",
    },
    {
        "name": "parsing_item_list",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": """A shopping list is written as one line: items separated by `,`, where an item is either `name` or `count*name`, for example `apple, 2*pear ,banana`.

Write a Python function with this exact signature:
    def parse_item_list(s: str) -> list[tuple[str, int]]

Rules:
- If s is empty or contains only whitespace, return an empty list.
- Otherwise split s on `,` and strip leading and trailing whitespace from each item. If an item is empty after stripping, raise ValueError (so a trailing comma is an error).
- If the item contains `*`, split it at the FIRST `*`: the part before is the count text, the part after is the name. Strip whitespace from both. Any further `*` characters stay inside the name.
- If the item contains no `*`, its count is 1 and the whole stripped item is the name.
- The count text must consist only of the ASCII digits 0-9 (leading zeros are allowed) and its value must be at least 1; otherwise raise ValueError. So `0*x`, `-2*x` and `x*y` are all errors.
- The name must not be empty after stripping; otherwise raise ValueError.
- Return one (name, count) tuple per item, in input order. Repeated names stay separate entries; never merge them.

Reply with the function in a single ```python code block and nothing else.""",
        "tests": """assert parse_item_list("apple, 2*pear ,banana") == [("apple", 1), ("pear", 2), ("banana", 1)]
assert parse_item_list("") == []
assert parse_item_list("     ") == []
assert parse_item_list("egg") == [("egg", 1)]
assert parse_item_list("007*egg") == [("egg", 7)]
assert parse_item_list("2 * pear") == [("pear", 2)]
assert parse_item_list("1*a,1*a") == [("a", 1), ("a", 1)]
assert parse_item_list("3*a*b") == [("a*b", 3)]
assert parse_item_list("hot dog , 12*nut") == [("hot dog", 1), ("nut", 12)]
try:
    parse_item_list("0*x")
    assert False, "expected ValueError for count 0"
except ValueError:
    pass
try:
    parse_item_list("-2*x")
    assert False, "expected ValueError for negative count"
except ValueError:
    pass
try:
    parse_item_list("a,,b")
    assert False, "expected ValueError for empty item"
except ValueError:
    pass
try:
    parse_item_list("2*")
    assert False, "expected ValueError for empty name"
except ValueError:
    pass
try:
    parse_item_list("x*y")
    assert False, "expected ValueError for non-digit count"
except ValueError:
    pass
try:
    parse_item_list("apple,")
    assert False, "expected ValueError for trailing comma"
except ValueError:
    pass
try:
    parse_item_list("٣*egg")
    assert False, "expected ValueError for a non-ASCII digit"
except ValueError:
    pass
try:
    parse_item_list("+2*x")
    assert False, "expected ValueError for a signed count"
except ValueError:
    pass""",
        "reference": """def parse_item_list(s: str) -> list[tuple[str, int]]:
    if s.strip() == '':
        return []
    out = []
    for raw in s.split(','):
        item = raw.strip()
        if item == '':
            raise ValueError("empty item")
        if '*' in item:
            left, right = item.split('*', 1)
            left = left.strip()
            name = right.strip()
            if left == '' or not all(c in '0123456789' for c in left):
                raise ValueError("bad count")
            count = int(left)
            if count < 1:
                raise ValueError("count must be at least 1")
        else:
            name = item
            count = 1
        if name == '':
            raise ValueError("empty name")
        out.append((name, count))
    return out""",
        "wrong": """def parse_item_list(s: str) -> list[tuple[str, int]]:
    if not s.strip():
        return []
    out = []
    for raw in s.split(','):
        item = raw.strip()
        if not item:
            raise ValueError("empty item")
        if '*' in item:
            left, right = item.split('*', 1)
            count = int(left.strip())
            name = right.strip()
        else:
            count = 1
            name = item
        if not name:
            raise ValueError("empty name")
        out.append((name, count))
    return out""",
        "wrong_explanation": "It trusts int() for the count instead of requiring digits with a value of at least 1, so `0*x` and `-2*x` are accepted rather than raising ValueError.",
    },
]


# ── Other languages ───────────────────────────────────────────────────────────
# Non-Python tasks: see docs/llm-benchmark-review-2026-09-05.md § R5.

LANGUAGE_TASKS = [
    {
        "name": "bash_split_comma_list",
        "kind": "spec-transcription",
        "lang": "bash",
        "function": "split_list",
        "prompt": """A build script needs to turn one comma-separated option into a list it can loop over.

Write a bash function with this exact signature:
    split_list() { ... }

It takes the list as its first argument and prints one item per line, in input order.

Rules:
- Split the argument on commas, and on commas only.
- Print each item exactly as it appeared between the commas: spaces inside an item are part of that item and must survive, and no glob character is ever expanded.
- Skip items that are empty, so "a,,b" prints two lines and a trailing comma adds nothing.
- An empty or missing argument prints nothing and returns 0.
- The caller's IFS is `$'\\n\\t'` (newline and tab, no space) and your function is called under `set -euo pipefail`. It must be correct under both.

Reply with the function in a single ```bash code block and nothing else.""",
        "tests": """IFS=$'\\n\\t'
assert_eq "a
b
c" "$(split_list "a,b,c")" "three items"
assert_eq "one item" "$(split_list "one item")" "no comma is a single item"
assert_eq "a b
c d" "$(split_list "a b,c d")" "spaces inside an item survive"
assert_eq "*
?" "$(split_list "*,?")" "glob characters are never expanded"
assert_eq "a
b" "$(split_list "a,,b,")" "empty items are skipped"
assert_eq "" "$(split_list "")" "an empty argument prints nothing"
assert_ok "an empty argument still returns 0" split_list ""
""",
        "reference": """split_list() {
    local list="${1-}"
    local item
    local -a parts=()
    IFS=',' read -r -a parts <<< "$list" || true
    for item in ${parts[@]+"${parts[@]}"}; do
        if [ -n "$item" ]; then
            printf '%s\\n' "$item"
        fi
    done
}""",
        "wrong": """split_list() {
    local list="${1-}"
    local item
    for item in ${list//,/ }; do
        printf '%s\\n' $item
    done
}""",
        "wrong_explanation": "Substituting spaces for commas and relying on word splitting does not split at all under the caller's IFS of newline/tab, so the whole list arrives as one bogus item -- AGENTS.md shell bug class 3. The unquoted $item also expands globs.",
    },
    {
        "name": "bash_count_matches",
        "kind": "bug-fix",
        "lang": "bash",
        "function": "count_matches",
        "prompt": """This helper is called from a script running under `set -euo pipefail`. It should print how many lines of a file contain a pattern, but the whole script dies whenever nothing matches, and it reports failure on a file that simply has no match.

    count_matches() {
        local file="$1" pattern="$2"
        local n
        n=$(grep -c -e "$pattern" "$file")
        printf '%s\\n' "$n"
        [ "$n" -gt 0 ] && return 0
    }

Rewrite it. Write a bash function with this exact signature:
    count_matches() { ... }

Rules:
- The first argument is a file, the second a pattern passed to grep.
- Print the number of matching lines on one line, and print `0` when there are none.
- Return 0 whenever the file is readable, whatever the count. Finding nothing is a legitimate result, not an error.
- Return a non-zero status and print nothing on stdout when the file does not exist or cannot be read.
- It runs under `set -euo pipefail`, so an empty grep result must not abort the caller, and the function must not end on a bare `[ ... ] && ...` whose false case becomes its return value.

Reply with the function in a single ```bash code block and nothing else.""",
        "tests": """printf 'alpha\\nbeta\\nalpha gamma\\n' > sample.txt
assert_eq "2" "$(count_matches sample.txt alpha)" "two matching lines"
assert_eq "1" "$(count_matches sample.txt beta)" "one matching line"
assert_eq "0" "$(count_matches sample.txt zeta)" "no match prints 0"
assert_ok "no match is not an error" count_matches sample.txt zeta
assert_ok "a match is not an error" count_matches sample.txt alpha
assert_fail "an unreadable file is an error" count_matches nope.txt alpha
assert_eq "" "$(count_matches nope.txt alpha 2>/dev/null || true)" "an unreadable file prints nothing"
rc=0
captured="$(count_matches sample.txt zeta)" || rc=$?
assert_eq "0" "$rc" "capturing a zero-match result must not fail the caller"
assert_eq "0" "$captured" "the captured value is 0"
""",
        "reference": """count_matches() {
    local file="${1-}"
    local pattern="${2-}"
    local n=0
    if [ ! -r "$file" ]; then
        return 1
    fi
    n=$(grep -c -e "$pattern" "$file" || true)
    if [ -z "$n" ]; then
        n=0
    fi
    printf '%s\\n' "$n"
    return 0
}""",
        "wrong": """count_matches() {
    local file="${1-}"
    local pattern="${2-}"
    local n
    n=$(grep -c -e "$pattern" "$file")
    printf '%s\\n' "$n"
    [ "$n" -gt 0 ] && return 0
}""",
        "wrong_explanation": "Unguarded $(grep -c) exits 1 on a legitimate empty result (AGENTS.md class 2) and the closing bare `[ ... ] && return 0` makes the healthy zero-match path return 1 (class 5).",
    },
    {
        "name": "bash_print_args",
        "kind": "spec-transcription",
        "lang": "bash",
        "function": "print_args",
        "prompt": """Write a bash function with this exact signature:
    print_args() { ... }

It prints each of its arguments on its own line, in order.

Rules:
- One line per argument, printed byte for byte as it was passed.
- An argument containing spaces stays ONE line; an argument containing a glob character such as `*` or `?` is printed literally and never expanded against the current directory.
- With no arguments it prints nothing and returns 0.
- An argument that begins with a hyphen, such as `-n` or `-e`, is printed as text, not treated as an option.
- It runs under `set -euo pipefail`.

Reply with the function in a single ```bash code block and nothing else.""",
        "tests": """assert_eq "a b
c d" "$(print_args "a b" "c d")" "spaces do not split an argument"
assert_eq "1" "$(print_args "x y z" | wc -l)" "one argument is one line"
assert_eq "*" "$(print_args "*")" "a glob is printed literally"
assert_eq "-n" "$(print_args "-n")" "a leading hyphen is text, not an option"
assert_eq "" "$(print_args)" "no arguments prints nothing"
assert_ok "no arguments returns 0" print_args
""",
        "reference": """print_args() {
    local arg
    for arg in "$@"; do
        printf '%s\\n' "$arg"
    done
}""",
        "wrong": """print_args() {
    for arg in $@; do
        echo $arg
    done
}""",
        "wrong_explanation": "Unquoted $@ and $arg word-split and glob-expand every argument (the unquoted-expansion class), and `echo -n` is swallowed as an option.",
    },
    {
        "name": "bash_head_of_file",
        "kind": "bug-fix",
        "lang": "bash",
        "function": "head_of_file",
        "prompt": """This helper prints the first N lines of a file. It reports success on a file that does not exist, because the pipeline's status is the status of `head`, not of `cat`, and the caller does not set `pipefail`.

    head_of_file() {
        local file="$1" n="$2"
        cat "$file" | head -n "$n"
    }

Rewrite it. Write a bash function with this exact signature:
    head_of_file() { ... }

Rules:
- The first argument is a file, the second the number of lines to print.
- Print the first N lines of the file; if the file is shorter, print all of it.
- Return a non-zero status, and print nothing on stdout, when the file does not exist or cannot be read.
- The caller may have `pipefail` switched OFF, so you must not depend on it: check the status yourself, or do not use a pipeline at all.

Reply with the function in a single ```bash code block and nothing else.""",
        "tests": """set +o pipefail
printf 'l1\\nl2\\nl3\\n' > lines.txt
assert_eq "l1
l2" "$(head_of_file lines.txt 2)" "the first two lines"
assert_eq "l1
l2
l3" "$(head_of_file lines.txt 10)" "n larger than the file prints all of it"
assert_ok "a readable file returns 0" head_of_file lines.txt 1
assert_fail "a missing file fails even with pipefail off" head_of_file missing.txt 2
assert_eq "" "$(head_of_file missing.txt 2 2>/dev/null || true)" "a missing file prints nothing"
""",
        "reference": """head_of_file() {
    local file="${1-}"
    local n="${2-10}"
    if [ ! -r "$file" ]; then
        return 1
    fi
    head -n "$n" -- "$file"
}""",
        "wrong": """head_of_file() {
    local file="${1-}"
    local n="${2-10}"
    cat "$file" | head -n "$n"
}""",
        "wrong_explanation": "With pipefail off the pipeline's status is head's, so a missing file is reported as success -- the pipefail-masking class.",
    },
    {
        "name": "cmake_normalize_arch",
        "kind": "spec-transcription",
        "lang": "cmake",
        "function": "normalize_arch",
        "prompt": """The build scripts see several spellings of the same machine and need one label per architecture.

Write a CMake function with this exact signature:
    function(normalize_arch out_var arch)

Rules:
- Compare `arch` case-insensitively.
- `x86_64`, `amd64` and `x64` all map to `amd64`.
- `aarch64` and `arm64` both map to `arm64`.
- `riscv64` maps to `riscv64`.
- Anything else, including an empty string, maps to the empty string.
- Set the result in the CALLER's scope, in the variable whose NAME is given by `out_var`. After `normalize_arch(result "x86_64")` the caller must see `${result}` equal to `amd64`.

Reply with the function in a single ```cmake code block and nothing else.""",
        "tests": """normalize_arch(r_a "x86_64")
assert_eq("amd64" "${r_a}" "x86_64")
normalize_arch(r_b "AMD64")
assert_eq("amd64" "${r_b}" "AMD64 is case-insensitive")
normalize_arch(r_c "x64")
assert_eq("amd64" "${r_c}" "x64")
normalize_arch(r_d "aarch64")
assert_eq("arm64" "${r_d}" "aarch64")
normalize_arch(r_e "arm64")
assert_eq("arm64" "${r_e}" "arm64")
normalize_arch(r_f "riscv64")
assert_eq("riscv64" "${r_f}" "riscv64")
normalize_arch(r_g "ppc64le")
assert_eq("" "${r_g}" "an unknown machine maps to nothing")
normalize_arch(r_h "")
assert_eq("" "${r_h}" "an empty machine maps to nothing")
""",
        "reference": """function(normalize_arch out_var arch)
  string(TOLOWER "${arch}" lowered)
  if(lowered STREQUAL "x86_64" OR lowered STREQUAL "amd64" OR lowered STREQUAL "x64")
    set(mapped "amd64")
  elseif(lowered STREQUAL "aarch64" OR lowered STREQUAL "arm64")
    set(mapped "arm64")
  elseif(lowered STREQUAL "riscv64")
    set(mapped "riscv64")
  else()
    set(mapped "")
  endif()
  set(${out_var} "${mapped}" PARENT_SCOPE)
endfunction()""",
        "wrong": """function(normalize_arch out_var arch)
  string(TOLOWER "${arch}" lowered)
  if(lowered STREQUAL "x86_64" OR lowered STREQUAL "amd64" OR lowered STREQUAL "x64")
    set(mapped "amd64")
  elseif(lowered STREQUAL "aarch64" OR lowered STREQUAL "arm64")
    set(mapped "arm64")
  elseif(lowered STREQUAL "riscv64")
    set(mapped "riscv64")
  else()
    set(mapped "")
  endif()
  set(${out_var} "${mapped}")
endfunction()""",
        "wrong_explanation": "Without PARENT_SCOPE the result is set in the function's own scope, so every caller reads an empty variable -- the classic CMake out-parameter bug.",
    },
    {
        "name": "dockerfile_pinned_tool_image",
        "kind": "design",
        "lang": "dockerfile",
        "prompt": """Write a Dockerfile for a small Debian-based image that runs one Python entrypoint script, `/app/tool.py`, which is in the build context.

Requirements:
- Exactly one `FROM`, and its image reference must carry an explicit tag that is not `latest`. An untagged image and `:latest` are both refused.
- Install `python3` with apt in a SINGLE `RUN` instruction that runs `apt-get update`, installs with `--no-install-recommends`, and ends with `apt-get clean` in that same `RUN`. Two separate `RUN` instructions for the update and the install are refused, because the cached update layer can then serve a stale package index.
- Set `WORKDIR /app` before any `COPY`.
- Use `COPY`, never `ADD`, to bring the script in.
- Create and switch to a non-root user with a `USER` instruction, and place it AFTER the install `RUN`.
- End with an `ENTRYPOINT` in exec form -- a JSON array such as `["python3", "/app/tool.py"]`, not a bare shell string.

Reply with the Dockerfile in a single ```dockerfile code block and nothing else.""",
        "tests": """froms = instructions("FROM")
assert len(froms) == 1, f"expected exactly one FROM, got {len(froms)}"
image = froms[0].split()[0]
assert ":" in image or "@" in image, f"the base image {image!r} carries no tag"
assert not image.endswith(":latest"), "the base image is pinned to :latest"

runs = instructions("RUN")
installs = [r for r in runs if "apt-get install" in r or "apt install" in r]
assert len(installs) == 1, f"expected one apt install RUN, got {len(installs)}"
assert "apt-get update" in installs[0], "apt-get update is not in the install RUN"
assert "--no-install-recommends" in installs[0], "installed without --no-install-recommends"
assert "apt-get clean" in installs[0], "the install RUN does not end with apt-get clean"
assert "python3" in installs[0], "python3 is not installed"
assert not [r for r in runs if "apt-get update" in r and r != installs[0]], \\
    "apt-get update also runs in a separate RUN"

order = verbs()
assert "WORKDIR" in order and "COPY" in order, "WORKDIR and COPY are both required"
assert order.index("WORKDIR") < order.index("COPY"), "WORKDIR must come before COPY"
assert "ADD" not in order, "ADD was used where COPY was required"
assert "USER" in order, "no USER instruction: the image runs as root"
assert order.index("USER") > order.index("RUN"), "USER must come after the install RUN"
assert instructions("USER")[-1].strip() not in ("root", "0"), "the final USER is root"

entry = instructions("ENTRYPOINT")
assert len(entry) == 1, f"expected exactly one ENTRYPOINT, got {len(entry)}"
assert entry[0].strip().startswith("["), "ENTRYPOINT is not in exec (JSON array) form"
assert "tool.py" in entry[0], "the entrypoint does not run tool.py"
""",
        "reference": """FROM debian:bookworm-slim
RUN apt-get update \\
 && apt-get install -y --no-install-recommends python3 \\
 && apt-get clean
RUN useradd --create-home --uid 10001 tool
USER tool
WORKDIR /app
COPY tool.py /app/tool.py
ENTRYPOINT ["python3", "/app/tool.py"]""",
        "wrong": """FROM debian:latest
RUN apt-get update
RUN apt-get install -y python3
COPY tool.py /app/tool.py
WORKDIR /app
ENTRYPOINT python3 /app/tool.py""",
        "wrong_explanation": "Pinned to :latest, apt update split from the install so the cached update layer can serve a stale index, no --no-install-recommends and no clean, COPY before WORKDIR, no USER (it runs as root) and a shell-form ENTRYPOINT.",
    },
]
