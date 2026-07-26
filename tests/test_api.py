"""
Integration tests for the FastAPI backend (src/api.py).

These run against the real committed outputs/threat_scores.parquet, so they
verify the API and the shipped detection results together.
"""

import pytest
from fastapi.testclient import TestClient

from src.api import SEVERITY_ORDER, SEVERITY_RANK, app


@pytest.fixture(scope="module")
def client():
    # Module-scoped: loading the 1.39M-row parquet is slow and cached globally.
    with TestClient(app) as c:
        yield c


def test_root_lists_endpoints(client):
    body = client.get("/").json()
    assert body["service"] == "Autonomous Threat Hunter API"
    assert "/stats" in body["endpoints"]


# --------------------------------------------------------------------------
# /stats
# --------------------------------------------------------------------------


def test_stats_is_internally_consistent(client):
    s = client.get("/stats").json()

    assert s["total_users"] > 0
    assert s["total_user_days"] > s["total_flagged"]

    breakdown = s["severity_breakdown"]
    assert set(breakdown) == set(SEVERITY_ORDER)

    # Every user-day lands in exactly one bucket.
    assert sum(breakdown.values()) == s["total_user_days"]
    # "Flagged" is everything that is not Normal.
    assert s["total_user_days"] - breakdown["Normal"] == s["total_flagged"]


def test_stats_date_range_is_ordered(client):
    r = client.get("/stats").json()["date_range"]
    assert r["start"] < r["end"]


def test_stats_matches_published_readme_figures(client):
    """Regression lock on the numbers quoted in the README and the pitch.

    If this fails, the committed results changed and the README is now wrong.
    """
    s = client.get("/stats").json()

    assert s["total_users"] == 4000
    assert s["total_user_days"] == 1393138
    assert s["total_flagged"] == 50520
    assert s["severity_breakdown"]["Critical"] == 377
    assert s["severity_breakdown"]["High"] == 1641


# --------------------------------------------------------------------------
# /threats
# --------------------------------------------------------------------------


def test_threats_never_returns_normal_rows(client):
    results = client.get("/threats", params={"limit": 200}).json()["results"]
    assert results
    assert all(r["severity"] != "Normal" for r in results)


def test_threats_sorted_by_severity_score_descending(client):
    results = client.get("/threats", params={"limit": 100}).json()["results"]
    scores = [r["severity_score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_threats_min_severity_filters_correctly(client):
    results = client.get("/threats", params={"min_severity": "High", "limit": 100}).json()["results"]
    assert results
    assert all(SEVERITY_RANK[r["severity"]] >= SEVERITY_RANK["High"] for r in results)


def test_threats_rejects_unknown_severity(client):
    assert client.get("/threats", params={"min_severity": "Catastrophic"}).status_code == 400


def test_threats_rejects_out_of_range_limit(client):
    assert client.get("/threats", params={"limit": 99999}).status_code == 422
    assert client.get("/threats", params={"limit": 0}).status_code == 422


def test_threats_limit_and_offset_paginate(client):
    first = client.get("/threats", params={"limit": 5}).json()
    second = client.get("/threats", params={"limit": 5, "offset": 5}).json()

    assert len(first["results"]) == 5
    assert first["total_matching"] == second["total_matching"]

    first_keys = {(r["user"], r["day"]) for r in first["results"]}
    second_keys = {(r["user"], r["day"]) for r in second["results"]}
    assert not (first_keys & second_keys), "pages must not overlap"


def test_threats_user_search_is_substring_and_case_insensitive(client):
    hits = client.get("/threats", params={"user": "acm2278", "limit": 10}).json()["results"]
    assert hits
    assert all("ACM2278" in r["user"].upper() for r in hits)


def test_threat_row_carries_an_explanation(client):
    """Every flagged row must be interrogable by an analyst."""
    results = client.get("/threats", params={"min_severity": "High", "limit": 50}).json()["results"]

    for row in results:
        assert isinstance(row["reasons"], str)
        assert row["reasons"].strip()
        assert row["reasons"] != "No specific rule triggered"


def test_threat_row_has_the_fields_the_dashboard_reads(client):
    row = client.get("/threats", params={"limit": 1}).json()["results"][0]
    expected = {
        "user", "day", "role", "department", "team", "severity", "severity_score",
        "reasons", "logon_count", "off_hours_logon_count", "distinct_pcs",
        "usb_connect_count", "off_hours_usb_count", "iso_anomaly",
        "drift_flag", "drift_reasons",
    }
    assert expected <= set(row)


# --------------------------------------------------------------------------
# Drift signal -- third, independent detection layer (see src/detect.py)
# --------------------------------------------------------------------------


def test_stats_reports_a_drift_flagged_count(client):
    """This is the headline number for the signal: user-days rated Normal by
    severity alone, but flagged by sustained trend instead of a single spike."""
    s = client.get("/stats").json()
    assert s["drift_flagged"] > 0


def test_threats_excludes_drift_only_rows_by_default(client):
    """include_drift defaults to off, so a plain /threats call stays exactly
    what test_threats_never_returns_normal_rows already asserts."""
    results = client.get("/threats", params={"min_severity": "Low", "limit": 5000}).json()["results"]
    assert all(r["severity"] != "Normal" for r in results)


def test_include_drift_surfaces_normal_severity_rows(client):
    """With include_drift=true, a day that never earned a severity score can
    still surface -- that is the entire point of a signal severity can't see."""
    results = client.get(
        "/threats",
        params={"min_severity": "Critical", "include_drift": "true", "limit": 5000},
    ).json()["results"]

    normal_drift_rows = [r for r in results if r["severity"] == "Normal"]
    assert normal_drift_rows, "expected at least one drift-only Normal row"
    assert all(r["drift_flag"] for r in normal_drift_rows)
    assert all(r["drift_reasons"] for r in normal_drift_rows)

    # Rows that qualify on severity alone must still be present too -- this is
    # additive (OR), not a replacement filter.
    assert any(r["severity"] == "Critical" for r in results)


def test_drift_reasons_empty_string_when_not_flagged(client):
    results = client.get("/threats", params={"min_severity": "Critical", "limit": 20}).json()["results"]
    for row in results:
        if not row["drift_flag"]:
            assert row["drift_reasons"] == ""


# --------------------------------------------------------------------------
# /user/{id}/timeline
# --------------------------------------------------------------------------


def test_timeline_unknown_user_returns_404(client):
    assert client.get("/user/NOPE9999/timeline").status_code == 404


def test_timeline_counts_agree_with_its_own_rows(client):
    body = client.get("/user/ACM2278/timeline").json()

    assert body["total_days"] == len(body["timeline"])
    assert body["flagged_days"] == sum(r["severity"] != "Normal" for r in body["timeline"])
    assert sum(body["severity_breakdown"].values()) == body["total_days"]


def test_timeline_is_chronological(client):
    days = [r["day"] for r in client.get("/user/ACM2278/timeline").json()["timeline"]]
    assert days == sorted(days)


def test_demo_walkthrough_users_still_hold_up(client):
    """The three users named in the README demo script must stay demo-worthy.

    EYD2871's exact counts are quoted verbatim in the README.
    """
    eyd = client.get("/user/EYD2871/timeline").json()
    assert eyd["role"] == "ProductionLineWorker"
    assert eyd["total_days"] == 358
    assert eyd["flagged_days"] == 52
    assert eyd["severity_breakdown"]["Critical"] == 9
    assert eyd["severity_breakdown"]["High"] == 21
    assert eyd["severity_breakdown"]["Medium"] == 22

    for user_id in ("ACM2278", "HMY0235"):
        body = client.get(f"/user/{user_id}/timeline").json()
        assert body["severity_breakdown"]["Critical"] > 0, f"{user_id} lost its Critical days"
