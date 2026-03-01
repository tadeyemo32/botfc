#!/bin/bash
# ─────────────────────────────────────────────
# Bot FC – Full System Runner
# ─────────────────────────────────────────────
# 1. Launches Python FastAPI server (uvicorn)
# 2. Boots Vite Frontend
# 3. Deploys Python brain to NAO robot
# ─────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
TRAIT="${1:-balanced}"

echo "================================================="
echo "   Bot FC - Full System Launcher                 "
echo "   Python Server + Vite Frontend + Python Brain  "
echo "================================================="

# Kill any lingering processes
echo "[i] Cleaning up zombie processes..."
pkill -f "vite" || true
pkill -f "uvicorn" || true
lsof -ti :5050 | xargs kill -9 2>/dev/null || true

# 1. Install Python dependencies if needed
echo "[i] Checking Python dependencies..."
python3 -m pip install --break-system-packages -q -r "$BACKEND_DIR/requirements.txt"

# 2. Boot Frontend Interface
echo "[i] Booting Vite Frontend on port 5173..."
cd "$FRONTEND_DIR" || exit
if [ ! -d "node_modules" ]; then
    echo "[i] Installing frontend dependencies..."
    npm install
fi
npm run dev &

# 3. Launch Python API Server
echo ""
echo "[i] Bot FC – Launching Python Server (uvicorn)..."
cd "$SCRIPT_DIR" || exit
python3 -m uvicorn backend.api.server:app --host 0.0.0.0 --port 5050 &
SERVER_PID=$!
sleep 2

# 4. Deploy Python Brain to Robot
echo ""
echo "[i] Deploying Python brain to NAO robot (trait: $TRAIT)..."
cd "$SCRIPT_DIR" || exit
bash deploy_brain.sh "$TRAIT"
DEPLOY_RC=$?
if [ $DEPLOY_RC -ne 0 ]; then
    echo ""
    echo "[!] Brain deployment failed (robot unreachable or SSH error)."
    echo "    Server + Frontend are still running."
    echo "    Deploy the brain manually later with:  ./deploy_brain.sh $TRAIT"
fi

# Wait for server
echo ""
echo "[i] Server PID: $SERVER_PID"
echo "[i] Press Ctrl+C to stop everything."
wait $SERVER_PID

exit 0
