"""
Mini Jarvis — User Preferences Tracker
Detects and stores simple user preferences from conversation.
"""

import json
import os
import re
from config import PREFERENCES_FILE


# ── Detection Patterns ───────────────────────────────────────────
# Each pattern: (compiled regex, preference key, group index for value)
PREFERENCE_PATTERNS = [
    # Name detection
    (re.compile(r"(?:call me|my name is|i(?:'| a)m)\s+([A-Z][a-z]+)", re.IGNORECASE), "user_name", 1),
    # Tone preferences
    (re.compile(r"(?:i prefer|please be|i like)\s+(concise|detailed|brief|verbose|formal|casual|friendly)\s+(?:answers?|responses?|tone)?", re.IGNORECASE), "preferred_tone", 1),
    # Language preference
    (re.compile(r"(?:respond|answer|reply|talk)\s+(?:in|using)\s+(english|hindi|spanish|french|german)", re.IGNORECASE), "preferred_language", 1),
]


class PreferencesManager:
    """Tracks and stores user preferences in a JSON file."""

    def __init__(self):
        self.preferences: dict = self._load()

    # ── Core API ─────────────────────────────────────────────────

    def detect_and_store(self, user_message: str) -> str | None:
        """
        Scan a user message for preference signals.
        Returns a confirmation string if a preference was detected, else None.
        """
        for pattern, key, group_idx in PREFERENCE_PATTERNS:
            match = pattern.search(user_message)
            if match:
                value = match.group(group_idx).strip()
                old_value = self.preferences.get(key)
                self.preferences[key] = value
                self._save()

                if old_value and old_value.lower() != value.lower():
                    return f"Updated preference: {key} → '{value}' (was '{old_value}')"
                elif not old_value:
                    return f"Noted preference: {key} → '{value}'"

        return None

    def get_preferences_prompt(self) -> str:
        """Build a prompt section describing known user preferences."""
        if not self.preferences:
            return ""

        lines = ["Here is what you know about the user:"]

        if name := self.preferences.get("user_name"):
            lines.append(f"- The user's name is {name}. Address them by name occasionally.")

        if tone := self.preferences.get("preferred_tone"):
            lines.append(f"- The user prefers {tone} responses.")

        if lang := self.preferences.get("preferred_language"):
            lines.append(f"- The user prefers responses in {lang}.")

        # Include any custom preferences
        known_keys = {"user_name", "preferred_tone", "preferred_language"}
        for key, value in self.preferences.items():
            if key not in known_keys:
                lines.append(f"- {key}: {value}")

        return "\n".join(lines)

    def get_display_summary(self) -> str:
        """Get a human-readable summary of all preferences."""
        if not self.preferences:
            return "No preferences stored yet."

        lines = []
        for key, value in self.preferences.items():
            label = key.replace("_", " ").title()
            lines.append(f"  {label}: {value}")

        return "\n".join(lines)

    def set_preference(self, key: str, value: str):
        """Manually set a preference."""
        self.preferences[key] = value
        self._save()

    # ── Persistence ──────────────────────────────────────────────

    def _load(self) -> dict:
        """Load preferences from disk."""
        if os.path.exists(PREFERENCES_FILE):
            try:
                with open(PREFERENCES_FILE, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                return {}
        return {}

    def _save(self):
        """Save preferences to disk."""
        with open(PREFERENCES_FILE, "w") as f:
            json.dump(self.preferences, f, indent=2, ensure_ascii=False)
