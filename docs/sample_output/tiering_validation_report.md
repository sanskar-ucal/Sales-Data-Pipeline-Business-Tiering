# Business Tiering - Validation Report

- Scoring as-of date: **2026-10-02** (813 active customers)
- Backtest: scored at **2026-04-05**, outcomes through **2026-10-02** (793 customers)
- Behavioural segments: k=3 (silhouette 0.252)

## Current tier distribution

| Tier | Customers |
|---|---|
| Platinum | 81 |
| Gold | 162 |
| Silver | 244 |
| Bronze | 326 |

## Applied weights

| Feature | Applied | Expert | Entropy |
|---|---|---|---|
| monetary_12m | 0.250 | 0.250 | 0.086 |
| frequency_12m | 0.150 | 0.150 | 0.156 |
| recency_days | 0.120 | 0.120 | 0.052 |
| gross_margin_pct | 0.120 | 0.120 | 0.163 |
| revenue_growth | 0.120 | 0.120 | 0.166 |
| category_breadth | 0.080 | 0.080 | 0.109 |
| active_month_ratio | 0.080 | 0.080 | 0.196 |
| late_payment_rate | 0.050 | 0.050 | 0.042 |
| return_rate | 0.030 | 0.030 | 0.030 |

## Backtest lift by tier

| Tier | Customers | Mean future revenue | Revenue share | Churn rate | High-value rate | Lift |
|---|---|---|---|---|---|---|
| Platinum | 79 | 128,449 | 55.6% | 0.0% | 75.9% | 3.79x |
| Gold | 158 | 32,942 | 28.5% | 0.6% | 43.0% | 2.15x |
| Silver | 238 | 10,434 | 13.6% | 4.2% | 12.2% | 0.61x |
| Bronze | 318 | 1,312 | 2.3% | 35.9% | 0.6% | 0.03x |

## Hypothesis tests

- Kruskal-Wallis (future revenue across tiers): H=464.1, p=2.86e-100, epsilon^2=0.584
- One-way ANOVA (log revenue): F=264.1, p=1.21e-118
- Chi-square (tier x churn): chi2=162.6, p=5.03e-35, Cramer's V=0.453
- Monotonic mean revenue by tier: True

Adjacent tiers (one-sided Mann-Whitney U, Holm-adjusted):

| Higher | Lower | P(superiority) | p (Holm) | Significant |
|---|---|---|---|---|
| Platinum | Gold | 0.791 | 1.43e-13 | True |
| Gold | Silver | 0.766 | 2.74e-19 | True |
| Silver | Bronze | 0.824 | 2.42e-39 | True |

Spearman(score, future revenue) = 0.790 (95% bootstrap CI 0.761 to 0.817)

## Stability and sensitivity

- Same tier after 180 days: 66.3%; within one tier: 98.4%; quadratic-weighted kappa 0.807; score PSI 0.019
- Weight perturbation ±20% (200 draws): mean tier agreement 96.7%, Kendall tau 0.971

## ML baselines (target: top-20% forward revenue, out-of-fold)

| Model | ROC AUC | PR AUC |
|---|---|---|
| composite_score | 0.901 | 0.686 |
| logistic_regression | 0.958 | 0.876 |
| random_forest | 0.956 | 0.842 |
| xgboost | 0.954 | 0.823 |

Best model (logistic_regression) minus composite score AUC: +0.056 (95% CI +0.038 to +0.076, p=0.000). Spearman(expert weights, logistic_regression importances) = 0.93.
