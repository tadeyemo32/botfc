#!/bin/bash
# ─────────────────────────────────────────────
# Bot FC – Runner
# ─────────────────────────────────────────────
# Usage:
#   ./run.sh              → Test "Hello world!"
#   ./run.sh --serve      → Start API server on :5000
#   ./run.sh --football   → Run football agent directly
#   ./run.sh --football --trait=offense
# ─────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/backend/.venv"

# Create / activate venv
if [ ! -d "$VENV_DIR" ]; then
    echo "[i] Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

echo "[i] Activating virtual environment..."
source "$VENV_DIR/bin/activate"

echo "[i] Ensuring dependencies are installed..."
pip install -q paramiko flask flask-cors pyyaml

echo ""
echo "[i] Bot FC – Starting Brain..."
python "$SCRIPT_DIR/backend/brain/app.py" "$@"