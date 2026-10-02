"""Composite scoring: transform -> winsorize -> orient -> normalize -> weight."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats


@dataclass
class ScoreResult:
    scores: pd.DataFrame          # customer_id, <feature>_score columns, composite_score
    normalized: pd.DataFrame      # direction-adjusted normalized features in [0, 1]
    weights: dict[str, float]
    expert_weights: dict[str, float]
    entropy_weights: dict[str, float]


def _normalize_weights(w: dict[str, float]) -> dict[str, float]:
    total = sum(w.values())
    if total <= 0:
        raise ValueError("feature weights must sum to a positive number")
    return {k: v / total for k, v in w.items()}


def prepare(features: pd.DataFrame, spec: dict[str, dict], winsor: tuple[float, float] | None) -> pd.DataFrame:
    """Impute, transform, winsorize and orient features so that higher is always better."""
    out = pd.DataFrame(index=features.index)
    for col, cfg in spec.items():
        s = pd.to_numeric(features[col], errors="coerce").astype(float)
        s = s.fillna(s.median() if s.notna().any() else 0.0)
        if cfg.get("transform") == "log1p":
            s = np.log1p(s.clip(lower=0))
        if winsor:
            lo, hi = s.quantile(winsor[0]), s.quantile(winsor[1])
            s = s.clip(lo, hi)
        out[col] = s * cfg.get("direction", 1)
    return out


def normalize(df: pd.DataFrame, method: str = "percentile") -> pd.DataFrame:
    if method == "percentile":
        return df.rank(pct=True, method="average")
    if method == "minmax":
        rng = (df.max() - df.min()).replace(0, 1)
        return (df - df.min()) / rng
    if method == "zscore":
        std = df.std(ddof=0).replace(0, 1)
        return pd.DataFrame(stats.norm.cdf((df - df.mean()) / std), index=df.index, columns=df.columns)
    raise ValueError(f"unknown normalization {method!r}")


def entropy_weights(oriented: pd.DataFrame) -> dict[str, float]:
    """Entropy weight method: features with more dispersion carry more information.

    Uses min-max scaling (not ranks) so the weights reflect actual value dispersion.
    """
    x = normalize(oriented, "minmax") + 1e-9
    p = x / x.sum()
    n = len(x)
    if n <= 1:
        return _normalize_weights({c: 1.0 for c in oriented.columns})
    e = -(p * np.log(p)).sum() / np.log(n)
    d = (1 - e).clip(lower=0)
    if d.sum() == 0:
        return _normalize_weights({c: 1.0 for c in oriented.columns})
    return _normalize_weights(d.to_dict())


def resolve_weights(method: str, expert: dict[str, float], entropy: dict[str, float]) -> dict[str, float]:
    if method == "config":
        return expert
    if method == "entropy":
        return entropy
    if method == "blend":
        return _normalize_weights({k: 0.5 * expert[k] + 0.5 * entropy[k] for k in expert})
    raise ValueError(f"unknown weighting_method {method!r}")


def composite_score(features: pd.DataFrame, cfg: dict, weights: dict[str, float] | None = None) -> ScoreResult:
    spec = cfg["features"]
    winsor = tuple(cfg["winsorize_quantiles"]) if cfg.get("winsorize_quantiles") else None
    oriented = prepare(features, spec, winsor)
    normalized = normalize(oriented, cfg.get("normalization", "percentile"))

    expert = _normalize_weights({k: float(v["weight"]) for k, v in spec.items()})
    entropy = entropy_weights(oriented)
    w = _normalize_weights(weights) if weights else resolve_weights(cfg.get("weighting_method", "config"),
                                                                    expert, entropy)

    w_series = pd.Series(w)[normalized.columns]
    composite = (normalized * w_series).sum(axis=1) * 100
    scores = normalized.add_suffix("_score")
    scores.insert(0, "customer_id", features["customer_id"].to_numpy())
    scores["composite_score"] = composite.clip(0, 100).round(4)
    return ScoreResult(scores, normalized, w, expert, entropy)
