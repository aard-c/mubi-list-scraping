#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
CHROME_BIN="${CHROME_BIN:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
CHROME_DEBUG_DIR="${CHROME_DEBUG_DIR:-/Users/ardacildan/ChromeDebug}"
CHROME_DEBUG_PORT="${CHROME_DEBUG_PORT:-9222}"

echo "[1/4] Installing Python requirements..."
"$PYTHON_BIN" -m pip install -r requirements.txt

echo "[2/4] Installing Chromium for Playwright..."
"$PYTHON_BIN" -m playwright install chromium

echo "[3/4] Scraping MubiFinder Turkey availability..."
"$PYTHON_BIN" mubifinder-tr.py

echo "[4/4] Restarting Chrome in remote-debug mode..."
pkill -x "Google Chrome" 2>/dev/null || true
sleep 2
"$CHROME_BIN" \
  --remote-debugging-port="$CHROME_DEBUG_PORT" \
  --user-data-dir="$CHROME_DEBUG_DIR" &

echo
echo "Chrome is launching in debug mode."
echo "Please log into Letterboxd in that window."
read -r -p "Press Enter here once you're logged in to continue with the sync..."

"$PYTHON_BIN" letterboxd_sync.py
