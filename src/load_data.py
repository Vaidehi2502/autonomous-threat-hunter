"""
load_data.py — Autonomous Threat Hunter
Loads logon.csv, device.csv, users.csv from data/, cleans and saves as parquet
for fast reload during feature engineering / iteration.
"""

import pandas as pd
from pathlib import Path

DATA_DIR = Path("data")
OUT_DIR = Path("outputs")
OUT_DIR.mkdir(exist_ok=True)


def load_logon():
    print("Loading logon.csv...")
    df = pd.read_csv(
        DATA_DIR / "logon.csv",
        usecols=["date", "user", "pc", "activity"],
        dtype={"user": "category", "pc": "category", "activity": "category"},
    )
    df["date"] = pd.to_datetime(df["date"], format="%m/%d/%Y %H:%M:%S")
    print(f"  logon rows: {len(df):,}  date range: {df['date'].min()} to {df['date'].max()}")
    df.to_parquet(OUT_DIR / "logon_clean.parquet")
    return df


def load_device():
    print("Loading device.csv...")
    df = pd.read_csv(
        DATA_DIR / "device.csv",
        usecols=["date", "user", "pc", "activity"],
        dtype={"user": "category", "pc": "category", "activity": "category"},
    )
    df["date"] = pd.to_datetime(df["date"], format="%m/%d/%Y %H:%M:%S")
    print(f"  device rows: {len(df):,}  date range: {df['date'].min()} to {df['date'].max()}")
    df.to_parquet(OUT_DIR / "device_clean.parquet")
    return df


def load_users():
    print("Loading users.csv...")
    df = pd.read_csv(
        DATA_DIR / "users.csv",
        usecols=["user_id", "role", "department", "team", "supervisor", "start_date", "end_date"],
    )
    df["start_date"] = pd.to_datetime(df["start_date"], errors="coerce")
    df["end_date"] = pd.to_datetime(df["end_date"], errors="coerce")
    print(f"  users rows: {len(df):,}")
    df.to_parquet(OUT_DIR / "users_clean.parquet")
    return df


if __name__ == "__main__":
    logon = load_logon()
    device = load_device()
    users = load_users()
    print("\nDone. Cleaned files saved to outputs/*.parquet")