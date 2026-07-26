# Container image for the FastAPI backend.
# Works on Cloud Run, Fly.io, Hugging Face Spaces, Railway or plain Docker.
#
#   docker build -t threat-hunter-api .
#   docker run -p 8000:8000 threat-hunter-api
#
# Render users do not need this -- see render.yaml.

FROM python:3.13-slim

# Hugging Face Spaces expect UID 1000; running unprivileged is good practice on
# every host, so do it everywhere rather than only there.
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Dependencies first so the layer caches across source edits.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser src/ ./src/

# Only the serving-optimised table (1.3 MB, sorted by user, 20k-row groups).
# The pipeline intermediates in outputs/ would add ~36 MB for nothing, and
# detect.py's raw output has row groups too large to query per user in a small
# container -- see src/prepare_serving.py.
COPY --chown=appuser:appuser outputs/threat_scores_serving.parquet ./outputs/threat_scores_serving.parquet

USER appuser

ENV PORT=8000
EXPOSE 8000

# Single worker on purpose: each worker loads its own ~293 MB copy of the table.
CMD ["sh", "-c", "uvicorn src.api:app --host 0.0.0.0 --port ${PORT} --workers 1"]
