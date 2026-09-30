"""Small statistics helpers, so scores are not published as bare fractions."""

import math
from fractions import Fraction


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


# Floor for the back-flip rate: seeing none in a few dozen cases does not make it zero.
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

    Returns None when nothing was attempted, else a dict: rate, se, design_effect,
    n_eff, low, high, n_cases, passes, attempts.
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
        # CR1 cluster-robust variance of a ratio estimator (Miller 2024, "Adding Error Bars to Evals").
        var = n_cases / (n_cases - 1) * resid / attempts**2
    if var is None or naive == 0:
        deff = None
        n_eff = attempts**2 / sum(m * m for _, m in counts)
    else:
        deff = var / naive
        # Floored at 1: repeats are not anti-correlated, so a lower ratio is noise.
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

    Returns None when no case is shared, else (mean, low, high, n_cases).
    """
    diffs = []
    for key in a_cases:
        if key not in b_cases:
            continue
        (pa, ma), (pb, mb) = _counts(a_cases[key]), _counts(b_cases[key])
        if ma > 0 and mb > 0:
            # Exact: float thirds that cancel printed "paired diff -0pt" for no change.
            diffs.append(Fraction(pb, mb) - Fraction(pa, ma))
    if not diffs:
        return None
    n = len(diffs)
    mean = sum(diffs, Fraction(0)) / n
    if n < 2:
        return (float(mean), -1.0, 1.0, n)
    spread = sum(((d - mean) ** 2 for d in diffs), Fraction(0)) / (n * (n - 1))
    m, se = float(mean), math.sqrt(float(spread))
    return (m, max(-1.0, m - z * se), min(1.0, m + z * se), n)


def pass_hat_k(cases, k):
    """Unbiased chance a case passes k draws out of k (tau-bench's pass^k), or None."""
    if k < 1:
        raise ValueError(f"k must be at least 1, got {k}")
    terms = [
        math.comb(p, k) / math.comb(m, k)
        for p, m in map(_counts, cases.values())
        if m >= k
    ]
    return sum(terms) / len(terms) if terms else None


def _sign_test_rejects(n_cases, alpha):
    """Per discordant count d, the most `better` cases still read as a regression, or -1."""
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
    """P(X = k) for k = 0..n, X ~ Binomial(n, p), in log space against underflow."""
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
    """Exact chance paired_sign_test flags `drop` (cases worsen at back_flip_rate + drop)."""
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
    """Smallest net drop, as a fraction of the cases, caught with `power`, or None."""
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


def back_flip_estimate(n_cases, back_flips=0):
    """(rate, source) for paired_mde: back_flips / n_cases, floored at DEFAULT_BACK_FLIP_RATE."""
    observed = back_flips / n_cases if n_cases > 0 else 0.0
    if observed >= DEFAULT_BACK_FLIP_RATE:
        return observed, "observed"
    return DEFAULT_BACK_FLIP_RATE, f"assumed, {back_flips or 'none'} observed"


def paired_mde_note(n_cases, back_flips=0, power=0.8, alpha=ALPHA):
    """One line: the smallest drop a paired comparison of n_cases would catch."""
    rate, source = back_flip_estimate(n_cases, back_flips)
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
    """' clustered [66-88%, deff 2.6]' when some case's repeats disagree, else ''."""
    if not any(m > 1 and 0 < p < m for p, m in map(_counts, cases.values())):
        return ""
    c = clustered_rate(cases)
    deff = "" if c["design_effect"] is None else f", deff {c['design_effect']:.1f}"
    return f" clustered [{100 * c['low']:.0f}-{100 * c['high']:.0f}%{deff}]"


def paired_diff_note(a_cases, b_cases):
    """'paired diff -22pt [-38, -6]' over the shared cases, or None if none are shared."""
    paired = paired_difference(a_cases, b_cases)
    if paired is None:
        return None
    mean, low, high, _ = paired
    return f"paired diff {100 * mean:+.0f}pt [{100 * low:+.0f}, {100 * high:+.0f}]"


def pass_k_note(a_cases, b_cases, k):
    """'pass^3 73% -> 73% (...)': an agent that retries nothing needs every draw to pass."""
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
    """The lowest rate still distinguishable from `from_rate` here, or None if none is."""
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
    """Newcombe 95 % interval for b_rate - a_rate; (-1.0, 1.0) when a side has no trials."""
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
    """Exact two-sided sign test p-value over the cases only A, or only B, got right."""
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
    """Count shared cases where A did better, B did better, or neither; unmeasured are skipped."""
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
    """How many cases must flip ONE way, with none flipping back, to be seen."""
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
    """Group ranked rows whose neighbours the paired sign test cannot separate.

    `key(row)` returns the row's per-case outcomes; a new tier starts where p < alpha.
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
