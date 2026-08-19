"""Statistics for the token-savings A/B benchmark — bootstrap CI + Mann-Whitney-U.

No scipy dependency (numpy only). Token distributions are skewed (long tail), so we do NOT
assume normality: savings get a non-parametric BOOTSTRAP confidence interval, and the
"is B really cheaper than A, not just noise?" question is answered by Mann-Whitney-U with a
normal approximation. Every number the report shows comes from here — nothing is invented.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class SavingsStat:
    """The measured savings for one task type (or overall)."""
    task_type: str
    n_pairs: int                 # runs per arm that entered the estimate
    a_median: float              # baseline (CEL off) median total tokens
    b_median: float              # vibe (CEL on) median total tokens
    savings_point: float         # (a - b) / a, from the medians
    ci_low: float                # 2.5th percentile of the bootstrap savings distribution
    ci_high: float               # 97.5th percentile
    p_value: float               # Mann-Whitney-U, one-sided H1: B < A
    significant: bool            # p < 0.05 AND ci_low > 0
    quality_ok_frac: float       # fraction of B runs whose quality >= paired A quality
    dropped_quality: int = 0     # runs excluded because B was worse than A


def _savings_from_medians(a: np.ndarray, b: np.ndarray) -> float:
    am = float(np.median(a))
    return (am - float(np.median(b))) / am if am > 0 else 0.0


def bootstrap_savings_ci(a_tokens, b_tokens, *, iters: int = 10000, seed: int = 12345,
                         alpha: float = 0.05) -> tuple[float, float, float]:
    """Bootstrap CI for savings = (median(A) - median(B)) / median(A).

    Resamples A and B independently `iters` times; returns (point, ci_low, ci_high).
    Deterministic given `seed`, so a re-run reproduces the same CI from the same raw data.
    """
    a = np.asarray(a_tokens, dtype=float)
    b = np.asarray(b_tokens, dtype=float)
    if a.size == 0 or b.size == 0 or np.median(a) <= 0:
        return 0.0, 0.0, 0.0
    rng = np.random.default_rng(seed)
    dist = np.empty(iters, dtype=float)
    for i in range(iters):
        ra = rng.choice(a, size=a.size, replace=True)
        rb = rng.choice(b, size=b.size, replace=True)
        dist[i] = _savings_from_medians(ra, rb)
    lo = float(np.percentile(dist, 100 * (alpha / 2)))
    hi = float(np.percentile(dist, 100 * (1 - alpha / 2)))
    return _savings_from_medians(a, b), lo, hi


def mann_whitney_u(a_tokens, b_tokens) -> tuple[float, float]:
    """Mann-Whitney-U, one-sided H1: B < A (vibe uses fewer tokens than baseline).

    Returns (U_B, p). Normal approximation with tie correction — valid for the sample
    sizes a benchmark realistically produces (n per arm >= ~8). No scipy needed.
    """
    a = np.asarray(a_tokens, dtype=float)
    b = np.asarray(b_tokens, dtype=float)
    n1, n2 = a.size, b.size
    if n1 == 0 or n2 == 0:
        return 0.0, 1.0
    allv = np.concatenate([a, b])
    order = allv.argsort()
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, allv.size + 1)
    # average ranks for ties
    _, inv, counts = np.unique(allv, return_inverse=True, return_counts=True)
    sums = np.zeros(counts.size)
    np.add.at(sums, inv, ranks)
    avg = sums / counts
    ranks = avg[inv]
    r_b = float(ranks[n1:].sum())
    u_b = r_b - n2 * (n2 + 1) / 2.0            # U for group B
    mu = n1 * n2 / 2.0
    # tie-corrected variance
    n = n1 + n2
    tie = float((counts ** 3 - counts).sum())
    sigma2 = (n1 * n2 / 12.0) * ((n + 1) - tie / (n * (n - 1))) if n > 1 else 0.0
    if sigma2 <= 0:
        return u_b, 1.0
    z = (u_b - mu) / np.sqrt(sigma2)           # H1: B<A → small U_B → negative z
    p = _norm_cdf(z)                            # one-sided lower-tail
    return u_b, float(p)


def _norm_cdf(z: float) -> float:
    """Standard-normal CDF via erf (math.erf) — stdlib, no scipy."""
    import math
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def summarise(pairs, task_type: str = "overall", *, seed: int = 12345) -> SavingsStat:
    """Compute a SavingsStat from paired runs. `pairs` is a list of dicts with keys
    a_tokens, b_tokens, a_quality, b_quality. A pair is DROPPED from the savings estimate
    when b_quality < a_quality (a token cut bought by a worse answer is not a saving)."""
    kept_a, kept_b, dropped = [], [], 0
    for p in pairs:
        if p["b_quality"] + 1e-9 >= p["a_quality"]:
            kept_a.append(p["a_tokens"])
            kept_b.append(p["b_tokens"])
        else:
            dropped += 1
    n = len(kept_a)
    if n == 0:
        return SavingsStat(task_type, 0, 0, 0, 0, 0, 0, 1.0, False, 0.0, dropped)
    point, lo, hi = bootstrap_savings_ci(kept_a, kept_b, seed=seed)
    _, p = mann_whitney_u(kept_a, kept_b)
    total = n + dropped
    return SavingsStat(
        task_type=task_type, n_pairs=n,
        a_median=float(np.median(kept_a)), b_median=float(np.median(kept_b)),
        savings_point=point, ci_low=lo, ci_high=hi, p_value=p,
        significant=(p < 0.05 and lo > 0),
        quality_ok_frac=n / total if total else 0.0, dropped_quality=dropped,
    )
