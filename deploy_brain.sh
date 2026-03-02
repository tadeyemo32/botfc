#!/bin/bash
# ─────────────────────────────────────────────
# Bot FC – Deploy Python Brain to NAO Robot
# ─────────────────────────────────────────────
# Reads robot.yaml config, copies botfc_brain.py
# to the robot via SCP, and starts it over SSH.
# ─────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$SCRIPT_DIR/config/robot.yaml"
BRAIN="$SCRIPT_DIR/backend/brain/botfc_brain.py"

# Parse robot.yaml (simple grep-based, works without pyyaml)
ROBOT_IP=$(grep 'ip:' "$CONFIG" | head -1 | sed 's/.*"\(.*\)".*/\1/')
ROBOT_PORT=$(grep 'port:' "$CONFIG" | head -1 | awk '{print $NF}')
ROBOT_USER=$(grep 'username:' "$CONFIG" | sed 's/.*"\(.*\)".*/\1/')
ROBOT_PASS=$(grep 'password:' "$CONFIG" | sed 's/.*"\(.*\)".*/\1/')
SERVER_PORT=$(grep -A1 'server:' "$CONFIG" | grep 'port:' | awk '{print $NF}')

# Default values
SERVER_PORT="${SERVER_PORT:-5050}"
TRAIT="${1:-balanced}"

echo "================================================="
echo "  Bot FC – Python Brain Deployer"
echo "  Robot: $ROBOT_USER@$ROBOT_IP:$ROBOT_PORT"
echo "  Trait: $TRAIT"
echo "================================================="

# Detect server IP (from robot's perspective, the IP of this machine
# on the same subnet as the robot)
SERVER_IP=$(ifconfig | grep -A4 "$(echo "$ROBOT_IP" | cut -d. -f1-3)" | grep 'inet ' | awk '{print $2}' | head -1)
if [ -z "$SERVER_IP" ]; then
    # Fallback: try en0
    SERVER_IP=$(ifconfig en0 2>/dev/null | grep 'inet ' | awk '{print $2}')
fi
if [ -z "$SERVER_IP" ]; then
    SERVER_IP="127.0.0.1"
fi
echo "[i] Server IP (for robot): $SERVER_IP"

# Pre-flight: check robot is reachable
echo "[i] Checking robot connectivity..."
if ! ping -c 1 -W 3 "$ROBOT_IP" > /dev/null 2>&1; then
    echo "[!] Robot at $ROBOT_IP is NOT reachable."
    echo "    Make sure you are on the same network as the robot."
    exit 2
fi
echo "[✓] Robot is online."

# Say hello world on the robot as connection test
echo "[i] Robot says Hello World..."
sshpass -p "$ROBOT_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 "$ROBOT_USER@$ROBOT_IP" \
    "export PYTHONPATH=/opt/aldebaran/lib/python2.7/site-packages && python -c 'from naoqi import ALProxy; ALProxy(\"ALTextToSpeech\", \"127.0.0.1\", $ROBOT_PORT).say(\"Hello World\")'" \
    && echo "[✓] TTS confirmed." \
    || echo "[!] TTS test failed (non-critical, continuing...)"

# 1. Kill any existing brain on the robot
echo "[i] Stopping any existing brain on robot..."
sshpass -p "$ROBOT_PASS" ssh -o StrictHostKeyChecking=no "$ROBOT_USER@$ROBOT_IP" \
    "pkill -f botfc_brain.py 2>/dev/null || true"

# 2. Copy brain to robot
echo "[i] Deploying botfc_brain.py to $ROBOT_USER@$ROBOT_IP:/home/nao/..."
sshpass -p "$ROBOT_PASS" scp -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
    "$BRAIN" "$ROBOT_USER@$ROBOT_IP:/home/nao/botfc_brain.py"

if [ $? -ne 0 ]; then
    echo "[!] SCP failed. Check credentials or install sshpass (brew install sshpass)."
    exit 1
fi

# 3. Launch brain on robot via SSH
echo "[i] Starting Python brain on robot..."
echo "    Command: python /home/nao/botfc_brain.py --ip=$ROBOT_IP --pport=$ROBOT_PORT --trait=$TRAIT --server-ip=$SERVER_IP --server-port=$SERVER_PORT"

sshpass -p "$ROBOT_PASS" ssh -o StrictHostKeyChecking=no "$ROBOT_USER@$ROBOT_IP" \
    "export PYTHONPATH=/opt/aldebaran/lib/python2.7/site-packages ; \
    nohup python /home/nao/botfc_brain.py \
        --ip=127.0.0.1 \
        --pport=$ROBOT_PORT \
        --trait=$TRAIT \
        --server-ip=$SERVER_IP \
        --server-port=$SERVER_PORT \
        > /home/nao/botfc_brain.log 2>&1 &"

echo ""
echo "================================================="
echo "  Brain deployed and running!"
echo "  View logs: ssh $ROBOT_USER@$ROBOT_IP 'tail -f /home/nao/botfc_brain.log'"
echo "  Stop:      ssh $ROBOT_USER@$ROBOT_IP 'pkill -f botfc_brain.py'"
echo "================================================="
