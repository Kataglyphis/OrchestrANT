#!/usr/bin/env python3
"""Small statistics helpers, so scores are not published as bare fractions.

A benchmark that prints "8/12" and "12/12" invites the reader to conclude the
second model is better. At that sample size the 95 % Wilson intervals are
[39 %, 86 %] and [76 %, 100 %] — they overlap, and the data does not support
the conclusion. Printing the interval next to the score makes that visible
instead of leaving it to be discovered later.

Wilson rather than the textbook normal approximation: the latter is badly
wrong exactly where this benchmark lives — small n, and proportions at 0 or 1,
where it produces a zero-width interval around a certainty nobody has.

Two models answering the SAME cases are a paired design. Overlap of two
independent intervals is the wrong test for that (see paired_sign_test); it
is kept as the fallback for reports that carry no per-case outcomes.
"""

import math


# Two-sided p-value below which a paired difference counts as separable.
ALPHA = 0.05


def wilson_interval(successes, trials, z=1.96):
    """95 % confidence interval for a proportion. Returns (low, high) in 0..1."""
    if trials <= 0:
        return (0.0, 1.0)
    if successes < 0 or successes > trials:
        raise ValueError(f"successes {successes} out of range for {trials} trials")
    p = successes / trials
    denom = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denom
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def intervals_overlap(a_succ, a_tot, b_succ, b_tot, z=1.96):
    """Do two scores' intervals overlap? If so, they are not separable."""
    a_lo, a_hi = wilson_interval(a_succ, a_tot, z)
    b_lo, b_hi = wilson_interval(b_succ, b_tot, z)
    return a_lo <= b_hi and b_lo <= a_hi


def format_score(successes, trials, width=None):
    """'8/12 = 67% [39-86%]' — the score with what it can actually support."""
    if trials <= 0:
        return "n/a"
    lo, hi = wilson_interval(successes, trials)
    pct = 100 * successes / trials
    s = f"{successes}/{trials} = {pct:.0f}% [{100 * lo:.0f}-{100 * hi:.0f}%]"
    return f"{s:{width}}" if width else s


def significance_note(a_label, a_succ, a_tot, b_label, b_succ, b_tot):
    """Plain-language verdict on whether two scores are separable at all."""
    if a_tot <= 0 or b_tot <= 0:
        return "one side has no observations — nothing to compare"
    a_rate, b_rate = a_succ / a_tot, b_succ / b_tot
    if intervals_overlap(a_succ, a_tot, b_succ, b_tot):
        better = a_label if a_rate > b_rate else b_label
        if a_rate == b_rate:
            return "identical rates"
        return (
            f"{better} scores higher, but the 95 % intervals OVERLAP at this "
            f"sample size — not separable on this evidence alone"
        )
    better = a_label if a_rate > b_rate else b_label
    return f"{better} is higher and the intervals do not overlap"


def smallest_separable_rate(trials, from_rate=1.0, z=1.96):
    """The lowest rate that is still distinguishable from `from_rate` here.

    Without it, "no regression" is ambiguous between "nothing changed" and
    "this suite is too small to tell" — and the second reads exactly like the
    first. Measured example: removing a system prompt took a model from 8/8 to
    6/8, a real and causally understood degradation, and at n=8 the intervals
    still overlapped. Detecting a 100%->75% drop needs 27 cases; 100%->87.5%
    needs 60.

    Returns None when no drop at all is provable at this sample size.
    """
    if trials <= 0:
        return None
    successes = round(from_rate * trials)
    for lower in range(successes - 1, -1, -1):
        if not intervals_overlap(successes, trials, lower, trials, z):
            return lower / trials
    return None


def power_note(trials, from_rate=1.0):
    """One line stating what a 'no regression' verdict is actually worth."""
    mde = smallest_separable_rate(trials, from_rate)
    if mde is None:
        return (
            f"at n={trials} this suite cannot prove ANY drop — "
            f"'no regression' here means 'cannot tell'"
        )
    return (
        f"at n={trials} the smallest provable drop is {100 * from_rate:.0f}% -> "
        f"{100 * mde:.0f}%; anything subtler passes unnoticed"
    )


def diff_interval(a_succ, a_tot, b_succ, b_tot, z=1.96):
    """Newcombe hybrid-score 95 % interval for the difference b_rate - a_rate.

    Two overlapping Wilson intervals do NOT mean the difference includes zero;
    the overlap rule is far more conservative than a test on the difference.
    Returns (low, high) in -1..1; (-1.0, 1.0) when either side has no trials.
    """
    if a_tot <= 0 or b_tot <= 0:
        return (-1.0, 1.0)
    pa, pb = a_succ / a_tot, b_succ / b_tot
    la, ua = wilson_interval(a_succ, a_tot, z)
    lb, ub = wilson_interval(b_succ, b_tot, z)
    d = pb - pa
    low = d - math.sqrt((pb - lb) ** 2 + (ua - pa) ** 2)
    high = d + math.sqrt((ub - pb) ** 2 + (pa - la) ** 2)
    return (max(-1.0, low), min(1.0, high))


def paired_sign_test(discordant_a, discordant_b):
    """Exact two-sided sign test on paired per-case outcomes.

    `discordant_a` = cases only A got right, `discordant_b` = cases only B got
    right; cases both got right or both got wrong carry no information about
    which is better and are not passed in. Under "no difference" each
    discordant case is a fair coin, so the p-value is the two-sided binomial
    tail. 6-0 gives 0.031; 3-0 gives 0.25; no discordant cases gives 1.0.
    """
    if discordant_a < 0 or discordant_b < 0:
        raise ValueError("discordant counts cannot be negative")
    n = discordant_a + discordant_b
    if n == 0:
        return 1.0
    k = min(discordant_a, discordant_b)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def _rate(value):
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    passes, attempts = value
    return passes / attempts if attempts else None


def paired_outcomes(a_cases, b_cases):
    """Count shared cases where A did better, B did better, or neither.

    Values are either a bool (one draw) or a (passes, attempts) pair; a case
    with no measured attempt on either side is skipped, not counted as a tie.
    """
    a_better = b_better = ties = 0
    for key in set(a_cases) & set(b_cases):
        ra, rb = _rate(a_cases[key]), _rate(b_cases[key])
        if ra is None or rb is None:
            continue
        if ra > rb:
            a_better += 1
        elif rb > ra:
            b_better += 1
        else:
            ties += 1
    return a_better, b_better, ties


def smallest_detectable_flips(alpha=ALPHA):
    """How many cases must flip ONE way, with none flipping back, to be seen.

    Independent of the suite size: a paired test looks only at the cases that
    disagreed. At alpha=0.05 the answer is 6 (2 * 0.5**6 = 0.031).
    """
    k = 1
    while paired_sign_test(k, 0) >= alpha:
        k += 1
    return k


def paired_power_note(alpha=ALPHA):
    k = smallest_detectable_flips(alpha)
    return (
        f"paired: {k} cases flipping the same way (none flipping back) would "
        f"be detected; fewer cannot be, whatever the suite size"
    )


def tiers(rows, key, alpha=ALPHA):
    """Group already-ranked rows whose neighbours are not separably different.

    `key(row)` returns that row's per-case outcomes ({case: bool} or
    {case: (passes, attempts)}). Adjacent rows are compared with the paired
    sign test; a new tier starts where p < alpha. Rows sharing a tier should be
    printed as a tie, not as an ordering the data does not support.
    """
    groups = []
    for row in rows:
        if groups:
            a_better, b_better, _ = paired_outcomes(key(groups[-1][-1]), key(row))
            if paired_sign_test(a_better, b_better) < alpha:
                groups.append([row])
                continue
            groups[-1].append(row)
        else:
            groups.append([row])
    return groups
