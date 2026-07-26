"""
api.py — Autonomous Threat Hunter
FastAPI backend serving precomputed detection results to the dashboard.

Serves from outputs/threat_scores.parquet (no live computation — detection
already ran offline via load_data.py -> features.py -> detect.py).

Memory design: only 50,520 of 1,393,138 user-days are severity-flagged (3.6%),
plus a small number more that are drift-flagged only (see detect.py's third
signal) -- /threats never returns anything outside that set. So the process
holds just those rows, precomputes the /stats aggregates during a single
streaming pass at startup, and reads individual user timelines back from the
parquet on demand. That keeps the resident set small enough for a 512 MB
container; holding the whole table instead costs ~870 MB.

Run: uvicorn src.api:app --reload --port 8000
"""

import json
import logging
import os
import secrets
import time
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Anchored to the project root so the API can be started from any working
# directory, not only from the repo root.
OUT_DIR = Path(__file__).resolve().parent.parent / "outputs"

# detect.py's raw output, and the serving-optimised rewrite of it produced by
# src/prepare_serving.py (sorted by user, 20k-row groups). The rewrite makes a
# single-user timeline lookup decode one small row group instead of the
# 1,048,576-row group detect.py emits. Contents are identical either way.
SOURCE_PATH = OUT_DIR / "threat_scores.parquet"
SERVING_PATH = OUT_DIR / "threat_scores_serving.parquet"


def scores_path() -> Path:
    """Prefer the serving-optimised file, falling back to detect.py's output."""
    return SERVING_PATH if SERVING_PATH.exists() else SOURCE_PATH

SEVERITY_ORDER = ["Normal", "Low", "Medium", "High", "Critical"]
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITY_ORDER)}

# Only the columns the API actually serves. threat_scores.parquet also holds the
# raw z-scores, rule_score, iso_score_raw and logon hours; those stay on disk.
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

# Small non-negative integers in practice, so float32 is exact for them.
NUMERIC_COLUMNS = [
    "severity_score",
    "logon_count",
    "off_hours_logon_count",
    "distinct_pcs",
    "usb_connect_count",
    "off_hours_usb_count",
]

# Low-cardinality even across the full table, so category dtype is a large win.
CATEGORICAL_COLUMNS = ["user", "day", "role", "department", "team", "severity", "reasons", "drift_reasons"]

# Rows converted per batch while streaming. Caps peak memory during startup.
BATCH_ROWS = 150_000

# Timelines are re-read from parquet per request; cache the most recent few so
# that clicking between users in the dashboard stays responsive.
TIMELINE_CACHE_SIZE = 32

app = FastAPI(title="Autonomous Threat Hunter API")

# Local dev origins are always allowed. Deployed dashboard origins can be added
# via CORS_ALLOW_ORIGINS (comma-separated) without touching this file.
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
ALLOWED_ORIGINS += [
    origin.strip()
    for origin in os.getenv("CORS_ALLOW_ORIGINS", "").split(",")
    if origin.strip()
]

# --------------------------------------------------------------------------
# Hardening
#
# This service exposes per-employee behavioural monitoring. The dataset here is
# CERT r4.2 -- synthetic, no real people -- so the public demo runs open. The
# controls below are what a real deployment needs, and are enabled by setting
# environment variables rather than editing code. See SECURITY.md.
# --------------------------------------------------------------------------

# When set, every data endpoint requires one of these keys in an X-API-Key
# header. Unset (the default) leaves the API open, which is what the hosted
# demo wants. Format: comma-separated "name:key" pairs, e.g.
# "alice:8f2a1c...,bob:9d3b2e...". The name exists so a key maps to a person,
# not just an opaque secret -- it is what makes the audit log (below)
# attributable to an analyst instead of just an IP. See SECURITY.md.
def _parse_api_keys(raw: str) -> dict:
    keys = {}
    for entry in raw.split(","):
        name, _, key = entry.strip().partition(":")
        name, key = name.strip(), key.strip()
        if name and key:
            keys[key] = name
    return keys

API_KEYS = _parse_api_keys(os.getenv("API_KEYS", ""))

# Paths that stay reachable without a key, so the service is still
# discoverable and health checks keep working.
UNAUTHENTICATED_PATHS = {"/", "/docs", "/redoc", "/openapi.json"}

# Fixed-window limit per client IP. Set RATE_LIMIT_REQUESTS=0 to disable.
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "120"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

# Stop the limiter's own bookkeeping from becoming a memory-exhaustion vector.
RATE_LIMIT_MAX_TRACKED_CLIENTS = 10_000

_request_times: dict = defaultdict(deque)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

# Records every read of a single employee's timeline -- "who looked up whom" is
# a standard requirement for insider-threat tooling monitoring itself. Emitted
# as JSON lines on stdout, which Render (and most hosts) capture and retain, so
# this needs no extra infrastructure. When API_KEYS identifies the caller (see
# enforce_api_key below), the entry names the analyst; otherwise it falls back
# to IP only.
audit_logger = logging.getLogger("threat_hunter.audit")
audit_logger.setLevel(logging.INFO)
if not audit_logger.handlers:
    _audit_handler = logging.StreamHandler()
    _audit_handler.setFormatter(logging.Formatter("%(message)s"))
    audit_logger.addHandler(_audit_handler)
    audit_logger.propagate = False


def _log_timeline_access(request: Request, user_id: str, found: bool) -> None:
    audit_logger.info(json.dumps({
        "event": "user_timeline_access",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "client": _client_key(request),
        "analyst": getattr(request.state, "analyst", None),
        "user_id": user_id,
        "found": found,
    }))


def _client_key(request: Request) -> str:
    """Identify the caller for rate limiting.

    Behind Render, Fly or any reverse proxy the socket address is the proxy, so
    prefer the first hop in X-Forwarded-For when present.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(request: Request) -> Optional[int]:
    """Record this request. Returns seconds to wait if the caller is over."""
    if RATE_LIMIT_REQUESTS <= 0:
        return None

    now = time.monotonic()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    key = _client_key(request)
    seen = _request_times[key]

    while seen and seen[0] < cutoff:
        seen.popleft()

    if len(seen) >= RATE_LIMIT_REQUESTS:
        return max(1, int(seen[0] + RATE_LIMIT_WINDOW_SECONDS - now) + 1)

    seen.append(now)

    if len(_request_times) > RATE_LIMIT_MAX_TRACKED_CLIENTS:
        for stale in [k for k, v in _request_times.items() if not v]:
            del _request_times[stale]

    return None


# Registration order matters: Starlette makes the most recently added
# middleware outermost. CORS must be outermost of all -- otherwise a request
# that gets rejected by a middleware below it (a 401, a 429, or a same-origin
# preflight OPTIONS) never reaches CORSMiddleware, comes back with no
# Access-Control-Allow-Origin header, and the browser reports a bare "Failed
# to fetch" instead of the actual error. It is registered last, below.


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response


def _match_api_key(provided: str) -> Optional[str]:
    """Return the analyst name for a valid key, or None.

    Checks every candidate with a constant-time comparison so a wrong key
    cannot be recovered by timing a single comparison; which analyst it
    belongs to is not a secret worth hiding from timing either way.
    """
    for key, name in API_KEYS.items():
        if secrets.compare_digest(provided, key):
            return name
    return None


@app.middleware("http")
async def enforce_api_key(request: Request, call_next):
    if API_KEYS and request.url.path not in UNAUTHENTICATED_PATHS:
        analyst = _match_api_key(request.headers.get("x-api-key", ""))
        if analyst is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid X-API-Key header"},
                headers=SECURITY_HEADERS,
            )
        request.state.analyst = analyst
    return await call_next(request)


@app.middleware("http")
async def apply_rate_limit(request: Request, call_next):
    retry_after = _rate_limited(request)
    if retry_after is not None:
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded. Slow down and retry."},
            headers={**SECURITY_HEADERS, "Retry-After": str(retry_after)},
        )
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_flagged: Optional[pd.DataFrame] = None
_stats: Optional[dict] = None


def _normalise(chunk: pd.DataFrame) -> pd.DataFrame:
    """Apply the presentation-level cleanups every endpoint depends on."""
    chunk["day"] = pd.to_datetime(chunk["day"]).dt.strftime("%Y-%m-%d")
    for col in ["role", "department", "team"]:
        chunk[col] = chunk[col].fillna("Unknown")
    return chunk


def _build_serving_state() -> tuple:
    """Stream the scored table once, keeping only what the API needs resident.

    Returns the flagged-rows frame plus the precomputed /stats payload. The
    corpus-wide totals in /stats cover every user-day including Normal ones, so
    they are accumulated during the pass rather than derived from the frame.
    """
    parquet = pq.ParquetFile(scores_path())

    flagged_chunks = []
    total_rows = 0
    severity_counts = Counter()
    drift_flagged = 0
    users = set()
    day_min = None
    day_max = None

    for batch in parquet.iter_batches(batch_size=BATCH_ROWS, columns=SERVED_COLUMNS):
        chunk = _normalise(batch.to_pandas())

        total_rows += len(chunk)
        severity_counts.update(chunk["severity"].value_counts().to_dict())
        drift_flagged += int(chunk["drift_flag"].sum())
        users.update(chunk["user"].unique())

        chunk_min, chunk_max = chunk["day"].min(), chunk["day"].max()
        day_min = chunk_min if day_min is None or chunk_min < day_min else day_min
        day_max = chunk_max if day_max is None or chunk_max > day_max else day_max

        # Drift is a separate, tertiary signal (see detect.py) and does not
        # feed into severity -- a day can be drift-flagged while Normal on the
        # primary score. Keep it resident too, or /threats could never surface
        # the exact cases this signal exists to catch.
        keep = chunk[(chunk["severity"] != "Normal") | chunk["drift_flag"]].copy()
        if not keep.empty:
            keep["severity_rank"] = keep["severity"].map(SEVERITY_RANK).astype("int8")
            for col in NUMERIC_COLUMNS:
                keep[col] = keep[col].astype("float32")
            for col in CATEGORICAL_COLUMNS:
                keep[col] = keep[col].astype("category")
            flagged_chunks.append(keep)

        del chunk

    df = pd.concat(flagged_chunks, ignore_index=True)
    del flagged_chunks

    # Concatenating chunks with differing category sets widens them back to
    # object, so unify once over the whole frame.
    for col in CATEGORICAL_COLUMNS:
        if not isinstance(df[col].dtype, pd.CategoricalDtype):
            df[col] = df[col].astype("category")

    stats = {
        "total_users": len(users),
        "total_user_days": total_rows,
        "total_flagged": total_rows - severity_counts.get("Normal", 0),
        "severity_breakdown": {s: int(severity_counts.get(s, 0)) for s in SEVERITY_ORDER},
        "drift_flagged": drift_flagged,
        "date_range": {"start": day_min, "end": day_max},
    }

    return df, stats


def _ensure_loaded() -> None:
    global _flagged, _stats
    if _flagged is None:
        _flagged, _stats = _build_serving_state()


def get_flagged() -> pd.DataFrame:
    """The flagged (non-Normal) user-days, held resident. Backs /threats."""
    _ensure_loaded()
    return _flagged


def get_stats_payload() -> dict:
    _ensure_loaded()
    return _stats


def row_to_dict(row: pd.Series) -> dict:
    return {
        "user": row["user"],
        "day": row["day"],
        "role": row["role"],
        "department": row["department"],
        "team": row["team"],
        "severity": row["severity"],
        "severity_score": float(row["severity_score"]),
        "reasons": row["reasons"],
        "logon_count": float(row["logon_count"]),
        "off_hours_logon_count": float(row["off_hours_logon_count"]),
        "distinct_pcs": float(row["distinct_pcs"]),
        "usb_connect_count": float(row["usb_connect_count"]),
        "off_hours_usb_count": float(row["off_hours_usb_count"]),
        "iso_anomaly": bool(row["iso_anomaly"]),
        "drift_flag": bool(row["drift_flag"]),
        "drift_reasons": row["drift_reasons"],
    }


@lru_cache(maxsize=TIMELINE_CACHE_SIZE)
def _timeline_payload(user_id: str) -> Optional[dict]:
    """Read one user's full history straight from the parquet.

    A timeline needs that user's Normal days too, which are not held in memory.
    The filter prunes at row-group granularity, so this is only cheap because
    prepare_serving.py sorted the file by user into small groups -- against
    detect.py's raw output the same call decodes a 1,048,576-row group.
    """
    table = pq.read_table(
        scores_path(),
        columns=SERVED_COLUMNS,
        filters=[("user", "==", user_id)],
    )
    if table.num_rows == 0:
        return None

    user_df = _normalise(table.to_pandas()).sort_values("day")
    first = user_df.iloc[0]

    return {
        "user": user_id,
        "role": first["role"],
        "department": first["department"],
        "team": first["team"],
        "total_days": int(len(user_df)),
        "flagged_days": int((user_df["severity"] != "Normal").sum()),
        "severity_breakdown": {
            s: int((user_df["severity"] == s).sum()) for s in SEVERITY_ORDER
        },
        "timeline": [row_to_dict(row) for _, row in user_df.iterrows()],
    }


@app.get("/stats")
def get_stats():
    return get_stats_payload()


@app.get("/threats")
def get_threats(
    min_severity: str = Query("Low", description="Minimum severity to include"),
    include_drift: bool = Query(
        False,
        description="Also include drift-flagged days regardless of severity -- "
                     "sustained behavioral trends that never spiked hard enough "
                     "on any single day to earn a severity score of their own.",
    ),
    user: Optional[str] = Query(None, description="Filter/search by user id (substring match)"),
    department: Optional[str] = Query(None, description="Filter by department"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    if min_severity not in SEVERITY_RANK:
        raise HTTPException(status_code=400, detail=f"min_severity must be one of {SEVERITY_ORDER}")

    # Already flagged-or-drifting only; a day that is neither is never held in
    # memory (see _build_serving_state).
    result = get_flagged()
    severity_ok = result["severity_rank"] >= SEVERITY_RANK[min_severity]
    result = result[severity_ok | result["drift_flag"]] if include_drift else result[severity_ok]

    if user:
        result = result[result["user"].str.contains(user, case=False, na=False)]
    if department:
        result = result[result["department"] == department]

    result = result.sort_values("severity_score", ascending=False)
    total_matching = int(len(result))
    page = result.iloc[offset : offset + limit]

    return {
        "total_matching": total_matching,
        "limit": limit,
        "offset": offset,
        "results": [row_to_dict(row) for _, row in page.iterrows()],
    }


@app.get("/user/{user_id}/timeline")
def get_user_timeline(user_id: str, request: Request):
    payload = _timeline_payload(user_id)
    _log_timeline_access(request, user_id, found=payload is not None)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"No data for user '{user_id}'")
    return payload


@app.get("/")
def root():
    return {
        "service": "Autonomous Threat Hunter API",
        "endpoints": ["/stats", "/threats", "/user/{user_id}/timeline"],
    }
