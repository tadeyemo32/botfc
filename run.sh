#!/bin/bash
# ─────────────────────────────────────────────
# Bot FC – Full System Runner
# ─────────────────────────────────────────────
# 1. Compiles & launches C++ Boost server
# 2. Boots Vite Frontend
# 3. Deploys Python brain to NAO robot
# ─────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
TRAIT="${1:-balanced}"

echo "================================================="
echo "   Bot FC - Full System Launcher                 "
echo "   C++ Server + Vite Frontend + Python Brain     "
echo "================================================="

# Kill any lingering processes
echo "[i] Cleaning up zombie processes..."
pkill -f "vite" || true
pkill -f "botfc_server" || true
lsof -ti :5050 | xargs kill -9 2>/dev/null || true

# 1. Compile Backend Server
echo "[i] Compiling the Boost::Beast C++ Backend Server..."
mkdir -p "$BACKEND_DIR/build"
cd "$BACKEND_DIR/build" || exit
cmake ..
make -j4
mkdir -p "$SCRIPT_DIR/bin"
cp botfc_server "$SCRIPT_DIR/bin/"

# 2. Boot Frontend Interface
echo "[i] Booting Vite Frontend on port 5173..."
cd "$FRONTEND_DIR" || exit
if [ ! -d "node_modules" ]; then
    echo "[i] Installing frontend dependencies..."
    npm install
fi
npm run dev &

# 3. Launch Backend Orchestrator (background)
echo ""
echo "[i] Bot FC – Launching C++ Server (botfc_server)..."
cd "$SCRIPT_DIR/bin" || exit
./botfc_server &
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