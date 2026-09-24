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

Repeated draws of one case are not independent trials either: the case is the
unit of sampling (clustered_rate), and a "no regression" is only worth the
drop the paired test could have caught (paired_mde).
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


# A case that flips back (A failed, B passes) between two runs of one model,
# assumed when a comparison observed none: zero back-flips in 31 cases does
# not make the rate zero (its Wilson upper bound is 11 %).
DEFAULT_BACK_FLIP_RATE = 0.05


def _counts(value):
    """(passes, attempts) from a (passes, attempts) pair or a one-draw bool."""
    if isinstance(value, bool):
        return (int(value), 1)
    passes, attempts = value
    if not 0 <= passes <= max(attempts, 0):
        raise ValueError(f"passes {passes} out of range for {attempts} attempts")
    return passes, attempts


def clustered_rate(cases, z=1.96):
    """Pooled pass rate with the CASE, not the draw, as the unit of sampling.

    Three draws of one prompt are not three independent trials. Measured on
    v070-npu-tools-r3: 42 cases, 31 passing every draw, 6 failing every draw,
    5 mixed; the pooled 98/124 = 79 % printed [71-85 %] where the data supports
    [66-88 %] -- a design effect of 2.6. After Miller 2024, "Adding Error Bars
    to Evals", the variance is the cluster-robust one of a ratio estimator,
    with the CR1 small-sample factor G / (G - 1) over G cases:

        var = G / (G - 1) * sum_c (passes_c - rate * attempts_c)^2 / attempts^2

    design_effect is var over the binomial rate * (1 - rate) / attempts, and
    n_eff = attempts / design_effect, with the design effect floored at 1 for
    n_eff: repeats of one prompt are not anti-correlated, so a ratio below 1 is
    noise and must not make the interval narrower than the unclustered one.
    The interval is Wilson on (rate * n_eff, n_eff), so it stays in [0, 1]
    (p +/- z * se reads [68-91 %] on the case above and escapes [0, 1] near
    the edges).

    One case, or every attempt agreeing, leaves the correlation inestimable:
    design_effect (and, for one case, se) is then None and the case is taken
    as the unit, n_eff = attempts^2 / sum_c attempts_c^2 -- the case count
    when every case has the same attempts, as the deterministic lanes do.

    `cases` maps a case key to (passes, attempts) or to a one-draw bool; a case
    with no attempt is skipped. Returns None when nothing was attempted, else a
    dict: rate, se, design_effect, n_eff, low, high, n_cases, passes, attempts.
    """
    counts = [c for c in map(_counts, cases.values()) if c[1] > 0]
    if not counts:
        return None
    passes = sum(p for p, _ in counts)
    attempts = sum(m for _, m in counts)
    n_cases = len(counts)
    rate = passes / attempts
    naive = rate * (1 - rate) / attempts
    var = None
    if n_cases > 1:
        resid = sum((p - rate * m) ** 2 for p, m in counts)
        var = n_cases / (n_cases - 1) * resid / attempts**2
    if var is None or naive == 0:
        deff = None
        n_eff = attempts**2 / sum(m * m for _, m in counts)
    else:
        deff = var / naive
        n_eff = attempts / max(1.0, deff)
    low, high = wilson_interval(rate * n_eff, n_eff, z)
    return {
        "rate": rate,
        "se": None if var is None else math.sqrt(var),
        "design_effect": deff,
        "n_eff": n_eff,
        "low": low,
        "high": high,
        "n_cases": n_cases,
        "passes": passes,
        "attempts": attempts,
    }


def paired_difference(a_cases, b_cases, z=1.96):
    """Mean per-case change in pass rate, B minus A, with a case-clustered interval.

    Both runs answered the same cases, so each case's own difference is the
    observation; the Newcombe interval on two aggregates treats them as
    independent samples and printed +/-10 pt around two byte-identical runs of
    98/124. With one difference per case, the CR1 cluster-robust standard error
    of their mean is the ordinary one, s / sqrt(G), so identical per-case
    outcomes give [0, 0]. Cases measured on only one side are skipped, as in
    paired_outcomes. Returns None when no case is shared, else
    (mean, low, high, n_cases); one shared case leaves the spread unknown and
    the interval at (-1, 1).
    """
    diffs = []
    for key in set(a_cases) & set(b_cases):
        ra, rb = _rate(a_cases[key]), _rate(b_cases[key])
        if ra is not None and rb is not None:
            diffs.append(rb - ra)
    if not diffs:
        return None
    n = len(diffs)
    mean = sum(diffs) / n
    if n < 2:
        return (mean, -1.0, 1.0, n)
    se = math.sqrt(sum((d - mean) ** 2 for d in diffs) / (n * (n - 1)))
    return (mean, max(-1.0, mean - z * se), min(1.0, mean + z * se), n)


def pass_hat_k(cases, k):
    """The chance a case passes k draws out of k, estimated without bias.

    tau-bench's pass^k: over the cases with at least k attempts, the mean of
    C(passes, k) / C(attempts, k) -- the share of k-subsets of a case's draws
    that all passed. Plugging the pooled rate in as rate**k is biased; this is
    not. A 2/3 case scores 1/3 at k=2 and 0 at k=3. Returns None when no case
    has k attempts.
    """
    if k < 1:
        raise ValueError(f"k must be at least 1, got {k}")
    terms = [
        math.comb(p, k) / math.comb(m, k)
        for p, m in map(_counts, cases.values())
        if m >= k
    ]
    return sum(terms) / len(terms) if terms else None


def _sign_test_rejects(n_cases, alpha):
    """Per discordant count d = 0..n_cases, the most `better` cases a split may
    carry and still read as a regression -- worse > better and
    paired_sign_test < alpha -- or -1 when no split of d is significant.

    The same float arithmetic as paired_sign_test, with the binomial tail
    accumulated instead of recomputed for every split.
    """
    limits = []
    for d in range(n_cases + 1):
        limit, tail, coef, k = -1, 0, 1, 0
        while 2 * k < d:
            tail += coef
            if min(1.0, 2 * (tail / 2**d)) >= alpha:
                break
            limit = k
            coef = coef * (d - k) // (k + 1)
            k += 1
        limits.append(limit)
    return limits


def _binomial_pmf(n, p):
    """P(X = k) for k = 0..n, X ~ Binomial(n, p), in log space: 500 draws at a
    small p underflow the direct product.
    """
    if p <= 0:
        return [1.0] + [0.0] * n
    if p >= 1:
        return [0.0] * n + [1.0]
    lp, lq, lg = math.log(p), math.log1p(-p), math.lgamma
    return [
        math.exp(lg(n + 1) - lg(k + 1) - lg(n - k + 1) + k * lp + (n - k) * lq)
        for k in range(n + 1)
    ]


def paired_power(
    n_cases, drop, back_flip_rate=DEFAULT_BACK_FLIP_RATE, alpha=ALPHA, _limits=None
):
    """Chance that paired_sign_test flags a regression of `drop`.

    Model: each case independently gets worse with probability
    back_flip_rate + drop, better with back_flip_rate, else ties. Exact: the
    discordant count D is Binomial(n, p_worse + p_better) and, given D, the
    better count is Binomial(D, p_better / (p_worse + p_better)) -- the
    multinomial over (worse, better) counts, summed one D at a time. Terms of
    D below 1e-15 are skipped. Measured: 31 cases catch a 10-point drop 8 %
    of the time with no back-flips and 11 % at 5 %.
    """
    p_worse, p_better = back_flip_rate + drop, back_flip_rate
    if drop < 0 or p_better < 0 or p_worse + p_better > 1:
        raise ValueError(f"no such case mix: drop {drop}, back-flip {back_flip_rate}")
    discordant = p_worse + p_better
    if n_cases <= 0 or discordant == 0:
        return 0.0
    limits = _limits or _sign_test_rejects(n_cases, alpha)
    power = 0.0
    for d, p_d in enumerate(_binomial_pmf(n_cases, discordant)):
        if limits[d] >= 0 and p_d >= 1e-15:
            better = _binomial_pmf(d, p_better / discordant)
            power += p_d * sum(better[: limits[d] + 1])
    return power


def paired_mde(n_cases, back_flip_rate=DEFAULT_BACK_FLIP_RATE, power=0.8, alpha=ALPHA):
    """Smallest net drop, as a fraction of the cases, caught with `power`.

    "No regression" from a paired sign test means little without it: 31
    cases catch a 10-point drop 8-11 % of the time. paired_power() is exact
    over the (worse, better) counts; power only grows with the drop -- turning
    a tie into a worse case never un-flags a regression -- so a bisection on
    the drop, to 1e-4, finds where it reaches `power`. Returns None when even
    the largest possible drop, 1 - 2 * back_flip_rate, falls short: at alpha
    0.05 the test needs 6 one-way flips, so 5 cases can never regress.
    """
    if n_cases <= 0:
        return None
    limits = _sign_test_rejects(n_cases, alpha)
    low, high = 0.0, 1.0 - 2 * back_flip_rate
    if high <= 0 or paired_power(n_cases, high, back_flip_rate, alpha, limits) < power:
        return None
    while high - low > 1e-4:
        mid = (low + high) / 2
        if paired_power(n_cases, mid, back_flip_rate, alpha, limits) >= power:
            high = mid
        else:
            low = mid
    return high


def paired_mde_note(n_cases, back_flips=0, power=0.8, alpha=ALPHA):
    """One line: the smallest drop a paired comparison of n_cases would catch.

    The back-flip rate is the observed one (back_flips / n_cases) when a case
    flipped back, else DEFAULT_BACK_FLIP_RATE; the line says which.
    """
    if back_flips:
        rate, source = back_flips / n_cases, "observed"
    else:
        rate, source = DEFAULT_BACK_FLIP_RATE, "assumed, none observed"
    mde = paired_mde(n_cases, rate, power, alpha)
    if mde is None:
        return (
            f"at {n_cases} paired cases no drop is caught with {power:.0%} power "
            f"(back-flip rate {rate:.0%}, {source}) — 'no regression' here "
            f"means 'cannot tell'"
        )
    return (
        f"minimum detectable drop at {power:.0%} power: {100 * mde:.0f}pt "
        f"(~{mde * n_cases:.0f} of {n_cases} paired cases; back-flip rate "
        f"{rate:.0%}, {source}) — a smaller real drop is missed more than "
        f"{1 - power:.0%} of the time"
    )


def clustered_note(cases):
    """' clustered [66-88%, deff 2.6]' to print beside format_score when some
    case's repeats disagree; '' when every case agrees with itself or was
    drawn once, where format_score's interval already fits.
    """
    if not any(m > 1 and 0 < p < m for p, m in map(_counts, cases.values())):
        return ""
    c = clustered_rate(cases)
    deff = "" if c["design_effect"] is None else f", deff {c['design_effect']:.1f}"
    return f" clustered [{100 * c['low']:.0f}-{100 * c['high']:.0f}%{deff}]"


def paired_diff_note(a_cases, b_cases):
    """'paired diff -22pt [-38, -6]' over the shared cases (paired_difference),
    or None when the two sides share no measured case.
    """
    paired = paired_difference(a_cases, b_cases)
    if paired is None:
        return None
    mean, low, high, _ = paired
    return f"paired diff {100 * mean:+.0f}pt [{100 * low:+.0f}, {100 * high:+.0f}]"


def pass_k_note(a_cases, b_cases, k):
    """'pass^3 73% -> 73% (...)'. "Passes 79 % of draws" and "passes every one
    of 3 draws" are different promises, and an agent that retries nothing
    needs the second.
    """
    rates = (pass_hat_k(cases, k) for cases in (a_cases, b_cases))
    shown = " -> ".join("n/a" if v is None else f"{v:.0%}" for v in rates)
    return f"pass^{k} {shown} (a case counts only when all {k} of its draws pass)"


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
