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

import hashlib
import json
import os
import platform
import subprocess
import time
import urllib.request
from datetime import UTC, datetime


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
    """Model ids the endpoint advertises — cheap fingerprint of what is served."""
    try:
        with urllib.request.urlopen(f"{base_url}/v1/models", timeout=timeout) as r:
            return sorted(m["id"] for m in json.load(r).get("data", []))
    except Exception:
        return None


def tool_fingerprint(*paths):
    """Hash of the benchmark's own source.

    A ranking can shift because the GRADER changed, not because a model did.
    Without this, that is indistinguishable from a real regression.
    """
    h = hashlib.sha256()
    here = os.path.dirname(os.path.abspath(__file__))
    for name in sorted(paths):
        try:
            with open(os.path.join(here, name), "rb") as f:
                h.update(f.read())
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
    live = []
    for name, entry in sorted(backends.items()):
        url = entry.get("base_url")
        if not url:
            continue
        try:
            with urllib.request.urlopen(f"{url.rstrip('/')}/v1/models", timeout=2):
                live.append(name)
        except Exception:
            continue
    return live


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


def determinism_probe(
    base_url, model, post, prompt="Reply with the single word: ready"
):
    """Send two identical tiny requests; record whether the outputs matched.

    `post(url, payload) -> dict` is injected so this can run without a server
    (tests) and so callers pick the transport. "Deterministic" here means two
    draws at temperature 0 agreed byte-for-byte — evidence, not proof, and it is
    recorded as such so a --repeats 1 flip can be read for what it is.
    """
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 8,
        "stream": False,
        "messages": [{"role": "user", "content": prompt}],
    }
    outputs = []
    try:
        for _ in range(2):
            reply = post(f"{base_url}/v1/chat/completions", payload)
            content = (
                (reply.get("choices") or [{}])[0].get("message", {}).get("content")
            )
            if content is None:
                raise ValueError("reply carried no message content")
            outputs.append(content)
    except Exception as e:
        return {
            "deterministic": None,
            "requests": len(outputs),
            "prompt": prompt,
            "error": f"{type(e).__name__}: {e}"[:200],
        }
    return {
        "deterministic": outputs[0] == outputs[1],
        "requests": 2,
        "prompt": prompt,
        "output_sha256": [hashlib.sha256(o.encode()).hexdigest()[:16] for o in outputs],
        "error": None,
    }


def collect(
    base_url=None,
    tool_files=(),
    extra=None,
    temperature=None,
    seed=None,
    determinism=None,
):
    """Return a provenance block for a report.

    `temperature`, `seed` and `determinism` (a determinism_probe() result) are
    recorded as explicit nulls when the caller does not supply them.
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
        "git_sha": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        # A dirty tree means the recorded SHA does not describe what actually ran.
        "git_dirty": bool(_git("status", "--porcelain")),
        "base_url": base_url,
        "server_models": _server_models(base_url) if base_url else None,
        "tool_sha256": tool_fingerprint(*tool_files) if tool_files else None,
        # Everything else that was answering when this started. A lane that was
        # busy slows the one being measured; recording it is the difference
        # between a comparable number and an unexplained one.
        "live_lanes": busy_lanes(),
        # Not energy. See energy_proxy() for why this host cannot measure that.
        "energy_proxy": energy_proxy(),
        "temperature": temperature,
        "seed": seed,
        "determinism_probe": determinism,
    }
    if extra:
        prov.update(extra)

    required = ("timestamp_utc", "host", "architecture", "git_sha")
    prov["incomplete"] = [k for k in required if not prov.get(k)]
    return prov


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
    if old.get("server_models") != new.get("server_models"):
        notes.append("served models differ between the runs")
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
