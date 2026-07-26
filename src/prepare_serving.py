"""
prepare_serving.py — Autonomous Threat Hunter

Rewrites outputs/threat_scores.parquet into a serving-optimised copy. This is a
lossless re-encode: no score, severity or reason is recomputed or changed, only
the physical layout on disk.

Why it exists
-------------
detect.py writes threat_scores.parquet in two row groups, the first holding
1,048,576 rows. Parquet is read at row-group granularity, so fetching one
user's timeline (a few hundred rows) forces the reader to decode a million,
spiking ~320 MB per request and pushing the API out of a 512 MB container.

Sorting by user and cutting the file into small row groups fixes both halves of
that: each row group carries user min/max statistics, so a lookup skips almost
every group, and the one group it does read is small.

Run: python src/prepare_serving.py
Input:  outputs/threat_scores.parquet
Output: outputs/threat_scores_serving.parquet
"""

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

OUT_DIR = Path(__file__).resolve().parent.parent / "outputs"
SOURCE = OUT_DIR / "threat_scores.parquet"
TARGET = OUT_DIR / "threat_scores_serving.parquet"

# Columns the API serves. The raw z-scores, rule_score, iso_score_raw and logon
# hours stay in the source file for analysis and are not needed at serving time.
SERVED_COLUMNS = [
    "user",
    "day",
    "role",
    "department",
    "team",
    "severity",
    "severity_score",
    "reasons",
    "logon_count",
    "off_hours_logon_count",
    "distinct_pcs",
    "usb_connect_count",
    "off_hours_usb_count",
    "iso_anomaly",
    "drift_flag",
    "drift_reasons",
]

# ~70 row groups over 1.39M rows. Small enough that one user lookup decodes a
# fraction of the table, large enough that per-group overhead stays negligible.
ROW_GROUP_SIZE = 20_000


def prepare():
    if not SOURCE.exists():
        raise SystemExit(f"Missing {SOURCE}. Run src/detect.py first.")

    print(f"Reading {SOURCE.name} ...")
    df = pd.read_parquet(SOURCE, columns=SERVED_COLUMNS)
    print(f"  {len(df):,} rows")

    # Sorting by user is what makes row-group statistics selective: a lookup for
    # one user can then skip every group whose user range excludes them.
    print("Sorting by (user, day) ...")
    df = df.sort_values(["user", "day"], kind="stable").reset_index(drop=True)

    print(f"Writing {TARGET.name} with row_group_size={ROW_GROUP_SIZE:,} ...")
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(
        table,
        TARGET,
        row_group_size=ROW_GROUP_SIZE,
        compression="zstd",
    )

    written = pq.ParquetFile(TARGET)
    print(
        f"Done. {written.metadata.num_rows:,} rows in "
        f"{written.metadata.num_row_groups} row groups, "
        f"{TARGET.stat().st_size / 1e6:.1f} MB on disk."
    )


if __name__ == "__main__":
    prepare()
