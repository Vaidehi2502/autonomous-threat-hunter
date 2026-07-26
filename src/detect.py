"""
detect.py — Autonomous Threat Hunter
Core detection engine: per-user behavioral baseline (z-scores) + Isolation Forest
ensemble, combined into a weighted severity score with human-readable reasons.
A third, independent layer (compute_drift_signal) catches slow behavioral drift
that neither of those can see.

Design principle: ML (Isolation Forest) is a SECONDARY signal, not the sole
verdict-driver. Rule-based z-score flags carry more weight and are explainable;
the ML layer catches multivariate patterns the simple rules miss, but never
overrides them alone. This avoids the false-positive trap of trusting a single
black-box anomaly model.

Drift detection deliberately does NOT feed into severity_score. A per-day
z-score can only ever compare one day against a user's whole-history average --
someone who escalates gradually over weeks, never spiking hard enough on any
single day to cross Z_THRESHOLD, sails through untouched. Drift compares a
rolling recent average against that same baseline instead of a single day,
which is far less noisy and can catch that slow-boil case. It is reported
alongside severity, not blended into it, so every existing severity figure
(and the numbers already written into the README and deck) stays exact.

Run: python src/detect.py
Input:  outputs/user_day_features.parquet
Output: outputs/threat_scores.parquet  (ranked, one row per user-day with severity + reasons)
"""

import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.ensemble import IsolationForest

OUT_DIR = Path("outputs")

FEATURE_COLS = [
    "logon_count",
    "off_hours_logon_count",
    "distinct_pcs",
    "usb_connect_count",
    "off_hours_usb_count",
]

Z_THRESHOLD = 2.5

RULE_WEIGHTS = {
    "off_hours_logon_count": 3,
    "off_hours_usb_count": 3,
    "usb_connect_count": 2,
    "distinct_pcs": 2,
    "logon_count": 1,
}

ISOLATION_FOREST_WEIGHT = 2

# Drift: compare a trailing rolling average against the user's whole-history
# baseline, using the standard error of an N-day mean rather than the noisier
# single-day std. That's what lets a smaller, sustained rise clear a
# statistically meaningful bar without needing DRIFT_WINDOW individual days to
# each independently cross Z_THRESHOLD.
DRIFT_WINDOW = 14
DRIFT_Z_THRESHOLD = 3.0


def compute_per_user_zscores(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    grouped = df.groupby("user")[FEATURE_COLS]
    means = grouped.transform("mean")
    stds = grouped.transform("std").replace(0, np.nan)

    for col in FEATURE_COLS:
        z_col = f"z_{col}"
        df[z_col] = (df[col] - means[col]) / stds[col]
        df[z_col] = df[z_col].fillna(0)
        # Kept so compute_rule_based_score can report a counterfactual threshold
        # ("would not flag below X") without recomputing per-user stats.
        df[f"mean_{col}"] = means[col]
        df[f"std_{col}"] = stds[col]

    return df


def compute_drift_signal(df: pd.DataFrame) -> pd.DataFrame:
    """Third, independent detection layer: sustained trend, not a single spike.

    Requires mean_/std_ columns from compute_per_user_zscores; if they are
    absent (e.g. a caller testing in isolation) every day is left un-flagged
    rather than guessing a baseline.
    """
    has_baseline = all(f"mean_{col}" in df.columns and f"std_{col}" in df.columns
                        for col in FEATURE_COLS)
    if not has_baseline or "day" not in df.columns:
        df = df.copy()
        df["drift_flag"] = False
        df["drift_reasons"] = ""
        return df

    df = df.sort_values(["user", "day"]).copy()
    df["drift_flag"] = False
    reasons = [[] for _ in range(len(df))]
    stderr_divisor = DRIFT_WINDOW ** 0.5

    for col in FEATURE_COLS:
        mean_col, std_col = f"mean_{col}", f"std_{col}"
        rolling_mean = df.groupby("user")[col].transform(
            lambda s: s.rolling(DRIFT_WINDOW, min_periods=DRIFT_WINDOW).mean()
        )
        stderr = df[std_col] / stderr_divisor
        drift_z = (rolling_mean - df[mean_col]) / stderr
        drift_z = drift_z.replace([np.inf, -np.inf], np.nan).fillna(0)

        triggered = drift_z > DRIFT_Z_THRESHOLD
        df.loc[triggered, "drift_flag"] = True
        label = col.replace("_", " ")
        for pos in np.flatnonzero(triggered.to_numpy()):
            idx = df.index[pos]
            reasons[pos].append(
                f"sustained rise in {label} over the trailing {DRIFT_WINDOW} days "
                f"(recent avg {rolling_mean.iat[pos]:.1f} vs personal baseline "
                f"{df.at[idx, mean_col]:.1f}, trend z={drift_z.iat[pos]:.1f})"
            )

    df["drift_reasons"] = ["; ".join(r) for r in reasons]
    return df


def compute_rule_based_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    Each triggered reason also carries a counterfactual: the value below which
    this specific day would not have been flagged. It's derived from the same
    per-user mean/std already used for the z-score (actual = mean + z*std, and
    the threshold is just Z_THRESHOLD standard deviations out), so it costs
    nothing extra to compute. Omitted when mean_/std_ aren't present (e.g. in
    tests that inject z-scores directly) rather than guessing a baseline.
    """
    df = df.copy()
    df["rule_score"] = 0.0
    df["reasons"] = [[] for _ in range(len(df))]

    for col, weight in RULE_WEIGHTS.items():
        z_col = f"z_{col}"
        mean_col = f"mean_{col}"
        std_col = f"std_{col}"
        has_baseline = mean_col in df.columns and std_col in df.columns

        triggered = df[z_col] > Z_THRESHOLD
        df.loc[triggered, "rule_score"] += weight
        label = col.replace("_", " ")

        for idx in df.index[triggered]:
            z = df.at[idx, z_col]
            reason = f"{label} unusually high for this user (z={z:.1f})"

            if has_baseline:
                mean, std = df.at[idx, mean_col], df.at[idx, std_col]
                if pd.notna(mean) and pd.notna(std) and std > 0:
                    actual = mean + z * std
                    ceiling = mean + Z_THRESHOLD * std
                    reason += f" — would not flag below {ceiling:.1f} (actual: {actual:.0f})"

            df.at[idx, "reasons"].append(reason)

    return df


def compute_isolation_forest_score(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    X = df[FEATURE_COLS].fillna(0)

    iso = IsolationForest(
        n_estimators=200,
        contamination=0.02,
        random_state=42,
    )
    preds = iso.fit_predict(X)
    scores = iso.decision_function(X)

    df["iso_anomaly"] = preds == -1
    df["iso_score_raw"] = scores

    for idx in df.index[df["iso_anomaly"]]:
        df.at[idx, "reasons"].append("Flagged as multivariate outlier by anomaly model")

    return df


def compute_final_severity(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["severity_score"] = df["rule_score"] + (df["iso_anomaly"].astype(int) * ISOLATION_FOREST_WEIGHT)

    def bucket(score):
        if score >= 8:
            return "Critical"
        elif score >= 5:
            return "High"
        elif score >= 2:
            return "Medium"
        elif score > 0:
            return "Low"
        return "Normal"

    df["severity"] = df["severity_score"].apply(bucket)
    return df


def run_detection():
    print("Loading feature table...")
    df = pd.read_parquet(OUT_DIR / "user_day_features.parquet")
    print(f"  {len(df):,} user-days loaded")

    print("Computing per-user z-score baselines...")
    df = compute_per_user_zscores(df)

    print("Computing rule-based severity...")
    df = compute_rule_based_score(df)

    print("Running Isolation Forest (secondary signal)...")
    df = compute_isolation_forest_score(df)

    print("Computing drift signal (tertiary, temporal)...")
    df = compute_drift_signal(df)

    print("Computing final severity scores...")
    df = compute_final_severity(df)

    df["reasons"] = df["reasons"].apply(lambda r: "; ".join(r) if r else "No specific rule triggered")

    flagged = df[df["severity"] != "Normal"].sort_values("severity_score", ascending=False)

    print(f"\nTotal user-days: {len(df):,}")
    print(f"Flagged (non-Normal): {len(flagged):,}")
    print(df["severity"].value_counts())
    drift_only = df[(df["severity"] == "Normal") & df["drift_flag"]]
    print(f"Drift-only (Normal severity, but a sustained trend): {len(drift_only):,}")

    df.to_parquet(OUT_DIR / "threat_scores.parquet")
    print("\nSaved full scored table to outputs/threat_scores.parquet")

    top20 = flagged.head(20)[["user", "day", "severity", "severity_score", "role", "department", "reasons"]]
    top20.to_csv(OUT_DIR / "top_threats_preview.csv", index=False)
    print("Saved top 20 preview to outputs/top_threats_preview.csv")

    return df


if __name__ == "__main__":
    result = run_detection()
    print("\nTop 10 highest-severity user-days:")
    cols = ["user", "day", "severity", "severity_score", "role", "reasons"]
    print(result.sort_values("severity_score", ascending=False)[cols].head(10).to_string())