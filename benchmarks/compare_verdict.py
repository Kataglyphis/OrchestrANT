"""bench_compare's verdict: its exit codes, their order, and the load gate."""

from orchestrant.benchmark.provenance import _load_notes, _other_cores

# Nothing to compare: never "no regression", and not argparse's usage-error 2.
NOT_COMPARED = 3
# A load-movable verdict was withheld (scores stay judged: load slows answers, not changes them).
CONDITIONS_DIFFER = 4

# Worded to fit every withholding, equally busy starts and a lane's own loaded requests too.
WHY = (
    "a run started on a busy host, the two started under different load, or a "
    "CPU lane's requests ran under other load"
)
# What a withheld verdict's line carries in place of SLOWER / faster / better.
WITHHELD = "   WITHHELD for load (the ! note above)"
# The busy side may be the baseline; a quiet re-run of the new side alone is refused again.
REMEDY = "re-run the busy side on a quiet host"
# A NOT judged line: the flag only lets it pass, never judges it (LoadGate.withhold_row).
ROW_LOAD = "(its requests' load)"


def exit_code(regressed, withheld, compared):
    """The one order of the codes: REGRESSION 1, CONDITIONS DIFFER 4, NOTHING COMPARED 3."""
    if regressed:
        return 1
    if withheld:
        return CONDITIONS_DIFFER
    return 0 if compared else NOT_COMPARED


class LoadGate:
    """One pairing's gate: `shut` when a load note fires, and what it withheld."""

    def __init__(self, old, new, allow=False, seen=None):
        """Shut exactly where provenance.compare() prints a load note; `allow` keeps it open."""
        sides = (old.get("provenance") or {}, new.get("provenance") or {})
        self.allow = allow
        self.shut = bool(_load_notes(*sides)) and not allow
        recorded = [c for c in map(_other_cores, sides) if c is not None]
        self.busiest = max(recorded, default=None)
        self.withheld = []
        if seen is not None:
            seen["withheld"] = self.withheld

    def withhold(self, label, verdict):
        """Record `verdict` as withheld; returns the mark its line carries."""
        self.withheld.append(f"{label} {verdict}")
        return WITHHELD

    def withhold_row(self, label, verdict):
        """Record `verdict`, NOT judged by its own requests' load, unless the flag passes it."""
        if not self.allow:
            self.withhold(label, f"{verdict} {ROW_LOAD}")

    def judge(self, label, verdict, worse_by, tolerance):
        """(mark, slower) for a relative change, `worse_by` > 0 worse; withheld while shut."""
        if self.shut:
            return self.withhold(label, verdict), False
        if worse_by > tolerance:
            return "   *** SLOWER ***", True
        if worse_by < -tolerance:
            return "   faster", False
        return "", False


def withheld_lines(withheld):
    """What a gate withheld, and why, under the closing verdict; [] if nothing."""
    if not withheld:
        return []
    rows = sum(w.endswith(ROW_LOAD) for w in withheld)
    unjudged = "let a NOT judged line pass unjudged"
    if not rows:
        flag = "judge these anyway"
    elif rows == len(withheld):
        flag = unjudged
    else:
        flag = f"judge the rest anyway and {unjudged}"
    return [
        f"WITHHELD for load: {', '.join(withheld)} -- {WHY} (the ! note or the "
        f"NOT judged line above says which)",
        f"scores, per-case flips and batching are never withheld; {REMEDY}, "
        f"or pass --allow-load-difference to {flag}",
    ]


def step_status(rc, lines):
    """(status, reason) for a `bench_compare --dir` step that exited `rc`.

    Exit 1 counts only with REGRESSION printed, and any verdict needs the closing
    "N report(s) paired" line; anything else is "failed".
    """
    done = any("report(s) paired" in line for line in lines)
    if rc == 0:
        return "ok", "compared; no regression"
    if rc == NOT_COMPARED:
        return "nothing-compared", "bench_compare exit 3"
    if rc == 1 and done and any(x.startswith("REGRESSION") for x in lines):
        return "regression", "bench_compare: REGRESSION"
    if rc == CONDITIONS_DIFFER and done:
        return "conditions-differ", (
            f"bench_compare withheld a speed or timing verdict: {WHY} -- {REMEDY}"
        )
    return "failed", f"bench_compare exited {rc} with no verdict"
