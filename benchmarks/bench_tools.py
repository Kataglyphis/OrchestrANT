#!/usr/bin/env python3
"""Measure tool/function calling — the capability an agent actually lives on.

A model that writes flawless code but cannot emit a valid tool call is useless
inside a coding agent: it never gets to read a file, run a test, or apply a
patch. Neither the speed benchmarks nor the coding benchmark touch this.

What is graded, per case:
  * did it call a tool at all when it should have (and NOT when it should not)
  * did it pick the right tool from several plausible ones
  * are the arguments valid JSON, of the type the schema declares
  * do the required arguments carry the right values
  * when N calls are needed at once, did it make exactly those N

The "no tool needed" cases are deliberate. Over-eager tool calling is a real
failure mode: a model that reaches for a tool on every turn burns a round trip
and, in an agent loop, can spin. Half of them say so ("do not use any tool");
the other half — the irrelevance cases — never mention tools at all.

Usage:
    python3 bench_tools.py --backend geniex-npu
    python3 bench_tools.py --compare candidates.json --repeats 3
    python3 bench_tools.py --backend geniex-cpu --tools opencode
"""

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# One request path for every tool: the entry's Authorization header and its
# request_extra live in bench_cli, not in six hand-built Requests.

# Standalone runs of these scripts (they are not a package) need the repo
# root on sys.path; the runner lives in orchestrant.benchmark.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from orchestrant.benchmark import client as bench_cli
import tools_opencode  # noqa: E402
from geniex_toolcall_shim import parse_tool_calls as _parse_qwen_blocks  # noqa: E402

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full contents of one file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file"},
                    "max_lines": {
                        "type": "integer",
                        "description": "Return only the first N lines",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List the files in a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory to list"},
                    "recursive": {
                        "type": "boolean",
                        "description": "Also descend into subdirectories",
                    },
                },
                "required": ["directory"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search the repository for a string.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text to search for"},
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "Match case exactly",
                    },
                    "include": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Only search files matching these glob patterns",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run the test suite and return the results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Optional path to a test file",
                    },
                    "verbosity": {
                        "type": "string",
                        "enum": ["quiet", "normal", "verbose"],
                        "description": "How much output to show",
                    },
                },
                "required": [],
            },
        },
    },
    # The four below exist to make SELECTION hard. With only distinct tools a
    # model can succeed by elimination; agents fail on near-neighbours -- write
    # vs patch, status vs diff -- so the set has to contain some.
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Overwrite a file with new content, replacing everything in it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file"},
                    "content": {
                        "type": "string",
                        "description": "The new full content",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": "Apply a unified diff to a file, changing only the lines it touches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file"},
                    "diff": {"type": "string", "description": "Unified diff to apply"},
                },
                "required": ["path", "diff"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "List which files have uncommitted changes. Does not show the changes themselves.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show the actual line-by-line changes in the working tree.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Optional path to limit the diff",
                    }
                },
                "required": [],
            },
        },
    },
]

TOOL_SETS = {"default": TOOLS, "opencode": tools_opencode.TOOLS}
TOOL_SET_SOURCES = {"default": "bench_tools.TOOLS", "opencode": tools_opencode.SOURCE}

_DIFF = "--- a/app.py\n+++ b/app.py\n@@\n-x = 1\n+x = 2\n"

# expect=None: no tool call. A list: exactly those calls, in any order.
# {"contains": [...]}: whitespace-normalised substrings, not equality.
# One defensible answer per case; size rationale in docs/llm-benchmark-roadmap.md.

CASES = [
    # ── does it call at all, and pick the obvious tool ──────────────────────
    {
        "name": "simple_read",
        "category": "call",
        "prompt": "Show me the contents of README.md.",
        "variants": ["What's in README.md?", "Please open README.md and display it."],
        "expect": {"name": "read_file", "args": {"path": "README.md"}},
    },
    {
        "name": "list_a_directory",
        "category": "call",
        "prompt": "Which files are in the directory src?",
        "variants": [
            "List the contents of the src directory.",
            "What does src contain? Just the file names.",
        ],
        "expect": {"name": "list_files", "args": {"directory": "src"}},
    },
    {
        "name": "nested_path",
        "category": "call",
        "prompt": "Open the file benchmarks/README.md and show it to me.",
        "expect": {"name": "read_file", "args": {"path": "benchmarks/README.md"}},
    },
    {
        "name": "run_the_tests",
        "category": "call",
        "prompt": "Run the test suite.",
        "expect": {"name": "run_tests", "args": {}},
    },
    {
        "name": "run_one_test_file",
        "category": "call",
        "prompt": "Run only the tests in tests/test_api.py.",
        "expect": {"name": "run_tests", "args": {"path": "tests/test_api.py"}},
    },
    # ── near-neighbour discrimination: the failure agents actually hit ──────
    # Variants share fewer than two content words with the tool description.
    {
        "name": "contents_not_names",
        "category": "selection",
        "prompt": "What does the file config.yaml contain?",
        "variants": [
            "Print out what is inside config.yaml.",
            "I want to see config.yaml itself, top to bottom.",
        ],
        "expect": {"name": "read_file", "args": {"path": "config.yaml"}},
    },
    {
        "name": "names_not_contents",
        "category": "selection",
        "prompt": "I only need the file names under docs, not what is in them.",
        "variants": [
            "Just tell me what sits under docs: the entries by name, nothing more.",
            "Give me the names of everything under docs, without opening any of them.",
        ],
        "expect": {"name": "list_files", "args": {"directory": "docs"}},
    },
    {
        "name": "overwrite_not_patch",
        "category": "selection",
        "prompt": "Replace the entire contents of notes.txt with the single word: done.",
        "variants": [
            "Wipe notes.txt and put the single word done into it.",
            "Make notes.txt hold exactly one word from now on: done.",
        ],
        "expect": {
            "name": "write_file",
            "args": {"path": "notes.txt", "content": "done"},
        },
    },
    {
        "name": "patch_not_overwrite",
        "category": "selection",
        "prompt": (
            "Apply this unified diff to app.py, changing only the lines it "
            "touches:\n" + _DIFF
        ),
        "variants": [
            "Here is a hunk for app.py; make exactly this edit and nothing else:\n"
            + _DIFF
        ],
        "expect": {
            "name": "apply_patch",
            "args": {"path": "app.py", "diff": {"contains": ["-x = 1", "+x = 2"]}},
        },
    },
    {
        "name": "which_files_changed",
        "category": "selection",
        "prompt": "Which files have uncommitted changes? I do not need to see the changes.",
        "variants": [
            "What have I modified since the last commit? Names only, no content.",
            "Give me the set of paths with pending edits, just the paths.",
        ],
        "expect": {"name": "git_status", "args": {}},
    },
    {
        "name": "what_changed_in_them",
        "category": "selection",
        "prompt": "Show me the actual line-by-line changes in the working tree.",
        "variants": [
            "Print every edit I have made so far, hunk by hunk.",
            "What exactly did I alter since my last commit? Give me the hunks.",
        ],
        "expect": {"name": "git_diff", "args": {}},
    },
    {
        "name": "diff_one_path",
        "category": "selection",
        "prompt": "Show me the line-by-line changes to src/main.py only.",
        "variants": [
            "Print the edits made to src/main.py so far, hunk by hunk, "
            "and nothing about other paths."
        ],
        "expect": {"name": "git_diff", "args": {"path": "src/main.py"}},
    },
    # ── argument extraction ────────────────────────────────────────────────
    {
        "name": "extract_query",
        "category": "arguments",
        "prompt": "Search the repository for the string merge_sorted.",
        "variants": [
            "Find merge_sorted in the code.",
            "Grep the repo for merge_sorted.",
        ],
        "expect": {"name": "search_code", "args": {"query": "merge_sorted"}},
    },
    {
        "name": "extract_query_with_spaces",
        "category": "arguments",
        "prompt": 'Search the repository for the exact phrase "no such file".',
        "expect": {"name": "search_code", "args": {"query": "no such file"}},
    },
    {
        "name": "extract_query_with_symbols",
        "category": "arguments",
        "prompt": "Find every occurrence of the string __init__.py in the code.",
        "expect": {"name": "search_code", "args": {"query": "__init__.py"}},
    },
    {
        "name": "optional_boolean_true",
        "category": "arguments",
        "prompt": "Search for the exact string Foo, matching case exactly.",
        "expect": {
            "name": "search_code",
            "args": {"query": "Foo", "case_sensitive": True},
        },
    },
    {
        "name": "optional_boolean_false",
        "category": "arguments",
        "prompt": "Search for the string foo, ignoring case entirely.",
        "expect": {
            "name": "search_code",
            "args": {"query": "foo", "case_sensitive": False},
        },
    },
    {
        "name": "path_not_query",
        "category": "arguments",
        "prompt": "Read the file src/search_code.py.",
        "expect": {"name": "read_file", "args": {"path": "src/search_code.py"}},
    },
    # ── typed arguments: the schema says integer/boolean/enum/array ────────
    {
        "name": "typed_int_max_lines",
        "category": "typed_args",
        "prompt": "Show me only the first 40 lines of CHANGELOG.md.",
        "expect": {
            "name": "read_file",
            "args": {"path": "CHANGELOG.md", "max_lines": 40},
        },
    },
    {
        "name": "typed_bool_recursive",
        "category": "typed_args",
        "prompt": "List every file under src, including the ones inside its subdirectories.",
        "expect": {
            "name": "list_files",
            "args": {"directory": "src", "recursive": True},
        },
    },
    {
        "name": "typed_enum_verbosity",
        "category": "typed_args",
        "prompt": "Run the test suite with verbose output.",
        "expect": {"name": "run_tests", "args": {"verbosity": "verbose"}},
    },
    {
        "name": "typed_array_include",
        "category": "typed_args",
        "prompt": "Search for the string TODO, but only in files matching *.py and *.md.",
        "expect": {
            "name": "search_code",
            "args": {"query": "TODO", "include": ["*.py", "*.md"]},
        },
    },
    # ── parallel: several independent calls wanted in ONE turn ─────────────
    {
        "name": "parallel_two_reads",
        "category": "parallel",
        "prompt": "Show me the contents of both README.md and LICENSE.",
        "expect": [
            {"name": "read_file", "args": {"path": "README.md"}},
            {"name": "read_file", "args": {"path": "LICENSE"}},
        ],
    },
    {
        "name": "parallel_two_searches",
        "category": "parallel",
        "prompt": "Search the repository for the string foo_bar and, separately, for the string baz_qux.",
        "expect": [
            {"name": "search_code", "args": {"query": "foo_bar"}},
            {"name": "search_code", "args": {"query": "baz_qux"}},
        ],
    },
    {
        "name": "parallel_multiple_read_and_status",
        "category": "parallel",
        "prompt": "Show me the contents of setup.py, and also tell me which files have uncommitted changes.",
        "expect": [
            {"name": "read_file", "args": {"path": "setup.py"}},
            {"name": "git_status", "args": {}},
        ],
    },
    {
        "name": "parallel_multiple_list_and_tests",
        "category": "parallel",
        "prompt": "List the files in the directory tests, and run the whole test suite.",
        "expect": [
            {"name": "list_files", "args": {"directory": "tests"}},
            {"name": "run_tests", "args": {}},
        ],
    },
    # ── restraint: told not to call ────────────────────────────────────────
    {
        "name": "no_tool_arithmetic",
        "category": "restraint",
        "prompt": "What is 2 + 2? Answer directly, do not use any tool.",
        "variants": [
            "Without calling anything, what is 2 plus 2?",
            "Answer from your own knowledge, no tools: what is 2+2?",
        ],
        "expect": None,
    },
    {
        "name": "no_tool_definition",
        "category": "restraint",
        "prompt": "In one sentence and without using any tool, what does the acronym API stand for?",
        "expect": None,
    },
    {
        "name": "no_tool_greeting",
        "category": "restraint",
        "prompt": "Say hello. Do not call any tool.",
        "expect": None,
    },
    # ── irrelevance: nothing here needs a tool, and nobody says so ─────────
    {
        "name": "irrelevance_concept",
        "category": "irrelevance",
        "prompt": "In one sentence, what is the difference between a list and a tuple in Python?",
        "expect": None,
    },
    {
        "name": "irrelevance_units",
        "category": "irrelevance",
        "prompt": "How many minutes are there in three and a half hours?",
        "expect": None,
    },
    {
        "name": "irrelevance_translation",
        "category": "irrelevance",
        "prompt": "How do you say 'good morning' in German?",
        "expect": None,
    },
    {
        "name": "irrelevance_opinion",
        "category": "irrelevance",
        "prompt": "Is it better to pin dependency versions in a library or in an application? One sentence.",
        "expect": None,
    },
]

# ── Multi-turn cases ──────────────────────────────────────────────────────────
#
# Where single-turn scores stop and agents keep going. A model that emits one
# perfect call and then ignores what came back is useless in a loop, and no
# single-turn score can see it.
#
# `kind` selects the grader: "use_result" wants the returned value used;
# "recover" wants a failure admitted or retried, never invented over.


def _tool_turn(user, name, arguments, result, call_id="call_x"):
    return [
        {"role": "user", "content": user},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "content": result},
    ]


def _tool_turns(user, turns):
    history = [{"role": "user", "content": user}]
    for i, (name, arguments, result) in enumerate(turns, 1):
        history += _tool_turn(user, name, arguments, result, call_id=f"call_{i}")[1:]
    return history


LONG_RESULT_FAILING_TEST = "tests/test_parser.py::test_handles_unicode_bom"


def _long_test_output(lines=150):
    """~2k tokens of pytest output with ONE failure buried two thirds in."""
    out = []
    for i in range(lines):
        out.append(
            f"tests/test_module_{i // 12:02d}.py::test_case_{i:03d} PASSED"
            f"    [{(i + 1) * 100 // (lines + 1):3d}%]"
        )
        if i == lines * 2 // 3:
            out.append(f"{LONG_RESULT_FAILING_TEST} FAILED    [ 67%]")
    out += [
        "",
        "=" * 24 + " FAILURES " + "=" * 24,
        "AssertionError: expected 'abc', got '\\ufeffabc'",
        f"FAILED {LONG_RESULT_FAILING_TEST} - AssertionError",
        f"{lines} passed, 1 failed in 12.34s",
    ]
    return "\n".join(out)


MULTI_CASES = [
    {
        "name": "use_file_contents",
        "kind": "use_result",
        "category": "use_result",
        "must_contain": ["9.4.1"],
        "history": _tool_turn(
            "What is in the file VERSION.txt?",
            "read_file",
            '{"path": "VERSION.txt"}',
            "9.4.1-rc2",
        ),
    },
    {
        "name": "use_search_hit",
        "kind": "use_result",
        "category": "use_result",
        "must_contain": ["bench_tools.py"],
        "history": _tool_turn(
            "Where is the function grade defined?",
            "search_code",
            '{"query": "def grade"}',
            "bench_tools.py:112: def grade(message, expect):",
        ),
    },
    {
        "name": "use_test_result",
        "kind": "use_result",
        "category": "use_result",
        "must_contain": ["3"],
        "history": _tool_turn(
            "Run the tests and tell me how many failed.",
            "run_tests",
            "{}",
            "17 passed, 3 failed",
        ),
    },
    {
        "name": "use_listing",
        "kind": "use_result",
        "category": "use_result",
        "must_contain": ["setup.py"],
        "history": _tool_turn(
            "Which files are in the project root?",
            "list_files",
            '{"directory": "."}',
            "README.md\nsetup.py\nsrc/",
        ),
    },
    {
        "name": "recover_missing_file",
        "kind": "recover",
        "category": "recover",
        "history": _tool_turn(
            "What is in config/secret.yaml?",
            "read_file",
            '{"path": "config/secret.yaml"}',
            "Error: ENOENT: no such file or directory",
        ),
    },
    {
        "name": "recover_permission_denied",
        "kind": "recover",
        "category": "recover",
        "history": _tool_turn(
            "Read /etc/shadow for me.",
            "read_file",
            '{"path": "/etc/shadow"}',
            "Error: EACCES: permission denied",
        ),
    },
    {
        "name": "long_result_find_failure",
        "kind": "use_result",
        "category": "long_result",
        "must_contain": ["test_handles_unicode_bom"],
        "history": _tool_turn(
            "Run the tests and tell me the name of the one test that failed.",
            "run_tests",
            '{"verbosity": "verbose"}',
            _long_test_output(),
        ),
    },
    {
        "name": "deep_history_version",
        "kind": "use_result",
        "category": "deep_history",
        "must_contain": ["2.7.3"],
        "history": _tool_turns(
            "I am preparing a release. Read setup.py for the declared version, then list "
            "src, run the tests, check git status and the diff, and finally tell me the "
            "version number setup.py declares.",
            [
                (
                    "read_file",
                    '{"path": "setup.py"}',
                    "from setuptools import setup\nsetup(name='demo', version='2.7.3')",
                ),
                ("list_files", '{"directory": "src"}', "demo/\n__init__.py\ncli.py"),
                ("run_tests", "{}", "40 passed in 3.2s"),
                ("git_status", "{}", "nothing to commit, working tree clean"),
                ("git_diff", "{}", ""),
            ],
        ),
    },
    {
        "name": "repeated_error_same_path",
        "kind": "recover",
        "category": "repeated_error",
        "history": _tool_turns(
            "What is in config/settings.yaml?",
            [
                (
                    "read_file",
                    '{"path": "config/settings.yaml"}',
                    "Error: ENOENT: no such file or directory",
                ),
                (
                    "read_file",
                    '{"path": "config/settings.yaml"}',
                    "Error: ENOENT: no such file or directory",
                ),
            ],
        ),
    },
]

# Never this file: from ~1.3k tokens the padding would carry the CASES table,
# prompt beside expected answer. bench_coding excludes its own source the same way.
PAD_SOURCES = ["bench_coding.py", "bench_agent.py", "inspect_gguf.py"]


def context_padding(approx_tokens):
    """Roughly `approx_tokens` tokens of real repository source.

    Long context and tool calling were measured SEPARATELY and never together,
    yet together is precisely what an agent turn is: a large prompt AND a tool
    call. On a bundle whose 4096-token limit is shared between input and output,
    a long prompt leaves little room to answer — and nobody had checked which
    breaks first.
    """
    if not approx_tokens:
        return ""
    here = os.path.dirname(os.path.abspath(__file__))
    chunks = []
    for name in PAD_SOURCES:
        try:
            with open(os.path.join(here, name)) as f:
                chunks.append(f"# ---- {name} ----\n{f.read()}")
        except OSError:
            continue
    body = "\n\n".join(chunks)
    while len(body) < approx_tokens * 4 and chunks:
        body += "\n\n" + "\n\n".join(chunks)
    return body[: approx_tokens * 4]


def turn_growth(
    base_url, model, system=None, max_turns=12, result_tokens=300, entry=None
):
    """How many agent turns fit before the context runs out.

    An agent loop grows its context every turn: each tool result is appended
    and re-sent. On a 4096-token model the interesting number is not tok/s but
    how many turns happen before the server silently returns nothing — which is
    exactly what it does past the limit: HTTP 200, zero tokens, no error.
    """
    print(f"\n  === turn growth: {model} ===", flush=True)
    history = [{"role": "user", "content": "Read README.md and tell me what it says."}]
    filler = context_padding(result_tokens)
    rows = []
    for turn in range(1, max_turns + 1):
        try:
            message, finish, wall = call_multi(
                base_url, model, history, system=system, entry=entry
            )
        except Exception as e:  # noqa: BLE001
            print(f"    turn {turn:2d}: ERROR {type(e).__name__}", flush=True)
            rows.append({"turn": turn, "error": str(e)[:80]})
            break
        called = bool(message.get("tool_calls"))
        text = message.get("content") or ""
        empty = not called and not text.strip()
        approx_ctx = sum(len(str(m.get("content") or "")) for m in history) // 4
        print(
            f"    turn {turn:2d}: ~{approx_ctx:5d} ctx tokens  "
            f"{'tool_call' if called else ('EMPTY' if empty else 'text')}  "
            f"{wall:6.2f}s  finish={finish}",
            flush=True,
        )
        rows.append(
            {
                "turn": turn,
                "approx_context_tokens": approx_ctx,
                "tool_call": called,
                "empty": empty,
                "wall_s": round(wall, 2),
                "finish_reason": finish,
            }
        )
        if empty:
            print(
                f"    -> went silent at turn {turn} (~{approx_ctx} context tokens). "
                f"No error was returned; an agent sees an empty answer.",
                flush=True,
            )
            break
        # Grow the history the way a real loop does.
        if called:
            call_id = message["tool_calls"][0].get("id", f"c{turn}")
            history.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": message["tool_calls"],
                }
            )
            history.append({"role": "tool", "tool_call_id": call_id, "content": filler})
        else:
            history.append({"role": "assistant", "content": text})
        history.append(
            {
                "role": "user",
                "content": f"Now search the repository for the string marker_{turn}.",
            }
        )
    else:
        print(f"    -> still answering after {max_turns} turns", flush=True)
    return rows


def call_multi(
    base_url, model, messages, timeout=900, system=None, tools=None, entry=None
):
    """One request over an arbitrary message history (multi-turn).

    `entry` is the backends.json entry; bench_cli.post_json applies its
    api_key_env, headers and request_extra, so a hosted endpoint is measurable
    and an explicit model/max_tokens/temperature here still wins over a
    registry default.
    """
    msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 600,
        "tools": tools or TOOLS,
        "tool_choice": "auto",
        "messages": msgs,
    }
    started = time.monotonic()
    with bench_cli.post_json(
        f"{base_url}/v1/chat/completions", body, entry=entry, timeout=timeout
    ) as r:
        data = r.json()
    choice = data["choices"][0]
    return (
        choice.get("message", {}),
        choice.get("finish_reason"),
        time.monotonic() - started,
    )


def call(base_url, model, prompt, timeout=900, system=None, tools=None, entry=None):
    return call_multi(
        base_url,
        model,
        [{"role": "user", "content": prompt}],
        timeout=timeout,
        system=system,
        tools=tools,
        entry=entry,
    )


# ── graders ──────────────────────────────────────────────────────────────────

JSON_TYPES = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def _param_schema(tools, tool_name, key):
    for t in tools:
        fn = t["function"]
        if fn["name"] == tool_name:
            return (fn.get("parameters") or {}).get("properties", {}).get(key)
    return None


def _type_ok(got, schema):
    jtype = (schema or {}).get("type")
    allowed = JSON_TYPES.get(jtype)
    if not allowed:
        return True
    # bool is an int to Python; to a JSON schema it never is.
    if isinstance(got, bool) and jtype != "boolean":
        return False
    return isinstance(got, allowed)


def _is_path_param(key):
    k = key.lower()
    return k in ("path", "directory", "dir", "file") or k.endswith(
        ("path", "dir", "directory")
    )


def _norm_path(s):
    """Exactly one leading './' and one trailing '/': the two spellings a model
    cannot be blamed for. Never a bare '.' -- 'done.' is not 'done'."""
    s = s.strip()
    if s.startswith("./"):
        s = s[2:]
    if len(s) > 1 and s.endswith("/"):
        s = s[:-1]
    return s


def _canon(key, value):
    if isinstance(value, str):
        return _norm_path(value) if _is_path_param(key) else value.strip()
    if isinstance(value, list):
        return sorted(json.dumps(v, sort_keys=True) for v in value)
    return value


def _parse_args(raw):
    """(args, error). Servers differ on whether arguments arrive as text or a dict."""
    if isinstance(raw, str):
        try:
            return (json.loads(raw) if raw.strip() else {}), None
        except json.JSONDecodeError as e:
            return None, f"arguments are not valid JSON: {e}"
    if isinstance(raw, dict):
        return raw, None
    return {}, None


def _arg_matches(key, got, want, schema):
    """None when the argument is right, else the detail to report."""
    if isinstance(want, dict) and "contains" in want:
        if not isinstance(got, str):
            return f"{key}={got!r}, expected text"
        flat = " ".join(got.split())
        for token in want["contains"]:
            if " ".join(token.split()) not in flat:
                return f"{key} does not contain {token!r}"
        return None
    if not _type_ok(got, schema):
        return f"{key}={got!r}, expected {want!r} ({schema['type']})"
    if (schema or {}).get("enum") and got not in schema["enum"]:
        return f"{key}={got!r}, not one of {schema['enum']}"
    if _canon(key, got) != _canon(key, want):
        return f"{key}={got!r}, expected {want!r}"
    return None


def _grade_one_call(call_, exp, tools):
    fn = call_.get("function", {})
    if fn.get("name") != exp["name"]:
        return f"called {fn.get('name')!r}, expected {exp['name']!r}"
    args, err = _parse_args(fn.get("arguments"))
    if err:
        return err
    for key, want in exp["args"].items():
        if key not in args:
            return f"missing argument {key!r}"
        bad = _arg_matches(key, args[key], want, _param_schema(tools, exp["name"], key))
        if bad:
            return bad
    return None


def _coerce_by_schema(name, args, tools):
    """Text-channel values are all strings; the schema says what they meant."""
    typed = {}
    for key, raw in args.items():
        jtype = (_param_schema(tools, name, key) or {}).get("type")
        val = raw.strip() if isinstance(raw, str) else raw
        if isinstance(val, str):
            low = val.lower()
            if jtype == "boolean" and low in ("true", "false"):
                val = low == "true"
            elif jtype == "integer" and val.lstrip("-").isdigit():
                val = int(val)
            elif jtype == "number":
                try:
                    val = float(val)
                except ValueError:
                    pass
            elif jtype in ("array", "object"):
                try:
                    val = json.loads(val)
                except json.JSONDecodeError:
                    pass
        typed[key] = val
    return typed


def _call_from_obj(obj):
    if not isinstance(obj, dict):
        return None
    name = obj.get("name") or obj.get("tool") or obj.get("function")
    if isinstance(name, dict):
        name = name.get("name")
    if not isinstance(name, str):
        return None
    args = obj.get("parameters") or obj.get("arguments") or obj.get("args") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return None
    if not isinstance(args, dict):
        return None
    return {
        "id": "recovered",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _tool_calls_from_text(message, tools):
    """Tool calls the model wrote as prose instead of emitting properly.

    Three models from three vendors (Llama-3.2-3B, Phi-4-mini, Qwen3.8-2B) fail
    most single-turn cases the same way: correct tool name, correct arguments,
    wrong channel. An agent with a fallback parser recovers those turns.

    Accepted shapes: Qwen's <tool_call><function=..><parameter=..> template
    (one call per block, via the shim's parser), and a JSON object or list of
    objects with a name and parameters/arguments. Nothing is guessed: without a
    recognisable name field this returns [], so the fallback can never
    manufacture a call the model did not describe.
    """
    content = (message.get("content") or "").strip()
    if "</think>" in content:
        content = content.split("</think>")[-1].strip()
    if not content:
        return []
    if "<function=" in content:
        _, calls = _parse_qwen_blocks(content)
        for c in calls:
            args, _ = _parse_args(c["function"]["arguments"])
            c["function"]["arguments"] = json.dumps(
                _coerce_by_schema(c["function"]["name"], args or {}, tools)
            )
        return calls
    starts = [i for i in (content.find("{"), content.find("[")) if i >= 0]
    if not starts:
        return []
    start = min(starts)
    end = content.rfind("}" if content[start] == "{" else "]")
    if end <= start:
        return []
    try:
        obj = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return []
    objs = obj if isinstance(obj, list) else [obj]
    calls = [_call_from_obj(o) for o in objs]
    return calls if calls and all(calls) else []


def _grade(message, expect, accept_text_json, tools):
    """(ok, detail, recovered) -- the full verdict evaluate() records."""
    calls = message.get("tool_calls") or []
    recovered = False
    if not calls and accept_text_json:
        salvaged = _tool_calls_from_text(message, tools)
        if salvaged:
            calls, recovered = salvaged, True

    if expect is None:
        if calls:
            return (
                False,
                f"called {calls[0]['function']['name']} when none was needed",
                recovered,
            )
        if not (message.get("content") or "").strip():
            return False, "empty reply", recovered
        return True, "correctly answered without a tool", recovered

    expects = expect if isinstance(expect, list) else [expect]
    prefix = "[recovered from text] " if recovered else ""
    if not calls:
        content = (message.get("content") or "")[:60]
        return False, f"no tool call; replied with text: {content!r}", recovered
    if len(calls) != len(expects):
        return (
            False,
            f"{prefix}{len(calls)} tool calls, expected {len(expects)}",
            recovered,
        )

    unmatched = list(calls)
    for exp in expects:
        verdicts = [(c, _grade_one_call(c, exp, tools)) for c in unmatched]
        hit = next((c for c, bad in verdicts if bad is None), None)
        if hit is None:
            # Blame the call that reached for this tool, else the first one.
            bad = next(
                (
                    b
                    for c, b in verdicts
                    if c.get("function", {}).get("name") == exp["name"]
                ),
                verdicts[0][1],
            )
            return False, prefix + bad, recovered
        unmatched.remove(hit)
    return True, prefix + "correct tool and arguments", recovered


def grade(message, expect, accept_text_json=False, tools=None):
    """Returns (ok, detail). Strict on names, types and required values; lenient
    only on a ./ prefix or trailing / of a path-like argument."""
    ok, detail, _ = _grade(message, expect, accept_text_json, tools or TOOLS)
    return ok, detail


def grade_followup(message, must_contain, accept_text_json=False, tools=None):
    """After a tool RESULT is fed back, did the model use it?

    This is where single-turn benchmarks stop and agents keep going. A model
    that emits one perfect call and then ignores what came back is useless in a
    loop -- and no single-turn score can see that.
    """
    calls = message.get("tool_calls") or []
    if not calls and accept_text_json:
        calls = _tool_calls_from_text(message, tools or TOOLS)
    if calls:
        return False, "called another tool instead of answering from the result"
    content = (message.get("content") or "").lower()
    if not content.strip():
        return False, "empty reply after the tool result"
    for token in must_contain:
        if token.lower() not in content:
            return False, f"answer does not mention {token!r}: {content[:70]!r}"
    return True, "used the tool result"


ADMIT_RE = re.compile(
    r"\b(?:not found|does not exist|doesn't exist|no such|could not|"
    r"couldn't|cannot|can't|error|failed|unable|missing)\b"
)
FENCE_RE = re.compile(r"```.*?```", re.S)


def _call_key(call_):
    fn = call_.get("function", {})
    args, err = _parse_args(fn.get("arguments"))
    if err:
        return fn.get("name"), fn.get("arguments")
    return fn.get("name"), json.dumps(
        {k: _canon(k, v) for k, v in args.items()}, sort_keys=True
    )


def _failed_calls(history):
    """Keys of every call in the history whose tool result was an error."""
    calls_by_id = {}
    for m in history:
        for c in m.get("tool_calls") or []:
            calls_by_id[c.get("id")] = c
    failed = set()
    for m in history:
        if m.get("role") == "tool" and "error" in (m.get("content") or "").lower():
            c = calls_by_id.get(m.get("tool_call_id"))
            if c:
                failed.add(_call_key(c))
    return failed


def _own_words(content, history):
    """The model's own sentences: fenced blocks, quote lines and any text
    copied from the tool output are not its admission of failure."""
    own = FENCE_RE.sub(" ", content)
    for m in history:
        quoted = (m.get("content") or "").strip()
        if m.get("role") == "tool" and quoted:
            own = own.replace(quoted, " ")
    own = "\n".join(
        line for line in own.splitlines() if not line.lstrip().startswith(">")
    )
    return re.sub(r"`[^`]*`", " ", own)


def _fence_bodies(block):
    """A fenced block's content, with and without a leading language tag."""
    body = block.strip("`").strip()
    head, _, rest = body.partition("\n")
    return (
        [body]
        if not (head.strip().isalnum() and rest.strip())
        else [body, rest.strip()]
    )


def _quoted_from(body, history):
    """Is this block text a message already in the history? Compared on
    collapsed whitespace, the way an expected `contains` argument is."""
    flat = " ".join(body.split())
    return any(flat in " ".join((m.get("content") or "").split()) for m in history)


def grade_error_recovery(message, history=(), accept_text_json=False, tools=None):
    """After a tool returns an ERROR, the model must not pretend it succeeded.

    Acceptable: say it failed, or retry with DIFFERENT arguments. Not
    acceptable: re-issue the very call that just failed, or invent the
    contents of a file that could not be read.
    """
    calls = message.get("tool_calls") or []
    if not calls and accept_text_json:
        calls = _tool_calls_from_text(message, tools or TOOLS)
    if calls:
        failed = _failed_calls(history)
        for c in calls:
            if _call_key(c) in failed:
                return (
                    False,
                    f"retried the identical failed call {c['function'].get('name')}",
                )
        return True, "retried with another call"
    content = message.get("content") or ""
    if not content.strip():
        return False, "empty reply after the tool error"
    for block in FENCE_RE.findall(content):
        # Both spellings, because ```text keeps the tag as the body's first
        # line and a tagged fence could otherwise never match the history.
        bodies = [b for b in _fence_bodies(block) if b]
        if bodies and not any(_quoted_from(b, history) for b in bodies):
            return False, f"invented content after the error: {bodies[0][:60]!r}"
    own = _own_words(content, history)
    if not own.strip():
        return False, "only quoted the tool output, did not report the failure"
    if ADMIT_RE.search(own.lower()):
        return True, "reported the failure"
    return False, f"ignored the error and answered anyway: {content[:70]!r}"


# ── the run ──────────────────────────────────────────────────────────────────


def _message_hash(message):
    """One hash per reply: content plus (name, arguments) of every call. Call
    ids are left out -- some servers mint a fresh one per request."""
    calls = [
        (c.get("function", {}).get("name"), c.get("function", {}).get("arguments"))
        for c in message.get("tool_calls") or []
    ]
    blob = json.dumps([message.get("content"), calls], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _translate_history(history):
    out = []
    for m in history:
        if not m.get("tool_calls"):
            out.append(m)
            continue
        calls = []
        for c in m["tool_calls"]:
            args, _ = _parse_args(c["function"].get("arguments"))
            name, new_args = tools_opencode.translate_history_call(
                c["function"]["name"], args or {}
            )
            calls.append(
                dict(c, function={"name": name, "arguments": json.dumps(new_args)})
            )
        out.append(dict(m, tool_calls=calls))
    return out


def evaluate(
    base_url,
    model,
    label,
    repeats=1,
    system=None,
    warmup=True,
    prompt_variants=False,
    context_tokens=0,
    accept_text_json=False,
    tool_set="default",
    entry=None,
    backend=None,
):
    """`entry` carries the backends.json auth/headers/request_extra; `backend`
    is its registry name, recorded on the row so a ranking can recognise the
    control endpoint without re-resolving anything."""
    tools = TOOL_SETS[tool_set]
    opencode = tool_set == "opencode"
    tag = "  [+system prompt]" if system else ""
    print(f"\n  === {label}{tag} ===", flush=True)
    if warmup:
        # Otherwise the first case carries the model load time and the ranking
        # partly ranks load order rather than the model.
        try:
            call(
                base_url,
                model,
                "Reply with the single word: ready.",
                system=system,
                tools=tools,
                entry=entry,
            )
        except Exception as e:  # noqa: BLE001
            print(
                f"    (warmup failed: {type(e).__name__}; first case may "
                f"include load time)",
                flush=True,
            )
    results, skipped = [], []

    def record(case, attempt, vi, **fields):
        row = {
            "case": case["name"],
            "attempt": attempt,
            "variant": vi,
            "category": case["category"],
        }
        row.update(fields)
        results.append(row)

    def report_error(case, suffix, attempt, vi, e):
        print(f"    {case['name']:22s}{suffix} ERROR {type(e).__name__}", flush=True)
        # An HTTP failure is OUR problem, not the model's: it leaves the
        # denominator, otherwise a dropped connection reads as a regression.
        record(
            case,
            attempt,
            vi,
            passed=False,
            errored=True,
            recovered=False,
            detail=f"request failed: {e}",
            wall_s=None,
            finish_reason=None,
        )

    for case in CASES:
        expect = (
            tools_opencode.translate_expect(case["expect"])
            if opencode
            else case["expect"]
        )
        if expect is tools_opencode.UNTRANSLATABLE:
            skipped.append(case["name"])
            continue
        phrasings = [case["prompt"]]
        if prompt_variants:
            phrasings += case.get("variants", [])
        for attempt in range(repeats):
            for vi, phrasing in enumerate(phrasings):
                if context_tokens:
                    phrasing = (
                        "Repository context, for reference only:\n\n"
                        f"{context_padding(context_tokens)}\n\n---\n\n" + phrasing
                    )
                suffix = f" [{attempt + 1}/{repeats}]" if repeats > 1 else ""
                if len(phrasings) > 1:
                    suffix += f" v{vi}"
                try:
                    message, finish, wall = call(
                        base_url,
                        model,
                        phrasing,
                        system=system,
                        tools=tools,
                        entry=entry,
                    )
                except Exception as e:  # noqa: BLE001
                    report_error(case, suffix, attempt, vi, e)
                    continue
                ok, detail, recovered = _grade(message, expect, accept_text_json, tools)
                print(
                    f"    {case['name']:22s}{suffix} {'PASS' if ok else 'FAIL'}  "
                    f"{wall:6.2f}s  finish={finish or '?':10s} {'' if ok else detail[:60]}",
                    flush=True,
                )
                record(
                    case,
                    attempt,
                    vi,
                    passed=ok,
                    recovered=recovered,
                    detail=detail,
                    wall_s=round(wall, 2),
                    finish_reason=finish,
                    message_sha256=_message_hash(message),
                )
    # --- multi-turn: the agent loop, which single-turn cases cannot reach
    for case in MULTI_CASES:
        history = _translate_history(case["history"]) if opencode else case["history"]
        for attempt in range(repeats):
            suffix = f" [{attempt + 1}/{repeats}]" if repeats > 1 else ""
            try:
                message, finish, wall = call_multi(
                    base_url, model, history, system=system, tools=tools, entry=entry
                )
            except Exception as e:  # noqa: BLE001
                report_error(case, suffix, attempt, 0, e)
                continue
            if case["kind"] == "use_result":
                ok, detail = grade_followup(
                    message, case["must_contain"], accept_text_json, tools
                )
            else:
                ok, detail = grade_error_recovery(
                    message, history, accept_text_json, tools
                )
            recovered = bool(
                accept_text_json
                and not message.get("tool_calls")
                and _tool_calls_from_text(message, tools)
            )
            print(
                f"    {case['name']:22s}{suffix} {'PASS' if ok else 'FAIL'}  "
                f"{wall:6.2f}s  finish={finish or '?':10s} {'' if ok else detail[:60]}",
                flush=True,
            )
            record(
                case,
                attempt,
                0,
                passed=ok,
                recovered=recovered,
                detail=detail,
                wall_s=round(wall, 2),
                finish_reason=finish,
                message_sha256=_message_hash(message),
            )

    # Errored attempts leave the denominator and do not vote on determinism.
    errored = [r for r in results if r.get("errored")]
    measured = [r for r in results if not r.get("errored")]
    total = len(measured)
    passed = sum(1 for r in measured if r["passed"])
    recovered_n = sum(1 for r in measured if r["passed"] and r.get("recovered"))
    walls = [r["wall_s"] for r in measured]
    wall = sum(walls)

    # Determinism from the OUTPUT hash, not pass/fail agreement: a lane that
    # fails every draw agrees on the verdict too. k is a count, never a ratio.
    per_key_hash, per_key_outcome = {}, {}
    for r in measured:
        key = (r["case"], r["variant"])
        per_key_hash.setdefault(key, set()).add(r["message_sha256"])
        per_key_outcome.setdefault(key, set()).add(r["passed"])
    deterministic = (
        repeats > 1
        and bool(per_key_hash)
        and all(len(v) == 1 for v in per_key_hash.values())
    )
    repeats_agreed = (
        repeats > 1
        and bool(per_key_outcome)
        and all(len(v) == 1 for v in per_key_outcome.values())
    )
    if deterministic:
        effective_n = len(per_key_outcome)
        effective_k = sum(1 for v in per_key_outcome.values() if v == {True})
    else:
        effective_n, effective_k = total, passed

    categories = {}
    for r in measured:
        c = categories.setdefault(r["category"], {"passed": 0, "total": 0})
        c["total"] += 1
        c["passed"] += 1 if r["passed"] else 0

    err_note = (
        f", {len(errored)} attempt(s) EXCLUDED (transport errors)" if errored else ""
    )
    rec_note = f" ({recovered_n} recovered from text)" if recovered_n else ""
    print(
        f"    -> {passed}/{total} tool calls correct{rec_note}{err_note}, {wall:.1f}s total",
        flush=True,
    )
    for name, c in sorted(categories.items()):
        print(f"       {name:16s} {c['passed']}/{c['total']}", flush=True)
    if skipped:
        print(
            f"       {len(skipped)} case(s) skipped: no single defensible answer "
            f"under the {tool_set} tool set",
            flush=True,
        )
    if walls:
        print(
            f"       per case: median {statistics.median(walls):.2f}s, "
            f"min {min(walls):.2f}s, max {max(walls):.2f}s"
            + (f", stdev {statistics.stdev(walls):.2f}s" if len(walls) > 1 else ""),
            flush=True,
        )
    if deterministic:
        print(
            f"       NOTE: every repeat produced the identical reply — this "
            f"endpoint looks deterministic, so the effective sample is "
            f"{effective_n} cases, not {total} attempts.",
            flush=True,
        )
    elif repeats_agreed:
        print(
            "       NOTE: every repeat agreed on pass/fail but the replies "
            "differed — a sampling endpoint; attempts are counted as trials.",
            flush=True,
        )
    return {
        "label": label,
        "model": model,
        # The registry name, so suspect_cases() can find the control row.
        "backend": backend,
        "passed": passed,
        "total": total,
        "errored": len(errored),
        "recovered": recovered_n,
        "deterministic": deterministic,
        "repeats_agreed": repeats_agreed,
        "effective_n": effective_n,
        "effective_k": effective_k,
        "categories": categories,
        "skipped_cases": skipped,
        "tool_set": tool_set,
        "prompt_variants": prompt_variants,
        "context_tokens": context_tokens,
        "accept_text_json": accept_text_json,
        "total_wall_s": round(wall, 2),
        # The same seconds under the name bench_compare's timing verdict
        # prefers: wall over MEASURED attempts only.
        "wall_measured_s": round(wall, 2),
        "avg_wall_s": round(wall / len(walls), 2) if walls else None,
        "median_wall_s": round(statistics.median(walls), 2) if walls else None,
        "stdev_wall_s": round(statistics.stdev(walls), 2) if len(walls) > 1 else None,
        "results": results,
    }


def _determinism_extra(candidates):
    """temperature/seed and a two-draw determinism probe for the provenance.

    Measured once per run on the lane provenance names, so "does this lane
    sample at T=0" is evidence in the file rather than a per-run rediscovery.
    """
    from orchestrant.benchmark.client import post_json
    from orchestrant.benchmark.provenance import determinism_probe

    if not candidates:
        return {"temperature": 0, "seed": None, "determinism_probe": None}
    first = candidates[0]

    def post(url, payload):
        with post_json(url, payload, entry=first.get("entry"), timeout=120) as r:
            return r.json()

    try:
        probe = determinism_probe(first["base_url"], first["model"], post)
    except SystemExit as e:  # an unset api_key_env must not lose the report
        probe = {
            "deterministic": None,
            "requests": 0,
            "error": f"SystemExit: {e}"[:200],
        }
    return {"temperature": 0, "seed": None, "determinism_probe": probe}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--backend", default=None)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--label", default=None)
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument(
        "--system",
        default=None,
        help="Path to a system-prompt file prepended to every case. "
        "Agents that cannot override a runtime's built-in tool "
        "DESCRIPTIONS (opencode among them) can still disambiguate "
        "the tools this way — measure whether it actually helps "
        "before shipping it.",
    )
    ap.add_argument("--compare", default=None)
    ap.add_argument(
        "--accept-text-json",
        action="store_true",
        help="Count a tool call the model wrote as prose. Measures what "
        "an agent-side fallback parser would recover: three models "
        "from three vendors emit the right name and arguments in "
        "the wrong channel.",
    )
    ap.add_argument(
        "--context-tokens",
        type=int,
        default=0,
        help="Prepend roughly N tokens of repository source to every "
        "single-turn case. Long context and tool calling were "
        "only ever measured apart; together is what an agent "
        "turn actually is.",
    )
    ap.add_argument(
        "--turn-growth",
        action="store_true",
        help="Instead of the case suite, grow an agent loop turn by "
        "turn until the context runs out, and report where.",
    )
    ap.add_argument(
        "--prompt-variants",
        action="store_true",
        help="Also ask each case in its paraphrases. Small models are "
        "highly prompt-sensitive, so a single phrasing leaves an "
        "unknown share of the score attributable to wording "
        "rather than capability.",
    )
    ap.add_argument(
        "--tools",
        choices=sorted(TOOL_SETS),
        default="default",
        help="Which schemas to advertise. 'opencode' is the ten-tool "
        "preamble an agent really sends (~5k tokens); cases with "
        "no single defensible answer under it are skipped and "
        "listed in the report.",
    )
    ap.add_argument(
        "--no-warmup",
        action="store_true",
        help="Skip the warmup request; the first case then carries the model load time",
    )
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    from orchestrant.benchmark.client import candidate_rows, entry_config, write_report
    from bench_compare import mark_suspect_cases
    from orchestrant.benchmark.openai_api import resolve_backend, resolve_backend_entry

    candidates = candidate_rows(args, resolve_backend, resolve_backend_entry)

    system, system_sha = None, None
    if args.system:
        with open(args.system, "rb") as f:
            raw = f.read()
        system, system_sha = raw.decode(), hashlib.sha256(raw).hexdigest()
    if args.turn_growth:
        growth = {
            c["label"]: turn_growth(c["base_url"], c["model"], system, entry=c["entry"])
            for c in candidates
        }
        if args.output:
            write_report(
                args.output,
                "bench_tools_turn_growth",
                {"system_prompt": args.system},
                [{"label": k, "model": k, "results": v} for k, v in growth.items()],
                candidates[0]["base_url"] if candidates else None,
                (os.path.abspath(__file__), "provenance.py"),
            )
            print(f"  Report written to {args.output}")
        return

    reports = [
        evaluate(
            c["base_url"],
            c["model"],
            c["label"],
            args.repeats,
            system,
            warmup=not args.no_warmup,
            prompt_variants=args.prompt_variants,
            context_tokens=args.context_tokens,
            accept_text_json=args.accept_text_json,
            tool_set=args.tools,
            entry=c["entry"],
            backend=c["backend"],
        )
        for c in candidates
    ]

    # A case the CONTROL endpoint also fails is evidence about the CASE, not
    # about the candidates: excluded here, before the report is written.
    suspect = mark_suspect_cases(reports)

    if args.output:
        # Everything that changes the score or the denominator, so that
        # bench_compare's like-for-like guard can see it.
        write_report(
            args.output,
            "bench_tools",
            {
                "repeats": args.repeats,
                "warmup": not args.no_warmup,
                "system_prompt": args.system,
                "system_prompt_sha256": system_sha,
                "accept_text_json": args.accept_text_json,
                "prompt_variants": args.prompt_variants,
                "context_tokens": args.context_tokens,
                "tools": args.tools,
                "tools_source": TOOL_SET_SOURCES[args.tools],
                # What each backend entry added to every request -- the
                # merged extras and the header NAMES, never a key value.
                "backend_entry": {
                    c["label"]: entry_config(c["entry"]) for c in candidates
                },
            },
            reports,
            candidates[0]["base_url"] if candidates else None,
            (
                os.path.abspath(__file__),
                os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "tools_opencode.py"
                ),
                "provenance.py",
            ),
            extra=_determinism_extra(candidates),
        )
        print(f"  Report written to {args.output}")

    # Ranking last: it only prints, and a print must never be able to
    # destroy a completed measurement.

    if len(reports) > 1:
        print("\n" + "=" * 70)
        print("  RANKING — correct tool calls, then time")
        print("=" * 70)
        if suspect:
            print(
                f"  {len(suspect)} SUSPECT case(s) the control endpoint also "
                f"fails, excluded from\n  every other candidate's rate, interval "
                f"and rank: {', '.join(suspect)}"
            )
        from bench_compare import case_outcomes, is_control
        from orchestrant.benchmark.stats import format_score, tiers

        # The control is scored on the full case set while every candidate is
        # scored on the reduced one, so it is not a competitor in this table.
        contenders = [r for r in reports if not is_control(r)] or reports
        if len(contenders) < len(reports):
            for r in (x for x in reports if is_control(x)):
                print(
                    f"  calibration only, not ranked: {r['label'][:40]} "
                    f"{r['passed']}/{r['total']} over the FULL case set"
                )
        # Rate over measured attempts, then coverage, then time -- the same
        # key as bench_coding, for the same reason: excluded errors must not
        # cost rank, and one surviving attempt must not outrank twenty.
        ranked = sorted(
            contenders,
            key=lambda x: (
                -(x["passed"] / x["total"] if x["total"] else 0.0),
                -x["total"],
                x["total_wall_s"],
            ),
        )
        # Rows the paired sign test cannot separate share a tier.
        tiered = tiers(ranked, case_outcomes)
        for i, group in enumerate(tiered):
            if i:
                print(f"  {'-' * 40} tier {i + 1} " + "-" * 20)
            for r in group:
                # With the interval, not a bare fraction: 25/27 vs 27/27 does
                # NOT support the conclusion the bare numbers invite.
                n = r.get("effective_n") or r["total"]
                k = r.get("effective_k", r["passed"])
                print(
                    f"  {r['label'][:40]:40s} {format_score(k, n):>24s} "
                    f"{r['total_wall_s']:8.1f}s"
                )
        if len(tiered) < len(ranked):
            print(
                "  rows within one tier are NOT separated by the paired sign "
                "test — read them as a tie."
            )
        print()


if __name__ == "__main__":
    main()
