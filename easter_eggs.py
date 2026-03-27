"""
Mini Jarvis — Easter Egg System
User-configurable trigger → response pairs appended after model output.
"""

import json
import os
import random

EASTER_EGGS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "easter_eggs.json"
)


class EasterEggManager:
    """Manages keyword-triggered response lines stored in JSON."""

    def __init__(self):
        self.triggers: dict[str, list[str]] = self._load()

    # ── Detection ────────────────────────────────────────────────

    def check(self, user_input: str) -> str | None:
        """If user_input contains any trigger keyword, return one random line."""
        lower = user_input.lower()
        for keyword, responses in self.triggers.items():
            if keyword.lower() in lower and responses:
                return random.choice(responses)
        return None

    # ── Management ───────────────────────────────────────────────

    def add(self, keyword: str, response: str) -> str:
        keyword = keyword.strip().lower()
        response = response.strip()
        if not keyword or not response:
            return "Keyword and response cannot be empty."
        self.triggers.setdefault(keyword, []).append(response)
        self._save()
        return f"Easter egg added: '{keyword}' → {response}"

    def remove_keyword(self, keyword: str) -> str:
        keyword = keyword.strip().lower()
        if keyword not in self.triggers:
            return f"No easter egg found for '{keyword}'."
        del self.triggers[keyword]
        self._save()
        return f"All easter eggs for '{keyword}' removed."

    def list_all(self) -> str:
        if not self.triggers:
            return "  No easter eggs configured."
        lines = []
        for kw, responses in self.triggers.items():
            lines.append(f"  [{kw}] ({len(responses)} responses)")
            for r in responses:
                lines.append(f"    → {r}")
        return "\n".join(lines)

    # ── Persistence ──────────────────────────────────────────────

    def _load(self) -> dict[str, list[str]]:
        if not os.path.exists(EASTER_EGGS_FILE):
            return {}
        try:
            with open(EASTER_EGGS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _save(self):
        with open(EASTER_EGGS_FILE, "w") as f:
            json.dump(self.triggers, f, indent=2, ensure_ascii=False)
