#!/usr/bin/env bash
# One-time setup for ontheball_web. Safe to re-run — skips venv creation
# if .venv already exists, just re-installs/updates packages into it.
set -e

cd "$(dirname "$0")"

if ! python3 -c "import venv" 2>/dev/null; then
    echo "python3-venv not found — installing (needs sudo)..."
    sudo apt-get update
    sudo apt-get install -y python3-venv
fi

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment in .venv/ ..."
    python3 -m venv .venv
else
    echo ".venv already exists, reusing it."
fi

source .venv/bin/activate

echo "Installing/updating dependencies from requirements.txt ..."
pip install --upgrade pip
pip install -r requirements.txt

echo
echo "Done. If PyQt6 complains about a missing xcb-cursor plugin the first"
echo "time you run it, that's a system package, not Python — install it"
echo "outside the venv with:"
echo "  sudo apt-get install -y libxcb-cursor0"
echo
echo "Setup complete — run ./run.sh to start ontheball."
