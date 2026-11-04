"""``orchestrant-text``: classify, name entities, summarize or translate a text on the chain GenAI; JSON on stdout."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from orchestrant.documents.extract import extract_document
from orchestrant.genai.chat import figures
from orchestrant.genai.engine import GenaiEngine, GenaiError
from orchestrant.genai.text_tasks import LANGUAGES, TaskLimits, TextTasks


if TYPE_CHECKING:
    from collections.abc import Sequence

    from orchestrant.genai.text_tasks import TaskAnswer


def build_parser() -> argparse.ArgumentParser:
    """Return the command line of ``orchestrant-text``."""
    limits = TaskLimits()
    parser = argparse.ArgumentParser(prog="orchestrant-text", description=__doc__)
    parser.add_argument(
        "task", choices=["classify", "entities", "summarize", "translate"]
    )
    parser.add_argument(
        "--model-dir", required=True, type=Path, help="a GenAI model directory"
    )
    parser.add_argument("--provider", default="cpu", choices=["cpu", "cuda", "dml"])
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--text", help="the text; without it or --input, stdin")
    source.add_argument(
        "--input", type=Path, help="a text, Office or PDF file, read as data"
    )
    parser.add_argument(
        "--language",
        default="auto",
        choices=["auto", *LANGUAGES],
        help="the language to work in; auto counts function words and falls back to en",
    )
    parser.add_argument(
        "--labels", help="classify: comma-separated labels, at least two"
    )
    parser.add_argument(
        "--to", choices=LANGUAGES, help="translate: the target language"
    )
    parser.add_argument("--max-new-tokens", default=limits.max_new_tokens, type=int)
    parser.add_argument(
        "--max-chars",
        default=limits.max_chars,
        type=int,
        help="the longest summary, translation or entity name",
    )
    return parser


def read_text(args: argparse.Namespace, stdin: TextIO) -> str:
    """Return the text to work on; a file that gives none fails naming why."""
    if args.text is not None:
        return args.text
    if args.input is None:
        return stdin.read()
    extraction = extract_document(args.input)
    if extraction.status not in {"ok", "needs_ocr"}:
        msg = f"{args.input}: {extraction.status} {extraction.detail}".rstrip()
        raise GenaiError(msg, kind="invalid_options")
    return extraction.text()


def run_task(args: argparse.Namespace, tasks: TextTasks, text: str) -> TaskAnswer:
    """Run the task the command line names."""
    if args.task == "classify":
        labels = [label.strip() for label in (args.labels or "").split(",")]
        return tasks.classify(text, labels, args.language)
    if args.task == "entities":
        return tasks.entities(text, args.language)
    if args.task == "summarize":
        return tasks.summarize(text, args.language)
    if args.to is None:
        msg = "translate needs --to"
        raise GenaiError(msg, kind="invalid_options")
    return tasks.translate(text, args.to)


def main(
    argv: Sequence[str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Print one task's answer as JSON; exit 2 on an error named on stderr."""
    args = build_parser().parse_args(argv)
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    try:
        text = read_text(args, stdin)
        engine = GenaiEngine(args.model_dir, args.provider)
        limits = TaskLimits(args.max_new_tokens, args.max_chars)
        answer = run_task(args, TextTasks(engine, limits), text)
    except (GenaiError, OSError) as exc:
        sys.stderr.write(f"orchestrant-text: {exc}\n")
        return 2
    json.dump(answer.report(), stdout, ensure_ascii=False)
    stdout.write("\n")
    sys.stderr.write(figures(answer.result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
