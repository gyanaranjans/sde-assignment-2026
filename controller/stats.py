"""Small, deterministic statistics. No randomness anywhere in winner selection."""
from __future__ import annotations

import math
from statistics import NormalDist

_N = NormalDist()


def z_two_prop(k1: int, n1: int, k2: int, n2: int) -> float:
    """z for p1 - p2 (pooled SE). 0 when either arm is empty."""
    if n1 == 0 or n2 == 0:
        return 0.0
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(max(p * (1 - p), 1e-12) * (1 / n1 + 1 / n2))
    return (k1 / n1 - k2 / n2) / se


def z_binom_excess(k: int, n: int, p0: float) -> float:
    """One-sided z that k/n exceeds a reference rate p0."""
    if n == 0:
        return 0.0
    p0 = min(max(p0, 1e-4), 1 - 1e-4)
    return (k - n * p0) / math.sqrt(n * p0 * (1 - p0))


def z_crit(alpha: float) -> float:
    return _N.inv_cdf(1 - alpha)


def rate(nk) -> float:
    return nk[1] / nk[0] if nk and nk[0] else 0.0


def bh_threshold(zs: list[float], q: float) -> float:
    """Benjamini–Hochberg on one-sided p-values; returns the z a test must exceed (inf if none pass)."""
    m = len(zs)
    if not m:
        return math.inf
    ps = sorted((1 - _N.cdf(z), z) for z in zs)
    thr = math.inf
    for i, (p, z) in enumerate(ps, 1):
        if p <= i / m * q:
            thr = z - 1e-9
    return thr
