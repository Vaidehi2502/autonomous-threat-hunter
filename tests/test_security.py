"""
Tests for the API hardening: security headers, optional API key, rate limiting,
and audit logging.

The controls are configured by module-level constants read from the environment
at import. These tests patch those constants directly rather than re-importing
the module, which would re-load the parquet.
"""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from src import api as api_module
from src.api import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def reset_limiter():
    """Each test starts with an empty request history and permissive limits."""
    original_requests = api_module.RATE_LIMIT_REQUESTS
    original_keys = api_module.API_KEYS
    api_module._request_times.clear()
    yield
    api_module.RATE_LIMIT_REQUESTS = original_requests
    api_module.API_KEYS = original_keys
    api_module._request_times.clear()


# --------------------------------------------------------------------------
# Security headers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header,expected",
    [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "no-referrer"),
        ("Cache-Control", "no-store"),
    ],
)
def test_security_headers_present(client, header, expected):
    assert client.get("/stats").headers[header] == expected


def test_security_headers_on_error_responses(client):
    """A 404 must be as locked down as a 200."""
    response = client.get("/user/NOPE9999/timeline")
    assert response.status_code == 404
    assert response.headers["X-Content-Type-Options"] == "nosniff"


# --------------------------------------------------------------------------
# CORS
#
# CORSMiddleware must be the outermost middleware. If a middleware below it
# rejects the request first (a 401, a 429, or a preflight OPTIONS), the
# response never reaches CORSMiddleware, comes back with no
# Access-Control-Allow-Origin header, and the browser reports a bare "Failed
# to fetch" instead of surfacing the real status. See the comment above the
# middleware registrations in src/api.py.
# --------------------------------------------------------------------------


def test_preflight_succeeds_even_when_key_required(client):
    """A same-origin browser must be able to complete its CORS preflight
    before the API key is even in play -- the browser sends no credentials
    on an OPTIONS preflight, so it must never be gated by auth."""
    api_module.API_KEYS = {"s3cret": "alice"}
    response = client.options(
        "/stats",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-api-key",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_401_response_carries_cors_headers(client):
    api_module.API_KEYS = {"s3cret": "alice"}
    response = client.get("/stats", headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_429_response_carries_cors_headers(client):
    api_module.RATE_LIMIT_REQUESTS = 1
    client.get("/", headers={"Origin": "http://localhost:5173"})
    response = client.get("/", headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 429
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


# --------------------------------------------------------------------------
# API key
# --------------------------------------------------------------------------


def test_open_by_default(client):
    """With no API_KEYS configured the service stays public, as the demo needs."""
    api_module.API_KEYS = {}
    assert client.get("/stats").status_code == 200


def test_key_required_when_configured(client):
    api_module.API_KEYS = {"s3cret": "alice"}
    assert client.get("/stats").status_code == 401
    assert client.get("/threats").status_code == 401
    assert client.get("/user/EYD2871/timeline").status_code == 401


def test_correct_key_accepted(client):
    api_module.API_KEYS = {"s3cret": "alice"}
    response = client.get("/stats", headers={"X-API-Key": "s3cret"})
    assert response.status_code == 200
    assert response.json()["total_flagged"] == 50520


def test_wrong_key_rejected(client):
    api_module.API_KEYS = {"s3cret": "alice"}
    assert client.get("/stats", headers={"X-API-Key": "wrong"}).status_code == 401
    # A prefix of the real key must not pass either.
    assert client.get("/stats", headers={"X-API-Key": "s3c"}).status_code == 401


def test_root_stays_reachable_without_key(client):
    """Health checks and discovery must survive authentication being enabled."""
    api_module.API_KEYS = {"s3cret": "alice"}
    assert client.get("/").status_code == 200
    assert client.get("/openapi.json").status_code == 200


# --------------------------------------------------------------------------
# Per-analyst keys
# --------------------------------------------------------------------------


def test_parse_api_keys_maps_key_to_name():
    parsed = api_module._parse_api_keys("alice:8f2a1c,bob:9d3b2e")
    assert parsed == {"8f2a1c": "alice", "9d3b2e": "bob"}


def test_parse_api_keys_ignores_malformed_entries():
    """A stray entry with no name (no colon) is dropped rather than treated
    as an anonymous, unattributable key."""
    parsed = api_module._parse_api_keys("alice:8f2a1c, ,justastring,bob:9d3b2e")
    assert parsed == {"8f2a1c": "alice", "9d3b2e": "bob"}


def test_two_analysts_can_both_authenticate(client):
    api_module.API_KEYS = {"alice-key": "alice", "bob-key": "bob"}
    assert client.get("/stats", headers={"X-API-Key": "alice-key"}).status_code == 200
    assert client.get("/stats", headers={"X-API-Key": "bob-key"}).status_code == 200
    assert client.get("/stats", headers={"X-API-Key": "carol-key"}).status_code == 401


def test_timeline_audit_entry_names_the_analyst(client, caplog):
    api_module.API_KEYS = {"alice-key": "alice"}
    with caplog.at_level(logging.INFO, logger="threat_hunter.audit"):
        client.get("/user/EYD2871/timeline", headers={"X-API-Key": "alice-key"})

    entry = json.loads(caplog.records[0].message)
    assert entry["analyst"] == "alice"


def test_timeline_audit_entry_has_no_analyst_when_auth_disabled(client, caplog):
    api_module.API_KEYS = {}
    with caplog.at_level(logging.INFO, logger="threat_hunter.audit"):
        client.get("/user/EYD2871/timeline")

    entry = json.loads(caplog.records[0].message)
    assert entry["analyst"] is None


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


def test_requests_allowed_under_the_limit(client):
    api_module.RATE_LIMIT_REQUESTS = 10
    for _ in range(9):
        assert client.get("/").status_code == 200


def test_limit_exceeded_returns_429_with_retry_after(client):
    api_module.RATE_LIMIT_REQUESTS = 5
    for _ in range(5):
        assert client.get("/").status_code == 200

    response = client.get("/")
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    assert "rate limit" in response.json()["detail"].lower()


def test_rate_limit_can_be_disabled(client):
    api_module.RATE_LIMIT_REQUESTS = 0
    for _ in range(50):
        assert client.get("/").status_code == 200


def test_clients_are_limited_independently(client):
    """One noisy caller must not lock everyone else out."""
    api_module.RATE_LIMIT_REQUESTS = 3
    noisy = {"X-Forwarded-For": "203.0.113.1"}
    quiet = {"X-Forwarded-For": "203.0.113.2"}

    for _ in range(3):
        assert client.get("/", headers=noisy).status_code == 200
    assert client.get("/", headers=noisy).status_code == 429

    assert client.get("/", headers=quiet).status_code == 200


def test_forwarded_for_uses_first_hop(client):
    """Behind a proxy the client is the first entry, not the proxy chain."""
    api_module.RATE_LIMIT_REQUESTS = 2
    chain = {"X-Forwarded-For": "198.51.100.7, 10.0.0.1, 10.0.0.2"}

    for _ in range(2):
        assert client.get("/", headers=chain).status_code == 200
    assert client.get("/", headers=chain).status_code == 429

    # Same proxy chain, different origin client -> tracked separately.
    other = {"X-Forwarded-For": "198.51.100.8, 10.0.0.1, 10.0.0.2"}
    assert client.get("/", headers=other).status_code == 200


# --------------------------------------------------------------------------
# Audit logging
# --------------------------------------------------------------------------


def test_timeline_access_is_logged(client, caplog):
    with caplog.at_level(logging.INFO, logger="threat_hunter.audit"):
        client.get("/user/EYD2871/timeline", headers={"X-Forwarded-For": "203.0.113.9"})

    assert len(caplog.records) == 1
    entry = json.loads(caplog.records[0].message)
    assert entry["event"] == "user_timeline_access"
    assert entry["user_id"] == "EYD2871"
    assert entry["client"] == "203.0.113.9"
    assert entry["found"] is True


def test_missing_user_timeline_access_is_logged_as_not_found(client, caplog):
    with caplog.at_level(logging.INFO, logger="threat_hunter.audit"):
        client.get("/user/NOPE9999/timeline")

    assert len(caplog.records) == 1
    entry = json.loads(caplog.records[0].message)
    assert entry["user_id"] == "NOPE9999"
    assert entry["found"] is False


def test_other_endpoints_do_not_write_timeline_audit_entries(client, caplog):
    with caplog.at_level(logging.INFO, logger="threat_hunter.audit"):
        client.get("/stats")
        client.get("/threats")

    assert len(caplog.records) == 0
