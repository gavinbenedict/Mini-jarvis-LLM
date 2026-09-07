#!/usr/bin/env python3
"""
import_memories.py — WhatsApp Chat Export → Jarvis Memory Importer

Reads .txt files from memory_imports/ (WhatsApp's standard export format),
parses them into conversation sessions, and writes them as session_*.json files
into data/conversations/ — the same format MemoryManager uses.

WhatsApp export format (two variants handled):
  [DD/MM/YYYY, HH:MM:SS] Name: Message
  [MM/DD/YY, H:MM:SS AM/PM] Name: Message

Session splitting:
  A new session is created whenever there's a gap of SESSION_GAP_HOURS or more
  between consecutive messages.

Usage:
    1. Export a WhatsApp chat (Chat > Export Chat > Without Media)
    2. Copy the .txt file into memory_imports/
    3. Run: python import_memories.py
    4. Optionally: python import_memories.py --your-name "Gavin"
       (so YOUR messages are tagged as "user", others as "assistant")

Arguments:
    --your-name   Your display name as it appears in the chat export.
                  If omitted, the first speaker is assumed to be "user".
    --dry-run     Parse and print summary without writing files.
    --gap-hours   Hours of silence that split sessions (default: 4)
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent
IMPORT_DIR      = BASE_DIR / "memory_imports"
CONVERSATIONS_DIR = BASE_DIR / "data" / "conversations"

# ── Constants ──────────────────────────────────────────────────────────
DEFAULT_SESSION_GAP_HOURS = 4
SYSTEM_MESSAGES = {
    "messages and calls are end-to-end encrypted",
    "missed voice call",
    "missed video call",
    "you deleted this message",
    "this message was deleted",
    "null",
    "<media omitted>",
}

# ── Timestamp patterns ─────────────────────────────────────────────────
# Pattern 1: [27/03/2026, 14:30:00]  (day/month/year, 24h)
_TS_PATTERN_1 = re.compile(
    r"^\[(\d{1,2}/\d{1,2}/\d{2,4}),\s*(\d{1,2}:\d{2}:\d{2})\]\s*(.+?):\s*(.*)",
    re.DOTALL,
)
# Pattern 2: [3/27/26, 2:30:05 PM]  (US format, 12h)
_TS_PATTERN_2 = re.compile(
    r"^\[(\d{1,2}/\d{1,2}/\d{2,4}),\s*(\d{1,2}:\d{2}:\d{2}\s*[APap][Mm])\]\s*(.+?):\s*(.*)",
    re.DOTALL,
)
# Pattern 3: No brackets — older Android export
# 27/03/2026, 14:30 - Name: Message
_TS_PATTERN_3 = re.compile(
    r"^(\d{1,2}/\d{1,2}/\d{2,4}),\s*(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?)\s*-\s*(.+?):\s*(.*)",
    re.DOTALL,
)

ALL_PATTERNS = [_TS_PATTERN_1, _TS_PATTERN_2, _TS_PATTERN_3]


def parse_timestamp(date_str: str, time_str: str) -> datetime | None:
    """Try multiple datetime formats. Returns a datetime or None."""
    combined = f"{date_str} {time_str}".strip()

    formats = [
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%y %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%y %H:%M:%S",
        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%y %I:%M:%S %p",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%y %I:%M:%S %p",
        "%d/%m/%Y %H:%M",
        "%d/%m/%y %H:%M",
        "%m/%d/%Y %H:%M",
        "%m/%d/%y %H:%M",
        "%d/%m/%Y %I:%M %p",
        "%m/%d/%y %I:%M %p",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(combined, fmt)
        except ValueError:
            continue
    return None


def parse_line(line: str) -> tuple[datetime, str, str] | None:
    """
    Parse a single WhatsApp export line.
    Returns (timestamp, sender_name, message_text) or None.
    """
    for pattern in ALL_PATTERNS:
        match = pattern.match(line.strip())
        if match:
            date_str, time_str, sender, message = match.groups()
            ts = parse_timestamp(date_str, time_str.strip())
            if ts:
                return ts, sender.strip(), message.strip()
    return None


def is_system_message(text: str) -> bool:
    """Return True if this looks like a WhatsApp system notification."""
    lower = text.lower().strip()
    return any(sys_msg in lower for sys_msg in SYSTEM_MESSAGES) or lower.startswith("~")


def parse_chat_file(filepath: Path) -> list[tuple[datetime, str, str]]:
    """
    Parse a .txt WhatsApp export into a list of (timestamp, sender, text) tuples.
    Handles multi-line messages (continuation lines have no timestamp header).
    """
    entries = []
    current: tuple[datetime, str, str] | None = None

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            parsed = parse_line(line)

            if parsed:
                # Flush previous entry
                if current:
                    ts, sender, text = current
                    if text and not is_system_message(text):
                        entries.append(current)
                current = parsed
            else:
                # Continuation of previous message
                if current:
                    ts, sender, text = current
                    current = (ts, sender, text + "\n" + line)

    # Flush last entry
    if current:
        ts, sender, text = current
        if text and not is_system_message(text):
            entries.append(current)

    return entries


def split_into_sessions(
    entries: list[tuple[datetime, str, str]],
    gap_hours: float,
) -> list[list[tuple[datetime, str, str]]]:
    """
    Split a flat list of messages into sessions.
    A new session starts whenever there's a gap of >= gap_hours.
    """
    if not entries:
        return []

    sessions = []
    current_session = [entries[0]]

    for prev, curr in zip(entries, entries[1:]):
        prev_ts = prev[0]
        curr_ts = curr[0]
        if curr_ts - prev_ts >= timedelta(hours=gap_hours):
            sessions.append(current_session)
            current_session = [curr]
        else:
            current_session.append(curr)

    sessions.append(current_session)
    return sessions


def build_session_json(
    session: list[tuple[datetime, str, str]],
    your_name: str | None,
    first_speaker: str,
) -> dict:
    """
    Convert a session (list of parsed messages) into the MemoryManager JSON format.

    Role assignment:
        - If your_name is set:  your_name → "user",  everyone else → "assistant"
        - Otherwise:            first_speaker → "user",  everyone else → "assistant"
    """
    speaker_as_user = (your_name or first_speaker).strip().lower()
    session_start = session[0][0]
    session_id = session_start.strftime("%Y-%m-%d_%H-%M-%S")

    messages = []
    for ts, sender, text in session:
        role = "user" if sender.strip().lower() == speaker_as_user else "assistant"
        messages.append(
            {
                "role": role,
                "content": text.strip(),
                "timestamp": ts.isoformat(),
            }
        )

    return {
        "session_id": session_id,
        "imported": True,
        "source": "whatsapp_export",
        "messages": messages,
    }


def import_file(
    filepath: Path,
    your_name: str | None,
    gap_hours: float,
    dry_run: bool,
    existing_sessions: set[str],
) -> tuple[int, int]:
    """
    Process one .txt file.
    Returns (sessions_written, messages_processed).
    """
    print(f"\n📂  Parsing: {filepath.name}")
    entries = parse_chat_file(filepath)

    if not entries:
        print("    ⚠️  No parseable messages found.")
        return 0, 0

    print(f"    Found {len(entries)} messages")

    sessions = split_into_sessions(entries, gap_hours)
    print(f"    Split into {len(sessions)} session(s) (gap ≥ {gap_hours}h)")

    # Determine "first speaker" from first message
    first_speaker = entries[0][1]

    sessions_written = 0
    total_messages = 0

    for session in sessions:
        session_start = session[0][0]
        session_id = session_start.strftime("%Y-%m-%d_%H-%M-%S")
        out_filename = f"session_{session_id}.json"
        out_path = CONVERSATIONS_DIR / out_filename

        # Skip if already imported
        if session_id in existing_sessions:
            print(f"    ⏭️  Skipping (already exists): {out_filename}")
            continue

        data = build_session_json(session, your_name, first_speaker)
        msg_count = len(data["messages"])

        print(
            f"    ✅  {out_filename}  —  {msg_count} messages  "
            f"[{session[0][0].strftime('%d %b %Y %H:%M')} → "
            f"{session[-1][0].strftime('%d %b %Y %H:%M')}]"
        )

        if not dry_run:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        sessions_written += 1
        total_messages += msg_count

    return sessions_written, total_messages


def main():
    parser = argparse.ArgumentParser(
        description="Import WhatsApp .txt chat exports into Jarvis memory."
    )
    parser.add_argument(
        "--your-name",
        metavar="NAME",
        default=None,
        help='Your display name in the chat export (e.g. "Gavin"). '
             "Your messages become 'user' role; others become 'assistant'.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print summary without writing any files.",
    )
    parser.add_argument(
        "--gap-hours",
        type=float,
        default=DEFAULT_SESSION_GAP_HOURS,
        help=f"Hours of silence that start a new session (default: {DEFAULT_SESSION_GAP_HOURS}).",
    )
    args = parser.parse_args()

    # ── Setup ──────────────────────────────────────────────────────
    IMPORT_DIR.mkdir(exist_ok=True)
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

    # Collect existing session IDs to avoid duplicates
    existing_sessions = {
        p.stem.replace("session_", "")
        for p in CONVERSATIONS_DIR.glob("session_*.json")
    }

    txt_files = sorted(IMPORT_DIR.glob("*.txt"))
    if not txt_files:
        print(f"No .txt files found in {IMPORT_DIR}/")
        print("Export a WhatsApp chat (Chat → Export Chat → Without Media)")
        print(f"and copy the .txt file into: {IMPORT_DIR}/")
        sys.exit(0)

    print("=" * 55)
    print("  Jarvis Memory Importer")
    if args.dry_run:
        print("  (DRY RUN — no files will be written)")
    print("=" * 55)
    print(f"  Import dir   : {IMPORT_DIR}")
    print(f"  Output dir   : {CONVERSATIONS_DIR}")
    print(f"  Your name    : {args.your_name or '(auto-detect first speaker)'}")
    print(f"  Session gap  : {args.gap_hours}h")
    print(f"  Files found  : {len(txt_files)}")
    print("=" * 55)

    total_sessions = 0
    total_messages = 0

    for txt_file in txt_files:
        s, m = import_file(
            txt_file,
            your_name=args.your_name,
            gap_hours=args.gap_hours,
            dry_run=args.dry_run,
            existing_sessions=existing_sessions,
        )
        total_sessions += s
        total_messages += m

    print("\n" + "=" * 55)
    action = "Would write" if args.dry_run else "Wrote"
    print(f"  {action} {total_sessions} session(s), {total_messages} message(s) total.")
    if args.dry_run:
        print("  Re-run without --dry-run to save.")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    main()
