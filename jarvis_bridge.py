#!/usr/bin/env python3
"""
jarvis_bridge.py — Persistent HTTP Bridge for Mini-Jarvis WhatsApp Bot

Architecture:
    bot.js  --HTTP POST /chat-->  this server  -->  Ollama (llama3:8b)
                                       |
                                  memory.py
                                  personality.py (preetam_v1, hard-locked)
                                  preferences.py

Runs as a long-lived Flask server so Python stays warm — no cold-start per message.
Supports per-sender memory sessions so the bot can handle multiple contacts.

Usage:
    python jarvis_bridge.py
    # Listens on http://127.0.0.1:5001

Endpoints:
    GET  /health          — liveness probe (used by bot.js on startup)
    POST /chat            — process a WhatsApp message
        Body: { "text": "...", "sender": "...", "chat_id": "..." }
        Returns: { "reply": "...", "ok": true }
"""

import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime
from threading import Lock

import requests
from flask import Flask, jsonify, request

# ── Ensure we can import sibling modules ─────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    ACTIVE_PERSONALITY,
    BRIDGE_HOST,
    BRIDGE_PORT,
    MAX_CURRENT_MESSAGES,
    MAX_PAST_SNIPPETS,
    MODEL_NAME,
    OLLAMA_API_URL,
    SYSTEM_IDENTITY,
)
from memory import MemoryManager
from personality import PersonalityManager
from preferences import PreferencesManager

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("jarvis_bridge")

# ── Flask app ────────────────────────────────────────────────────────
app = Flask(__name__)

# ── Global personality (shared, read-only after init) ────────────────
# Locks to preetam_v1 on startup — never changes at runtime.
personality: PersonalityManager | None = None

# ── Per-sender state ─────────────────────────────────────────────────
# Each sender gets their own MemoryManager and PreferencesManager.
# Keyed by normalized sender ID string.
_sender_memory: dict[str, MemoryManager] = {}
_sender_prefs: dict[str, PreferencesManager] = {}
_state_lock = Lock()

# ── Duplicate message prevention ─────────────────────────────────────
# Cache of recently processed message hashes (hash -> timestamp).
_processed_hashes: dict[str, float] = {}
_DEDUP_TTL_SECONDS = 10  # ignore exact same message within 10 seconds

# ── Ollama health ─────────────────────────────────────────────────────


def check_ollama() -> bool:
    """Return True if Ollama is up and the model is available."""
    try:
        resp = requests.get(f"{OLLAMA_API_URL}/api/tags", timeout=5)
        if resp.status_code != 200:
            return False
        models = [m["name"] for m in resp.json().get("models", [])]
        if not any(MODEL_NAME in m for m in models):
            log.error(
                "Model '%s' not found in Ollama. Available: %s",
                MODEL_NAME,
                ", ".join(models) or "none",
            )
            return False
        return True
    except Exception as exc:
        log.error("Ollama unreachable: %s", exc)
        return False


# ── State helpers ────────────────────────────────────────────────────


def _get_sender_memory(sender_id: str) -> MemoryManager:
    """Get or create a MemoryManager for this sender."""
    with _state_lock:
        if sender_id not in _sender_memory:
            log.info("Creating new memory session for sender: %s", sender_id)
            _sender_memory[sender_id] = MemoryManager()
        return _sender_memory[sender_id]


def _get_sender_prefs(sender_id: str) -> PreferencesManager:
    """Get or create a PreferencesManager for this sender."""
    with _state_lock:
        if sender_id not in _sender_prefs:
            _sender_prefs[sender_id] = PreferencesManager()
        return _sender_prefs[sender_id]


# ── System prompt builder ────────────────────────────────────────────


def build_system_prompt(
    prefs: PreferencesManager,
    memory: MemoryManager,
    user_message: str,
) -> str:
    """
    Construct the full system prompt in layers:
    1. Base identity (Preetam-specific, from config)
    2. Personality (preetam_v1 custom prompt + rules + examples)
    3. User preferences (name, language, etc.)
    4. Past conversation context (relevant snippets)
    """
    assert personality is not None, "Personality not initialized"

    parts = [SYSTEM_IDENTITY]

    # Layer 2: Personality
    parts.append(f"\n{personality.build_personality_prompt()}")

    # Layer 3: User preferences
    pref_prompt = prefs.get_preferences_prompt()
    if pref_prompt:
        parts.append(f"\n{pref_prompt}")

    # Layer 4: Relevant past context
    past_snippets = memory.search_past_sessions(user_message)
    if past_snippets:
        parts.append("\nRelevant things from past conversations:")
        for snippet in past_snippets:
            parts.append(f"  {snippet}")

    return "\n".join(parts)


# ── Ollama call ──────────────────────────────────────────────────────


def call_ollama(system_prompt: str, messages: list[dict]) -> str:
    """
    Call Ollama chat API (non-streaming).
    Returns the response string, or raises on error.
    """
    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "stream": False,
    }

    resp = requests.post(
        f"{OLLAMA_API_URL}/api/chat",
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()

    data = resp.json()
    reply = data.get("message", {}).get("content", "").strip()
    return reply


# ── Duplicate detection ──────────────────────────────────────────────


def _is_duplicate(sender: str, text: str) -> bool:
    """Return True if this exact (sender, text) was processed very recently."""
    key = hashlib.md5(f"{sender}:{text}".encode()).hexdigest()
    now = time.time()

    # Expire old entries
    expired = [k for k, ts in _processed_hashes.items() if now - ts > _DEDUP_TTL_SECONDS]
    for k in expired:
        del _processed_hashes[k]

    if key in _processed_hashes:
        return True

    _processed_hashes[key] = now
    return False


# ── Routes ───────────────────────────────────────────────────────────


@app.route("/health", methods=["GET"])
def health():
    """Liveness probe. Returns 200 when bridge is ready."""
    ollama_ok = check_ollama()
    return jsonify(
        {
            "ok": True,
            "personality": personality.active_personality_name if personality else None,
            "ollama": ollama_ok,
            "model": MODEL_NAME,
        }
    )


@app.route("/chat", methods=["POST"])
def chat():
    """
    Process an incoming WhatsApp message.

    Expected JSON body:
        {
            "text":    "message content",
            "sender":  "1234567890@c.us",   // normalized sender JID
            "chat_id": "1234567890@c.us"    // chat JID (may differ for groups)
        }

    Returns:
        { "ok": true, "reply": "response text" }
        { "ok": false, "error": "reason" }
    """
    if personality is None:
        log.error("Personality not initialized — bridge not ready")
        return jsonify({"ok": False, "error": "bridge not ready"}), 503

    body = request.get_json(silent=True)
    if not body:
        return jsonify({"ok": False, "error": "invalid JSON body"}), 400

    text = (body.get("text") or "").strip()
    sender = (body.get("sender") or "unknown").strip()
    chat_id = (body.get("chat_id") or sender).strip()

    if not text:
        return jsonify({"ok": False, "error": "empty message text"}), 400

    # Duplicate guard
    if _is_duplicate(sender, text):
        log.warning("Duplicate message from %s — skipping: %s", sender, text[:60])
        return jsonify({"ok": False, "error": "duplicate message skipped"}), 200

    log.info("💬 [%s] %s", sender, text[:80])

    # Per-sender state
    mem = _get_sender_memory(sender)
    prefs = _get_sender_prefs(sender)

    # Detect preferences
    pref_note = prefs.detect_and_store(text)
    if pref_note:
        log.info("📝 Preference detected: %s", pref_note)

    # Build system prompt
    system_prompt = build_system_prompt(prefs, mem, text)

    # Add user message to memory
    mem.add_message("user", text)

    # Build context window for Ollama
    context_messages = mem.get_context_messages()

    # Call Ollama
    try:
        reply = call_ollama(system_prompt, context_messages)
    except requests.Timeout:
        log.error("Ollama timed out for message: %s", text[:60])
        return jsonify({"ok": False, "error": "ollama timeout"}), 504
    except requests.ConnectionError as exc:
        log.error("Ollama connection error: %s", exc)
        return jsonify({"ok": False, "error": "ollama unreachable"}), 502
    except Exception as exc:
        log.error("Ollama error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500

    if not reply:
        log.warning("Ollama returned empty reply for: %s", text[:60])
        return jsonify({"ok": False, "error": "empty ollama response"}), 500

    # Save assistant reply to memory
    mem.add_message("assistant", reply)

    log.info("🤖 [%s] %s", sender, reply[:80])
    return jsonify({"ok": True, "reply": reply})


# ── Startup ───────────────────────────────────────────────────────────


def init():
    """Initialize the bridge. Called once before serving."""
    global personality

    log.info("=" * 55)
    log.info("  Mini-Jarvis Bridge  |  %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 55)

    # Load and lock personality to preetam_v1
    log.info("Loading personality: %s ...", ACTIVE_PERSONALITY)
    try:
        personality = PersonalityManager(force_personality=ACTIVE_PERSONALITY)
        log.info(
            "✅ Personality locked → %s (%s)",
            personality.active_personality_name,
            personality.name,
        )
    except ValueError as exc:
        log.error("❌ Personality error: %s", exc)
        sys.exit(1)

    # Check Ollama
    log.info("Checking Ollama (%s) ...", OLLAMA_API_URL)
    if not check_ollama():
        log.error("❌ Ollama is not available. Start it with: ollama serve")
        log.error("   Then pull the model: ollama pull %s", MODEL_NAME)
        sys.exit(1)
    log.info("✅ Ollama connected — model: %s", MODEL_NAME)

    log.info("🚀 Bridge ready on http://%s:%d", BRIDGE_HOST, BRIDGE_PORT)
    log.info("=" * 55)


if __name__ == "__main__":
    init()
    app.run(host=BRIDGE_HOST, port=BRIDGE_PORT, debug=False, use_reloader=False)