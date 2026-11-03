"""The speed lane's command line."""

import argparse


def speed_parser(epilog: str | None) -> argparse.ArgumentParser:
    """The speed lane's CLI surface; main() only acts on it."""
    parser = argparse.ArgumentParser(
        description="Benchmark any OpenAI-compatible LLM endpoint (--backend selects one)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )
    parser.add_argument("--model", default=None, help="Model id (else --backend's)")
    parser.add_argument("--prompts", type=int, default=0, help="Prompts (0 = all)")
    parser.add_argument(
        "--max-tokens", type=int, default=256, help="Max tokens per response"
    )
    parser.add_argument(
        "--temperature", type=float, default=0.0, help="Sampling temperature"
    )
    parser.add_argument("--stream", action="store_true", help="Use streaming responses")
    parser.add_argument(
        "--output", default=None, help="Write results as JSON to this file"
    )
    parser.add_argument("--no-warmup", action="store_true", help="Skip warmup request")
    parser.add_argument(
        "--extra-params",
        default=None,
        help='Extra JSON params for the request body (e.g. \'{"num_ctx":16000,"repeat_penalty":1.1}\')',
    )
    parser.add_argument(
        "--load", default=None, help="Load and re-display results from a JSON file"
    )
    parser.add_argument(
        "--backend",
        default=None,
        help="Named backend from backends.json (e.g. ollama, geniex-npu). "
        "Supplies the base URL and a default model.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Explicit base URL; overrides --backend and the environment",
    )
    parser.add_argument(
        "--list-backends",
        action="store_true",
        help="Show the configured backends and exit",
    )
    parser.add_argument(
        "--correctness",
        action="store_true",
        help="LB1: also run the verifiable-answer probe (catches a model that is "
        "fast but broken — speed metrics cannot)",
    )
    parser.add_argument(
        "--correctness-only",
        action="store_true",
        help="Run ONLY the correctness probe and exit (quick health check; "
        "the exit code reads its integrity items only)",
    )
    parser.add_argument(
        "--correctness-max-tokens",
        type=int,
        default=4000,
        help="Token budget per probe. Must be generous: a reasoning model "
        "truncated mid-thought scores WRONG by design (a truncated run is "
        "not a correct one). Measured: Qwen3-4B needs >900 for arithmetic "
        "(default 4000)",
    )
    parser.add_argument(
        "--no-energy",
        action="store_true",
        help="Do not read the Windows Energy Meter rails (on by default where present)",
    )
    parser.add_argument(
        "--idle-seconds",
        type=float,
        default=5.0,
        help="Idle baseline taken after the warmup and netted out of each "
        "request's CPU-rail energy (default 5)",
    )
    return parser
