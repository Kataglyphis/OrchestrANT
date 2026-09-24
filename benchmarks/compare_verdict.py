"""bench_compare's verdict: its exit codes, their order, and the load gate.

Since 2026-09-24 provenance.compare() has named two runs started under
different load, or on a busy host, and the exit status did not move: a SLOWER,
or a "no regression", taken across them still read as evidence. On the lab
host a CPU lane loses ~14.5 tok/s per core of other load (30 tok/s quiet, 14.1
at 0.93 cores). When those notes fire now, the verdicts load can move -- the
speed tripwire's, the per-attempt time, a lane's throughput -- are WITHHELD
and the run exits CONDITIONS_DIFFER; --allow-load-difference judges them
anyway. Scores, per-case flips and batching stay judged: load slows an answer,
it does not change it.
"""

from orchestrant.benchmark.provenance import _load_notes

# Exit code when two reports share nothing to compare: never "no regression",
# and not argparse's usage-error 2 either.
NOT_COMPARED = 3
# Exit code when a verdict load can move was withheld and nothing judged
# regressed: the remedy is a re-run on a quiet host, not a fix.
CONDITIONS_DIFFER = 4

# What a withheld verdict's line carries in place of SLOWER / faster / better.
WITHHELD = "   WITHHELD: the runs did not start under like load"


def exit_code(regressed, withheld, compared):
    """The one order of the codes, for a single pair or over a --dir run.

    REGRESSION (1) first: a score that fell was judged on evidence load does
    not move, and a timing verdict withheld beside it makes it no less true.
    CONDITIONS DIFFER (4) next: something WAS compared, and 0 would say the
    withheld verdict passed. NOTHING COMPARED (3) only when nothing was; a
    withheld verdict implies something was, so 3 never hides a 4.
    """
    if regressed:
        return 1
    if withheld:
        return CONDITIONS_DIFFER
    return 0 if compared else NOT_COMPARED


class LoadGate:
    """One pairing's gate: `shut` when a load note fires, and what it withheld."""

    def __init__(self, old, new, allow=False, seen=None):
        """Shut exactly where provenance.compare() prints a load note for these
        two normalised reports: one function decides both, so the note and the
        refusal cannot disagree. A side that predates the record reads
        `unrecorded`, which alone never shuts the gate; `allow` (the flag
        --allow-load-difference) keeps it open. `seen`, when a dict, gets the
        list of withheld verdicts as "withheld"."""
        notes = _load_notes(old.get("provenance") or {}, new.get("provenance") or {})
        self.shut = bool(notes) and not allow
        self.withheld = []
        if seen is not None:
            seen["withheld"] = self.withheld

    def withhold(self, label, verdict):
        """Record `verdict` as withheld; returns the mark its line carries."""
        self.withheld.append(f"{label} {verdict}")
        return WITHHELD

    def judge(self, label, verdict, worse_by, tolerance):
        """(mark, slower) for a relative change, `worse_by` > 0 being worse.

        Per-attempt time and lane throughput share the timing tolerance, and
        both are withheld, whichever way they moved, while the gate is shut: a
        busy baseline makes a real slowdown look flat.
        """
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
    return [
        f"WITHHELD for load: {', '.join(withheld)} -- the runs did not start "
        f"under like load (the ! note above)",
        "scores, per-case flips and batching are never withheld; re-run on a "
        "quiet host, or pass --allow-load-difference to judge these anyway",
    ]
