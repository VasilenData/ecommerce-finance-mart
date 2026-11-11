"""Point-in-time repeat-purchase and 90-day gross-spend research on retail invoices.

This module deliberately does not use the all-history RFM/customer dimensions.
All windows are half-open and timestamps are the source's naive local wall time.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, mean_absolute_error, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

FEATURES = [
    "recency_days",
    "observed_age_days",
    "orders_180d",
    "active_days_180d",
    "products_180d",
    "gross_30d",
    "gross_90d",
    "gross_180d",
    "returns_180d",
]
TRAIN = ["2010-07-01", "2010-10-01"]
VALIDATION = "2011-01-01"
TEST = ["2011-04-01", "2011-07-01"]
COVERAGE_END = "2011-12-09"  # Dec 9 itself is partial and excluded.
COVERAGE_START = "2009-12-01"


def timestamp(value: str) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if pd.isna(result) or result.tz is not None or result != result.normalize():
        raise ValueError("Cutoffs must be valid timezone-naive midnight dates")
    return result


def validate_splits(train: list[str], validation: str, test: list[str], coverage_end: str, horizon: int = 90) -> None:
    if not train or not test or horizon != 90:
        raise ValueError("Nonempty train/test cutoffs and the fixed 90-day horizon are required")
    dates = [timestamp(x) for x in [*train, validation, *test]]
    for left, right in zip(dates, dates[1:], strict=False):
        if left + pd.Timedelta(days=horizon) > right:
            raise ValueError("Label windows must be ordered, disjoint and mature before the next phase")
    if dates[-1] + pd.Timedelta(days=horizon) > timestamp(coverage_end):
        raise ValueError("Final outcomes are not fully observed")


def snapshot(
    con, cutoff: str, coverage_end: str, *, labelled: bool = True, horizon: int = 90, coverage_start: str = COVERAGE_START
) -> pd.DataFrame:
    """One row per registered customer with a positive purchase in the past 180 days.

    Labels cover [cutoff, cutoff + horizon); features stop strictly before cutoff.
    Gross spend excludes returns, postage and fees. Returns are a separate historical
    feature. This is repeat purchase and future gross spend, not contractual churn/LTV.
    """
    point, end = timestamp(cutoff), timestamp(coverage_end)
    start = timestamp(coverage_start)
    if horizon != 90 or point > end:
        raise ValueError("The target requires a 90-day horizon and a cutoff within source coverage")
    if point - pd.Timedelta(days=180) < start:
        raise ValueError("The full 180-day observation window is not covered by the declared source history")
    label_end = point + pd.Timedelta(days=horizon)
    if labelled and label_end > end:
        raise ValueError("Unmatured outcome window; use labelled=False for shadow scoring")
    frame = con.execute(
        """
        with past as (
            select * from fact_lines_base
            where customer_id is not null and invoiced_at < ?
        ), sales as (
            select * from past where line_type = 'sale' and invoiced_at >= ?
        ), history as (
            select customer_id, min(invoiced_at) first_observed
            from past where line_type = 'sale' group by customer_id
        ), refunds as (
            select customer_id, -sum(amount) returns_180d from past
            where line_type = 'return' and invoiced_at >= ? group by customer_id
        )
        select cast(s.customer_id as varchar) customer_id,
            date_diff('day', max(s.invoiced_at), ?)::double recency_days,
            date_diff('day', h.first_observed, ?)::double observed_age_days,
            count(distinct s.invoice_no)::double orders_180d,
            count(distinct cast(s.invoiced_at as date))::double active_days_180d,
            count(distinct s.stock_code)::double products_180d,
            coalesce(sum(s.amount) filter (where s.invoiced_at >= ?), 0)::double gross_30d,
            coalesce(sum(s.amount) filter (where s.invoiced_at >= ?), 0)::double gross_90d,
            sum(s.amount)::double gross_180d,
            coalesce(r.returns_180d, 0)::double returns_180d
        from sales s join history h using (customer_id) left join refunds r using (customer_id)
        group by s.customer_id, h.first_observed, r.returns_180d order by customer_id
    """,
        [
            point,
            point - pd.Timedelta(days=180),
            point - pd.Timedelta(days=180),
            point,
            point,
            point - pd.Timedelta(days=30),
            point - pd.Timedelta(days=90),
        ],
    ).df()
    frame.insert(0, "cutoff", point.date().isoformat())
    if frame.empty or frame["customer_id"].duplicated().any():
        raise ValueError("Snapshot must contain unique eligible customers")
    values = frame[FEATURES].to_numpy(float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Features must be finite and nonnegative")
    if labelled:
        outcomes = con.execute(
            """
            select cast(customer_id as varchar) customer_id, sum(amount)::double future_gross_90d
            from fact_lines_base where line_type = 'sale' and customer_id is not null
            and invoiced_at >= ? and invoiced_at < ? group by customer_id
        """,
            [point, label_end],
        ).df()
        frame = frame.merge(outcomes, how="left", on="customer_id", validate="one_to_one")
        frame["future_gross_90d"] = frame["future_gross_90d"].fillna(0)
        frame["repeat_90d"] = (frame["future_gross_90d"] > 0).astype(int)
    return frame


@dataclass
class RecencyBaseline:
    """Smoothed training repeat rate by fixed recency buckets, never fitted on test."""

    rates: np.ndarray | None = None

    def fit(self, x: pd.DataFrame, y: np.ndarray):
        groups = np.digitize(x["recency_days"], [7, 30, 60, 90, 180])
        prior = float(np.mean(y))
        self.rates = np.array([(y[groups == i].sum() + 10 * prior) / ((groups == i).sum() + 10) for i in range(6)])
        return self

    def predict_proba(self, x: pd.DataFrame) -> np.ndarray:
        if self.rates is None:
            raise ValueError("Baseline has not been fitted")
        p = self.rates[np.digitize(x["recency_days"], [7, 30, 60, 90, 180])]
        return np.column_stack([1 - p, p])


def ranked_selection(frame: pd.DataFrame, values: np.ndarray, fraction: float = 0.2) -> np.ndarray:
    """Exactly ceil(n*fraction) rows, score descending then stable customer ID."""
    if not np.isfinite(fraction) or not 0 < fraction <= 1:
        raise ValueError("Capacity fraction must be in (0, 1]")
    if len(values) != len(frame) or not np.isfinite(values).all():
        raise ValueError("Scores must be aligned and finite")
    order = np.lexsort((frame["customer_id"].astype(str).to_numpy(), -np.asarray(values)))
    selected = np.zeros(len(frame), dtype=bool)
    selected[order[: int(np.ceil(len(frame) * fraction))]] = True
    return selected


def classification_metrics(y, p) -> dict:
    y, p = np.asarray(y), np.asarray(p)
    if not len(y) or len(y) != len(p) or not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError("Invalid aligned probabilities")
    distinct = len(np.unique(y)) == 2
    return {
        "brier": float(brier_score_loss(y, p)),
        "roc_auc": float(roc_auc_score(y, p)) if distinct else None,
        "average_precision": float(average_precision_score(y, p)) if distinct else None,
    }


def reliability(y, p) -> list[dict]:
    groups = np.minimum((np.asarray(p) * 10).astype(int), 9)
    return [
        {
            "bin": i,
            "n": int((groups == i).sum()),
            "mean_probability": float(np.mean(np.asarray(p)[groups == i])),
            "observed_repeat_rate": float(np.mean(np.asarray(y)[groups == i])),
        }
        for i in range(10)
        if (groups == i).any()
    ]


def train_select(train: pd.DataFrame, validation: pd.DataFrame) -> tuple[dict, dict]:
    """Fixed candidates; select on one mature validation window, then freeze."""
    x, y = train[FEATURES], train["repeat_90d"].to_numpy()
    if len(np.unique(y)) != 2:
        raise ValueError("Training requires both repeat-purchase outcomes")
    classifiers = {
        "recency_rate": RecencyBaseline(),
        "logistic": make_pipeline(FunctionTransformer(np.log1p), StandardScaler(), LogisticRegression(C=1, max_iter=1000)),
        "hist_gradient_boosting": HistGradientBoostingClassifier(
            max_iter=120, max_leaf_nodes=7, min_samples_leaf=50, l2_regularization=10, early_stopping=False, random_state=17
        ),
    }
    classification = {}
    for name, model in classifiers.items():
        model.fit(x, y)
        classification[name] = classification_metrics(validation["repeat_90d"], model.predict_proba(validation[FEATURES])[:, 1])
    regressor = HistGradientBoostingRegressor(
        loss="poisson", max_iter=120, max_leaf_nodes=7, min_samples_leaf=50, l2_regularization=10, early_stopping=False, random_state=17
    )
    regressor.fit(x, train["future_gross_90d"])
    revenue = {
        "past_90d": float(mean_absolute_error(validation["future_gross_90d"], validation["gross_90d"])),
        "hist_gradient_boosting": float(mean_absolute_error(validation["future_gross_90d"], regressor.predict(validation[FEATURES]))),
    }
    selected_classifier = min(classification, key=lambda name: (classification[name]["brier"], name))
    selected_revenue = min(revenue, key=lambda name: (revenue[name], name))
    models = {
        "classifier": classifiers[selected_classifier],
        "recency_baseline": classifiers["recency_rate"],
        "classifier_name": selected_classifier,
        "revenue": regressor,
        "revenue_name": selected_revenue,
        "features": FEATURES,
    }
    return models, {
        "classification": classification,
        "revenue_mae_gbp": revenue,
        "selected_classifier": selected_classifier,
        "selected_revenue": selected_revenue,
    }


def predict(models: dict, frame: pd.DataFrame) -> pd.DataFrame:
    out = frame[["cutoff", "customer_id"]].copy()
    out["repeat_probability"] = models["classifier"].predict_proba(frame[FEATURES])[:, 1]
    out["recency_baseline_probability"] = models["recency_baseline"].predict_proba(frame[FEATURES])[:, 1]
    out["predicted_gross_90d_gbp"] = (
        frame["gross_90d"].to_numpy() if models["revenue_name"] == "past_90d" else models["revenue"].predict(frame[FEATURES])
    )
    if not np.isfinite(out[["repeat_probability", "predicted_gross_90d_gbp"]]).all().all():
        raise ValueError("Nonfinite predictions")
    out["capacity_top_20_percent"] = ranked_selection(frame, out["predicted_gross_90d_gbp"].to_numpy())
    return out


def paired_interval(differences: np.ndarray, seed: int = 17, iterations: int = 1000) -> list[float]:
    """Exploratory customer bootstrap for one window, candidate minus baseline."""
    values = np.asarray(differences, dtype=float)
    if values.ndim != 1 or len(values) < 2 or not np.isfinite(values).all():
        raise ValueError("At least two finite paired customer differences required")
    rng = np.random.default_rng(seed)
    means = [float(values[rng.integers(0, len(values), size=len(values))].mean()) for _ in range(iterations)]
    return [float(v) for v in np.quantile(means, [0.025, 0.975])]


def assess(frame: pd.DataFrame, scores: pd.DataFrame) -> dict:
    if not frame[["cutoff", "customer_id"]].equals(scores[["cutoff", "customer_id"]]):
        raise ValueError("Prediction identities/order do not match outcomes")
    y, p = frame["repeat_90d"].to_numpy(), scores["repeat_probability"].to_numpy()
    value, predicted = frame["future_gross_90d"].to_numpy(), scores["predicted_gross_90d_gbp"].to_numpy()
    top = scores["capacity_top_20_percent"].to_numpy()
    total = float(value.sum())
    baseline_p = scores["recency_baseline_probability"].to_numpy()
    return {
        "cutoff": frame["cutoff"].iloc[0],
        "customers": len(frame),
        "repeat_rate": float(y.mean()),
        **classification_metrics(y, p),
        "recency_baseline": classification_metrics(y, baseline_p),
        "paired_brier_difference_95_interval": paired_interval((y - p) ** 2 - (y - baseline_p) ** 2),
        "paired_mae_difference_95_interval_gbp": paired_interval(np.abs(value - predicted) - np.abs(value - frame["gross_90d"].to_numpy())),
        "revenue_mae_gbp": float(mean_absolute_error(value, predicted)),
        "past_90d_baseline_mae_gbp": float(mean_absolute_error(value, frame["gross_90d"])),
        "actual_gross_gbp": total,
        "predicted_gross_gbp": float(predicted.sum()),
        "top_20_percent_customers": int(top.sum()),
        "top_20_percent_future_revenue_share": float(value[top].sum() / total) if total else None,
        "calibration": reliability(y, p),
    }
