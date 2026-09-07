#!/bin/bash
# start.sh — Start Mini-Jarvis WhatsApp Bot
#
# Usage:
#   chmod +x start.sh
#   ./start.sh
#
# This script:
#   1. Activates the Python venv
#   2. Starts the Python bridge in the background
#   3. Starts the Node.js WhatsApp bot (foreground)
#   4. Kills the bridge when the bot exits

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Config ────────────────────────────────────────────────────────────
VENV_PYTHON="/Users/gabeee/Programming/venv/bin/python3"
BRIDGE_SCRIPT="$SCRIPT_DIR/jarvis_bridge.py"
BOT_SCRIPT="$SCRIPT_DIR/bot.js"
BRIDGE_LOG="$SCRIPT_DIR/bridge.log"

# ── Sanity checks ─────────────────────────────────────────────────────
if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌  Python not found at: $VENV_PYTHON"
    echo "    Adjust VENV_PYTHON in this script to point to your Python executable."
    exit 1
fi

if ! command -v node &>/dev/null; then
    echo "❌  node is not installed or not on PATH"
    exit 1
fi

if ! command -v ollama &>/dev/null; then
    echo "⚠️   ollama not found on PATH — make sure it is running already"
fi

# ── Check Ollama is running ───────────────────────────────────────────
echo "🔍  Checking Ollama..."
if ! curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "⚠️   Ollama doesn't appear to be running."
    echo "    Start it in another terminal: ollama serve"
    echo ""
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# ── Start bridge ──────────────────────────────────────────────────────
echo ""
echo "🐍  Starting Python bridge..."
"$VENV_PYTHON" "$BRIDGE_SCRIPT" > "$BRIDGE_LOG" 2>&1 &
BRIDGE_PID=$!
echo "    Bridge PID : $BRIDGE_PID"
echo "    Bridge log : $BRIDGE_LOG"

# Cleanup bridge on exit
cleanup() {
    echo ""
    echo "🛑  Shutting down bridge (PID $BRIDGE_PID)..."
    kill "$BRIDGE_PID" 2>/dev/null || true
    echo "👋  Done."
}
trap cleanup EXIT INT TERM

# ── Start bot ─────────────────────────────────────────────────────────
echo ""
echo "🤖  Starting WhatsApp bot..."
echo "    (The bot will wait for the bridge to be ready before connecting)"
echo ""
node "$BOT_SCRIPT"
