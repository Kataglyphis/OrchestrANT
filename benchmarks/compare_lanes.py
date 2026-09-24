#!/usr/bin/env python3
"""Each lane's serving runtime, diffed lane by lane (roadmap OPS-7).

A `lanes` report drives several endpoints at once, and each lane row carries
that lane's own `runtime` (provenance.runtime_info(): the build, the serve
flags, the model files, the drivers). The provenance block is collected for
ONE URL -- the first lane's, or the batching endpoint's under --batching -- so
provenance.compare(), the only runtime diff bench_compare ran, saw that
endpoint and no other: a second lane rebuilt, relaunched with another --nctx
or re-pulled behind the same id moved its tok/s with no note saying why. The
notes are provenance's own
(runtime_notes(): SERVING RUNTIME CHANGED, the serve flags, MODEL FILES
CHANGED and the QAIRT bundle's edited files), named per lane.
"""

from orchestrant.benchmark.provenance import runtime_notes


def _evidence(runtime):
    """The runtime when it names a server, else None.

    lanes.py records {"error": ...} for a lane it could not read. That is no
    evidence about a build; compared as one it reads as SERVING RUNTIME
    CHANGED to "? ?", where "recorded on one side only" is what is known.
    """
    return runtime if isinstance(runtime, dict) and runtime.get("server") else None


def lane_runtimes(rows, prov=None):
    """{lane: runtime or None} over a report's raw rows; {} if no row is a lane.

    A lane row is one with a `together` measurement (lanes.build_reports());
    the aggregate and batching rows are no one lane's. A row written before
    the field existed has no `runtime` key at all: when its URL is the one
    the provenance block was collected for, that block's runtime is the
    lane's -- the tracked lanes reports of 2026-09-23 recorded their first
    lane there and no other. A `runtime` of None is the tool's own answer
    (nobody could attribute the lane) and stays None.
    """
    prov = prov or {}
    out = {}
    for row in rows:
        if "together" not in row:
            continue
        runtime = row.get("runtime")
        url = row.get("base_url")
        if "runtime" not in row and url and url == prov.get("base_url"):
            runtime = prov.get("runtime")
        out[row.get("label")] = _evidence(runtime)
    return out


def lane_findings(old, new):
    """Two normalised reports' lane runtimes, as "! lane NAME: note" lines.

    A lane served on one side only is a finding of its own: the aggregate
    row then covers another set of lanes. Neither kind is a regression --
    like provenance.compare()'s notes, they say what else moved.
    """
    before = old.get("lane_runtimes") or {}
    after = new.get("lane_runtimes") or {}
    gone, added = before.keys() - after.keys(), after.keys() - before.keys()
    findings = [
        f"! lane {lane}: in the {side} run only — the aggregate row covers "
        f"another set of lanes, so its tok/s is not like-for-like"
        for side, lanes in (("old", gone), ("new", added))
        for lane in sorted(lanes)
    ]
    for lane in sorted(before.keys() & after.keys()):
        findings += [
            f"! lane {lane}: {note}"
            for note in runtime_notes(before[lane], after[lane])
        ]
    return findings


def lane_set_changed(old, new):
    """Did two lanes reports run different sets of lanes? Then the aggregate
    row sums another set, and lane_findings() says which lanes moved."""
    before = old.get("lane_runtimes") or {}
    after = new.get("lane_runtimes") or {}
    return bool(before and after) and before.keys() != after.keys()
