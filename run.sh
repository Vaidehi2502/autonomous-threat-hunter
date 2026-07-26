#!/usr/bin/env bash
# Launches the FastAPI backend and the Vite dashboard together for a demo.
set -e
cd "$(dirname "$0")"

source venv/bin/activate
uvicorn src.api:app --port 8000 &
API_PID=$!

cd dashboard
npm run dev -- --port 5173 &
DASH_PID=$!
cd ..

trap 'kill $API_PID $DASH_PID 2>/dev/null' EXIT INT TERM

echo "API:       http://localhost:8000"
echo "Dashboard: http://localhost:5173"
echo "Press Ctrl+C to stop both."

wait
