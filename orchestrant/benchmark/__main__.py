"""``orchestrant-bench`` -- the measurement commands.

One entry point over the three CLIs the runner ships: the endpoint speed
benchmark, the lane/batching probes, and the result summariser. Each module
keeps its own argparse surface; this dispatcher only routes.
"""

from __future__ import annotations

import sys

from orchestrant.benchmark import lanes, openai_api, report


COMMANDS = {
    "speed": openai_api.main,
    "lanes": lanes.main,
    "report": report.main,
}

USAGE = """usage: orchestrant-bench <command> [options]

commands:
  speed   throughput, TTFT, decode and correctness against an endpoint
  lanes   streaming, batching and multi-lane additivity probes
  report  summarise result files and build a viewer manifest
"""


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(USAGE, end="" if args else "", file=sys.stdout if args else sys.stderr)
        return 0 if args else 2
    command = args.pop(0)
    handler = COMMANDS.get(command)
    if handler is None:
        print(USAGE, end="", file=sys.stderr)
        return 2
    sys.argv = [f"orchestrant-bench {command}", *args]
    return handler()


if __name__ == "__main__":
    raise SystemExit(main())
