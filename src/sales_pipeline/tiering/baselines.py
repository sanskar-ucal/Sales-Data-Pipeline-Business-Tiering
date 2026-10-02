"""ML baselines (scikit-learn / XGBoost) benchmarked against the composite score.

The composite score is an unsupervised, explainable ranking. The baselines show
how much predictive signal a supervised model could extract from the same
features, and a paired bootstrap tests whether the gap is significant.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

log = logging.getLogger(__name__)


def _models(random_state: int) -> dict[str, Any]:
    models: dict[str, Any] = {
        "logistic_regression": make_pipeline(
            SimpleImputer(strategy="median"), StandardScaler(),
            LogisticRegression(max_iter=2000, class_weight="balanced")),
        "random_forest": make_pipeline(
            SimpleImputer(strategy="median"),
            RandomForestClassifier(n_estimators=300, min_samples_leaf=5, class_weight="balanced",
                                   random_state=random_state, n_jobs=-1)),
    }
    try:
        from xgboost import XGBClassifier

        models["xgboost"] = XGBClassifier(
            n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
            eval_metric="logloss", random_state=random_state, n_jobs=-1)
    except ImportError:  # xgboost is optional at runtime
        from sklearn.ensemble import HistGradientBoostingClassifier

        log.warning("xgboost not installed; using HistGradientBoostingClassifier")
        models["gradient_boosting"] = HistGradientBoostingClassifier(random_state=random_state)
    return models


def paired_bootstrap_auc(y, p_a, p_b, iterations: int = 1000, seed: int = 42) -> dict[str, float]:
    """Bootstrap AUC(a) - AUC(b) with a two-sided p-value for H0: no difference."""
    y = np.asarray(y)
    p_a = np.asarray(p_a)
    p_b = np.asarray(p_b)
    rng = np.random.default_rng(seed)
    n = len(y)
    diffs = []
    for _ in range(iterations):
        idx = rng.integers(0, n, n)
        if y[idx].min() == y[idx].max():
            continue
        diffs.append(roc_auc_score(y[idx], p_a[idx]) - roc_auc_score(y[idx], p_b[idx]))
    diffs = np.asarray(diffs)
    observed = roc_auc_score(y, p_a) - roc_auc_score(y, p_b)
    p_value = min(1.0, 2 * min((diffs <= 0).mean(), (diffs >= 0).mean()))
    return {"auc_difference": float(observed), "ci_low": float(np.percentile(diffs, 2.5)),
            "ci_high": float(np.percentile(diffs, 97.5)), "p_value": float(p_value)}


def run_baselines(
    X: pd.DataFrame,
    y: pd.Series,
    composite: pd.Series,
    expert_weights: dict[str, float],
    folds: int = 5,
    bootstrap_iterations: int = 1000,
    random_state: int = 42,
) -> dict[str, Any]:
    X = X.astype(float)
    y = y.astype(int).to_numpy()
    cv = StratifiedKFold(n_splits=folds, shuffle=True, random_state=random_state)

    metrics: dict[str, dict[str, float]] = {}
    oof: dict[str, np.ndarray] = {}
    score_prob = (composite.to_numpy() / 100.0)
    metrics["composite_score"] = {
        "roc_auc": float(roc_auc_score(y, score_prob)),
        "pr_auc": float(average_precision_score(y, score_prob)),
    }
    oof["composite_score"] = score_prob

    fitted: dict[str, Any] = {}
    for name, model in _models(random_state).items():
        proba = cross_val_predict(model, X, y, cv=cv, method="predict_proba")[:, 1]
        oof[name] = proba
        metrics[name] = {
            "roc_auc": float(roc_auc_score(y, proba)),
            "pr_auc": float(average_precision_score(y, proba)),
            "brier": float(brier_score_loss(y, proba)),
        }
        fitted[name] = model.fit(X, y)

    best = max((m for m in metrics if m != "composite_score"), key=lambda m: metrics[m]["roc_auc"])
    comparison = paired_bootstrap_auc(y, oof[best], oof["composite_score"], bootstrap_iterations, random_state)
    comparison["model"] = best

    importance = _importances(fitted, X.columns)
    shared = [f for f in expert_weights if f in importance.index]
    rho = stats.spearmanr([expert_weights[f] for f in shared], importance.loc[shared, best])[0] if shared else np.nan

    return {
        "label_positive_rate": float(y.mean()),
        "n": int(len(y)),
        "metrics": metrics,
        "best_model": best,
        "best_vs_composite": comparison,
        "feature_importance": importance.round(4).reset_index(names="feature").to_dict(orient="records"),
        "weights_vs_importance_spearman": float(rho),
    }


def _importances(fitted: dict[str, Any], columns) -> pd.DataFrame:
    out = {}
    for name, model in fitted.items():
        est = model[-1] if hasattr(model, "steps") else model
        if hasattr(est, "feature_importances_"):
            imp = np.asarray(est.feature_importances_, dtype=float)
        elif hasattr(est, "coef_"):
            imp = np.abs(est.coef_.ravel())
        else:
            continue
        out[name] = imp / imp.sum() if imp.sum() else imp
    return pd.DataFrame(out, index=list(columns))
