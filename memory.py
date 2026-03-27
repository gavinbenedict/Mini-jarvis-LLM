"""
Mini Jarvis — Conversation Memory Manager
Handles saving, loading, and searching conversation history.
"""

import json
import os
import glob
from datetime import datetime
from config import CONVERSATIONS_DIR, MAX_CURRENT_MESSAGES, MAX_PAST_SNIPPETS, MAX_SNIPPET_LENGTH


class MemoryManager:
    """Manages conversation history with JSON-based storage."""

    def __init__(self):
        self.session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.messages: list[dict] = []
        self._session_file = os.path.join(
            CONVERSATIONS_DIR, f"session_{self.session_id}.json"
        )

    # ── Current Session ──────────────────────────────────────────

    def add_message(self, role: str, content: str):
        """Add a message to the current session and auto-save."""
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })
        self._save_session()

    def get_context_messages(self) -> list[dict]:
        """Get recent messages formatted for the LLM (role + content only)."""
        recent = self.messages[-MAX_CURRENT_MESSAGES:]
        return [{"role": m["role"], "content": m["content"]} for m in recent]

    def clear_session(self):
        """Start a fresh session (saves the old one first)."""
        self._save_session()
        self.session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.messages = []
        self._session_file = os.path.join(
            CONVERSATIONS_DIR, f"session_{self.session_id}.json"
        )

    # ── Past Sessions (Search) ───────────────────────────────────

    def search_past_sessions(self, query: str) -> list[str]:
        """
        Search all past session files for messages matching the query.
        Returns a list of relevant snippets.
        """
        if not query.strip():
            return []

        keywords = query.lower().split()
        snippets = []

        session_files = sorted(
            glob.glob(os.path.join(CONVERSATIONS_DIR, "session_*.json")),
            reverse=True,  # Most recent first
        )

        for filepath in session_files:
            # Skip the current session file
            if os.path.abspath(filepath) == os.path.abspath(self._session_file):
                continue

            try:
                with open(filepath, "r") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                continue

            for msg in data.get("messages", []):
                content_lower = msg["content"].lower()
                # Check if any keyword appears in the message
                if any(kw in content_lower for kw in keywords):
                    # Build a snippet: include the user question + assistant answer
                    snippet = f"[{msg['role'].upper()}]: {msg['content']}"
                    if len(snippet) > MAX_SNIPPET_LENGTH:
                        snippet = snippet[:MAX_SNIPPET_LENGTH] + "..."
                    snippets.append(snippet)

                    if len(snippets) >= MAX_PAST_SNIPPETS:
                        return snippets

        return snippets

    def get_session_history_summary(self) -> str:
        """Get a formatted summary of the current session for display."""
        if not self.messages:
            return "No messages in current session."

        lines = []
        for msg in self.messages:
            role = "You" if msg["role"] == "user" else "Jarvis"
            text = msg["content"]
            if len(text) > 80:
                text = text[:80] + "..."
            lines.append(f"  {role}: {text}")

        return "\n".join(lines)

    # ── Persistence ──────────────────────────────────────────────

    def _save_session(self):
        """Save the current session to a JSON file."""
        data = {
            "session_id": self.session_id,
            "messages": self.messages,
        }
        with open(self._session_file, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_past_session_count(self) -> int:
        """Return the number of saved past sessions."""
        files = glob.glob(os.path.join(CONVERSATIONS_DIR, "session_*.json"))
        return len(files)
