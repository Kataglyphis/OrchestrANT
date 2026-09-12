#!/usr/bin/env python3
"""The front end bench_coding.py and bench_tools.py share.

Not extracted for tidiness. The candidate-resolution block was byte-identical
across 17 lines in both tools, comment included — and the None-label defect an
audit found existed in BOTH copies and had to be fixed twice, in one commit,
because a sweep happened to walk every file. The next such lesson would have
been written into one copy only.

Extracting it also gives that lesson a test seam it never had: nothing in
tests/ imports either tool's main(), so candidate resolution — the code that
decides WHICH endpoint gets measured — had no coverage at all.

Deliberately NOT here:
  * grading logic (TASKS, extract_code, run_candidate, grade, evaluate) stays
    in each tool, and this module is deliberately kept OUT of the
    `tool_sha256` fingerprint. That hash exists so bench_compare can say "a
    score difference may be the grader, not the model"; folding plumbing into
    it would fire that alarm on every --compare-schema edit while the grader is
    provably unchanged — a false-positive generator on the suite's headline
    safety signal.
  * bench_lanes.resolve_lane, which parses a different surface
    (`name=URL,model=MODEL` strings, not a JSON candidate file). Merging them
    would produce one resolver with two input grammars.
"""

import json
import os
import time
import urllib.request


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
    """Make every candidate label unique, in place, or refuse loudly.

    Two lanes serving the same GGUF resolve to the same bare model id, and
    every consumer keys on the label: the ranking collapsed them into one row
    and bench_compare read a 3/3 -> 0/3 collapse as "unchanged". A bare
    model id is still the label when it is unique, so a stored baseline
    recorded under one keeps comparing.
    """
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
    """Read a candidates/--compare file into disambiguated candidate rows.

    An element carrying only `_comment` is documentation, not a candidate: an
    example file has to be able to explain its own fields.
    """
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
    """The same candidates as resolve_candidates, as whole rows.

    A tuple cannot carry the registry NAME, and a ranking needs it: the
    calibration endpoint is the one whose backend is "control", and losing that
    name meant a control lane could only be recognised by its label.
    """
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

    `resolve_backend` is passed in rather than imported so this module does not
    drag orchestrant.benchmark.openai_api into every importer, and so tests can
    substitute a stub without a server. Pass `resolve_entry` (openai_api's
    resolve_backend_entry) to get 4-tuples ending in the backends.json entry —
    the api_key_env/headers/request_extra a request needs. The 3-tuple stays
    the default because every existing caller unpacks three.

    The label fallback chain is load-bearing: both `label` and `model` are
    optional in a --compare file (the model may come from backends.json), and a
    None label used to crash the ranking print AFTER every request had been
    made and BEFORE the report was written — destroying hours of measurement
    over a format string.
    """
    rows = candidate_rows(args, resolve_backend, resolve_entry)
    if resolve_entry is None:
        return [(r["label"], r["base_url"], r["model"]) for r in rows]
    return [(r["label"], r["base_url"], r["model"], r["entry"]) for r in rows]


def write_report(path, benchmark, config, reports, base_url, tool_files, extra=None):
    """Write one report in the shared envelope.

    Written BEFORE anything that merely prints: a ranking table must never be
    able to destroy a completed measurement, which is exactly what happened
    when a format string raised on a None label.

    `tool_files` stays per-tool on purpose — see the module docstring on why
    this file is not in the fingerprint. `extra` is merged into the provenance
    block; its `incomplete` list extends collect()'s rather than replacing it.
    """
    from orchestrant.benchmark.provenance import collect

    provenance = collect(base_url, tool_files)
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
    # Atomic: a Ctrl-C during the write used to be able to leave a truncated
    # JSON that a later comparison would silently misread.
    os.replace(tmp, path)
    return payload


# ── the one request path ─────────────────────────────────────────────────────
# One function, one place where an API key is read. Why six sites were not:
# docs/llm-benchmark-review-2026-09-05.md § R10.


class Response:
    """What post_json returns: one shape both kinds of caller can use.

    Non-streamed caller: `resp.json()` — the parsed body.
    Streamed caller: iterate `resp.lines()` (decoded, stripped SSE lines) and
    then read `resp.gave_up`, True when `deadline` seconds elapsed and the
    stream was abandoned rather than finished. The deadline clock starts before
    the request is sent, so a slow prefill counts against it.

    Use it as a context manager; urllib does not close the socket for you.
    `raw` is the urlopen response for anything neither accessor covers.
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
    """Content-Type, the entry's own headers, then Bearer auth from api_key_env.

    backends.json holds the NAME of the environment variable, never a key. The
    value is read here and nowhere else: it is not stored, printed or written
    to a report, and an unset variable is a loud failure rather than an
    anonymous 401 an hour into a sweep.
    """
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


def entry_config(entry):
    """What a report may say about a backends.json entry.

    Header NAMES only and the api-key VARIABLE name only: a value could be the
    key itself, and reports are committed.
    """
    entry = entry or {}
    return {
        "request_extra": request_extras(entry),
        "headers": sorted(entry.get("headers") or {}),
        "api_key_env": entry.get("api_key_env"),
        "probe": bool(entry.get("probe", True)),
    }


def post_json(url, body, entry=None, stream=False, timeout=300, deadline=None):
    """POST one JSON body, with the entry's auth, headers and request_extra.

    request_extra is applied BEFORE `body`, so an explicit key the caller sets
    (model, max_tokens, temperature) always wins over the registry default —
    a per-backend num_ctx must never be able to silently change the budget a
    benchmark is measuring. The merge is one level deep.

    Returns a `Response`; see it for the streamed/non-streamed accessors.
    HTTPError propagates unchanged: callers classify 4xx bodies themselves.
    """
    payload = request_extras(entry)
    payload.update(body or {})
    if stream:
        payload.setdefault("stream", True)
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=request_headers(entry)
    )
    started = time.monotonic()
    return Response(urllib.request.urlopen(req, timeout=timeout), started, deadline)
