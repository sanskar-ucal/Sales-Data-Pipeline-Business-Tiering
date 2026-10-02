import numpy as np
import pandas as pd
import pytest

from sales_pipeline.config import tiering_config
from sales_pipeline.tiering.features import SCORE_FEATURES, build_features, forward_labels
from sales_pipeline.tiering.scoring import composite_score, entropy_weights
from sales_pipeline.tiering.segmentation import assign_tiers
from sales_pipeline.tiering.validation import holm_adjust, population_stability_index


@pytest.fixture(scope="module")
def features(mart_frames):
    orders, lines, customers = mart_frames
    return build_features(orders, lines, customers, orders["order_date"].max(), 365)


def test_features_are_point_in_time(mart_frames):
    orders, lines, customers = mart_frames
    as_of = orders["order_date"].max() - pd.Timedelta(days=200)
    f = build_features(orders, lines, customers, as_of, 365)
    assert set(SCORE_FEATURES) <= set(f.columns)
    assert (f["recency_days"] >= 0).all()
    assert f["revenue_growth"].between(-1, 1).all()
    assert f["late_payment_rate"].dropna().between(0, 1).all()
    future = orders[orders["order_date"] > as_of]
    mutated = pd.concat([orders[orders["order_date"] <= as_of],
                         future.assign(net_revenue=future["net_revenue"] * 100)])
    f2 = build_features(mutated, lines, customers, as_of, 365)
    pd.testing.assert_series_equal(f["monetary_12m"], f2["monetary_12m"])


def test_composite_score_bounds_and_direction(features):
    cfg = tiering_config()
    res = composite_score(features, cfg)
    assert res.scores["composite_score"].between(0, 100).all()
    assert abs(sum(res.weights.values()) - 1) < 1e-9
    assert abs(sum(entropy_weights(res.normalized).values()) - 1) < 1e-9
    corr = np.corrcoef(res.scores["composite_score"], features["recency_days"])[0, 1]
    assert corr < 0  # recency has direction -1


def test_tier_shares():
    tiers_cfg = tiering_config()["tiers"]
    scores = pd.Series(np.linspace(0, 100, 1000))
    counts = assign_tiers(scores, tiers_cfg).value_counts()
    assert counts.to_dict() == {"Platinum": 100, "Gold": 200, "Silver": 300, "Bronze": 400}
    assert assign_tiers(scores, tiers_cfg).iloc[-1] == "Platinum"


def test_forward_labels(mart_frames, features):
    orders, _, _ = mart_frames
    as_of = orders["order_date"].max() - pd.Timedelta(days=180)
    labels = forward_labels(orders, features["customer_id"], as_of, 180)
    assert set(labels["churned"]) <= {0, 1}
    assert labels["high_value"].mean() <= 0.25


def test_stats_helpers():
    assert holm_adjust([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    rng = np.random.default_rng(0)
    a = rng.normal(size=5000)
    assert population_stability_index(a, rng.normal(size=5000)) < 0.02
    assert population_stability_index(a, rng.normal(1, 1, 5000)) > 0.2
