"""Tier assignment (score cut-offs) and behavioural segmentation (k-means)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler


def tier_names(tiers_cfg: list[dict]) -> list[str]:
    return [t["name"] for t in tiers_cfg]


def assign_tiers(scores: pd.Series, tiers_cfg: list[dict]) -> pd.Series:
    """Assign tiers top-down by score rank using each tier's share of customers."""
    shares = np.array([t["share"] for t in tiers_cfg], dtype=float)
    shares = shares / shares.sum()
    bounds = np.cumsum(shares)
    pct = scores.rank(ascending=False, method="first") / len(scores)
    idx = np.searchsorted(bounds, pct.to_numpy() - 1e-12, side="left")
    idx = np.clip(idx, 0, len(tiers_cfg) - 1)
    return pd.Series(np.array(tier_names(tiers_cfg))[idx], index=scores.index, name="tier")


def tier_rank(tiers: pd.Series, tiers_cfg: list[dict]) -> pd.Series:
    """Ordinal code: 1 = best tier."""
    order = {name: i + 1 for i, name in enumerate(tier_names(tiers_cfg))}
    return tiers.map(order).astype(int)


@dataclass
class SegmentationResult:
    labels: pd.Series
    k: int
    silhouette: float
    silhouette_by_k: dict[int, float]
    profiles: pd.DataFrame


def _name_segment(c: pd.Series) -> str:
    value = c.mean()
    if value >= 0.65:
        return "Champions"
    if c.get("revenue_growth", 0) >= 0.6 and value >= 0.45:
        return "Rising Stars"
    if c.get("recency_days", 1) < 0.35 or c.get("active_month_ratio", 1) < 0.3:
        return "At Risk"
    if c.get("frequency_12m", 0) >= 0.55 and c.get("monetary_12m", 1) < 0.5:
        return "Frequent Small Buyers"
    if c.get("gross_margin_pct", 0) >= 0.6:
        return "Profitable Niche"
    if c.get("late_payment_rate", 1) < 0.35 or c.get("return_rate", 1) < 0.35:
        return "Credit / Service Risk"
    return "Steady Core"


def behavioral_segments(normalized: pd.DataFrame, k_range=(3, 7), random_state: int = 42) -> SegmentationResult:
    """K-means on direction-adjusted features; k chosen by silhouette score."""
    x = StandardScaler().fit_transform(normalized.to_numpy())
    lo, hi = k_range
    hi = min(hi, len(normalized) - 1)
    sample = min(len(x), 3000)
    sil: dict[int, float] = {}
    models: dict[int, KMeans] = {}
    for k in range(lo, hi + 1):
        km = KMeans(n_clusters=k, n_init=10, random_state=random_state).fit(x)
        sil[k] = float(silhouette_score(x, km.labels_, sample_size=sample, random_state=random_state))
        models[k] = km
    best_k = max(sil, key=sil.get)
    labels = pd.Series(models[best_k].labels_, index=normalized.index)

    centroids = normalized.groupby(labels.to_numpy()).mean()
    names: dict[int, str] = {}
    used: dict[str, int] = {}
    for cid in centroids.mean(axis=1).sort_values(ascending=False).index:
        name = _name_segment(centroids.loc[cid])
        used[name] = used.get(name, 0) + 1
        names[cid] = name if used[name] == 1 else f"{name} {used[name]}"

    profiles = centroids.copy()
    profiles.insert(0, "segment", [names[c] for c in profiles.index])
    profiles.insert(1, "customers", labels.value_counts().reindex(profiles.index).to_numpy())
    profiles = profiles.reset_index(names="cluster_id")
    return SegmentationResult(labels.map(names).rename("segment"), best_k, sil[best_k], sil, profiles)
