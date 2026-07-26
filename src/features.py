"""
features.py — Autonomous Threat Hunter
Builds a per-user-per-day feature table from logon.csv + device.csv + users.csv.

Run: python src/features.py
Input:  outputs/logon_clean.parquet, outputs/device_clean.parquet, outputs/users_clean.parquet
Output: outputs/user_day_features.parquet
"""

import pandas as pd
import numpy as np
from pathlib import Path

OUT_DIR = Path("outputs")

# Off-hours definition: before 6am or after 8pm counts as off-hours.
# Adjust if your dataset's normal work pattern looks different after inspection.
OFF_HOURS_START = 20
OFF_HOURS_END = 6


def is_off_hours(hour_series):
    return (hour_series >= OFF_HOURS_START) | (hour_series < OFF_HOURS_END)


def build_logon_features(logon: pd.DataFrame) -> pd.DataFrame:
    logon = logon.copy()
    logon["day"] = logon["date"].dt.date
    logon["hour"] = logon["date"].dt.hour
    logon["is_off_hours"] = is_off_hours(logon["hour"])

    logons_only = logon[logon["activity"] == "Logon"]

    grouped = logons_only.groupby(["user", "day"], observed=True).agg(
        logon_count=("activity", "count"),
        off_hours_logon_count=("is_off_hours", "sum"),
        distinct_pcs=("pc", "nunique"),
        first_logon_hour=("hour", "min"),
        last_logon_hour=("hour", "max"),
    ).reset_index()

    return grouped


def build_device_features(device: pd.DataFrame) -> pd.DataFrame:
    device = device.copy()
    device["day"] = device["date"].dt.date
    device["hour"] = device["date"].dt.hour
    device["is_off_hours"] = is_off_hours(device["hour"])

    connects_only = device[device["activity"] == "Connect"]

    grouped = connects_only.groupby(["user", "day"], observed=True).agg(
        usb_connect_count=("activity", "count"),
        off_hours_usb_count=("is_off_hours", "sum"),
    ).reset_index()

    return grouped


def build_feature_table():
    print("Loading cleaned parquet files...")
    logon = pd.read_parquet(OUT_DIR / "logon_clean.parquet")
    device = pd.read_parquet(OUT_DIR / "device_clean.parquet")
    users = pd.read_parquet(OUT_DIR / "users_clean.parquet")

    print("Building logon features...")
    logon_feats = build_logon_features(logon)
    print(f"  {len(logon_feats):,} user-day rows from logon")

    print("Building device features...")
    device_feats = build_device_features(device)
    print(f"  {len(device_feats):,} user-day rows from device")

    print("Merging logon + device on (user, day)...")
    merged = pd.merge(logon_feats, device_feats, on=["user", "day"], how="outer")

    # Fill missing device activity with 0 (no USB activity that day is normal, not missing data)
    for col in ["usb_connect_count", "off_hours_usb_count"]:
        merged[col] = merged[col].fillna(0)

    # Fill missing logon fields defensively (shouldn't happen often, but outer join can produce gaps)
    for col in ["logon_count", "off_hours_logon_count", "distinct_pcs"]:
        merged[col] = merged[col].fillna(0)

    print("Joining user context (role/department/team)...")
    users_small = users[["user_id", "role", "department", "team"]].rename(columns={"user_id": "user"})
    merged = merged.merge(users_small, on="user", how="left")

    merged["day"] = pd.to_datetime(merged["day"])

    print(f"\nFinal feature table: {len(merged):,} rows, {merged['user'].nunique():,} users")
    merged.to_parquet(OUT_DIR / "user_day_features.parquet")
    print("Saved to outputs/user_day_features.parquet")

    return merged


if __name__ == "__main__":
    df = build_feature_table()
    print("\nSample:")
    print(df.head(10))
    print("\nFeature summary stats:")
    print(df.describe())