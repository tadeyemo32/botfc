#!/bin/bash

# Ensure context/pepper exists
CONTEXT_REPO="https://github.com/RGU-Computing/pepper"
CONTEXT_DIR="$(dirname "$0")/context/pepper"

if [ ! -d "$CONTEXT_DIR" ]; then
    echo "[i] Context repository not found. Cloning it now..."
    mkdir -p "$(dirname "$0")/context"
    git clone "$CONTEXT_REPO" "$CONTEXT_DIR"
fi

# Navigate to the backend directory
cd "$(dirname "$0")/backend" || exit

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "[!] Python 3 is not installed. Please install Python 3 to run the backend."
    exit 1
fi

# Setup Virtual Environment
VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "[i] First run detected. Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# Activate virtual environment
echo "[i] Activating virtual environment..."
source "$VENV_DIR/bin/activate"

# Install dependencies if requirements.txt exists
if [ -f "requirements.txt" ]; then
    echo "[i] Ensuring dependencies are installed..."
    pip install -r requirements.txt -q
fi

# Inform user of connection test
echo "[i] Bot FC - Starting Backend Connection Test..."
echo "[i] Reading configuration from config/config.toml"

# Run the Flask backend
python3 app.py

# Deactivate when done
deactivate

# Keep terminal open if needed (useful if launching directly via double-click on some OS)
# read -p "Press enter to continue"
