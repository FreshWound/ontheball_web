#!/usr/bin/env bash
# Launches ontheball_web. Run ./install.sh first if you haven't already.
set -e

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "No .venv found — run ./install.sh first."
    exit 1
fi

source .venv/bin/activate
python3 ontheball_web.py
