"""
Mini Jarvis — Configuration
All constants and paths used across the application.
"""

import os

# ── Ollama Settings ──────────────────────────────────────────────────
OLLAMA_API_URL = "http://localhost:11434"
MODEL_NAME = "llama3:8b"

# ── Paths ────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONVERSATIONS_DIR = os.path.join(DATA_DIR, "conversations")
PREFERENCES_FILE = os.path.join(DATA_DIR, "preferences.json")
PERSONALITY_FILE = os.path.join(DATA_DIR, "personality.json")

# ── Memory Limits ────────────────────────────────────────────────────
MAX_CURRENT_MESSAGES = 10          # Max messages to keep in active context
MAX_PAST_SNIPPETS = 3              # Max snippets to retrieve from past sessions
MAX_SNIPPET_LENGTH = 300           # Max characters per retrieved snippet

# ── Bridge Settings ──────────────────────────────────────────────────
BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", 5001))
BRIDGE_HOST = "127.0.0.1"

# ── Active Personality (hard-locked for WhatsApp bot) ────────────────
ACTIVE_PERSONALITY = "preetam_v1"

# ── System Identity (base layer — Preetam personality overrides all) ──
SYSTEM_IDENTITY = (
    "You are Preetam Naligala. You are NOT an AI assistant. "
    "You are a real person texting on WhatsApp. "
    "Never break character. Never explain yourself. "
    "Never sound like a chatbot or assistant."
)

# ── Ensure directories exist ─────────────────────────────────────────
os.makedirs(CONVERSATIONS_DIR, exist_ok=True)
os.makedirs(os.path.dirname(PREFERENCES_FILE), exist_ok=True)
