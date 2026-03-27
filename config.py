"""
Mini Jarvis — Configuration
All constants and paths used across the application.
"""

import os

# ── Ollama Settings ──────────────────────────────────────────────
OLLAMA_API_URL = "http://localhost:11434"
MODEL_NAME = "mistral"

# ── Paths ────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONVERSATIONS_DIR = os.path.join(DATA_DIR, "conversations")
PREFERENCES_FILE = os.path.join(DATA_DIR, "preferences.json")
PERSONALITY_FILE = os.path.join(DATA_DIR, "personality.json")

# ── Memory Limits ────────────────────────────────────────────────
MAX_CURRENT_MESSAGES = 20          # Max messages to keep in active context
MAX_PAST_SNIPPETS = 3              # Max snippets to retrieve from past sessions
MAX_SNIPPET_LENGTH = 300           # Max characters per ret rieved snippet

# ── System Prompt (neutral base — personality layer controls behavior) ─
SYSTEM_IDENTITY = """You are a response engine.
Your behavior, tone, and personality are defined entirely by the personality system.
Do not assume you are a helpful assistant unless explicitly instructed by the active personality.
Do not add default greetings, explanations, or structure."""

# ── Ensure directories exist ─────────────────────────────────────
os.makedirs(CONVERSATIONS_DIR, exist_ok=True)
os.makedirs(os.path.dirname(PREFERENCES_FILE), exist_ok=True)
