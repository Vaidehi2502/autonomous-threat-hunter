---
title: Autonomous Threat Hunter API
emoji: 🛡️
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 8000
pinned: false
license: mit
---

# Autonomous Threat Hunter — API

FastAPI backend for the Autonomous Threat Hunter insider-threat detection
system. Serves precomputed detection results over 1,393,138 user-days from the
CERT r4.2 insider threat dataset.

Source: https://github.com/Vaidehi2502/autonomous-threat-hunter

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /stats` | Corpus totals and severity breakdown |
| `GET /threats` | Ranked flagged user-days, filterable by severity, user and department |
| `GET /user/{user_id}/timeline` | Full day-by-day history for one user |
| `GET /docs` | Swagger UI |

## Note

This Space hosts the API only. The SOC analyst dashboard is deployed
separately and points at this Space via `VITE_API_BASE`.

The first request after the Space wakes from sleep takes 30–60 seconds: the
service loads the full scored table into memory on startup.
