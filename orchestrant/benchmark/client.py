"""The front end the bench tools share: plumbing, so kept out of `tool_sha256` and grading."""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

from orchestrant.benchmark.gateway import note_reply


def _row(entry_label, backend, base_url, model, url):
    """One candidate as a dict, before disambiguation."""
    label = entry_label or model or backend or url or "unnamed"
    return {
        "label": label,
        "explicit_label": bool(entry_label),
        "backend": backend,
        "base_url": url,
        "raw_base_url": base_url,
        "model": model,
        "entry": {},
    }


def disambiguate(rows):
    """Make every candidate label unique, in place, or refuse loudly: consumers key on it."""
    seen = {}
    for row in rows:
        seen.setdefault(row["label"], []).append(row)
    for label, group in seen.items():
        if len(group) < 2:
            continue
        explicit = [r for r in group if r["explicit_label"]]
        if explicit:
            raise SystemExit(
                f"two candidates share the label {label!r}. A label names one "
                f"measured endpoint; give each an explicit distinct --label / "
                f'"label" field.'
            )
        for row in group:
            suffix = row["backend"] or row["base_url"]
            if suffix:
                row["label"] = f"{label} ({suffix})"
    final = [r["label"] for r in rows]
    if len(set(final)) != len(final):
        dupes = sorted({lbl for lbl in final if final.count(lbl) > 1})
        raise SystemExit(
            f"candidate labels still collide after disambiguation: {dupes}. "
            f'Give each candidate a distinct "label".'
        )
    return rows


def load_candidates(path, resolve_backend, resolve_entry=None):
    """Read a candidates/--compare file into rows; a `_comment`-only element is not one."""
    with open(path) as f:
        entries = json.load(f)
    if not isinstance(entries, list):
        raise SystemExit(f"{path}: expected a JSON list of candidates")
    rows = []
    for entry in entries:
        if isinstance(entry, dict) and set(entry) <= {"_comment"}:
            continue
        backend, base_url = entry.get("backend"), entry.get("base_url")
        url, model, _ = resolve_backend(backend, base_url)
        row = _row(
            entry.get("label"), backend, base_url, entry.get("model") or model, url
        )
        if resolve_entry is not None:
            row["entry"] = resolve_entry(backend, base_url) or {}
        rows.append(row)
    return disambiguate(rows)


def candidate_rows(args, resolve_backend, resolve_entry=None):
    """The same candidates as resolve_candidates, as whole rows that keep the registry NAME."""
    compare_file = getattr(args, "compare", None)
    if compare_file:
        return load_candidates(compare_file, resolve_backend, resolve_entry)
    backend, base_url = getattr(args, "backend", None), getattr(args, "base_url", None)
    url, model, _ = resolve_backend(backend, base_url)
    row = _row(
        getattr(args, "label", None),
        backend,
        base_url,
        getattr(args, "model", None) or model,
        url,
    )
    if resolve_entry is not None:
        row["entry"] = resolve_entry(backend, base_url) or {}
    return disambiguate([row])


def resolve_candidates(args, resolve_backend, resolve_entry=None):
    """Return [(label, base_url, model)] from --compare or the single-run flags.

    With `resolve_entry`, 4-tuples ending in the backends.json entry.
    """
    rows = candidate_rows(args, resolve_backend, resolve_entry)
    if resolve_entry is None:
        return [(r["label"], r["base_url"], r["model"]) for r in rows]
    return [(r["label"], r["base_url"], r["model"], r["entry"]) for r in rows]


def run_start(tool_files, base_url=None, seconds=3):
    """The start time, source hash and host load a tool records before its first request.

    Pass the same `tool_files` to write_report, or the start hash is not compared.
    """
    from orchestrant.benchmark import hostload
    from orchestrant.benchmark.provenance import tool_fingerprint

    record = {
        "started_utc": datetime.now(UTC).isoformat(),
        "tool_files": list(tool_files),
        "tool_sha256": tool_fingerprint(*tool_files) if tool_files else None,
    }
    try:
        record["host_load"] = hostload.load_snapshot(seconds, lane=base_url)
    except Exception as e:  # a load reading must never cost the run
        record["host_load"] = {
            "other_cores": None,
            "note": f"{type(e).__name__}: {e}"[:160],
        }
    print(hostload.load_line(record["host_load"]), flush=True)
    return record


def _served_model(reports):
    """The one model id every report row served, or None when they differ."""
    models = {r.get("model") for r in reports or () if isinstance(r, dict)}
    return models.pop() if len(models) == 1 else None


def write_report(
    path,
    benchmark,
    config,
    reports,
    base_url,
    tool_files,
    extra=None,
    *,
    run_start=None,
):
    """Write one report in the shared envelope, BEFORE anything that merely prints."""
    from orchestrant.benchmark.provenance import collect

    start, same_files = run_start or {}, True
    started = {}
    if start:
        # Hashes of different file sets always differ; comparing them would invent an edit.
        same_files = list(start.get("tool_files") or ()) == list(tool_files)
        started = {
            "tool_sha256_at_start": start.get("tool_sha256") if same_files else None,
            "host_load": start.get("host_load"),
            "run_started_utc": start.get("started_utc"),
        }
    provenance = collect(base_url, tool_files, model=_served_model(reports), **started)
    if not same_files:
        provenance.setdefault("incomplete", []).append("tool_sha256_at_start")
    for key, value in (extra or {}).items():
        if key == "incomplete":
            provenance["incomplete"] = provenance.get("incomplete", []) + list(value)
        else:
            provenance[key] = value
    payload = {
        "benchmark": benchmark,
        "provenance": provenance,
        "config": config,
        "reports": reports,
    }
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    # Atomic: a Ctrl-C mid-write must not leave a truncated JSON.
    os.replace(tmp, path)
    return payload


# One request path, one API-key read: see benchmarks/docs/llm-benchmark-review-2026-09-05.md § R10


class Response:
    """What post_json returns: `json()` for a body; `lines()`, then `gave_up`, for a stream.

    Use it as a context manager; urllib does not close the socket for you.
    """

    def __init__(self, raw, started, deadline):
        self.raw = raw
        self.status = getattr(raw, "status", None)
        self.headers = getattr(raw, "headers", {})
        self.started = started
        self.gave_up = False
        self._deadline = deadline

    def json(self):
        return json.load(self.raw)

    def lines(self):
        for chunk in self.raw:
            if self._deadline and time.monotonic() - self.started > self._deadline:
                self.gave_up = True
                return
            yield chunk.decode("utf-8", "replace").strip()

    def close(self):
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def request_headers(entry):
    """Content-Type, the entry's own headers, then Bearer auth read here from api_key_env."""
    entry = entry or {}
    headers = {"Content-Type": "application/json"}
    for key, value in (entry.get("headers") or {}).items():
        headers[str(key)] = str(value)
    var = entry.get("api_key_env")
    if var:
        if not os.environ.get(var):
            raise SystemExit(
                f"this backend reads its API key from the environment variable "
                f"{var}, which is unset or empty. Export it before benchmarking "
                f"(the value is never printed or written to a report)."
            )
        headers["Authorization"] = f"Bearer {os.environ[var]}"
    return headers


def request_extras(entry):
    """The entry's request_extra body keys — safe to record in a report."""
    return dict((entry or {}).get("request_extra") or {})


def utf8_stdio():
    """Write stdout/stderr as UTF-8: a piped Windows stdout is cp1252 and raises on '→'."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def spacer(base_url, model, entry=None):
    """One throwaway request: GenieX answers an identical follow-up along a cache path."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        "max_tokens": 1,
        "stream": False,
    }
    try:
        with post_json(
            f"{base_url}/v1/chat/completions", body, entry=entry, timeout=120
        ) as r:
            r.json()
    except Exception:  # nosec B110 -- best effort, see the docstring
        pass


def entry_config(entry):
    """What a committed report may say about a backends.json entry: names, never values."""
    entry = entry or {}
    return {
        "request_extra": request_extras(entry),
        "headers": sorted(entry.get("headers") or {}),
        "api_key_env": entry.get("api_key_env"),
        "probe": bool(entry.get("probe", True)),
    }


REDACTED = "<redacted>"
# Whole words only: --api-key loses its value, --max-tokens keeps its own.
_SECRET_WORDS = frozenset(
    ("key", "apikey", "token", "secret", "password", "passwd", "auth", "bearer")
)
# Credential-shaped values under any flag; hf_/ghp_ need one alphanumeric run, sparing paths.
_SECRET_SHAPES = (
    (re.compile(r"\b(?:sk|pk|rk|glpat)-[\w-]{16,}"), REDACTED),
    (re.compile(r"\b(?:hf|gh[pousr])_[A-Za-z0-9]{16,}"), REDACTED),
    (re.compile(r"\bgithub_pat_\w{16,}"), REDACTED),
    (re.compile(r"(?i)\b(bearer\s+)\S+"), rf"\1{REDACTED}"),
    (
        re.compile(r"(?i)([?&](?:api[-_]?key|key|token|access[-_]?token)=)[^&#\s]+"),
        rf"\1{REDACTED}",
    ),
    (re.compile(r"(://[^/:@\s]+:)[^@/\s]+@"), rf"\1{REDACTED}@"),
)


def _secret_flag(flag):
    """Is `flag` (an argument up to any '=') named for a credential?"""
    if not flag.startswith("-"):
        return False
    return bool(_SECRET_WORDS & set(re.split(r"[-_.]", flag.lstrip("-").lower())))


def redact_argv(argv):
    """A command line as a report may record it: every credential value replaced."""
    out, hide_next = [], False
    for arg in map(str, argv):
        if hide_next and not arg.startswith("-"):
            out.append(REDACTED)
            hide_next = False
            continue
        flag, has_value, _ = arg.partition("=")
        secret = _secret_flag(flag)
        hide_next = secret and not has_value
        kept = f"{flag}={REDACTED}" if secret and has_value else arg
        for pattern, replacement in _SECRET_SHAPES:
            kept = pattern.sub(replacement, kept)
        out.append(kept)
    return out


def post_json(url, body, entry=None, stream=False, timeout=300, deadline=None):
    """POST one JSON body, with the entry's auth, headers and request_extra.

    request_extra goes in BEFORE `body`, so the caller's keys always win; HTTPError propagates.
    """
    payload = request_extras(entry)
    payload.update(body or {})
    if stream:
        payload.setdefault("stream", True)
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=request_headers(entry)
    )
    started = time.monotonic()
    try:
        raw = urllib.request.urlopen(req, timeout=timeout)  # nosec B310 -- endpoint URL from config
    except urllib.error.HTTPError as e:
        note_reply(url, payload, e.headers)
        raise
    note_reply(url, payload, getattr(raw, "headers", None))
    return Response(raw, started, deadline)


def http_error_detail(exc, limit=500):
    """(status, body) of the HTTPError post_json let through; None for any other."""
    if not isinstance(exc, urllib.error.HTTPError):
        return None
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:  # an unreadable body still has its status
        body = ""
    return exc.code, body[:limit]
