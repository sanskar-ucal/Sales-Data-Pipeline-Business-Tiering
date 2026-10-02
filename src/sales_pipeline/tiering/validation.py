"""Statistical validation of tier outputs against forward-looking outcomes."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.metrics import cohen_kappa_score

from sales_pipeline.tiering.scoring import composite_score
from sales_pipeline.tiering.segmentation import assign_tiers, tier_names


def holm_adjust(pvalues: list[float]) -> list[float]:
    """Holm-Bonferroni step-down adjusted p-values."""
    p = np.asarray(pvalues, dtype=float)
    m = len(p)
    order = np.argsort(p)
    adjusted = np.empty(m)
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p[idx]))
        adjusted[idx] = running
    return adjusted.tolist()


def lift_table(df: pd.DataFrame, tiers_cfg: list[dict]) -> pd.DataFrame:
    """Per-tier outcome summary; df needs tier, composite_score, future_revenue, churned, high_value."""
    total = df["future_revenue"].sum() or 1.0
    base_hv = df["high_value"].mean() or 1.0
    g = df.groupby("tier")
    out = pd.DataFrame({
        "customers": g.size(),
        "avg_score": g["composite_score"].mean(),
        "mean_future_revenue": g["future_revenue"].mean(),
        "median_future_revenue": g["future_revenue"].median(),
        "revenue_share": g["future_revenue"].sum() / total,
        "churn_rate": g["churned"].mean(),
        "high_value_rate": g["high_value"].mean(),
    })
    out["high_value_lift"] = out["high_value_rate"] / base_hv
    return out.reindex(tier_names(tiers_cfg)).dropna(how="all").reset_index(names="tier")


def tier_separation_tests(df: pd.DataFrame, tiers_cfg: list[dict], alpha: float = 0.05) -> dict[str, Any]:
    """Do tiers separate customers by future outcomes, and in the right order?"""
    names = [n for n in tier_names(tiers_cfg) if n in set(df["tier"])]
    groups = [df.loc[df["tier"] == n, "future_revenue"].to_numpy() for n in names]
    n = len(df)
    k = len(groups)

    kw = stats.kruskal(*groups)
    epsilon_sq = (kw.statistic - k + 1) / (n - k) if n > k else float("nan")
    anova = stats.f_oneway(*[np.log1p(g) for g in groups])

    pairs = []
    raw_p = []
    for hi_name, lo_name, hi, lo in zip(names[:-1], names[1:], groups[:-1], groups[1:], strict=True):
        res = stats.mannwhitneyu(hi, lo, alternative="greater")
        auc = res.statistic / (len(hi) * len(lo))  # common-language effect size
        pairs.append({"higher": hi_name, "lower": lo_name, "u_statistic": float(res.statistic),
                      "p_value": float(res.pvalue), "prob_superiority": float(auc)})
        raw_p.append(res.pvalue)
    for pair, adj in zip(pairs, holm_adjust(raw_p), strict=True):
        pair["p_holm"] = adj
        pair["significant"] = adj < alpha

    ct = pd.crosstab(df["tier"], df["churned"]).reindex(names)
    chi2, chi_p, dof, _ = stats.chi2_contingency(ct)
    cramers_v = float(np.sqrt(chi2 / (ct.to_numpy().sum() * (min(ct.shape) - 1)))) if min(ct.shape) > 1 else 0.0

    medians = [float(np.median(g)) for g in groups]
    means = [float(np.mean(g)) for g in groups]
    return {
        "kruskal_wallis": {"h_statistic": float(kw.statistic), "p_value": float(kw.pvalue),
                           "epsilon_squared": float(epsilon_sq), "significant": kw.pvalue < alpha},
        "anova_log_revenue": {"f_statistic": float(anova.statistic), "p_value": float(anova.pvalue),
                              "significant": anova.pvalue < alpha},
        "adjacent_tier_mann_whitney": pairs,
        "churn_chi_square": {"chi2": float(chi2), "p_value": float(chi_p), "dof": int(dof),
                             "cramers_v": cramers_v, "significant": chi_p < alpha},
        "monotonic_mean_revenue": all(a >= b for a, b in zip(means[:-1], means[1:], strict=True)),
        "monotonic_median_revenue": all(a >= b for a, b in zip(medians[:-1], medians[1:], strict=True)),
    }


def bootstrap_spearman(x, y, iterations: int = 1000, seed: int = 42) -> dict[str, float]:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    rho, p = stats.spearmanr(x, y)
    rng = np.random.default_rng(seed)
    n = len(x)
    boots = []
    for _ in range(iterations):
        idx = rng.integers(0, n, n)
        r = stats.spearmanr(x[idx], y[idx])[0]
        if not np.isnan(r):
            boots.append(r)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"spearman_rho": float(rho), "p_value": float(p), "ci_low": float(lo), "ci_high": float(hi)}


def population_stability_index(expected, actual, bins: int = 10) -> float:
    expected = np.asarray(expected, dtype=float)
    actual = np.asarray(actual, dtype=float)
    edges = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(expected, edges)[0] / len(expected)
    a = np.histogram(actual, edges)[0] / len(actual)
    e = np.clip(e, 1e-6, None)
    a = np.clip(a, 1e-6, None)
    return float(np.sum((a - e) * np.log(a / e)))


def tier_stability(prev: pd.DataFrame, curr: pd.DataFrame, tiers_cfg: list[dict]) -> dict[str, Any]:
    """Compare two tier assignments (columns customer_id, tier, composite_score)."""
    order = {name: i for i, name in enumerate(tier_names(tiers_cfg))}
    m = prev.merge(curr, on="customer_id", suffixes=("_prev", "_curr"))
    if m.empty:
        return {"customers_compared": 0}
    a = m["tier_prev"].map(order)
    b = m["tier_curr"].map(order)
    transitions = pd.crosstab(m["tier_prev"], m["tier_curr"]).reindex(
        index=tier_names(tiers_cfg), columns=tier_names(tiers_cfg), fill_value=0)
    return {
        "customers_compared": int(len(m)),
        "same_tier_rate": float((a == b).mean()),
        "within_one_tier_rate": float(((a - b).abs() <= 1).mean()),
        "upgraded_rate": float((b < a).mean()),
        "downgraded_rate": float((b > a).mean()),
        "weighted_kappa": float(cohen_kappa_score(a, b, weights="quadratic")),
        "score_psi": population_stability_index(prev["composite_score"], curr["composite_score"]),
        "transition_matrix": transitions.to_dict(),
    }


def weight_sensitivity(features: pd.DataFrame, cfg: dict, base_weights: dict[str, float],
                       iterations: int = 200, perturbation: float = 0.2, seed: int = 42) -> dict[str, float]:
    """Randomly perturb each weight by up to ±perturbation and measure ranking/tier stability."""
    rng = np.random.default_rng(seed)
    base = composite_score(features, cfg, base_weights).scores
    base_tiers = assign_tiers(base["composite_score"], cfg["tiers"])
    taus, agreements = [], []
    for _ in range(iterations):
        w = {k: v * rng.uniform(1 - perturbation, 1 + perturbation) for k, v in base_weights.items()}
        s = composite_score(features, cfg, w).scores["composite_score"]
        taus.append(stats.kendalltau(base["composite_score"], s)[0])
        agreements.append(float((assign_tiers(s, cfg["tiers"]) == base_tiers).mean()))
    return {
        "iterations": iterations,
        "perturbation": perturbation,
        "kendall_tau_mean": float(np.mean(taus)),
        "kendall_tau_p05": float(np.percentile(taus, 5)),
        "tier_agreement_mean": float(np.mean(agreements)),
        "tier_agreement_p05": float(np.percentile(agreements, 5)),
    }
