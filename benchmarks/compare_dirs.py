"""--dir comparison and stored baselines; bench_compare's judges are passed in, never imported."""

import os

from compare_verdict import exit_code, withheld_lines

BASELINE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baselines")


def pair_directories(old_dir, new_dir):
    """Match reports between two run directories by file name -> (pairs, only_new, only_old)."""

    def reports(d):
        return {
            f for f in os.listdir(d) if f.endswith(".json") and f != "_manifest.json"
        }

    o, n = reports(old_dir), reports(new_dir)
    pairs = sorted(
        (f, os.path.join(old_dir, f), os.path.join(new_dir, f)) for f in (o & n)
    )
    return pairs, sorted(n - o), sorted(o - n)


def baseline_path(name):
    return os.path.join(BASELINE_DIR, f"{name}.json")


def compare_directories(args, compare, load, mde_lines):
    """Compare two run directories report by report with bench_compare's judges; exit code."""
    if len(args.reports) != 2:
        raise SystemExit("--dir takes exactly two directories: OLD NEW")
    old_dir, new_dir = args.reports
    for d in (old_dir, new_dir):
        if not os.path.isdir(d):
            raise SystemExit(f"not a directory: {d}")
    pairs, only_new, only_old = pair_directories(old_dir, new_dir)

    # A config that appeared or vanished between runs IS a change.
    for f in only_old:
        print(f"  ! {f}: in {old_dir} but not in {new_dir}")
    for f in only_new:
        print(f"  ! {f}: new in {new_dir}, nothing to compare against")
    if not pairs:
        raise SystemExit(f"no report names in common between {old_dir} and {new_dir}")

    # A Namespace built by hand (the tests, a script) may predate the flag.
    allow = getattr(args, "allow_load_difference", False)
    regressed_any, blind, differ = False, 0, 0
    for name, old_path, new_path in pairs:
        print(f"\n  {name}")
        seen = {}
        findings, regressed = compare(
            load(old_path), load(new_path), args.time_tolerance, seen, allow
        )
        for line in findings:
            print(f"    {line}")
        if regressed:
            print("    REGRESSION")
        elif seen["withheld"]:
            print("    CONDITIONS DIFFER -- nothing judged regressed")
        elif not seen.get("compared"):
            print("    NOTHING COMPARED -- no verdict")
            blind += 1
        else:
            print("    no regression detected")
            for note in mde_lines(seen):
                print(f"    {note}")
        for line in withheld_lines(seen["withheld"]):
            print(f"    {line}")
        regressed_any = regressed_any or regressed
        differ += bool(seen["withheld"])

    print(
        f"\n  {len(pairs)} report(s) paired, {blind} with nothing to compare, "
        f"{differ} with a verdict withheld for load, "
        f"{len(only_old)} gone, {len(only_new)} new"
    )
    return exit_code(regressed_any, differ, blind < len(pairs))
