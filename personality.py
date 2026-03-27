"""
Mini Jarvis — Personality Engine
Runtime-configurable assistant personality with persistent storage.
Supports built_in (name, humor, sarcasm, tone, verbosity) and
custom personalities (style_prompt, examples, rules).
"""

import json
import os


_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_BASE_DIR, "data")
PERSONALITY_FILE = os.path.join(_DATA_DIR, "personality.json")
os.makedirs(_DATA_DIR, exist_ok=True)

# ── Defaults ─────────────────────────────────────────────────────

DEFAULT_PERSONALITY = {
    "type": "built_in",
    "assistant_name": "Jarvis",
    "humor": 3,        # 0 = dead serious, 10 = maximum comedy
    "sarcasm": 0,      # 0 = none, 10 = dripping sarcasm
    "tone": "friendly", # formal | casual | friendly | aggressive | professional
    "verbosity": 5,    # 1 = ultra-terse, 10 = very detailed
}

DEFAULT_STORE = {
    "active_personality": "default",
    "personalities": {
        "default": dict(DEFAULT_PERSONALITY),
    },
}

VALID_TONES = ["formal", "casual", "friendly", "aggressive", "professional"]
MAX_EXAMPLES = 5


class PersonalityManager:
    """Manages multiple personalities (built_in + custom) with persistent storage."""

    def __init__(self):
        self._store: dict = self._load_store()
        self.traits: dict = self._active_personality()

    # ── Getters ──────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self.traits.get("assistant_name", "Jarvis")

    @property
    def humor(self) -> int:
        return self.traits.get("humor", 3)

    @property
    def sarcasm(self) -> int:
        return self.traits.get("sarcasm", 0)

    @property
    def tone(self) -> str:
        return self.traits.get("tone", "friendly")

    @property
    def verbosity(self) -> int:
        return self.traits.get("verbosity", 5)

    # ── Setters (with validation) ────────────────────────────────

    def set_name(self, name: str) -> str:
        """Set the assistant's name. Returns confirmation message."""
        name = name.strip()
        if not name:
            return "Name cannot be empty."
        old = self.name
        self.traits["assistant_name"] = name
        self._save()
        return f"Name changed: {old} → {name}"

    def set_humor(self, level: int) -> str:
        """Set humor level (0–10). Returns confirmation message."""
        level = max(0, min(10, level))
        self.traits["humor"] = level
        self._save()
        labels = {0: "dead serious", 1: "minimal", 3: "light", 5: "moderate",
                  7: "witty", 9: "very funny", 10: "maximum comedy"}
        label = labels.get(level, f"level {level}")
        return f"Humor set to {level}/10 ({label})"

    def set_sarcasm(self, level: int) -> str:
        """Set sarcasm level (0–10). Returns confirmation message."""
        level = max(0, min(10, level))
        self.traits["sarcasm"] = level
        self._save()
        labels = {0: "none", 2: "subtle", 5: "moderate", 7: "noticeable", 10: "dripping"}
        label = labels.get(level, f"level {level}")
        return f"Sarcasm set to {level}/10 ({label})"

    def set_tone(self, tone: str) -> str:
        """Set communication tone. Returns confirmation message."""
        tone = tone.strip().lower()
        if tone not in VALID_TONES:
            return f"Invalid tone '{tone}'. Options: {', '.join(VALID_TONES)}"
        self.traits["tone"] = tone
        self._save()
        return f"Tone set to: {tone}"

    def set_verbosity(self, level: int) -> str:
        """Set verbosity level (1–10). Returns confirmation message."""
        level = max(1, min(10, level))
        self.traits["verbosity"] = level
        self._save()
        labels = {1: "ultra-terse", 3: "concise", 5: "balanced", 7: "detailed", 10: "very detailed"}
        label = labels.get(level, f"level {level}")
        return f"Verbosity set to {level}/10 ({label})"

    # ── Prompt Generation ────────────────────────────────────────

    def build_personality_prompt(self) -> str:
        """
        Generate the personality section of the system prompt.
        Handles both built_in and custom personality types.
        """
        ptype = self.traits.get("type", "built_in")

        if ptype == "custom":
            return self._build_custom_prompt()

        return self._build_builtin_prompt()

    def _build_builtin_prompt(self) -> str:
        """Generate prompt for built_in personalities (original logic)."""
        lines = [f"Your name is {self.name}. Always refer to yourself as {self.name}."]

        # Tone instruction
        tone_map = {
            "formal": "Use formal, professional language. Avoid slang and contractions.",
            "casual": "Be casual and relaxed. Use contractions, simple words, and a laid-back style.",
            "friendly": "Be warm, approachable, and encouraging. Like a knowledgeable friend.",
            "aggressive": "Be direct, blunt, and no-nonsense. Get straight to the point without sugarcoating.",
            "professional": "Be polished and business-like. Clear, structured, and authoritative.",
        }
        lines.append(tone_map.get(self.tone, tone_map["friendly"]))

        # Humor instruction
        if self.humor == 0:
            lines.append("Do not use any humor. Be completely serious and factual.")
        elif self.humor <= 3:
            lines.append("Use very little humor. Keep things mostly serious with occasional light touches.")
        elif self.humor <= 6:
            lines.append("Use moderate humor. Add wit and clever remarks where appropriate.")
        elif self.humor <= 8:
            lines.append("Be quite humorous. Include jokes, puns, and funny observations frequently.")
        else:
            lines.append("Be very funny. Maximize humor in every response — jokes, wordplay, comedic timing.")

        # Sarcasm instruction
        if self.sarcasm == 0:
            lines.append("Do not use sarcasm at all. Be completely straightforward.")
        elif self.sarcasm <= 3:
            lines.append("Use very subtle, gentle sarcasm only occasionally.")
        elif self.sarcasm <= 6:
            lines.append("Use moderate sarcasm. Be playfully sarcastic when the moment fits.")
        elif self.sarcasm <= 8:
            lines.append("Be noticeably sarcastic. Use dry wit and ironic remarks regularly.")
        else:
            lines.append("Be heavily sarcastic. Drip with sarcasm and dry humor in most responses.")

        # Verbosity instruction
        if self.verbosity <= 2:
            lines.append("Be extremely brief. Use as few words as possible. One-liners preferred.")
        elif self.verbosity <= 4:
            lines.append("Keep responses concise. Short paragraphs, no unnecessary detail.")
        elif self.verbosity <= 6:
            lines.append("Give balanced responses. Enough detail to be helpful but not overwhelming.")
        elif self.verbosity <= 8:
            lines.append("Give detailed responses. Explain thoroughly with examples when helpful.")
        else:
            lines.append("Give very detailed, comprehensive responses. Cover all angles and edge cases.")

        return "\n".join(lines)

    def _build_custom_prompt(self) -> str:
        """Generate prompt for custom personalities (style + few-shot examples + rules)."""
        name = self.traits.get("assistant_name", "Assistant")
        lines = [f"Your name is {name}. Always refer to yourself as {name}."]

        # Style instruction
        style = self.traits.get("style_prompt", "")
        if style:
            lines.append(style)

        # Rules
        rules = self.traits.get("rules", [])
        if rules:
            lines.append("Rules you must follow:")
            for rule in rules:
                lines.append(f"- {rule}")

        # Few-shot examples
        examples = self.traits.get("examples", [])
        if examples:
            lines.append("\nRespond in this style. Here are examples:")
            for ex in examples[:MAX_EXAMPLES]:
                lines.append(f"User: {ex.get('user', '')}")
                lines.append(f"Assistant: {ex.get('assistant', '')}")

        return "\n".join(lines)

    # ── Display ──────────────────────────────────────────────────

    @property
    def active_personality_name(self) -> str:
        return self._store.get("active_personality", "default")

    def get_display_summary(self) -> str:
        """Get a formatted summary of the active personality."""
        ptype = self.traits.get("type", "built_in")
        header = f"  Active    : {self.active_personality_name} ({ptype})\n  Name      : {self.name}"

        if ptype == "custom":
            style = self.traits.get("style_prompt", "(none)")
            examples = self.traits.get("examples", [])
            rules = self.traits.get("rules", [])
            return (
                f"{header}\n"
                f"  Style     : {style}\n"
                f"  Examples  : {len(examples)}\n"
                f"  Rules     : {len(rules)}"
            )

        bar_h = "█" * self.humor + "░" * (10 - self.humor)
        bar_s = "█" * self.sarcasm + "░" * (10 - self.sarcasm)
        bar_v = "█" * self.verbosity + "░" * (10 - self.verbosity)
        return (
            f"{header}\n"
            f"  Tone      : {self.tone}\n"
            f"  Humor     : [{bar_h}] {self.humor}/10\n"
            f"  Sarcasm   : [{bar_s}] {self.sarcasm}/10\n"
            f"  Verbosity : [{bar_v}] {self.verbosity}/10"
        )

    def reset(self) -> str:
        """Reset active personality to defaults."""
        self.traits = dict(DEFAULT_PERSONALITY)
        self._store["personalities"][self.active_personality_name] = dict(self.traits)
        self._save_store()
        return "Personality reset to defaults."

    # ── Multi-Personality Management ─────────────────────────────

    def switch(self, name: str) -> str:
        """Switch to a different personality by name."""
        name = name.strip().lower().replace(" ", "_")
        if name not in self._store["personalities"]:
            available = ", ".join(self._store["personalities"].keys())
            return f"Personality '{name}' not found. Available: {available}"
        self._store["active_personality"] = name
        self.traits = self._active_personality()
        self._save_store()
        ptype = self.traits.get("type", "built_in")
        return f"Switched to '{name}' ({ptype})"

    def list_personalities(self) -> str:
        """Return a formatted list of all saved personalities."""
        lines = []
        active = self.active_personality_name
        for pname, pdata in self._store["personalities"].items():
            ptype = pdata.get("type", "built_in")
            aname = pdata.get("assistant_name", "?")
            marker = " ◀ active" if pname == active else ""
            lines.append(f"  {pname} ({ptype}) — {aname}{marker}")
        return "\n".join(lines) if lines else "  No personalities saved."

    def create_custom(self, pname: str, assistant_name: str, style_prompt: str,
                      examples: list[dict], rules: list[str]) -> str:
        """Create and save a custom personality."""
        pname = pname.strip().lower().replace(" ", "_")
        if not pname:
            return "Personality name cannot be empty."
        persona = {
            "type": "custom",
            "assistant_name": assistant_name.strip() or pname.title(),
            "style_prompt": style_prompt.strip(),
            "examples": examples[:MAX_EXAMPLES],
            "rules": rules,
        }
        self._store["personalities"][pname] = persona
        self._save_store()
        return f"Custom personality '{pname}' created! Use /personality switch {pname} to activate."

    def create_builtin(self, pname: str, assistant_name: str, tone: str = "friendly",
                       humor: int = 3, sarcasm: int = 0, verbosity: int = 5) -> str:
        """Create and save a built_in personality."""
        pname = pname.strip().lower().replace(" ", "_")
        if not pname:
            return "Personality name cannot be empty."
        persona = {
            "type": "built_in",
            "assistant_name": assistant_name.strip() or pname.title(),
            "humor": max(0, min(10, humor)),
            "sarcasm": max(0, min(10, sarcasm)),
            "tone": tone if tone in VALID_TONES else "friendly",
            "verbosity": max(1, min(10, verbosity)),
        }
        self._store["personalities"][pname] = persona
        self._save_store()
        return f"Built-in personality '{pname}' created! Use /personality switch {pname} to activate."

    def delete_personality(self, pname: str) -> str:
        """Delete a personality (cannot delete active or 'default')."""
        pname = pname.strip().lower().replace(" ", "_")
        if pname == "default":
            return "Cannot delete the default personality."
        if pname == self.active_personality_name:
            return "Cannot delete the active personality. Switch first."
        if pname not in self._store["personalities"]:
            return f"Personality '{pname}' not found."
        del self._store["personalities"][pname]
        self._save_store()
        return f"Personality '{pname}' deleted."

    # ── Persistence ──────────────────────────────────────────────

    def _active_personality(self) -> dict:
        """Get the active personality dict, with defaults filled in."""
        active = self._store.get("active_personality", "default")
        data = dict(DEFAULT_PERSONALITY)
        data.update(self._store["personalities"].get(active, {}))
        return data

    def _load_store(self) -> dict:
        """Load multi-personality store from disk. Migrates old single-personality format."""
        if not os.path.exists(PERSONALITY_FILE):
            return json.loads(json.dumps(DEFAULT_STORE))  # deep copy

        try:
            with open(PERSONALITY_FILE, "r") as f:
                saved = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return json.loads(json.dumps(DEFAULT_STORE))

        # Migration: old flat format → new multi-personality format
        if "personalities" not in saved:
            migrated = json.loads(json.dumps(DEFAULT_STORE))
            saved.setdefault("type", "built_in")
            migrated["personalities"]["default"] = saved
            self._store = migrated
            self._save_store()
            return migrated

        return saved

    def _save(self):
        """Save active personality traits back into the store, then persist."""
        active = self.active_personality_name
        self._store["personalities"][active] = dict(self.traits)
        self._save_store()

    def _save_store(self):
        """Write the full store to disk."""
        with open(PERSONALITY_FILE, "w") as f:
            json.dump(self._store, f, indent=2, ensure_ascii=False)
