"""``orchestrant-chat``: a GenAI model directory on the chain GenAI, its answers streamed to stdout."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from orchestrant.genai.engine import GenaiEngine, GenaiError, GenerationOptions


if TYPE_CHECKING:
    from collections.abc import Sequence

    from orchestrant.genai.engine import GenerationResult


def build_parser() -> argparse.ArgumentParser:
    """Return the command line of ``orchestrant-chat``."""
    parser = argparse.ArgumentParser(prog="orchestrant-chat", description=__doc__)
    parser.add_argument(
        "--model-dir", required=True, type=Path, help="a GenAI model directory"
    )
    parser.add_argument(
        "--prompt",
        help="answer this one message; without it, each stdin line is a turn",
    )
    parser.add_argument("--system", help="a system message before the conversation")
    parser.add_argument("--provider", default="cpu", choices=["cpu", "cuda", "dml"])
    parser.add_argument(
        "--max-new-tokens", default=GenerationOptions.max_new_tokens, type=int
    )
    parser.add_argument(
        "--temperature",
        default=GenerationOptions.temperature,
        type=float,
        help="0 decodes greedily",
    )
    parser.add_argument(
        "--seed", type=int, help="repeat a sampled answer by fixing its random seed"
    )
    parser.add_argument(
        "--json-schema",
        type=Path,
        help="a JSON schema file the answer must follow (needs GenAI built with llguidance)",
    )
    parser.add_argument(
        "--stop",
        action="append",
        default=[],
        help="end the answer before this string; repeatable",
    )
    return parser


def options_from(args: argparse.Namespace) -> GenerationOptions:
    """Return the generation options the command line asks for."""
    schema = args.json_schema.read_text(encoding="utf-8") if args.json_schema else None
    return GenerationOptions(
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,
        seed=args.seed,
        json_schema=schema,
        stop=tuple(args.stop),
    )


def figures(result: GenerationResult) -> str:
    """Return the line of speed figures that follows each answer on stderr."""
    return (
        f"{result.completion_tokens} tokens, {result.time_to_first_token_ms:.0f} ms to the first, "
        f"{result.decode_tokens_per_second:.1f} tokens/s, finish {result.finish_reason}"
    )


def turn(
    engine: GenaiEngine,
    messages: list[dict[str, Any]],
    options: GenerationOptions,
    out: TextIO,
) -> GenerationResult:
    """Stream one answer to ``out`` and keep it in the conversation."""

    def write(piece: str) -> None:
        out.write(piece)
        out.flush()

    result = engine.generate(messages, options, write)
    out.write("\n")
    messages.append({"role": "assistant", "content": result.text})
    sys.stderr.write(figures(result) + "\n")
    return result


def main(
    argv: Sequence[str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Answer ``--prompt``, or every line read from stdin as one conversation."""
    args = build_parser().parse_args(argv)
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    messages: list[dict[str, Any]] = (
        [{"role": "system", "content": args.system}] if args.system else []
    )
    try:
        options = options_from(args)
        engine = GenaiEngine(args.model_dir, args.provider)
        prompts = (
            [args.prompt]
            if args.prompt is not None
            else (line.rstrip("\n") for line in stdin)
        )
        for prompt in prompts:
            if prompt.strip():
                messages.append({"role": "user", "content": prompt})
                turn(engine, messages, options, stdout)
    except (GenaiError, OSError) as exc:
        sys.stderr.write(f"orchestrant-chat: {exc}\n")
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
