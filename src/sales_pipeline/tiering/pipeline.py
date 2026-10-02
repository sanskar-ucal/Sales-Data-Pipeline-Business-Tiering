"""End-to-end tiering run: features -> score -> tiers/segments -> backtest -> publish."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import sqlalchemy as sa

from sales_pipeline import warehouse
from sales_pipeline.config import get_settings, pipeline_config, tiering_config
from sales_pipeline.tiering.baselines import run_baselines
from sales_pipeline.tiering.features import MODEL_FEATURES, build_features, forward_labels
from sales_pipeline.tiering.scoring import ScoreResult, composite_score
from sales_pipeline.tiering.segmentation import (
    SegmentationResult,
    assign_tiers,
    behavioral_segments,
    tier_rank,
)
from sales_pipeline.tiering.validation import (
    bootstrap_spearman,
    lift_table,
    tier_separation_tests,
    tier_stability,
    weight_sensitivity,
)

log = logging.getLogger(__name__)


@dataclass
class TieringOutput:
    tiers: pd.DataFrame
    score: ScoreResult
    segmentation: SegmentationResult
    as_of: pd.Timestamp


def load_inputs(engine) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    orders = warehouse.read_sql(engine, """
        SELECT order_id, customer_id, order_date, status, net_revenue, gross_margin,
               discount_pct, payment_terms_days, paid_date
        FROM marts.fct_orders""")
    lines = warehouse.read_sql(engine, """
        SELECT order_id, customer_id, order_date, status, category, net_revenue
        FROM marts.fct_order_lines""")
    customers = warehouse.read_sql(engine, """
        SELECT customer_id, business_name, industry, region, employee_count, signup_date
        FROM marts.dim_customers""")
    return orders, lines, customers


def score_customers(orders, lines, customers, as_of, cfg: dict | None = None,
                    segment: bool = True) -> TieringOutput:
    cfg = cfg or tiering_config()
    feats = build_features(orders, lines, customers, as_of, cfg["lookback_days"], cfg["min_orders"])
    if feats.empty:
        raise ValueError(f"no customers with >= {cfg['min_orders']} orders in the lookback window at {as_of}")
    score = composite_score(feats, cfg)
    tiers = feats.merge(score.scores, on="customer_id")
    tiers["tier"] = assign_tiers(tiers["composite_score"], cfg["tiers"])
    tiers["tier_rank"] = tier_rank(tiers["tier"], cfg["tiers"])
    tiers["score_percentile"] = tiers["composite_score"].rank(pct=True).round(4)

    seg = None
    if segment:
        seg_cfg = cfg["segmentation"]
        seg = behavioral_segments(score.normalized, tuple(seg_cfg["k_range"]), seg_cfg["random_state"])
        tiers["segment"] = seg.labels.to_numpy()
    return TieringOutput(tiers, score, seg, pd.Timestamp(as_of).normalize())


def backtest(orders, lines, customers, as_of, cfg: dict | None = None) -> dict[str, Any]:
    """Score at ``as_of - horizon`` and evaluate against what happened in the horizon."""
    cfg = cfg or tiering_config()
    vcfg = cfg["validation"]
    as_of = pd.Timestamp(as_of).normalize()
    bt_date = as_of - pd.Timedelta(days=cfg["horizon_days"])

    past = score_customers(orders, lines, customers, bt_date, cfg, segment=False)
    labels = forward_labels(orders, past.tiers["customer_id"], bt_date, cfg["horizon_days"],
                            vcfg["high_value_quantile"])
    df = past.tiers.merge(labels, on="customer_id")

    results: dict[str, Any] = {
        "backtest_as_of": bt_date.date().isoformat(),
        "outcome_window_end": (bt_date + pd.Timedelta(days=cfg["horizon_days"])).date().isoformat(),
        "customers": int(len(df)),
        "lift_table": lift_table(df, cfg["tiers"]).round(4).to_dict(orient="records"),
        "hypothesis_tests": tier_separation_tests(df, cfg["tiers"], vcfg["alpha"]),
        "score_vs_future_revenue": bootstrap_spearman(
            df["composite_score"], df["future_revenue"], vcfg["bootstrap_iterations"], vcfg["random_state"]),
        "weight_sensitivity": weight_sensitivity(
            past.tiers, cfg, past.score.weights, vcfg["sensitivity_iterations"],
            vcfg["weight_perturbation"], vcfg["random_state"]),
        "baselines": run_baselines(
            df[MODEL_FEATURES], df["high_value"], df["composite_score"], past.score.expert_weights,
            vcfg["cv_folds"], vcfg["bootstrap_iterations"], vcfg["random_state"]),
    }
    current = score_customers(orders, lines, customers, as_of, cfg, segment=False)
    results["tier_stability"] = tier_stability(
        past.tiers[["customer_id", "tier", "composite_score"]],
        current.tiers[["customer_id", "tier", "composite_score"]], cfg["tiers"])
    return results


def _write_tables(engine, out: TieringOutput, validation: dict[str, Any], run_id: str) -> None:
    schema = pipeline_config()["tiering_schema"]
    run_ts = pd.Timestamp(datetime.now(timezone.utc))
    tiers = out.tiers.assign(run_id=run_id, scored_at=run_ts)

    warehouse.write_dataframe(engine, tiers, "customer_tiers", schema, mode="replace",
                              dtype={"scored_at": sa.DateTime(timezone=True), "as_of_date": sa.Date})

    history = tiers[["customer_id", "as_of_date", "composite_score", "tier", "tier_rank", "segment",
                     "run_id", "scored_at"]]
    if warehouse.table_exists(engine, schema, "customer_tier_history"):
        warehouse.execute(engine, f"DELETE FROM {schema}.customer_tier_history WHERE as_of_date = :d",
                          {"d": out.as_of.date()})
    warehouse.write_dataframe(engine, history, "customer_tier_history", schema, mode="append",
                              dtype={"scored_at": sa.DateTime(timezone=True), "as_of_date": sa.Date})

    weights = pd.DataFrame({
        "feature": list(out.score.weights),
        "applied_weight": list(out.score.weights.values()),
        "expert_weight": [out.score.expert_weights[f] for f in out.score.weights],
        "entropy_weight": [out.score.entropy_weights[f] for f in out.score.weights],
    }).assign(run_id=run_id, as_of_date=out.as_of.date())
    warehouse.write_dataframe(engine, weights, "tier_weights", schema, mode="replace")

    profiles = out.segmentation.profiles.assign(run_id=run_id, as_of_date=out.as_of.date())
    warehouse.write_dataframe(engine, profiles, "segment_profiles", schema, mode="replace")

    lift = pd.DataFrame(validation["lift_table"]).assign(run_id=run_id, backtest_as_of=validation["backtest_as_of"])
    warehouse.write_dataframe(engine, lift, "backtest_lift", schema, mode="replace")

    metrics = pd.DataFrame(_flatten_metrics(validation)).assign(run_id=run_id, as_of_date=out.as_of.date(),
                                                                recorded_at=run_ts)
    warehouse.write_dataframe(engine, metrics, "validation_metrics", schema, mode="append",
                              dtype={"recorded_at": sa.DateTime(timezone=True)})


def _flatten_metrics(v: dict[str, Any]) -> list[dict[str, Any]]:
    ht = v["hypothesis_tests"]
    b = v["baselines"]
    rows = [
        ("kruskal_wallis_h", ht["kruskal_wallis"]["h_statistic"]),
        ("kruskal_wallis_p", ht["kruskal_wallis"]["p_value"]),
        ("kruskal_wallis_epsilon_sq", ht["kruskal_wallis"]["epsilon_squared"]),
        ("anova_log_revenue_p", ht["anova_log_revenue"]["p_value"]),
        ("churn_chi2_p", ht["churn_chi_square"]["p_value"]),
        ("churn_cramers_v", ht["churn_chi_square"]["cramers_v"]),
        ("monotonic_mean_revenue", float(ht["monotonic_mean_revenue"])),
        ("spearman_score_future_revenue", v["score_vs_future_revenue"]["spearman_rho"]),
        ("spearman_ci_low", v["score_vs_future_revenue"]["ci_low"]),
        ("spearman_ci_high", v["score_vs_future_revenue"]["ci_high"]),
        ("tier_same_rate", v["tier_stability"].get("same_tier_rate")),
        ("tier_weighted_kappa", v["tier_stability"].get("weighted_kappa")),
        ("score_psi", v["tier_stability"].get("score_psi")),
        ("sensitivity_tier_agreement_mean", v["weight_sensitivity"]["tier_agreement_mean"]),
        ("sensitivity_kendall_tau_mean", v["weight_sensitivity"]["kendall_tau_mean"]),
        ("best_minus_composite_auc", b["best_vs_composite"]["auc_difference"]),
        ("best_minus_composite_auc_p", b["best_vs_composite"]["p_value"]),
    ]
    rows += [(f"{model}_roc_auc", m["roc_auc"]) for model, m in b["metrics"].items()]
    rows += [(f"{model}_pr_auc", m["pr_auc"]) for model, m in b["metrics"].items()]
    return [{"metric": k, "value": None if val is None else float(val)} for k, val in rows]


def render_report(out: TieringOutput, v: dict[str, Any]) -> str:
    ht = v["hypothesis_tests"]
    b = v["baselines"]
    tier_counts = out.tiers["tier"].value_counts().reindex([t["name"] for t in tiering_config()["tiers"]])
    lines = [
        "# Business Tiering - Validation Report",
        "",
        f"- Scoring as-of date: **{out.as_of.date()}** ({len(out.tiers)} active customers)",
        f"- Backtest: scored at **{v['backtest_as_of']}**, outcomes through **{v['outcome_window_end']}** "
        f"({v['customers']} customers)",
        f"- Behavioural segments: k={out.segmentation.k} (silhouette {out.segmentation.silhouette:.3f})",
        "",
        "## Current tier distribution",
        "",
        "| Tier | Customers |",
        "|---|---|",
        *[f"| {t} | {int(c)} |" for t, c in tier_counts.items()],
        "",
        "## Applied weights",
        "",
        "| Feature | Applied | Expert | Entropy |",
        "|---|---|---|---|",
        *[f"| {f} | {w:.3f} | {out.score.expert_weights[f]:.3f} | {out.score.entropy_weights[f]:.3f} |"
          for f, w in out.score.weights.items()],
        "",
        "## Backtest lift by tier",
        "",
        "| Tier | Customers | Mean future revenue | Revenue share | Churn rate | High-value rate | Lift |",
        "|---|---|---|---|---|---|---|",
        *[f"| {r['tier']} | {int(r['customers'])} | {r['mean_future_revenue']:,.0f} | {r['revenue_share']:.1%} | "
          f"{r['churn_rate']:.1%} | {r['high_value_rate']:.1%} | {r['high_value_lift']:.2f}x |"
          for r in v["lift_table"]],
        "",
        "## Hypothesis tests",
        "",
        f"- Kruskal-Wallis (future revenue across tiers): H={ht['kruskal_wallis']['h_statistic']:.1f}, "
        f"p={ht['kruskal_wallis']['p_value']:.2e}, epsilon^2={ht['kruskal_wallis']['epsilon_squared']:.3f}",
        f"- One-way ANOVA (log revenue): F={ht['anova_log_revenue']['f_statistic']:.1f}, "
        f"p={ht['anova_log_revenue']['p_value']:.2e}",
        f"- Chi-square (tier x churn): chi2={ht['churn_chi_square']['chi2']:.1f}, "
        f"p={ht['churn_chi_square']['p_value']:.2e}, Cramer's V={ht['churn_chi_square']['cramers_v']:.3f}",
        f"- Monotonic mean revenue by tier: {ht['monotonic_mean_revenue']}",
        "",
        "Adjacent tiers (one-sided Mann-Whitney U, Holm-adjusted):",
        "",
        "| Higher | Lower | P(superiority) | p (Holm) | Significant |",
        "|---|---|---|---|---|",
        *[f"| {p['higher']} | {p['lower']} | {p['prob_superiority']:.3f} | {p['p_holm']:.2e} | {p['significant']} |"
          for p in ht["adjacent_tier_mann_whitney"]],
        "",
        f"Spearman(score, future revenue) = {v['score_vs_future_revenue']['spearman_rho']:.3f} "
        f"(95% bootstrap CI {v['score_vs_future_revenue']['ci_low']:.3f} to "
        f"{v['score_vs_future_revenue']['ci_high']:.3f})",
        "",
        "## Stability and sensitivity",
        "",
        f"- Same tier after {tiering_config()['horizon_days']} days: {v['tier_stability']['same_tier_rate']:.1%}; "
        f"within one tier: {v['tier_stability']['within_one_tier_rate']:.1%}; "
        f"quadratic-weighted kappa {v['tier_stability']['weighted_kappa']:.3f}; "
        f"score PSI {v['tier_stability']['score_psi']:.3f}",
        f"- Weight perturbation ±{v['weight_sensitivity']['perturbation']:.0%} "
        f"({v['weight_sensitivity']['iterations']} draws): mean tier agreement "
        f"{v['weight_sensitivity']['tier_agreement_mean']:.1%}, Kendall tau "
        f"{v['weight_sensitivity']['kendall_tau_mean']:.3f}",
        "",
        "## ML baselines (target: top-20% forward revenue, out-of-fold)",
        "",
        "| Model | ROC AUC | PR AUC |",
        "|---|---|---|",
        *[f"| {m} | {r['roc_auc']:.3f} | {r['pr_auc']:.3f} |" for m, r in b["metrics"].items()],
        "",
        f"Best model ({b['best_model']}) minus composite score AUC: "
        f"{b['best_vs_composite']['auc_difference']:+.3f} (95% CI {b['best_vs_composite']['ci_low']:+.3f} to "
        f"{b['best_vs_composite']['ci_high']:+.3f}, p={b['best_vs_composite']['p_value']:.3f}). "
        f"Spearman(expert weights, {b['best_model']} importances) = {b['weights_vs_importance_spearman']:.2f}.",
        "",
    ]
    return "\n".join(lines)


def run(engine=None, as_of=None, write: bool = True, run_id: str | None = None,
        report_dir: Path | None = None) -> dict[str, Any]:
    engine = engine or warehouse.get_engine()
    cfg = tiering_config()
    orders, lines, customers = load_inputs(engine)
    as_of = pd.Timestamp(as_of) if as_of else pd.to_datetime(orders["order_date"]).max()
    run_id = run_id or datetime.now(timezone.utc).strftime("tier_%Y%m%dT%H%M%S")

    out = score_customers(orders, lines, customers, as_of, cfg)
    validation = backtest(orders, lines, customers, as_of, cfg)

    if write:
        _write_tables(engine, out, validation, run_id)

    report_dir = report_dir or (get_settings().output_dir / "reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "tiering_validation.json").write_text(json.dumps(validation, indent=2, default=str))
    (report_dir / "tiering_validation_report.md").write_text(render_report(out, validation))
    log.info("Tiering complete: %d customers scored as of %s", len(out.tiers), out.as_of.date())
    return {
        "run_id": run_id,
        "as_of": out.as_of.date().isoformat(),
        "customers": len(out.tiers),
        "tier_counts": out.tiers["tier"].value_counts().to_dict(),
        "spearman": validation["score_vs_future_revenue"]["spearman_rho"],
        "kruskal_p": validation["hypothesis_tests"]["kruskal_wallis"]["p_value"],
    }
