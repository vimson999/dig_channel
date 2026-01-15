#!/bin/bash

# Kill background processes on exit
trap "kill 0" EXIT

echo "🚀 Starting Bili-Flow Control Center..."

# 1. Start Backend
echo "Backend: Starting FastAPI on http://localhost:8000"
./venv/bin/python -m uvicorn biliflow.web.main:app --host 0.0.0.0 --port 8000 --reload &

# 2. Start Frontend
echo "Frontend: Starting Vite on http://localhost:5173"
cd web-ui && npm run dev &

# Wait for all background processes
wait
