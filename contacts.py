"""
Mini Jarvis — Persistent WhatsApp Identity Registry

Maps WhatsApp JIDs (e.g. 919876543210@c.us) to confirmed human names.
Persists to data/contacts.json so identity survives all restarts.

Architecture:
    - Each WhatsApp JID is a unique key.
    - The confirmed name is permanently associated with the JID.
    - Pending-intro state (awaiting name reply) is in-memory only.
    - Name extraction from replies uses conservative regex patterns.
"""

import json
import os
import re
from datetime import datetime

from config import DATA_DIR

CONTACTS_FILE = os.path.join(DATA_DIR, "contacts.json")

# ── Name extraction patterns ─────────────────────────────────────────
# Conservative patterns to extract a person's name from their reply.
# Order matters — more specific patterns first, bare name last.
_NAME_PATTERNS = [
    # "my name is Ashwath", "i'm Ashwath", "i am Ashwath", "im Ashwath"
    re.compile(
        r"(?:my name is|i(?:'| a)m|im)\s+([A-Z][a-z]{1,15})",
        re.IGNORECASE,
    ),
    # "call me Ashwath"
    re.compile(
        r"call me\s+([A-Z][a-z]{1,15})",
        re.IGNORECASE,
    ),
    # "it's Ashwath", "its Ashwath", "this is Ashwath"
    re.compile(
        r"(?:it(?:'?s)|this is)\s+([A-Z][a-z]{1,15})",
        re.IGNORECASE,
    ),
    # "Ashwath da", "Ashwath bro", "Ashwath here", "Ashwath nigga"
    # A capitalized word followed by a common filler/suffix
    re.compile(
        r"^([A-Z][a-z]{1,15})\s+(?:da|bro|here|machi|di|dude|man|nigga|dei|ma|ya|la)[\s!.]*$",
        re.IGNORECASE,
    ),
    # Bare single word that looks like a name (1-16 chars, starts uppercase)
    # Only matches if the ENTIRE message is just the name (with optional punctuation)
    re.compile(
        r"^([A-Z][a-z]{1,15})[.!]?$",
    ),
]

# Words that should NOT be treated as names (common false positives)
_NOT_NAMES = frozenset({
    "hello", "hi", "hey", "yes", "no", "ok", "okay", "sure", "thanks",
    "what", "who", "why", "how", "when", "where", "which", "nothing",
    "stop", "bruh", "bro", "dude", "man", "lol", "lmao", "haha",
    "please", "help", "wait", "bye", "sorry", "nah", "yeah", "yep",
    "nope", "good", "bad", "fine", "nice", "cool", "damn", "shit",
    "fuck", "bitch", "stfu", "true", "false", "maybe", "idk",
    "hmm", "huh", "sup", "wassup", "whatsup", "yo",
})


class ContactsRegistry:
    """Persistent WhatsApp JID → confirmed name registry."""

    def __init__(self):
        self._contacts: dict[str, dict] = self._load()
        self._pending_intro: set[str] = set()  # JIDs awaiting name reply (in-memory only)

    # ── Public API ───────────────────────────────────────────────

    def lookup(self, jid: str) -> str | None:
        """Return the confirmed name for a JID, or None if unknown."""
        entry = self._contacts.get(jid)
        return entry["name"] if entry else None

    def register(self, jid: str, name: str):
        """Permanently associate a JID with a confirmed name. Saves immediately."""
        self._contacts[jid] = {
            "name": name,
            "first_seen": self._contacts.get(jid, {}).get(
                "first_seen", datetime.now().isoformat()
            ),
            "confirmed_at": datetime.now().isoformat(),
        }
        self._save()

    def is_pending_intro(self, jid: str) -> bool:
        """Return True if we've asked this JID for their name and are awaiting a reply."""
        return jid in self._pending_intro

    def set_pending_intro(self, jid: str):
        """Mark this JID as awaiting a name introduction."""
        self._pending_intro.add(jid)

    def clear_pending_intro(self, jid: str):
        """Clear the pending-intro state for this JID."""
        self._pending_intro.discard(jid)

    def known_count(self) -> int:
        """Return the number of known contacts."""
        return len(self._contacts)

    # ── Name extraction ──────────────────────────────────────────

    @staticmethod
    def try_extract_name(text: str) -> str | None:
        """
        Try to extract a person's name from a message.

        Returns the extracted name (title-cased) if confident, else None.
        Intentionally conservative — better to ask again than save garbage.
        """
        text = text.strip()

        # Skip very long messages — a name reply is typically short
        if len(text) > 60:
            return None

        for pattern in _NAME_PATTERNS:
            match = pattern.search(text)
            if match:
                candidate = match.group(1).strip()
                # Validate: not a common word, reasonable length
                if candidate.lower() in _NOT_NAMES:
                    continue
                if len(candidate) < 2:
                    continue
                # Return properly capitalised
                return candidate.capitalize()

        return None

    # ── Persistence ──────────────────────────────────────────────

    def _load(self) -> dict[str, dict]:
        """Load contacts from disk. Returns empty dict if file missing/corrupt."""
        if not os.path.exists(CONTACTS_FILE):
            return {}
        try:
            with open(CONTACTS_FILE, "r") as f:
                data = json.load(f)
            # Validate structure: must be a dict of dicts with "name" keys
            if not isinstance(data, dict):
                return {}
            return {
                jid: entry
                for jid, entry in data.items()
                if isinstance(entry, dict) and "name" in entry
            }
        except (json.JSONDecodeError, FileNotFoundError, OSError):
            return {}

    def _save(self):
        """Write the contacts registry to disk."""
        with open(CONTACTS_FILE, "w") as f:
            json.dump(self._contacts, f, indent=2, ensure_ascii=False)
