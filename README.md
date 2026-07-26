# Autonomous Threat Hunter

**Problem Statement:** Autonomous Threat Hunter for Insider Attacks — build a
system that autonomously surfaces suspicious insider behavior (logons, device
usage, off-hours activity) from raw enterprise logs, without relying on a
single black-box model that security teams can't trust or explain.

Built solo in a 24-hour hackathon on the CERT Insider Threat dataset (r4.2,
~4,000 users, ~1.5 years of logon + device activity).

## Live deployment

| | |
| --- | --- |
| **Dashboard** | <https://threat-hunter-dashboard.onrender.com> |
| **API** | <https://threat-hunter-api.onrender.com> |
| **API docs** | <https://threat-hunter-api.onrender.com/docs> |

Hosted on Render's free tier, which sleeps after 15 minutes of inactivity. The
first request after a sleep takes ~15 seconds while the scored table loads;
everything after that responds in well under a second.

## Architecture

```
 ┌───────────────┐     ┌────────────────┐     ┌──────────────────┐
 │  data/*.csv   │────▶│ src/load_data  │────▶│ outputs/*_clean  │
 │ logon/device/ │     │      .py       │     │    .parquet      │
 │   users       │     └────────────────┘     └────────┬─────────┘
 └───────────────┘                                      │
                                                         ▼
                                              ┌──────────────────────┐
                                              │   src/features.py     │
                                              │  per-user-per-day     │
                                              │  feature table        │
                                              └──────────┬────────────┘
                                                          ▼
                                              ┌──────────────────────┐
                                              │   src/detect.py        │
                                              │  per-user z-score      │
                                              │  baselines (primary)   │
                                              │       +                │
                                              │  Isolation Forest       │
                                              │  (secondary signal)     │
                                              │       ↓                 │
                                              │  weighted severity      │
                                              │  score + reasons        │
                                              └──────────┬───────────── ┘
                                                          ▼
                                        outputs/threat_scores.parquet
                                        (1.39M user-days, scored + ranked)
                                                          │
                                                          ▼
                                              ┌──────────────────────┐
                                              │    src/api.py          │
                                              │  FastAPI, serves       │
                                              │  precomputed parquet   │
                                              │  /stats /threats       │
                                              │  /user/{id}/timeline   │
                                              └──────────┬───────────── ┘
                                                          ▼  (CORS, localhost:8000)
                                              ┌──────────────────────┐
                                              │  dashboard/ (Vite +   │
                                              │  React + recharts)    │
                                              │  SOC analyst console  │
                                              └──────────────────────┘
```

## Key design decision: ML as a secondary signal, never the sole verdict-driver

Every user-day is scored against **that user's own historical behavior** —
per-user z-scores on logon count, off-hours logons, distinct PCs used,
USB connects, and off-hours USB activity — not a global population baseline.
A stockroom clerk and a physicist have very different "normal," so comparing
either to a company-wide average would drown real anomalies in false
positives (or vice versa).

An Isolation Forest runs on the same feature set as a **secondary,
multivariate signal**: it can catch combinations of subtly-off behavior that
simple per-feature z-scores miss. But it never drives a verdict on its own —
it only adds weight on top of the explainable, rule-based z-score flags. Every
flagged day carries a human-readable reason string (e.g. *"off hours logon
count unusually high for this user (z=4.8)"*), so an analyst never has to
trust an opaque anomaly score. This avoids the classic false-positive trap of
handing a SOC team a black-box model they can't interrogate.

Severity buckets (Normal / Low / Medium / High / Critical) come from a
weighted sum of triggered rules + the Isolation Forest bonus — see
`src/detect.py` for exact weights and thresholds.

### Every reason is a counterfactual, not just an observation

Each triggered rule states the exact value at which it would stop firing, e.g.
*"off hours usb count unusually high for this user (z=9.5) — would not flag
below 1.1 (actual: 4)"*. It costs nothing extra to compute — it's the same
per-user mean/std already used for the z-score — but it turns a score into
something an analyst can act on directly.

### A third, independent signal: behavioral drift

Per-day z-scores have a structural blind spot: someone who escalates
*gradually*, never spiking hard enough on any single day to cross the
threshold, sails through untouched. `src/detect.py`'s `compute_drift_signal`
compares a trailing 14-day rolling average against that same per-user
baseline instead of one day, using the standard error of the mean rather than
the noisier single-day std — so a smaller, sustained rise can clear a
statistically meaningful bar. It **does not feed into `severity_score`**, so
every severity figure below stays exact; it's reported alongside severity as
its own `drift_flag`/`drift_reasons` fields, surfaced in the dashboard behind
a "Show drift-only days" toggle. On the committed dataset it catches **3,989**
user-days that severity alone rates Normal.

## Current results

- **1,393,138** user-days analyzed across **4,000** users (2010-01-02 to 2011-06-01)
- **50,520** flagged user-days (3.6%)
- Severity breakdown: **377 Critical**, **1,641 High**, **47,600 Medium**, **902 Low**
- **3,989** additional user-days caught only by drift (Normal severity, sustained trend)

## How to run

Requires Python 3.12+ and Node 18+. (`requirements.txt` pins `numpy==2.5.1`,
which is published for Python 3.12 and above only.)

**You do not need the raw dataset.** The scored results in `outputs/*.parquet`
are committed, so the API and dashboard run straight from a fresh clone. Steps
2 and 3 below are only for rebuilding the pipeline from scratch.

### 1. One-time setup

```bash
# macOS / Linux
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cd dashboard && npm install && cd ..
```

```powershell
# Windows (PowerShell)
python -m venv venv; .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd dashboard; npm install; cd ..
```

### 2. Run everything

```bash
./run.sh      # macOS / Linux
```

```powershell
.\run.ps1     # Windows
```

Both launch the API on `http://localhost:8000` and the dashboard on
`http://localhost:5173`, and shut both down on Ctrl+C. To start them
individually instead:

```bash
uvicorn src.api:app --reload --port 8000   # terminal 1
cd dashboard && npm run dev                # terminal 2
```

API docs (Swagger UI) are at `http://localhost:8000/docs` once the backend is up.

### 3. Rebuilding the pipeline (optional)

Only needed if you want to regenerate `outputs/` yourself. The raw CERT
Insider Threat dataset is not redistributed here — download **r4.2** from
[Carnegie Mellon's CERT division](https://kilthub.cmu.edu/articles/dataset/Insider_Threat_Test_Dataset/12841247)
and place `logon.csv`, `device.csv` and `users.csv` in `data/`.

```bash
python src/load_data.py       # raw CSV       -> outputs/*_clean.parquet
python src/features.py        # clean parquet -> outputs/user_day_features.parquet
python src/detect.py          # features      -> outputs/threat_scores.parquet
python src/prepare_serving.py # scores        -> outputs/threat_scores_serving.parquet
```

`prepare_serving.py` is a lossless re-encode for the API — it sorts by user and
writes small row groups so a single-user lookup stays cheap. It changes no
score, severity or reason.

Note that re-running `detect.py` overwrites the committed results the figures
in this README refer to.

## Tests

```bash
pip install pytest
pytest
```

Covers the detection engine (per-user z-score baselines, rule weights, severity
bucket boundaries, the "ML never drives a verdict alone" guarantee, the
counterfactual thresholds, and the drift signal — including the motivating
case where a sustained rise is caught even though no single day's z-score
crosses the threshold) and all three API endpoints against the real committed
results.

## Configuration

Everything runs with no configuration. For deployment, copy `.env.example` to
`.env`:

| Variable | Side | Purpose |
| --- | --- | --- |
| `CORS_ALLOW_ORIGINS` | backend | Extra allowed origins, comma-separated. Local dev origins are always permitted. |
| `API_KEYS` | backend | Comma-separated `name:key` pairs. When set, requires a matching `X-API-Key` header on every data endpoint, and attributes audit log entries to that name. Unset = open. |
| `RATE_LIMIT_REQUESTS` | backend | Requests per client per window (default 120). `0` disables. |
| `RATE_LIMIT_WINDOW_SECONDS` | backend | Window length in seconds (default 60). |
| `VITE_API_BASE` | frontend | API base URL, read at build time. Defaults to `http://localhost:8000`. |

## Security

The API rate-limits per client, sets hardening response headers, allowlists
CORS origins explicitly, and can require an API key. It is read-only — no
endpoint mutates state.

The public demo runs unauthenticated on purpose: CERT r4.2 is synthetic data
with no real employees in it. [SECURITY.md](SECURITY.md) documents the controls,
how to enable authentication, and — candidly — what this would still need
before being pointed at real logs.

## Deployment

The two halves deploy independently. Do the backend first, since the frontend
needs its URL at build time.

### Backend

The `Dockerfile` builds a self-contained image and is the most portable option
— Cloud Run, Hugging Face Spaces, Fly.io, Railway or plain Docker. It copies
only `outputs/threat_scores.parquet`, since the other parquet files are
pipeline intermediates the API never reads.

```bash
docker build -t threat-hunter-api .
docker run -p 8000:8000 --memory=768m threat-hunter-api
```

`render.yaml` is a ready Render blueprint and the recommended free option:
**New → Blueprint → select this repo**. It pins Python 3.13, one worker, and a
`/` health check, and fits Render's free 512 MB instance.

`deploy/huggingface/` holds a Space card and `publish.ps1` for Hugging Face.
Note that Docker Spaces now require a PRO subscription — only Static Spaces are
free — so this path costs money unless that changes.

**Sizing — measured, not estimated.** The running container uses **~330 MiB**.
Verified by running the image under hard memory caps:

| Cap | Result |
| --- | --- |
| 256 MB | OOM-killed |
| 384 MB | boots, serves every endpoint |
| 512 MB | boots, comfortable headroom (330 MiB used) |

So it fits a 512 MB free instance. Timeline requests measured 104 ms cold,
31 ms for a subsequent different user, 26 ms cached.

This depends on `outputs/threat_scores_serving.parquet` being present. The API
falls back to `detect.py`'s raw `threat_scores.parquet` if it is missing, but
that file's first row group holds 1,048,576 rows, and since parquet is read at
row-group granularity a single user lookup then decodes all of them — pushing
the requirement to ~640 MB. Regenerate the serving file with
`python src/prepare_serving.py` after any re-run of `detect.py`.

Run a single worker regardless of host: each worker loads its own copy of the
flagged table. Scale with more instances behind a load balancer, not more
workers per instance — the service is read-only and stateless, so instances
need no coordination.

### Frontend

The same `render.yaml` blueprint also declares the dashboard as a free Render
static site, so both halves deploy from one sync with no manual configuration:
`VITE_API_BASE` and `CORS_ALLOW_ORIGINS` are set in the blueprint and already
point at each other.

`VITE_API_BASE` is inlined by Vite at **build** time, not read at run time — so
changing it requires a rebuild, not a restart.

`dashboard/vercel.json` is kept for deploying the frontend to Vercel instead.
In that case set Root Directory to `dashboard`, set `VITE_API_BASE` before the
first build, and update `CORS_ALLOW_ORIGINS` on the API to the Vercel origin —
an exact match including scheme and without a trailing slash, or the browser
blocks every request. Local development needs none of this; `localhost:5173`
is always permitted.

## Suggested demo walkthrough

Three standout cases from `outputs/top_threats_preview.csv`, chosen because
each tells a different, concrete story rather than a one-off statistical blip:

1. **`EYD2871` — ProductionLineWorker, Assembly.** The strongest "sustained
   insider escalation" narrative: 52 of 358 tracked days flagged (9 Critical, 21
   High, 22 Medium), recurring across the *entire* monitoring window (Apr
   2010 through May 2011), not a single incident. Same signature every time —
   off-hours logons, off-hours USB activity, and unusually many distinct PCs
   for a role that should have a fixed workstation. Pull up this user's
   drill-down and point at the repeated spikes on the timeline chart — this
   is the "look, it's not a fluke" moment.

2. **`ACM2278` — Salesman, Sales.** Two nearly-identical Critical days
   twelve days apart (2010-08-13 and 2010-08-25), both score 11.0, both
   driven by heavy off-hours USB connects (z=4.7 and z=9.5) plus off-hours
   logons. A salesperson repeatedly moving data via USB outside business
   hours is a textbook data-exfiltration-before-departure pattern — good
   for framing "what would you investigate first."

3. **`HMY0235` — ComputerProgrammer, SoftwareManagement.** Appears 3 times
   in the top 20 (2011-01-10, 2011-03-24, 2011-04-28) with consistent
   distinct-PC and off-hours-USB elevation. Useful as the "different role,
   same detection logic" contrast to EYD2871 and ACM2278 — shows the
   per-user-baseline approach generalizes across very different job
   functions instead of overfitting to one department's behavior.

4. **`ACV1946` — SoftwareDeveloper, WebSoftware — the drift signal's case.**
   Check "Show drift-only days" and search this user: 25 of their 359 days are
   flagged *only* by the temporal drift signal, rated completely Normal by
   severity. Off-hours USB activity crept from a personal baseline of ~0 up to
   a sustained trailing-14-day average, never spiking hard enough on any
   single day to trip a z-score. This is the concrete answer to "what does
   drift catch that severity can't" — pull up the drift strip under this
   user's chart and point at the solid violet block with no corresponding
   bar in the severity chart above it.

Recommended flow: open the dashboard → show the stat bar (point at "Drift
Detected") and full ranked table → filter to Critical → search `EYD2871` →
click into the drill-down → narrate the repeat pattern → briefly repeat for
`ACM2278` to show the USB-exfiltration variant → finish on `ACV1946` with
"Show drift-only days" checked, to demonstrate the case severity alone would
have missed entirely.

**For a ~60-second demo video specifically**, cut it down to five beats in
this order — this is the set that actually needs to be on screen; everything
else (architecture, scalability) is already covered by the deck, not the video:

1. Stat bar — 4,000 users, 1.39M user-days, severity breakdown.
2. The **Drift Detected** card, named explicitly — it's the answer to "what's
   innovative here."
3. Filter to Critical → search `EYD2871` → drill-down → the repeated-spike
   timeline chart.
4. One reason string with its counterfactual — *"would not flag below X
   (actual: Y)"* — and a sentence on why that beats a bare anomaly score.
5. Check "Show drift-only days" → search `ACV1946` → point at the violet
   drift strip where the severity chart above it shows nothing. This is the
   single most important beat: visual proof of catching what the primary
   system structurally cannot.

`ACM2278` and `HMY0235` are worth keeping only if there's time to spare.

## Project structure

```
data/                    raw CERT CSVs (logon, device, users) - not committed
outputs/                 cleaned + feature + scored parquet files (committed)
src/load_data.py         raw CSV -> cleaned parquet
src/features.py          cleaned parquet -> per-user-per-day features
src/detect.py            feature table -> severity-scored threat table
src/prepare_serving.py   lossless re-encode of the scored table for serving
src/api.py               FastAPI backend serving the scored table
dashboard/               Vite + React + recharts SOC analyst dashboard
tests/                   pytest suite for the detection engine and the API
run.sh / run.ps1         launch API + dashboard together (macOS-Linux / Windows)
render.yaml              Render blueprint for the backend
Dockerfile               container image for the backend
dashboard/vercel.json    Vercel config for the frontend
.github/workflows/ci.yml runs the test suite and a dashboard build on push
```

## License

MIT — see [LICENSE](LICENSE).

The CERT Insider Threat dataset is not included and remains under its own terms.
