"""The viewer, from the repository root: ``python -m frontend.frontend [--web] [--port N]``."""

from __future__ import annotations

import argparse

import flet as ft

from .benchmarks import main


def cli(argv: list[str] | None = None) -> None:
    """Open the viewer in a window, or serve it to a browser with --web."""
    parser = argparse.ArgumentParser(
        prog="python -m frontend.frontend",
        description="The LLM benchmark lab's result viewer (ORCHESTRANT_BENCHMARK_MANIFEST picks the run).",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="serve to a browser instead of opening a window",
    )
    parser.add_argument(
        "--port", type=int, default=0, help="the port to serve on; 0 picks a free one"
    )
    args = parser.parse_args(argv)
    view = ft.AppView.WEB_BROWSER if args.web else ft.AppView.FLET_APP
    ft.run(main, view=view, port=args.port)


if __name__ == "__main__":
    cli()
