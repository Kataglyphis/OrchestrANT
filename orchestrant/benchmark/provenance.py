#!/usr/bin/env python3
"""Record WHAT produced a measurement, so two reports can be compared later.

A benchmark result without provenance is a number without a claim. Four weeks
on you cannot say which model build, which runtime, or which repository state
produced it -- which makes run-to-run regression comparison impossible, and
makes an old number worse than no number because it looks authoritative.

Every field that cannot be determined is recorded as an explicit `null` and
listed in `incomplete`, rather than being silently omitted: a gap you can see
is a gap you can fix.
"""

import glob
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

# Re-exported: the probe moved to its own module so the tools can fingerprint
# it without fingerprinting this plumbing (see determinism.py).
from orchestrant.benchmark.determinism import (  # noqa: F401
    PROBE_PROMPT,
    SPACER_PROMPT,
    determinism_probe,
)


SCHEMA_VERSION = 1


def _git(*args):
    try:
        out = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def _server_models(base_url, timeout=5):
    """Model ids the endpoint advertises.

    Not what is LOADED: GenieX lists its whole local model cache here and
    Ollama every pulled tag, so this changes whenever a model is pulled and
    says nothing about which one produced a number — the report's own config
    names that.
    """
    try:
        with urllib.request.urlopen(f"{base_url}/v1/models", timeout=timeout) as r:
            return sorted(m["id"] for m in json.load(r).get("data", []))
    except Exception:
        return None


_GENIEX_VERSION_KEYS = {
    "geniex cli version": "cli",
    "qairt runtime version": "qairt",
    "llamacpp runtime hash": "llama_cpp",
}


def parse_geniex_version(text):
    """`geniex --version` -> {"cli", "qairt", "llama_cpp"}; absent lines stay absent.

    All three lines matter: the llama.cpp hash decides GGUF behaviour (the
    i-quant kernels, the output cap) independently of the CLI version.
    """
    out = {}
    for line in (text or "").splitlines():
        name, sep, value = line.partition(":")
        key = _GENIEX_VERSION_KEYS.get(name.strip().lower())
        if sep and key and value.strip():
            out[key] = value.strip()
    return out


def _installed_geniex():
    """The GenieX CLI a lane on this machine would run, or None.

    A WSL2 client looks on the Windows side first: that is where the documented
    topology runs the lanes, and a Linux-side `geniex` is a different install.
    """
    candidates = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "GenieX CLI", "geniex.exe"))
    candidates += sorted(
        glob.glob("/mnt/c/Users/*/AppData/Local/GenieX CLI/geniex.exe")
    )
    found = shutil.which("geniex")
    if found:
        candidates.append(found)
    return next((c for c in candidates if os.path.isfile(c)), None)


def _geniex_version(exe, timeout=30):
    try:
        out = subprocess.run(
            [exe, "--version", "--skip-update"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return None
    return parse_geniex_version(out.stdout + out.stderr) or None


def _ollama_version(base_url, timeout=3):
    try:
        with urllib.request.urlopen(f"{base_url}/api/version", timeout=timeout) as r:  # nosec B310
            return json.load(r).get("version")
    except Exception:
        return None


def _serves_geniex_root(base_url, timeout=3):
    """Does the root page look like GenieX's Swagger UI?

    GenieX exposes no version route; that page is its only HTTP signature
    (checked on v0.6.1). Weak alone — llama.cpp and vLLM serve other things
    there — which is why it only gates the installed-binary fallback and never
    produces `verified: True`.
    """
    try:
        with urllib.request.urlopen(f"{base_url}/", timeout=timeout) as r:  # nosec B310
            return b"swagger-ui" in r.read(4096)
    except Exception:
        return False


def runtime_info(base_url):
    """WHICH server build produced a measurement, and launched with WHICH flags.

    GenieX v0.5 -> v0.6 changed four behaviours the tooling had encoded (the
    output cap, max_tokens, tool-call parsing, the prefix cache), and every
    published table has carried its version in prose since, because no report
    recorded it. The serve command line matters as much: --nctx, --keepalive
    and --power-mode all move a number, and the CLI's defaults for the first
    two were wrong for benchmarking.

    Best evidence first. The process listening on the lane's port gives the
    exact binary and its flags (`verified: True`). From WSL2 that process is
    invisible, so a loopback lane that is not Ollama is attributed to the
    INSTALLED GenieX, marked `verified: False` — an upgrade mid-session would
    make the two differ, and the report must not claim more than it saw.
    """
    if not base_url:
        return None
    from orchestrant.benchmark.hostload import LaneProcess, _port

    lane = LaneProcess(base_url)
    proc = lane.info() if lane.available else None
    exe = (proc or {}).get("exe") or ""
    # Either separator: a Windows lane's path read on Linux (a report, a test)
    # does not split at backslashes under os.path.
    name = os.path.basename(exe.replace("\\", "/")).lower()
    if proc is not None and name.startswith("geniex"):
        return {
            "server": "geniex",
            **(_geniex_version(exe) or {}),
            "serve_args": (proc.get("cmdline") or [])[1:],
            # When this process started: two reports with different values
            # were served by different launches, even with identical flags.
            "started": proc.get("started"),
            "verified": True,
            "source": f"lane process pid {proc['pid']}: {exe} --version",
        }
    version = _ollama_version(base_url)
    if version:
        return {
            "server": "ollama",
            "version": version,
            "verified": True,
            "source": "/api/version",
        }
    # Only a lane that ANSWERS may be attributed to the installed binary: an
    # unreachable loopback URL is not evidence that GenieX serves it.
    installed = None
    if (
        _port(base_url)
        and _server_models(base_url, timeout=2) is not None
        and _serves_geniex_root(base_url)
    ):
        installed = _installed_geniex()
    if installed:
        return {
            "server": "geniex",
            **(_geniex_version(installed) or {}),
            "serve_args": None,
            "verified": False,
            "source": f"installed binary {installed}; {lane.reason}",
        }
    return None


def runtime_label(runtime):
    """One line naming a runtime, for notes and tables."""
    if not runtime:
        return "unknown"
    if runtime.get("server") == "geniex":
        return (
            f"geniex {runtime.get('cli', '?')} (QAIRT {runtime.get('qairt', '?')}, "
            f"llama.cpp {runtime.get('llama_cpp', '?')})"
        )
    return f"{runtime.get('server', '?')} {runtime.get('version', '?')}"


def _runtime_key(runtime):
    keys = ("server", "cli", "qairt", "llama_cpp", "version")
    return tuple((runtime or {}).get(k) for k in keys)


def _serve_flags(runtime):
    """The serve arguments minus --host: a port move is not a config change."""
    args = list((runtime or {}).get("serve_args") or [])
    if "--host" in args:
        i = args.index("--host")
        del args[i : i + 2]
    return args


def tool_fingerprint(*paths):
    """Hash of the benchmark's own source.

    A ranking can shift because the GRADER changed, not because a model did.
    Without this, that is indistinguishable from a real regression.

    Pass only what decides a report's numbers or verdicts: the tool, its case
    tables, and determinism.py where the tool runs the probe. Not plumbing —
    not client.py, not this file: every tool once listed provenance.py here
    for the probe's sake, so each edit to the recording code read on the next
    comparison as "the grader changed".

    Line endings are normalised first: with core.autocrlf the same commit is
    CRLF in a Windows checkout and LF in WSL, CI or a fresh clone, and the
    hash of identical source must not depend on which one ran it.
    """
    h = hashlib.sha256()
    here = os.path.dirname(os.path.abspath(__file__))
    for name in sorted(paths):
        try:
            with open(os.path.join(here, name), "rb") as f:
                h.update(f.read().replace(b"\r\n", b"\n"))
        except OSError:
            return None
    return h.hexdigest()[:16]


def busy_lanes(registry_path=None):
    """Which other endpoints were serving while this ran.

    Results shift with what else is running: a CPU lane measured 23.7 tok/s
    alone and 18.6 next to a busy NPU lane. Two runs taken under different load
    are not comparable, and without recording it nobody can tell which was
    which.
    """
    try:
        from orchestrant.benchmark.openai_api import load_backends

        backends, _ = load_backends(registry_path)
    except Exception:
        return None
    # probe:false is the registry's own rule for paid hosts, and this used to
    # ask them anyway on every report. In parallel: on Windows each refused
    # connect waits out its full timeout, and that was ~40 s per report.
    urls = {
        name: entry["base_url"]
        for name, entry in sorted(backends.items())
        if entry.get("base_url") and entry.get("probe", True)
    }

    def answers(url):
        try:
            with urllib.request.urlopen(f"{url.rstrip('/')}/v1/models", timeout=2):
                return True
        except Exception:
            return False

    with ThreadPoolExecutor(max_workers=max(1, len(urls))) as pool:
        up = dict(zip(urls, pool.map(answers, urls.values()), strict=True))
    return [name for name in urls if up[name]]


def energy_proxy():
    """CPU-seconds consumed, as the closest available stand-in for energy.

    Energy per token is the interesting axis on a battery device and the NPU's
    strongest argument over the CPU lane — 165 % of 800 % CPU against 752 % for
    the same work. But this host exposes no power rail: there is no RAPL on
    aarch64 here, no battery discharge counter reachable from WSL2, and the
    Snapdragon's own sensors are not surfaced. Reporting joules would be
    inventing them.

    So: total CPU time, which is proportional to energy for CPU-bound work and
    silent about the NPU's own draw. Recorded as a PROXY under that name, never
    as a measurement, so nobody later mistakes it for one.
    """
    try:
        import resource

        me = resource.getrusage(resource.RUSAGE_SELF)
        kids = resource.getrusage(resource.RUSAGE_CHILDREN)
        return {
            "cpu_seconds_self": round(me.ru_utime + me.ru_stime, 3),
            "cpu_seconds_children": round(kids.ru_utime + kids.ru_stime, 3),
            "note": (
                "CPU time, not joules. This host exposes no power rail "
                "(no RAPL on aarch64, no battery counter through WSL2), and "
                "this number says nothing about NPU or GPU draw."
            ),
        }
    except Exception:
        return None


# Windows 11's power-mode slider (the "overlay" on the active plan). It moves
# CPU clocks, so a CPU-lane number without it is missing a condition.
_POWER_OVERLAYS = {
    "961cc777-2547-4f9d-8174-7d86181b8a7a": "best power efficiency",
    "00000000-0000-0000-0000-000000000000": "balanced",
    "3af9b8d9-7c97-431d-ad78-34a8bfea439f": "better performance",
    "ded574b5-45a0-4f42-8737-46345c09c238": "best performance",
}


def host_power():
    """AC or battery, and the Windows power mode; None off Windows."""
    if sys.platform != "win32":
        return None
    out = {}
    try:
        import ctypes

        class _Status(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_byte),
                ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte),
                ("SystemStatusFlag", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]

        status = _Status()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            out["on_ac"] = {0: False, 1: True}.get(status.ACLineStatus)
            out["battery"] = status.BatteryFlag != -128  # 128: no system battery
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:120]
    try:
        import winreg

        key = r"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            for name in ("ActiveOverlayAcPowerScheme", "ActiveOverlayDcPowerScheme"):
                try:
                    guid = str(winreg.QueryValueEx(k, name)[0]).lower()
                except OSError:
                    continue
                out[name] = _POWER_OVERLAYS.get(guid, guid)
    except Exception as e:
        out.setdefault("error", f"{type(e).__name__}: {e}"[:120])
    return out


def collect(
    base_url=None,
    tool_files=(),
    extra=None,
    temperature=None,
    seed=None,
    determinism=None,
    tool_sha256_at_start=None,
    *,
    host_load=None,
    run_started_utc=None,
):
    """Return a provenance block for a report.

    `temperature`, `seed` and `determinism` (a determinism_probe() result) are
    recorded as explicit nulls when the caller does not supply them.
    `tool_sha256_at_start` is the fingerprint taken when the run began: the
    block is written at the END, and a source edited mid-run would otherwise
    stamp the report with code that did not produce its first rows. Given, it
    sets `source_changed_during_run` either way, so "checked, unchanged" reads
    differently from "never checked". `host_load` (hostload.load_snapshot())
    and `run_started_utc` come from the same run-start record.
    """
    prov = {
        "schema_version": SCHEMA_VERSION,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "monotonic_ns": time.monotonic_ns(),
        "host": platform.node() or None,
        "os": f"{platform.system()} {platform.release()}"
        if platform.system()
        else None,
        "architecture": platform.machine() or None,
        "python": platform.python_version(),
        # The interpreter's OWN platform. On Windows on ARM an x64 Python runs
        # under emulation and platform.machine() still says ARM64, so without
        # this an emulated grader and a native one look identical.
        "interpreter": sysconfig.get_platform(),
        "git_sha": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        # A dirty tree means the recorded SHA does not describe what actually ran.
        "git_dirty": bool(_git("status", "--porcelain")),
        "base_url": base_url,
        "server_models": _server_models(base_url) if base_url else None,
        # Which server build, and the lane's own serve flags when visible.
        "runtime": runtime_info(base_url) if base_url else None,
        "tool_sha256": tool_fingerprint(*tool_files) if tool_files else None,
        # What that hash covers: a changed file SET changes it too, and must
        # be told apart from a changed grader.
        "tool_files": sorted(os.path.basename(p) for p in tool_files) or None,
        "run_started_utc": run_started_utc,
        # Everything else that was answering when this started. A lane that was
        # busy slows the one being measured; recording it is the difference
        # between a comparable number and an unexplained one.
        "live_lanes": busy_lanes(),
        # Liveness is not load: an idle lane and one under a sweep both answer.
        # How busy the machine was at the start is what moves a CPU lane.
        "host_load": host_load,
        # Not energy. See energy_proxy() for why this host cannot measure that.
        "energy_proxy": energy_proxy(),
        "host_power": host_power(),
        "temperature": temperature,
        "seed": seed,
        "determinism_probe": determinism,
    }
    if tool_sha256_at_start:
        changed = tool_sha256_at_start != prov["tool_sha256"]
        if changed:
            prov["tool_sha256_at_start"] = tool_sha256_at_start
        prov["source_changed_during_run"] = changed
    if extra:
        prov.update(extra)

    required = ("timestamp_utc", "host", "architecture", "git_sha")
    prov["incomplete"] = [k for k in required if not prov.get(k)]
    return prov


def collect_or_error(base_url, tool_files, tool_sha256_at_start=None):
    """Return collect(), or the error that stopped it, never raising.

    For a report that must be written regardless: one without provenance
    beats none, and says why it has none.
    """
    try:
        return collect(base_url, tool_files, tool_sha256_at_start=tool_sha256_at_start)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"[:200]}


def _runtime_notes(old_rt, new_rt):
    """A different server build, or a lane launched with different flags."""
    if bool(old_rt) != bool(new_rt):
        return [
            "runtime recorded on one side only — a runtime change cannot be ruled out"
        ]
    if not old_rt:
        return []
    notes = []
    if _runtime_key(old_rt) != _runtime_key(new_rt):
        notes.append(
            f"SERVING RUNTIME CHANGED — {runtime_label(old_rt)} → "
            f"{runtime_label(new_rt)}: a score difference may be the runtime, "
            f"not the model"
        )
    both_seen = (
        old_rt.get("serve_args") is not None and new_rt.get("serve_args") is not None
    )
    if both_seen and _serve_flags(old_rt) != _serve_flags(new_rt):
        notes.append(
            f"lane launched with different serve flags: {_serve_flags(old_rt)} vs "
            f"{_serve_flags(new_rt)}"
        )
    return notes


def _condition_notes(old, new):
    """A report whose own source moved mid-run, and a changed power condition."""
    notes: list[str] = [
        f"{label} run's benchmark source changed WHILE it ran — its first rows "
        f"came from other code than its tool_sha256 names"
        for label, prov in (("the old", old), ("the new", new))
        if prov.get("source_changed_during_run")
    ]
    before, after = old.get("host_power"), new.get("host_power")
    if before and after and before != after:
        notes.append(
            f"host power differs: {before} vs {after} — CPU clocks, and so a "
            f"CPU lane's numbers, move with the Windows power mode"
        )
    return notes


def compare(old, new):
    """Differences between two provenance blocks, worst first.

    Used when diffing two runs: a result that moved while the runtime, the
    grader or the served models also moved is not evidence about the model.
    """
    notes = []
    if old.get("tool_sha256") != new.get("tool_sha256"):
        notes.append(
            "BENCHMARK SOURCE CHANGED — a score difference may be the "
            "grader, not the model"
        )
    notes += _condition_notes(old, new)
    notes += _runtime_notes(old.get("runtime"), new.get("runtime"))
    if old.get("server_models") != new.get("server_models"):
        notes.append(
            "served models differ between the runs (on GenieX /v1/models is the "
            "whole local cache, so a pull alone changes it)"
        )
    if old.get("architecture") != new.get("architecture") or old.get("host") != new.get(
        "host"
    ):
        notes.append("different host or architecture")
    if (
        old.get("live_lanes") is not None
        and new.get("live_lanes") is not None
        and old["live_lanes"] != new["live_lanes"]
    ):
        notes.append(
            f"different lanes were live: {old['live_lanes']} vs "
            f"{new['live_lanes']} — a busy lane slows the one measured"
        )
    if new.get("git_dirty") or old.get("git_dirty"):
        notes.append("at least one run came from a dirty working tree")
    if old.get("git_sha") != new.get("git_sha"):
        notes.append(
            f"repository moved {str(old.get('git_sha'))[:8]} → "
            f"{str(new.get('git_sha'))[:8]}"
        )
    for key in ("temperature", "seed"):
        if old.get(key) != new.get(key):
            notes.append(
                f"{key} differs: {old.get(key)!r} vs {new.get(key)!r} — "
                f"a flip may be sampling, not the model"
            )
    return notes


def known_deterministic(prov):
    """True only when a probe in this provenance block saw two draws agree."""
    probe = (prov or {}).get("determinism_probe") or {}
    return probe.get("deterministic") is True
