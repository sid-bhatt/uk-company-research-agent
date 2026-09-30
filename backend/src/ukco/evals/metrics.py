"""Evaluation metrics. Pure functions, no dependencies beyond the stdlib."""

import random
from collections.abc import Sequence


def auc(scores: Sequence[float], positives: Sequence[bool]) -> float | None:
    """ROC AUC = P(score of a random positive > score of a random negative), ties count ½.
    Rank-based, so it's valid on a stratified sample (unlike precision)."""
    pos = [s for s, p in zip(scores, positives, strict=True) if p]
    neg = [s for s, p in zip(scores, positives, strict=True) if not p]
    if not pos or not neg:
        return None
    wins = sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in pos for b in neg)
    return wins / (len(pos) * len(neg))


def bootstrap_auc_ci(
    scores: Sequence[float],
    positives: Sequence[bool],
    *,
    resamples: int = 2000,
    seed: int = 7,
    level: float = 0.95,
) -> tuple[float, float] | None:
    """Percentile bootstrap interval. Small eval sets have wide intervals; show them."""
    rng = random.Random(seed)
    pairs = list(zip(scores, positives, strict=True))
    values = []
    for _ in range(resamples):
        sample = [rng.choice(pairs) for _ in pairs]
        value = auc([s for s, _ in sample], [p for _, p in sample])
        if value is not None:
            values.append(value)
    if not values:
        return None
    values.sort()
    lo = values[int((1 - level) / 2 * len(values))]
    hi = values[min(len(values) - 1, int((1 + level) / 2 * len(values)))]
    return lo, hi


def rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None
