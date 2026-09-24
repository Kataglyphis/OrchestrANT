#!/usr/bin/env python3
"""After a serving-runtime upgrade: the whole protocol, per lane, into one directory.

The GenieX v0.6.1 -> v0.7.0 round (benchmarks/docs/geniex-v0.7.0-cpu-npu-2026-09-24.md,
"The v0.7.0 NPU slowdown is `--log info`, not the runtime") was measured by hand, one command at a time, and its review had to rule the
order out as a confound: the `--log info` speed numbers came from a lane the
contract's power_mode check had just reloaded twice. So, per lane, in this
order, never two lanes at once:

  contract      orchestrant-bench contract, then with --previous `contract
                --diff` -- first, because it names what the runtime changed
  speed         orchestrant-bench speed --stream --correctness (a broken
                kernel is FAST; a wrong answer from the gate fails the step)
  speed-answer  the same at --max-tokens 2048: at the default 256, 6 of 9
                thinking replies never left <think>, so time to the cap read
                as time to an answer
  tools         bench_tools --repeats 3
  coding        bench_coding -- Linux-only: through WSL with --wsl on
                Windows, else recorded as skipped with the reason

then bench_compare --dir against --previous. --out must not exist yet; it gets
<lane>-<step>.json with the step's whole output in the .log beside it,
steps.jsonl (argv, exit code, start, end, duration, appended as each step ends)
and MANIFEST.md (file -> exact command -> exit code, and each lane's serving
runtime), rewritten after every step so a killed run still says how far it got.

Exit 0 only when a step ran, every step that ran passed and, with --previous,
bench_compare compared something and found no regression; 130 on Ctrl-C. A
contract answer that moved is listed, not failed: after an upgrade it is the
finding, and <lane>-contract-diff.log is the first thing to read.

Usage:
    python3 upgrade_check.py --lanes geniex-npu,geniex-cpu \\
        --out benchmark_results/2026-10-01-geniex-v080 \\
        --previous benchmark_results/2026-09-24-geniex-v070 --wsl
"""

import argparse
import json
import os
import platform
import re
import shlex
import subprocess  # nosec B404 -- runs this repository's own benchmark tools
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
for _path in (HERE, REPO_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_compare  # noqa: E402
from bench_sweep import slug  # noqa: E402

from orchestrant.benchmark import contract as contract_probe  # noqa: E402
from orchestrant.benchmark import openai_api  # noqa: E402
from orchestrant.benchmark.client import utf8_stdio  # noqa: E402
from orchestrant.benchmark.provenance import _git, _server_models  # noqa: E402
from orchestrant.benchmark.provenance import runtime_info, runtime_label  # noqa: E402

# The selectable steps, in the protocol's order. `--steps` picks among them and
# never reorders them; the contract diff rides with `contract`.
STEPS = ("contract", "speed", "speed-answer", "tools", "coding")
ANSWER_MAX_TOKENS = 2048

DEFAULT_WSL_DISTRO = "Ubuntu-26.04"
# How the lab host runs its Linux-only tests in WSL. A shell fragment on
# purpose: `~` must expand inside WSL, so it is never quoted.
DEFAULT_WSL_PYTHON = (
    "~/.local/bin/uv run --no-project --with psutil --with requests "
    "--with loguru --with matplotlib python"
)

# Statuses that fail the check. "changed" (a contract answer moved) and
# "skipped" (always with its reason) do not.
FAILING = ("failed", "interrupted")
_DRIVE_PATH = re.compile(r"^([A-Za-z]):[\\/]*(.*)$", re.DOTALL)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def needs_wsl():
    """True where bench_coding (`resource`, an `unshare -rn` sandbox) cannot run."""
    return sys.platform == "win32"


def to_wsl_path(path):
    """C:\\GitHub\\x -> /mnt/c/GitHub/x, where WSL's default automount shows it."""
    match = _DRIVE_PATH.match(str(path))
    if not match:
        raise ValueError(f"{path} is not on a drive letter, so WSL cannot reach it")
    drive, rest = match[1], match[2].replace("\\", "/").rstrip("/")
    return f"/mnt/{drive.lower()}" + (f"/{rest}" if rest else "")


def wsl_argv(args, script, tool_args):
    """The argv that runs one benchmarks/ script inside WSL, on this checkout.

    Straight into the distro, not a container: the grader sandboxes itself, and
    the lab host has no Rancher Desktop (its containers are rootless nerdctl in
    that same distro). Named, not `wsl`'s per-user default, because the grader
    must run where its tools (bash, shellcheck, uv) are installed. LLM_BACKENDS
    does not cross into WSL by itself, and the child must read our registry.
    """
    registry = os.environ.get("LLM_BACKENDS")
    registry = registry and to_wsl_path(registry)
    command = [
        f"cd {shlex.quote(to_wsl_path(HERE))} &&",
        *([f"LLM_BACKENDS={shlex.quote(registry)}"] if registry else []),
        f"PYTHONPATH={shlex.quote(to_wsl_path(REPO_ROOT))} PYTHONUNBUFFERED=1",
        f"{args.wsl_python} {script}",
        *(shlex.quote(a) for a in tool_args),
    ]
    return ["wsl", "-d", args.wsl_distro, "--", "bash", "-lc", " ".join(command)]


def format_command(argv):
    """The argv as one line a shell on this host accepts."""
    if not argv:
        return "-"
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


# ── the plan ─────────────────────────────────────────────────────────────────


def _step(lane, kind, argv, output=None, log=None, needs=(), skip=None):
    """One planned step. `output`, `log` and `needs` are file names in --out."""
    if log is None and output:
        log = output.removesuffix(".json") + ".log"
    step = {"lane": lane, "kind": kind, "argv": argv, "output": output, "log": log}
    return {**step, "needs": list(needs), "skip": skip}


def _bench(*args):
    return [sys.executable, "-m", "orchestrant.benchmark", *args]


def _script(name, *args):
    return [sys.executable, os.path.join(HERE, name), *args]


def diff_step(name, stem, args):
    """`contract --diff` against the previous run's report of the same lane."""
    report = f"{stem}-contract.json"
    old = os.path.join(args.previous, report)
    argv = _bench("contract", "--diff", old, os.path.join(args.out, report))
    skip = None if os.path.exists(old) else f"{args.previous} has no {report}"
    log = f"{stem}-contract-diff.log"
    return _step(name, "contract-diff", argv, log=log, needs=(report,), skip=skip)


def coding_step(name, stem, args):
    """bench_coding: natively on Linux, through WSL on Windows, else skipped."""
    report = f"{stem}-coding.json"
    tool_args = ["--backend", name, "--label", name, "--task-set", "all"]
    tool_args += ["--repeats", str(args.coding_repeats), "--keep-output", "--output"]
    if not needs_wsl():
        argv = _script("bench_coding.py", *tool_args, os.path.join(args.out, report))
        return _step(name, "coding", argv, report)
    try:
        wsl_out = to_wsl_path(os.path.join(args.out, report))
        argv = wsl_argv(args, "bench_coding.py", [*tool_args, wsl_out])
    except ValueError as e:
        if args.wsl:
            raise SystemExit(f"--wsl: {e}") from e
        argv = None
    # Without --wsl the WSL command is still planned and shown, so the step can
    # be run by hand later and land beside the others.
    skip = None if args.wsl else "bench_coding needs Linux; pass --wsl to run it in WSL"
    return _step(name, "coding", argv, report, skip=skip)


def lane_steps(lane, args):
    """One lane's steps, in the protocol's order whatever --steps said."""
    name, stem = lane["name"], slug(lane["name"])
    tokens = args.overflow.get(name)
    overflow = ["--overflow-tokens", str(tokens)] if tokens else []
    gate = [] if args.no_correctness else ["--correctness"]
    runner = {
        "contract": ["contract", *overflow],
        "speed": ["speed", "--stream", *gate],
        "speed-answer": ["speed", "--stream", "--max-tokens", str(ANSWER_MAX_TOKENS)],
    }
    steps = []
    for kind in args.steps:
        report = f"{stem}-{kind}.json"
        output = ["--output", os.path.join(args.out, report)]
        if kind == "coding":
            steps.append(coding_step(name, stem, args))
            continue
        if kind == "tools":
            label = ["--label", name, "--repeats", str(args.tools_repeats)]
            argv = _script("bench_tools.py", "--backend", name, *label, *output)
        else:
            command, *flags = runner[kind]
            argv = _bench(command, "--backend", name, *flags, *output)
        steps.append(_step(name, kind, argv, report))
        if kind == "contract" and args.previous:
            steps.append(diff_step(name, stem, args))
    return steps


def plan(lanes, args):
    """Every step of the check, in the order it runs: lane by lane, then compare."""
    steps = [step for lane in lanes for step in lane_steps(lane, args)]
    if args.previous:
        argv = _script("bench_compare.py", "--dir", args.previous, args.out)
        steps.append(_step(None, "compare", argv, log="compare.log"))
    else:
        skip = "no --previous run named to compare with"
        steps.append(_step(None, "compare", None, skip=skip))
    return steps


def resolve_lanes(spec):
    """[{name, base_url, model}] for --lanes, or refuse before measuring."""
    names = [n.strip() for n in spec.split(",") if n.strip()]
    if not names:
        raise SystemExit("--lanes needs at least one backend name from backends.json")
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise SystemExit(f"--lanes names {', '.join(dupes)} more than once")
    env = os.environ.get("LLM_BASE_URL") or os.environ.get("OLLAMA_BASE_URL")
    if env:
        # Env beats --backend in every tool on purpose (a wrapper's export must
        # win over a stale config), so here every lane would measure one URL.
        raise SystemExit(
            f"LLM_BASE_URL / OLLAMA_BASE_URL is set ({env}); it overrides every "
            f"--backend, so each lane would measure that one endpoint. Unset it."
        )
    lanes = []
    for name in names:
        base_url, model, _ = openai_api.resolve_backend(name)
        lanes.append({"name": name, "base_url": base_url, "model": model})
    stems = [slug(n) for n in names]
    if len(set(stems)) != len(stems):
        raise SystemExit(f"lanes {names} would write the same files ({stems})")
    return lanes


def parse_steps(value):
    chosen = {s.strip() for s in value.split(",") if s.strip()}
    if not chosen or chosen - set(STEPS):
        raise SystemExit(
            f"--steps {value!r}: choose from {', '.join(STEPS)} (run in that order)"
        )
    return [s for s in STEPS if s in chosen]


def parse_overflow(values, names):
    """--overflow-tokens LANE=N, per lane, because the right size is per lane.

    6000 overflows the NPU lane's 4096-token bundle in seconds; on a 16k GGUF
    lane it is a prompt that fits and prefills for about a minute.
    """
    overflow = {}
    for value in values or ():
        lane, sep, count = value.partition("=")
        if not sep or lane not in names or not count.isdigit() or int(count) <= 0:
            raise SystemExit(
                f"--overflow-tokens {value!r}: expected LANE=N, N > 0, with LANE "
                f"one of {', '.join(names)}"
            )
        overflow[lane] = int(count)
    return overflow


def check_directories(args):
    if os.path.exists(args.out):
        raise SystemExit(
            f"{args.out} already exists: a MANIFEST.md in it would vouch for "
            f"files this check did not write. Name a fresh --out."
        )
    if args.previous and not os.path.isdir(args.previous):
        raise SystemExit(f"--previous {args.previous} is not a directory")


# ── running ──────────────────────────────────────────────────────────────────


def child_env():
    """The environment every step runs in.

    Piped, a Python child's stdout is block-buffered -- a two-hour coding run
    would show nothing until it ended -- and on Windows it is cp1252, where the
    first '→' raised and exited 1, the code bench_compare and `contract --diff`
    use for "regression" and "changed"; wsl.exe writes UTF-16 without WSL_UTF8.
    """
    env = dict(os.environ)
    paths = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    paths = [REPO_ROOT, *(p for p in paths if p != REPO_ROOT)]
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", WSL_UTF8="1")
    return env


def run_logged(argv, log_path, cwd, env):
    """Run one step, its output streamed to the console and to its .log.

    Returns the exit code, 127 when the command could not be started at all.
    The seam every test replaces: no benchmark ever runs in a test.
    """
    with open(log_path, "wb") as log:
        log.write(f"$ {format_command(argv)}{os.linesep}{os.linesep}".encode())
        log.flush()
        try:
            proc = subprocess.Popen(  # nosec B603 -- argv from plan(), no shell
                argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
        except OSError as e:
            log.write(f"could not start: {e}{os.linesep}".encode())
            print(f"    could not start: {e}", flush=True)
            return 127
        try:
            for raw in proc.stdout or ():
                log.write(raw)
                sys.stdout.write(raw.decode("utf-8", "replace").replace("\r\n", "\n"))
                sys.stdout.flush()
            return proc.wait()
        except KeyboardInterrupt:
            proc.kill()
            proc.wait()
            raise


def lane_answers(base_url):
    """True when the lane answers GET /v1/models; the tests' network seam."""
    return _server_models(base_url, timeout=5) is not None


def contract_changes(old_path, new_path):
    """[[check, before, after]] for every contract answer that moved."""
    try:
        with open(old_path, encoding="utf-8") as f:
            old = json.load(f)
        with open(new_path, encoding="utf-8") as f:
            new = json.load(f)
        rows = contract_probe.diff(old, new)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []
    return [[check, before, after] for check, before, after, moved in rows if moved]


def check_output(step, path):
    """What is wrong with a report its tool wrote and exited 0 on, else None.

    A dying lane still yields reports its tool exits 0 on (every contract check
    `error`, no completed prompt), and so does a failed correctness gate.
    """
    if not os.path.exists(path):
        return f"exited 0 but wrote no {os.path.basename(path)}"
    try:  # a shape its tool never writes fails the step, not the whole check
        with open(path, encoding="utf-8") as f:
            return _report_problem(step, json.load(f))
    except (OSError, ValueError, AttributeError, TypeError, IndexError, KeyError) as e:
        return f"unreadable report: {type(e).__name__}: {e}"[:200]


def _report_problem(step, report):
    reports = report.get("reports") or []
    if step["kind"] == "contract":
        checks = (reports[0] if reports else {}).get("checks") or []
        if all(c.get("answer") == "error" for c in checks):
            return "no contract check got an answer -- the lane stopped answering"
    elif step["kind"] in ("speed", "speed-answer"):
        return _speed_problem(report, "--correctness" in step["argv"])
    elif not any(r.get("total") for r in reports):
        return "nothing was measured: every attempt errored, was cut or skipped"
    return None


def _speed_problem(report, gated):
    """The speed runner exits 0 on errored prompts and on a failed gate alike."""
    config, gate = report.get("config") or {}, report.get("correctness")
    done, asked = config.get("prompts_completed"), config.get("prompts_requested")
    if not done or (asked and done < asked):
        return f"{done or 0} of {asked or '?'} prompts completed"
    if gated and not gate:
        return "the correctness gate scored no probe (every one errored)"
    if gated and gate.get("wrong"):
        return f"correctness gate: {gate['wrong']} of {gate['total']} answers wrong"
    return None


def _outcome(status, reason, **extra):
    return {"status": status, "reason": reason, **extra}


def _log_lines(out, step):
    with open(os.path.join(out, step["log"]), encoding="utf-8", errors="replace") as f:
        return [line.strip() for line in f]


def classify(step, rc, out):
    """{status, reason[, changes]} for a step that ran and exited `rc`.

    bench_compare's codes stay distinct: 1 is a regression only when it says
    REGRESSION (an unreadable report also exits 1), and 3 is NOTHING COMPARED.
    `contract --diff` exits 1 when an answer moved -- checked against its
    CHANGED lines and the reports themselves, since a crash exits 1 too.
    """
    kind = step["kind"]
    lines = _log_lines(out, step) if kind in ("compare", "contract-diff") else []
    if kind == "compare":
        # --dir prints "N report(s) paired" last: without it, it died part-way.
        done = any("report(s) paired" in line for line in lines)
        if rc == 0:
            return _outcome("ok", "compared; no regression")
        if rc == bench_compare.NOT_COMPARED:
            return _outcome("nothing-compared", "bench_compare exit 3")
        if rc == 1 and done and any(x.startswith("REGRESSION") for x in lines):
            return _outcome("regression", "bench_compare: REGRESSION")
        return _outcome("failed", f"bench_compare exited {rc} with no verdict")
    if kind == "contract-diff":
        if rc == 0:
            return _outcome("ok", "no contract answer moved", changes=[])
        changes = contract_changes(step["argv"][-2], step["argv"][-1])
        if rc == 1 and changes and any(x.startswith("CHANGED") for x in lines):
            reason = f"{len(changes)} contract answer(s) moved"
            return _outcome("changed", reason, changes=changes)
        return _outcome("failed", f"exited {rc}")
    if rc != 0:
        return _outcome("failed", f"exited {rc}")
    problem = check_output(step, os.path.join(out, step["output"]))
    return _outcome("failed" if problem else "ok", problem)


def _not_run(step, state):
    """(status, reason) for a step that will not run, else (None, None)."""
    if step["skip"]:
        return "skipped", step["skip"]
    out = state["out"]
    missing = [f for f in step["needs"] if not os.path.exists(os.path.join(out, f))]
    if missing:
        return "skipped", f"needs {', '.join(missing)}, which no earlier step wrote"
    # A dead lane's contract "moves" every answer to error: not the runtime's.
    failed = {s["output"] for s in state["steps"] if s["status"] in FAILING}
    if failed & set(step["needs"]):
        return "skipped", f"needs {', '.join(step['needs'])}, from a step that failed"
    if step["kind"] == "compare":
        pairs, _, _ = bench_compare.pair_directories(state["previous"], out)
        if not pairs:
            # bench_compare --dir exits 1 here -- its code for a regression.
            reason = f"no report file name in common with {state['previous']}"
            return "nothing-compared", reason
    return None, None


def _append_log(state, record):
    path = os.path.join(state["out"], "steps.jsonl")
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record) + "\n")


def execute(step, state):
    """Run (or record as not run) one step; log it and rewrite the manifest."""
    record = {"type": "step", "n": len(state["steps"]) + 1, "lane": step["lane"]}
    record |= {"step": step["kind"], **{k: step[k] for k in ("argv", "output", "log")}}
    record |= dict.fromkeys(("rc", "start", "end", "duration_s"))
    status, reason = _not_run(step, state)
    # What the record says if this function itself raises: never a null status.
    outcome = _outcome(status or "failed", reason or "upgrade_check stopped here")
    started = time.monotonic()
    try:
        if status is None:
            print(f"\n  ▸ [{record['n']}] {step['lane'] or 'all'}: {step['kind']}")
            record["start"] = _now()
            log_path = os.path.join(state["out"], step["log"])
            record["rc"] = run_logged(step["argv"], log_path, HERE, child_env())
            outcome = classify(step, record["rc"], state["out"])
    except KeyboardInterrupt:
        outcome = _outcome("interrupted", "Ctrl-C during the step")
        raise
    finally:
        if record["start"]:
            record["end"] = _now()
            record["duration_s"] = round(time.monotonic() - started, 1)
        record.update(outcome)
        state["steps"].append(record)
        _append_log(state, record)
        write_manifest(state)
        print(f"    {record['status']}: {record['reason'] or '-'}", flush=True)
    return record


_IDENTITY = ("server", "cli", "qairt", "llama_cpp", "version", "started", "serve_args")


def runtime_moved(before, after):
    """Why the lane at the end is not the lane at the start, else None."""
    if before is None:
        return None
    if after is None:
        return "the lane could no longer be identified at the end of its steps"
    if [before.get(k) for k in _IDENTITY] == [after.get(k) for k in _IDENTITY]:
        return None
    if runtime_label(before) != runtime_label(after):
        was, now = runtime_label(before), runtime_label(after)
        return f"the serving runtime changed during the check: {was} -> {now}"
    return (
        "the lane was relaunched during the check (its start time or serve "
        "flags changed): later steps measured another lane process"
    )


def previous_runtime(previous, name):
    """The runtime the previous run's contract report recorded for this lane."""
    if not previous:
        return None
    path = os.path.join(previous, f"{slug(name)}-contract.json")
    try:
        with open(path, encoding="utf-8") as f:
            return (json.load(f).get("provenance") or {}).get("runtime")
    except (OSError, ValueError, AttributeError):
        return None


def run_lane(lane, steps, state):
    """One lane's steps, with its runtime named before and re-checked after."""
    print(f"\n  == lane {lane['name']} @ {lane['base_url']}", flush=True)
    up = lane_answers(lane["base_url"])
    problem = None if up else f"{lane['base_url']} did not answer GET /v1/models"
    lane["runtime"] = runtime_info(lane["base_url"]) if problem is None else None
    lane["previous_runtime"] = previous_runtime(state["previous"], lane["name"])
    _append_log(state, {"type": "lane", "phase": "start", **lane})
    for step in steps:
        if problem and not step["skip"]:
            step = {**step, "skip": f"lane unreachable: {problem}"}
        execute(step, state)
    if problem is None:
        lane["runtime_after"] = runtime_info(lane["base_url"])
        problem = runtime_moved(lane["runtime"], lane["runtime_after"])
    lane["problem"] = problem
    _append_log(state, {"type": "lane", "phase": "end", **lane})
    write_manifest(state)


def verdict(state):
    """(words, exit code) for the check as it stands."""
    steps = state["steps"]
    failed = any(s["status"] in FAILING for s in steps)
    failed = failed or any(lane.get("problem") for lane in state["lanes"])
    regressed = any(s["status"] == "regression" for s in steps)
    blind = any(s["status"] == "nothing-compared" for s in steps)
    # Every step skipped measured nothing: exit 0 on that is not a pass.
    idle = state["final"] and all(s["rc"] is None for s in steps)
    flags = (
        (not state["final"], "INCOMPLETE"),
        (state["interrupted"], "INTERRUPTED"),
        (failed, "FAILED"),
        (regressed, "REGRESSION"),
        (blind, "NOTHING COMPARED"),
        (idle, "NOTHING RAN"),
    )
    words = [word for flag, word in flags if flag] or ["OK"]
    if state["interrupted"]:
        return words, 130
    return words, 1 if (failed or regressed or blind or idle) else 0


# ── the manifest ─────────────────────────────────────────────────────────────


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def _code(argv):
    return f"`{_cell(format_command(argv))}`" if argv else "-"


def _header_lines(state):
    words, code = verdict(state)
    ctx = state["context"]
    dirty = " (dirty tree: tool_sha256 may match no commit)" if ctx["dirty"] else ""
    lines = [
        f"# Upgrade check: {os.path.basename(state['out'])}",
        "",
        f"**Verdict: {', '.join(words)}** (exit {code})",
        "",
        f"- Command: {_code(state['command'])}",
        f"- Started {state['started']}, finished {state['finished'] or '-'}",
        f"- Host: {ctx['host']} ({ctx['os']}, {ctx['machine']}), Python {ctx['python']}",
        f"- Repository: {ctx['git_sha'] or 'unknown'}{dirty}",
        f"- Previous run: {state['previous'] or 'none named'}",
    ]
    for s in state["steps"]:
        if s["status"] in (*FAILING, "regression", "nothing-compared"):
            where = f"{s['lane'] or 'all lanes'}, {s['step']}"
            lines.append(f"- Step {s['n']} ({where}): {s['status']} -- {s['reason']}")
    for lane in state["lanes"]:
        if lane.get("problem"):
            lines.append(f"- Lane {lane['name']}: {lane['problem']}")
    return lines


def lane_warnings(runtime):
    """Launch conditions this lab has measured moving a number."""
    runtime = runtime or {}
    args = runtime.get("serve_args") or []
    level = (args[args.index("--log") + 1 :] or ["?"])[0] if "--log" in args else None
    if runtime.get("server") != "geniex" or level in (None, "none", "error"):
        return []
    return [
        f"launched with --log {level}: on GenieX v0.7.0 that cost the NPU lane "
        f"13.7 % of its decode rate (benchmarks/docs/geniex-v0.7.0-cpu-npu-2026-09-24.md); "
        f"relaunch with --log none before trusting a speed number"
    ]


def _runtime_cell(lane):
    if "runtime" not in lane:
        return "not probed yet"
    runtime = lane["runtime"]
    seen = " (verified)" if (runtime or {}).get("verified") else " (not verified)"
    return _cell(runtime_label(runtime)) + (seen if runtime else "")


def _lane_lines(state):
    lines = ["", "## Lanes", ""]
    lines += ["| Lane | Endpoint | Model | Serving runtime | Previous run's runtime |"]
    lines += ["|---|---|---|---|---|"]
    notes = []
    for lane in state["lanes"]:
        before = lane.get("previous_runtime")
        previous = _cell(runtime_label(before)) if before else "-"
        model = _cell(lane["model"] or "detected by each tool")
        lines.append(
            f"| {lane['name']} | {lane['base_url']} | {model} "
            f"| {_runtime_cell(lane)} | {previous} |"
        )
        runtime = lane.get("runtime") or {}
        if runtime:
            flags = runtime.get("serve_args")
            flags = f"; serve flags `{' '.join(flags)}`" if flags else ""
            notes.append(f"- {lane['name']}: {_cell(runtime.get('source', ''))}{flags}")
        notes += [f"- {lane['name']}: {w}" for w in lane_warnings(runtime)]
    return lines + ([""] + notes if notes else [])


def _change_lines(state):
    diffs = [s for s in state["steps"] if s.get("changes") is not None]
    if not diffs:
        return []
    lines = ["", "## Contract answers that moved", ""]
    for s in diffs:
        rows = [f"`{check}` {old} -> {new}" for check, old, new in s["changes"]]
        lines += [f"- {s['lane']}: {row}" for row in rows or ["none"]]
    return lines


def _step_lines(state):
    lines = ["", "## Steps", ""]
    lines += ["| # | Lane | Step | Files | Exit | Status | Seconds | Command |"]
    lines += ["|---|---|---|---|---|---|---|---|"]
    for s in state["steps"]:
        files = [f for f in (s["output"], s["log"]) if f]
        files = [f for f in files if os.path.exists(os.path.join(state["out"], f))]
        written = ", ".join(f"`{f}`" for f in files) or "-"
        status = _cell(s["status"] + (f": {s['reason']}" if s["reason"] else ""))
        rc = "-" if s["rc"] is None else s["rc"]
        seconds = "-" if s["duration_s"] is None else s["duration_s"]
        lines.append(
            f"| {s['n']} | {s['lane'] or 'all'} | {s['step']} | {written} | {rc} "
            f"| {status} | {seconds} | {_code(s['argv'])} |"
        )
    pending = "not run: interrupted" if state["final"] else "pending"
    done = len(state["steps"])
    for n, step in enumerate(state["plan"][done:], done + 1):
        lines.append(
            f"| {n} | {step['lane'] or 'all'} | {step['kind']} | - | - | {pending} "
            f"| - | {_code(step['argv'])} |"
        )
    return lines


LEGEND = (
    "",
    "Exit codes: bench_compare 0 = no regression, 1 = REGRESSION (only when it "
    "says so; an unreadable report exits 1 too and is a failure here), 3 = "
    "NOTHING COMPARED. `contract --diff` 1 = an answer moved: a finding, not a "
    "failure. Each step's whole output is in its `.log`, and `steps.jsonl` has "
    "every step's argv, exit code, start, end and duration.",
)


def render_manifest(state):
    lines = _header_lines(state) + _lane_lines(state) + _change_lines(state)
    return "\n".join([*lines, *_step_lines(state), *LEGEND]) + "\n"


def write_manifest(state):
    path = os.path.join(state["out"], "MANIFEST.md")
    with open(f"{path}.tmp", "w", encoding="utf-8", newline="\n") as f:
        f.write(render_manifest(state))
    os.replace(f"{path}.tmp", path)


# ── the command ──────────────────────────────────────────────────────────────


def check_context():
    """Where the check ran: one block for the manifest header."""
    return {
        "host": platform.node() or "unknown",
        "os": f"{platform.system()} {platform.release()}",
        "machine": platform.machine() or "unknown",
        "python": platform.python_version(),
        "git_sha": _git("rev-parse", "HEAD"),
        "dirty": bool(_git("status", "--porcelain")),
    }


def run_check(state, lanes, steps):
    """Run the plan lane by lane, then the comparison; return the exit code."""
    keys = ("command", "started", "out", "previous")
    _append_log(state, {"type": "check", **{k: state[k] for k in keys}})
    write_manifest(state)
    try:
        for lane in lanes:
            run_lane(lane, [s for s in steps if s["lane"] == lane["name"]], state)
        for step in (s for s in steps if s["lane"] is None):
            execute(step, state)
    except KeyboardInterrupt:
        state["interrupted"] = True
    finally:
        state["final"] = True
        state["finished"] = _now()
        write_manifest(state)
    words, code = verdict(state)
    _append_log(state, {"type": "verdict", "verdict": words, "exit": code})
    manifest = os.path.join(state["out"], "MANIFEST.md")
    print(f"\n  Upgrade check: {', '.join(words)} -- {manifest}", flush=True)
    return code


def print_plan(steps):
    for n, step in enumerate(steps, 1):
        skip = step["skip"]
        what = f"skipped: {skip}" if skip else step["output"] or step["log"]
        print(f"  {n:>2}. {step['lane'] or 'all lanes'}: {step['kind']} -> {what}")
        print(f"      {format_command(step['argv'])}")


def parse_args(argv):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add = ap.add_argument
    add("--lanes", required=True, help="backends.json names, comma-separated")
    add("--out", required=True, help="run directory to create; must not exist")
    add("--previous", help="an earlier run directory to diff and compare with")
    add("--steps", default=",".join(STEPS), help="a subset; order is fixed")
    add("--tools-repeats", type=int, default=3, help="bench_tools --repeats")
    add("--coding-repeats", type=int, default=1, help="bench_coding --repeats")
    add("--no-correctness", action="store_true", help="speed without its gate")
    add("--overflow-tokens", action="append", metavar="LANE=N", help="per lane")
    add("--wsl", action="store_true", help="on Windows, run bench_coding in WSL")
    add("--wsl-distro", default=DEFAULT_WSL_DISTRO, help="the distro for --wsl")
    add("--wsl-python", default=DEFAULT_WSL_PYTHON, help="starts Python in WSL")
    add("--dry-run", action="store_true", help="print the plan; touch nothing")
    args = ap.parse_args(argv)
    args.steps = parse_steps(args.steps)
    args.out = os.path.abspath(args.out)
    args.previous = os.path.abspath(args.previous) if args.previous else None
    return args


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    args = parse_args(argv)
    if args.wsl and not needs_wsl():
        raise SystemExit("--wsl is for a Windows host; here bench_coding runs natively")
    if os.environ.get("LLM_BACKENDS"):  # the children run in benchmarks/, or WSL
        os.environ["LLM_BACKENDS"] = os.path.abspath(os.environ["LLM_BACKENDS"])
    lanes = resolve_lanes(args.lanes)
    args.overflow = parse_overflow(args.overflow_tokens, [x["name"] for x in lanes])
    check_directories(args)
    steps = plan(lanes, args)
    if args.dry_run:
        print_plan(steps)
        return 0
    os.makedirs(args.out)
    state = {"out": args.out, "previous": args.previous, "lanes": lanes, "plan": steps}
    state["command"] = [sys.executable, os.path.abspath(__file__), *argv]
    state |= {"context": check_context(), "started": _now(), "finished": None}
    state |= {"steps": [], "final": False, "interrupted": False}
    return run_check(state, lanes, steps)


if __name__ == "__main__":
    utf8_stdio()
    sys.exit(main())
