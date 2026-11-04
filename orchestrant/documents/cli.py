"""``orchestrant-docs``: index a document tree, search it, and ask it a question; nothing leaves the machine."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from orchestrant.documents.ask import AskLimits, answer_from, no_sources
from orchestrant.documents.index import (
    DEFAULT_EXCLUDES,
    DocumentIndex,
    DocumentIndexError,
    IndexLimits,
)
from orchestrant.genai.engine import GenaiEngine, GenaiError
from orchestrant.genai.text_tasks import LANGUAGES


if TYPE_CHECKING:
    from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    """Return the command line of ``orchestrant-docs``."""
    parser = argparse.ArgumentParser(prog="orchestrant-docs", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index", help="walk a tree, read-only, into the index")
    index.add_argument(
        "--root", required=True, type=Path, help="the tree, such as a mounted NAS share"
    )
    index.add_argument(
        "--db",
        required=True,
        type=Path,
        help="the index file, on a local disk outside --root",
    )
    index.add_argument(
        "--max-files",
        default=0,
        type=int,
        help="stop after this many files; 0 walks all",
    )
    index.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="another directory name pattern to skip; repeatable",
    )
    index.add_argument(
        "--allow-empty", action="store_true", help="an empty tree may empty the index"
    )
    search = commands.add_parser("search", help="print the best chunks; no model")
    ask = commands.add_parser(
        "ask", help="answer from the best chunks on the chain GenAI"
    )
    for command in (search, ask):
        command.add_argument("question")
        command.add_argument("--db", required=True, type=Path)
        command.add_argument("--top-k", default=4, type=int)
    ask.add_argument(
        "--model-dir", required=True, type=Path, help="a GenAI model directory"
    )
    ask.add_argument("--provider", default="cpu", choices=["cpu", "cuda", "dml"])
    ask.add_argument(
        "--include-suspect",
        action="store_true",
        help="offer the model a source that reads like orders to it; withheld by default",
    )
    ask.add_argument("--language", default="auto", choices=["auto", *LANGUAGES])
    ask.add_argument("--max-new-tokens", default=AskLimits.max_new_tokens, type=int)
    ask.add_argument(
        "--max-chars", default=AskLimits.max_chars, type=int, help="the longest answer"
    )
    return parser


def _run_index(args: argparse.Namespace, out: TextIO) -> int:
    limits = IndexLimits(
        max_files=args.max_files,
        excludes=(*DEFAULT_EXCLUDES, *args.exclude),
        allow_empty=args.allow_empty,
    )
    index = _open(args.db, create=True)
    try:
        report = index.update(args.root, limits)
    finally:
        index.close()
    _print(out, report.as_dict())
    return 1 if report.walk_errors or report.purge_refused else 0


def _open(path: Path, *, create: bool = False) -> DocumentIndex:
    if not create and not path.is_file():
        msg = f"{path} does not exist; run orchestrant-docs index first"
        raise DocumentIndexError(msg)
    return DocumentIndex(path)


def _print(out: TextIO, value: object) -> None:
    json.dump(value, out, ensure_ascii=False, indent=2)
    out.write("\n")


def _run_search(args: argparse.Namespace, out: TextIO) -> int:
    index = _open(args.db)
    try:
        hits = index.search(args.question, args.top_k)
    finally:
        index.close()
    _print(out, [asdict(hit) for hit in hits])
    return 0


def _run_ask(args: argparse.Namespace, out: TextIO) -> int:
    index = _open(args.db)
    try:
        hits = index.search(args.question, args.top_k)
    finally:
        index.close()
    if not hits:
        _print(out, no_sources().report())
        return 0
    engine = GenaiEngine(args.model_dir, args.provider)
    limits = AskLimits(
        args.max_new_tokens, args.max_chars, args.language, args.include_suspect
    )
    _print(out, answer_from(engine, args.question, hits, limits).report())
    return 0


def main(argv: Sequence[str] | None = None, stdout: TextIO | None = None) -> int:
    """Run one command; exit 2 on an error named on stderr, 1 when an index walk could not be trusted."""
    args = build_parser().parse_args(argv)
    runner = {"index": _run_index, "search": _run_search, "ask": _run_ask}
    try:
        return runner[args.command](args, stdout or sys.stdout)
    except (DocumentIndexError, GenaiError, OSError) as exc:
        sys.stderr.write(f"orchestrant-docs: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
