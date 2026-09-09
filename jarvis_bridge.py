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
from contacts import ContactsRegistry

# Mutable state for CLI overrides
_current_model = MODEL_NAME
_current_personality = ACTIVE_PERSONALITY

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

# ── Global identity registry (persistent, loaded once at init) ───────
contacts: ContactsRegistry | None = None

# ── Per-chat state ───────────────────────────────────────────────────
# Memory is keyed by chat_id so that all participants in a group share
# one conversation timeline. For DMs, chat_id == sender, so the
# behaviour is identical to the previous per-sender keying.
_chat_memory: dict[str, MemoryManager] = {}
_chat_prefs: dict[str, PreferencesManager] = {}
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
        if not any(_current_model in m for m in models):
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


def _get_chat_memory(chat_id: str) -> MemoryManager:
    """Get or create a MemoryManager for this chat (group or DM)."""
    with _state_lock:
        if chat_id not in _chat_memory:
            log.info("Creating new memory session for chat: %s", chat_id)
            _chat_memory[chat_id] = MemoryManager()
        return _chat_memory[chat_id]


def _get_chat_prefs(chat_id: str) -> PreferencesManager:
    """Get or create a PreferencesManager for this chat."""
    with _state_lock:
        if chat_id not in _chat_prefs:
            _chat_prefs[chat_id] = PreferencesManager()
        return _chat_prefs[chat_id]


# ── System prompt builder ────────────────────────────────────────────


def build_system_prompt(
    prefs: PreferencesManager,
    memory: MemoryManager,
    user_message: str,
    *,
    is_group: bool = False,
    current_sender: str = "",
    sender_known: bool = True,
) -> str:
    """
    Construct the full system prompt in layers:
    1. Base identity (Preetam-specific, from config)
    2. Personality (preetam_v1 custom prompt + rules + examples)
    3. User preferences (name, language, etc.)
    4. Past conversation context (relevant snippets)
    5. Group chat context (if applicable)
    6. Unknown-sender prompt (if applicable)
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

    # Layer 5: Group chat awareness
    if is_group:
        group_note = (
            "\nYou are in a WhatsApp group chat with multiple people. "
            "Messages from participants are prefixed with their name in [brackets]. "
            "Each [Name] is a different person. Keep track of who said what. "
            "Do not confuse one person's words with another's."
        )
        if current_sender:
            group_note += f" The message you are replying to right now was sent by {current_sender}."
        parts.append(group_note)

    # Layer 6: Unknown sender — ask for introduction
    if not sender_known:
        parts.append(
            "\nYou don't recognize the person who just messaged you. "
            "Casually ask who they are — keep it natural, like texting someone "
            "whose number you don't have saved. Don't be formal about it."
        )

    return "\n".join(parts)


# ── Ollama call ──────────────────────────────────────────────────────


def call_ollama(system_prompt: str, messages: list[dict]) -> str:
    """
    Call Ollama chat API (non-streaming).
    Returns the response string, or raises on error.
    """
    payload = {
        "model": _current_model,
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
            "model": _current_model,
        }
    )


@app.route("/chat", methods=["POST"])
def chat():
    """
    Process an incoming WhatsApp message.

    Expected JSON body:
        {
            "text":       "message content",
            "sender":     "1234567890@c.us",   // individual sender JID
            "sender_name": "Ashwath",           // sender's WhatsApp display name
            "chat_id":    "1234567890@c.us"     // chat JID (differs from sender for groups)
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
    sender_name = (body.get("sender_name") or "").strip()
    chat_id = (body.get("chat_id") or sender).strip()

    if not text:
        return jsonify({"ok": False, "error": "empty message text"}), 400

    # Duplicate guard
    if _is_duplicate(sender, text):
        log.warning("Duplicate message from %s — skipping: %s", sender, text[:60])
        return jsonify({"ok": False, "error": "duplicate message skipped"}), 200

    # Detect group chat: chat_id is the group JID, sender is the individual author.
    # In a DM, chat_id == sender, so is_group will be False.
    is_group = chat_id != sender and sender != "unknown"

    # ── Identity resolution via persistent contacts registry ─────
    display_name = None   # confirmed name from registry (or None if unknown)
    sender_known = False

    if contacts and sender != "unknown":
        confirmed = contacts.lookup(sender)
        if confirmed:
            # Known person — use their confirmed name
            display_name = confirmed
            sender_known = True
        elif contacts.is_pending_intro(sender):
            # We already asked — try to extract name from their reply
            extracted = contacts.try_extract_name(text)
            if extracted:
                contacts.register(sender, extracted)
                contacts.clear_pending_intro(sender)
                display_name = extracted
                sender_known = True
                log.info("📇 Registered contact: %s → %s", sender, extracted)
            else:
                # Couldn't extract name — keep pending, model will ask again
                display_name = None
                sender_known = False
        else:
            # First-time unknown sender — mark as pending intro
            contacts.set_pending_intro(sender)
            display_name = None
            sender_known = False
            log.info("👤 Unknown sender: %s — will ask for name", sender)

    display = display_name or sender_name or sender
    log.info("💬 [%s] %s", display, text[:80])

    # Per-chat state (all group participants share one memory timeline)
    mem = _get_chat_memory(chat_id)
    prefs = _get_chat_prefs(chat_id)

    # Detect preferences
    pref_note = prefs.detect_and_store(text)
    if pref_note:
        log.info("📝 Preference detected: %s", pref_note)

    # Build system prompt (with group context and identity awareness)
    system_prompt = build_system_prompt(
        prefs, mem, text,
        is_group=is_group,
        current_sender=display_name or "",
        sender_known=sender_known,
    )

    # For group messages, prefix with sender identity so the LLM can
    # distinguish who said what.  Use confirmed name from the identity
    # registry; fall back to a short "Unknown" label for unrecognised senders.
    if is_group:
        if display_name:
            tag = display_name
        else:
            jid_num = sender.split("@")[0][-4:]  # last 4 digits for readability
            tag = f"Unknown ({jid_num})"
        tagged_text = f"[{tag}]: {text}"
    else:
        tagged_text = text

    # Add user message to memory (with sender metadata for auditability)
    mem.add_message("user", tagged_text, sender=sender if is_group else "")

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

    log.info("🤖 [%s] %s", display, reply[:80])
    return jsonify({"ok": True, "reply": reply})


# ── Startup ───────────────────────────────────────────────────────────



@app.route("/model", methods=["GET", "POST"])
def handle_model():
    global _current_model
    if request.method == "POST":
        data = request.json or {}
        if "model" in data:
            _current_model = data["model"]
    return jsonify({"model": _current_model})

@app.route("/personality", methods=["GET", "POST"])
def handle_personality():
    global _current_personality, personality
    if request.method == "POST":
        data = request.json or {}
        if "personality" in data:
            new_p = data["personality"]
            try:
                # Test loading it
                test_p = PersonalityManager(force_personality=new_p)
                personality = test_p
                _current_personality = new_p
            except Exception as e:
                return jsonify({"error": str(e)}), 400
    return jsonify({"personality": _current_personality})

def init():
    """Initialize the bridge. Called once before serving."""
    global personality, contacts

    log.info("=" * 55)
    log.info("  Mini-Jarvis Bridge  |  %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 55)

    # Load and lock personality to preetam_v1
    log.info("Loading personality: %s ...", _current_personality)
    try:
        personality = PersonalityManager(force_personality=_current_personality)
        log.info(
            "✅ Personality locked → %s (%s)",
            personality.active_personality_name,
            personality.name,
        )
    except ValueError as exc:
        log.error("❌ Personality error: %s", exc)
        sys.exit(1)

    # Load persistent identity registry
    contacts = ContactsRegistry()
    log.info("📇 Contacts registry loaded — %d known contact(s)", contacts.known_count())

    # Check Ollama
    log.info("Checking Ollama (%s) ...", OLLAMA_API_URL)
    if not check_ollama():
        log.error("❌ Ollama is not available. Start it with: ollama serve")
        log.error("   Then pull the model: ollama pull %s", _current_model)
        sys.exit(1)
    log.info("✅ Ollama connected — model: %s", _current_model)

    log.info("🚀 Bridge ready on http://%s:%d", BRIDGE_HOST, BRIDGE_PORT)
    log.info("=" * 55)


if __name__ == "__main__":
    init()
    app.run(host=BRIDGE_HOST, port=BRIDGE_PORT, debug=False, use_reloader=False)