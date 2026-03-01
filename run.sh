#!/bin/bash
# ─────────────────────────────────────────────
# Bot FC – Full System Runner  (v2)
# ─────────────────────────────────────────────
# 1. Compiles & launches C++ Boost server
# 2. Starts Host Vision Server (OpenCV)
# 3. Boots Vite Frontend
# 4. Deploys Python brain v2 to NAO robot
# ─────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
BRAIN_DIR="$BACKEND_DIR/src/brain"
TRAIT="${1:-balanced}"

cleanup() {
    echo ""
    echo "[i] Shutting down all services..."
    pkill -f "host_vision.py" 2>/dev/null || true
    pkill -f "vite" 2>/dev/null || true
    pkill -f "botfc_server" 2>/dev/null || true
    lsof -ti :5050 | xargs kill -9 2>/dev/null || true
    lsof -ti :5060 | xargs kill -9 2>/dev/null || true
    pkill -f "sshpass.*tail.*botfc_brain.log" 2>/dev/null || true
    exit 0
}
trap cleanup SIGINT SIGTERM

echo "═══════════════════════════════════════════════════"
echo "   Bot FC v2 — Full System Launcher"
echo "   C++ Server + Host Vision + Frontend + Brain"
echo "═══════════════════════════════════════════════════"
echo ""

# Kill any lingering processes
echo "[i] Cleaning up zombie processes..."
pkill -f "host_vision.py" 2>/dev/null || true
pkill -f "vite" 2>/dev/null || true
pkill -f "botfc_server" 2>/dev/null || true
lsof -ti :5050 | xargs kill -9 2>/dev/null || true
lsof -ti :5060 | xargs kill -9 2>/dev/null || true

# 1. Compile Backend Server
echo "[i] Compiling the Boost::Beast C++ Backend Server..."
mkdir -p "$BACKEND_DIR/build"
cd "$BACKEND_DIR/build" || exit
cmake ..
make -j4
mkdir -p "$SCRIPT_DIR/bin"
cp botfc_server "$SCRIPT_DIR/bin/"

# 2. Start Host Vision Server (OpenCV processing)
echo ""
echo "[i] Starting Host Vision Server (OpenCV on port 5060)..."
cd "$SCRIPT_DIR" || exit
python3 "$BRAIN_DIR/host_vision.py" --port 5060 &
VISION_PID=$!
echo "    Vision PID: $VISION_PID"
sleep 1

# 3. Boot Frontend Interface
echo ""
echo "[i] Booting Vite Frontend on port 5173..."
cd "$FRONTEND_DIR" || exit
if [ ! -d "node_modules" ]; then
    echo "[i] Installing frontend dependencies..."
    npm install
fi
npm run dev &

# 4. Launch Backend C++ Server
echo ""
echo "[i] Bot FC – Launching C++ Server on port 5050..."
cd "$SCRIPT_DIR/bin" || exit
./botfc_server &
SERVER_PID=$!
sleep 2

# 5. Deploy Python Brain v2 to Robot
echo ""
echo "[i] Deploying Python brain v2 to NAO robot (trait: $TRAIT)..."
cd "$SCRIPT_DIR" || exit
bash deploy_brain.sh "$TRAIT"
DEPLOY_RC=$?
if [ $DEPLOY_RC -ne 0 ]; then
    echo ""
    echo "[!] Brain deployment failed (robot unreachable or SSH error)."
    echo "    Server + Frontend + Vision are still running."
    echo "    Deploy the brain manually later with:  ./deploy_brain.sh $TRAIT"
fi

# Status summary
echo ""
echo "═══════════════════════════════════════════════════"
echo "   All systems running:"
echo "   ├── C++ Server:    port 5050  (PID $SERVER_PID)"
echo "   ├── Host Vision:   port 5060  (PID $VISION_PID)"
echo "   ├── Vite Frontend: port 5173"
echo "   └── Robot Brain:   deployed (trait: $TRAIT)"
echo "═══════════════════════════════════════════════════"
echo ""

if [ $DEPLOY_RC -eq 0 ]; then
    echo "[i] Streaming live logs from Robot (botfc_brain.log)..."
    echo "    (Prefix: [BOT])"
    CONFIG="$SCRIPT_DIR/config/robot.yaml"
    ROBOT_IP=$(grep 'ip:' "$CONFIG" | head -1 | sed 's/.*"\(.*\)".*/\1/')
    ROBOT_USER=$(grep 'username:' "$CONFIG" | sed 's/.*"\(.*\)".*/\1/')
    ROBOT_PASS=$(grep 'password:' "$CONFIG" | sed 's/.*"\(.*\)".*/\1/')
    
    sshpass -p "$ROBOT_PASS" ssh -o StrictHostKeyChecking=no "$ROBOT_USER@$ROBOT_IP" "tail -F /home/nao/botfc_brain.log" | sed -e 's/^/[BOT] /' &
fi

echo "[i] Press Ctrl+C to stop everything."
wait $SERVER_PID

exit 0