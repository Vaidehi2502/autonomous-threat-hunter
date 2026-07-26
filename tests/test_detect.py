"""
Unit tests for the detection engine (src/detect.py).

These assert the CURRENT behaviour of the scoring logic. They are a regression
lock: outputs/threat_scores.parquet and every figure in the README were produced
by exactly these weights and thresholds, so a failure here means the committed
results no longer match the code.
"""

import numpy as np
import pandas as pd
import pytest

from src.detect import (
    DRIFT_WINDOW,
    DRIFT_Z_THRESHOLD,
    FEATURE_COLS,
    ISOLATION_FOREST_WEIGHT,
    RULE_WEIGHTS,
    Z_THRESHOLD,
    compute_drift_signal,
    compute_final_severity,
    compute_per_user_zscores,
    compute_rule_based_score,
)


def make_feature_frame(rows):
    """Build a feature table, defaulting any unspecified FEATURE_COL to 0."""
    filled = []
    for row in rows:
        base = {col: 0 for col in FEATURE_COLS}
        base.update(row)
        filled.append(base)
    return pd.DataFrame(filled)


# --------------------------------------------------------------------------
# Per-user baselines -- the core design claim of the project
# --------------------------------------------------------------------------


def test_zscore_baseline_is_per_user_not_global():
    """The same raw value is anomalous for a light user, normal for a heavy one.

    This is the project's central argument for per-user baselines over a
    population baseline, so it gets a direct test.
    """
    quiet = [{"user": "quiet", "logon_count": 2} for _ in range(9)]
    quiet.append({"user": "quiet", "logon_count": 20})  # the spike

    # Same value of ~20, but it is this user's everyday normal.
    busy = [{"user": "busy", "logon_count": v} for v in [19, 20, 21, 20, 19, 21, 20, 19, 21, 20]]

    df = compute_per_user_zscores(make_feature_frame(quiet + busy))

    quiet_spike = df[df["user"] == "quiet"]["z_logon_count"].max()
    busy_max = df[df["user"] == "busy"]["z_logon_count"].max()

    assert quiet_spike > Z_THRESHOLD, "20 logons should be anomalous for the quiet user"
    assert busy_max < Z_THRESHOLD, "20 logons should be unremarkable for the busy user"


def test_zscore_handles_zero_variance_without_nan():
    """A user with no variation must score 0, not NaN -- otherwise scoring breaks."""
    rows = [{"user": "flat", "logon_count": 5} for _ in range(10)]
    df = compute_per_user_zscores(make_feature_frame(rows))

    z = df["z_logon_count"]
    assert not z.isna().any()
    assert (z == 0).all()


def test_zscore_column_created_for_every_feature():
    rows = [{"user": "u", "logon_count": i} for i in range(5)]
    df = compute_per_user_zscores(make_feature_frame(rows))

    for col in FEATURE_COLS:
        assert f"z_{col}" in df.columns


def test_zscore_retains_the_baseline_it_was_computed_from():
    """mean_/std_ are kept so downstream reasons can state a counterfactual
    threshold, not just the z-score itself."""
    rows = [{"user": "u", "logon_count": v} for v in [2, 4, 6, 8, 10]]
    df = compute_per_user_zscores(make_feature_frame(rows))

    assert (df["mean_logon_count"] == 6.0).all()
    assert np.isclose(df["std_logon_count"].iloc[0], np.std([2, 4, 6, 8, 10], ddof=1))


def test_single_day_user_does_not_crash():
    """One row per user means std is NaN; must degrade to 0, not explode."""
    df = compute_per_user_zscores(make_feature_frame([{"user": "solo", "logon_count": 99}]))
    assert df["z_logon_count"].iloc[0] == 0


# --------------------------------------------------------------------------
# Rule scoring
# --------------------------------------------------------------------------


def zframe(**z_values):
    """Build a one-row frame with z_ columns set directly."""
    row = {f"z_{col}": 0.0 for col in FEATURE_COLS}
    row.update({f"z_{k}": v for k, v in z_values.items()})
    return pd.DataFrame([row])


def test_rule_score_zero_when_nothing_exceeds_threshold():
    df = compute_rule_based_score(zframe(logon_count=Z_THRESHOLD))
    assert df["rule_score"].iloc[0] == 0
    assert df["reasons"].iloc[0] == []


@pytest.mark.parametrize("feature,weight", sorted(RULE_WEIGHTS.items()))
def test_each_rule_contributes_its_own_weight(feature, weight):
    df = compute_rule_based_score(zframe(**{feature: Z_THRESHOLD + 1}))
    assert df["rule_score"].iloc[0] == weight


def test_rule_scores_accumulate_across_features():
    df = compute_rule_based_score(
        zframe(off_hours_logon_count=5.0, off_hours_usb_count=5.0, logon_count=5.0)
    )
    expected = (
        RULE_WEIGHTS["off_hours_logon_count"]
        + RULE_WEIGHTS["off_hours_usb_count"]
        + RULE_WEIGHTS["logon_count"]
    )
    assert df["rule_score"].iloc[0] == expected
    assert len(df["reasons"].iloc[0]) == 3


def test_triggered_rule_produces_human_readable_reason():
    """Explainability is the product's differentiator -- reasons must be legible."""
    df = compute_rule_based_score(zframe(off_hours_usb_count=4.2))
    reasons = df["reasons"].iloc[0]

    assert len(reasons) == 1
    assert "off hours usb count" in reasons[0]
    assert "z=4.2" in reasons[0]
    assert "_" not in reasons[0], "underscores should be humanised"


def test_reason_has_no_counterfactual_without_a_baseline():
    """zframe() injects a z-score with no mean_/std_ columns behind it -- there
    is nothing to derive a threshold from, so none should be invented."""
    df = compute_rule_based_score(zframe(off_hours_usb_count=4.2))
    assert "would not flag" not in df["reasons"].iloc[0][0]


def test_reason_includes_counterfactual_when_baseline_is_known():
    """Given this user's mean=2, std=1, z=4.2 -> actual value is 2 + 4.2 = 6.2,
    and the flag would stop firing below mean + Z_THRESHOLD*std = 2 + 2.5 = 4.5."""
    row = {f"z_{col}": 0.0 for col in FEATURE_COLS}
    row["z_off_hours_usb_count"] = 4.2
    row["mean_off_hours_usb_count"] = 2.0
    row["std_off_hours_usb_count"] = 1.0
    df = compute_rule_based_score(pd.DataFrame([row]))

    reason = df["reasons"].iloc[0][0]
    assert "would not flag below 4.5" in reason
    assert "actual: 6" in reason


def test_counterfactual_omitted_when_std_is_missing_or_zero():
    """A user with no variance in this feature has no meaningful counterfactual
    (and couldn't have triggered a rule in the first place outside a test)."""
    row = {f"z_{col}": 0.0 for col in FEATURE_COLS}
    row["z_off_hours_usb_count"] = 4.2
    row["mean_off_hours_usb_count"] = 2.0
    row["std_off_hours_usb_count"] = 0.0
    df = compute_rule_based_score(pd.DataFrame([row]))

    assert "would not flag" not in df["reasons"].iloc[0][0]


# --------------------------------------------------------------------------
# Severity bucketing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score,expected",
    [
        (0.0, "Normal"),
        (0.1, "Low"),
        (1.9, "Low"),
        (2.0, "Medium"),
        (4.9, "Medium"),
        (5.0, "High"),
        (7.9, "High"),
        (8.0, "Critical"),
        (11.0, "Critical"),
    ],
)
def test_severity_bucket_boundaries(score, expected):
    df = pd.DataFrame([{"rule_score": score, "iso_anomaly": False}])
    assert compute_final_severity(df)["severity"].iloc[0] == expected


def test_isolation_forest_adds_its_bonus_weight():
    df = pd.DataFrame([{"rule_score": 3.0, "iso_anomaly": True}])
    result = compute_final_severity(df)
    assert result["severity_score"].iloc[0] == 3.0 + ISOLATION_FOREST_WEIGHT


def test_isolation_forest_alone_cannot_reach_critical():
    """ML is a secondary signal: on its own it must never drive a top verdict."""
    df = pd.DataFrame([{"rule_score": 0.0, "iso_anomaly": True}])
    result = compute_final_severity(df)

    assert result["severity_score"].iloc[0] == ISOLATION_FOREST_WEIGHT
    assert result["severity"].iloc[0] not in ("High", "Critical")


def test_max_rule_score_without_ml_still_reaches_critical():
    """Explainable rules alone must be able to produce the top severity."""
    df = pd.DataFrame([{"rule_score": float(sum(RULE_WEIGHTS.values())), "iso_anomaly": False}])
    assert compute_final_severity(df)["severity"].iloc[0] == "Critical"


# --------------------------------------------------------------------------
# Drift signal -- the third, independent layer
# --------------------------------------------------------------------------


def make_daily_series(user, values):
    """One user's day-by-day history, in order, for drift's rolling window."""
    return make_feature_frame(
        [{"user": user, "day": f"2020-01-{i + 1:02d}" if i < 31 else f"2020-02-{i - 30:02d}",
          "logon_count": v}
         for i, v in enumerate(values)]
    )


def test_gradual_escalation_triggers_drift_but_not_a_single_days_zscore():
    """The motivating case: 40 quiet days at 2, then a sustained (not spiking)
    rise to 5 for 40 more days. No single day at the new level is far enough
    from the *whole-history* mean to cross Z_THRESHOLD -- but the trailing
    14-day average is a real, sustained departure the per-day rule cannot see.
    """
    values = [2] * 40 + [5] * 40
    df = compute_per_user_zscores(make_daily_series("creeper", values))

    assert df["z_logon_count"].max() < Z_THRESHOLD, (
        "this scenario is only meaningful if per-day z-scores stay unremarkable"
    )

    drifted = compute_drift_signal(df)
    assert drifted["drift_flag"].any(), "sustained escalation should trip the drift signal"
    assert "sustained rise in logon count" in drifted.loc[drifted["drift_flag"], "drift_reasons"].iloc[0]


def test_isolated_spike_does_not_trigger_drift():
    """One anomalous day surrounded by normal ones is a spike, not a trend --
    it barely moves a 14-day rolling average and must not read as drift."""
    values = [2] * 40 + [20] + [2] * 40
    df = compute_per_user_zscores(make_daily_series("spiker", values))
    drifted = compute_drift_signal(df)

    assert not drifted["drift_flag"].any()


def test_drift_requires_a_full_window_of_history():
    """Fewer than DRIFT_WINDOW days means the rolling mean is undefined --
    must degrade to not-flagged, never crash or false-positive on noise."""
    values = [5] * (DRIFT_WINDOW - 1)
    df = compute_per_user_zscores(make_daily_series("newuser", values))
    drifted = compute_drift_signal(df)

    assert not drifted["drift_flag"].any()


def test_drift_signal_skipped_without_a_baseline():
    """Without mean_/std_ columns (baseline not computed upstream) there is
    nothing to compare a trend against -- flag nothing rather than guess."""
    df = make_daily_series("u", [2] * 20)
    drifted = compute_drift_signal(df)

    assert not drifted["drift_flag"].any()
    assert (drifted["drift_reasons"] == "").all()


def test_drift_reason_reports_the_trend_zscore():
    values = [2] * 40 + [5] * 40
    df = compute_per_user_zscores(make_daily_series("creeper", values))
    drifted = compute_drift_signal(df)

    reason = drifted.loc[drifted["drift_flag"], "drift_reasons"].iloc[0]
    assert f"trailing {DRIFT_WINDOW} days" in reason
    assert "trend z=" in reason
