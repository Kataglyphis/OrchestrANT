"""Every lab module a lane imports is in the files its tool_sha256 hashes, or named here as one that decides nothing."""

import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_agent  # noqa: E402
import bench_chat  # noqa: E402
import bench_coding  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Modules a lane imports that cannot change one of its rows; a reason per row, so a new import has to choose.
DECIDES_NOTHING = {
    "bench_compare": "ranks and prints after the report is written",
    "bench_tools": "bench_chat takes only the determinism probe's runner; determinism.py is hashed",
    "compare_suspect": "joins the hash when a control runs (suspect_tool_files), and recounts only then",
    "bench_tasks": "joins the hash under the task-set flag that loads it",
    "bench_variants": "joins the hash under --prompt-variants",
    "agent_env": "which environment variables reach the agent's shell, not how a run is graded",
    "orchestrant.benchmark": "plumbing: the HTTP client and the report envelope",
    "orchestrant.benchmark.client": "plumbing: the HTTP client and the report envelope",
    "orchestrant.benchmark.openai_api": "plumbing: one request path",
    "orchestrant.benchmark.provenance": "plumbing: the provenance block",
}
# stats only prints the ranking in chat and coding; agent stores pass^k and its interval, so it hashes stats.py.
PRINT_ONLY = {
    "bench_chat": {"orchestrant.benchmark.stats"},
    "bench_coding": {"orchestrant.benchmark.stats"},
}


def lab_imports(module):
    """The lab and benchmarks modules a lane's source imports, by module name."""
    tree = ast.parse(open(module.__file__, encoding="utf-8").read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return {
        name
        for name in names
        if name.startswith("orchestrant.benchmark")
        or os.path.exists(os.path.join(HERE, f"{name.split('.')[0]}.py"))
    }


def declared(files):
    """The basenames a TOOL_FILES tuple hashes."""
    return {os.path.basename(path) for path in files}


@pytest.mark.parametrize(
    "lane", [bench_chat, bench_coding, bench_agent], ids=lambda m: m.__name__
)
def test_every_lab_module_a_lane_imports_is_hashed_or_decides_nothing(lane):
    hashed = declared(lane.TOOL_FILES)
    for name in sorted(lab_imports(lane)):
        if name in DECIDES_NOTHING or name in PRINT_ONLY.get(lane.__name__, set()):
            continue
        assert f"{name.rsplit('.', 1)[-1]}.py" in hashed, (
            f"{lane.__name__} imports {name}, which its tool_sha256 misses"
        )
