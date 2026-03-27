#!/usr/bin/env python3
"""
Mini Jarvis — Fully Offline AI Assistant
A local ChatGPT-like assistant powered by Ollama + Mistral.
Features: streaming chat, conversation memory, user preferences,
          and a fully customizable personality system.
"""

import json
import sys
import requests
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config import OLLAMA_API_URL, MODEL_NAME, SYSTEM_IDENTITY
from memory import MemoryManager
from preferences import PreferencesManager
from personality import PersonalityManager


console = Console()


# ── Ollama API ───────────────────────────────────────────────────

def check_ollama_connection() -> bool:
    """Check if Ollama server is running and model is available."""
    try:
        resp = requests.get(f"{OLLAMA_API_URL}/api/tags", timeout=5)
        if resp.status_code != 200:
            return False
        models = [m["name"] for m in resp.json().get("models", [])]
        if not any(MODEL_NAME in m for m in models):
            console.print(
                f"\n[red]✗ Model '{MODEL_NAME}' not found.[/red]\n"
                f"  Available models: {', '.join(models) if models else 'none'}\n"
                f"  Run: [bold]ollama pull {MODEL_NAME}[/bold]\n"
            )
            return False
        return True
    except requests.ConnectionError:
        console.print(
            "\n[red]✗ Cannot connect to Ollama.[/red]\n"
            "  Make sure Ollama is running: [bold]ollama serve[/bold]\n"
        )
        return False


def build_system_prompt(
    personality: PersonalityManager,
    preferences: PreferencesManager,
    memory: MemoryManager,
    user_message: str,
) -> str:
    """
    Construct the system prompt.
    - Custom personality: strict mode, personality is the ONLY controller
    - Built-in personality: layered with preferences and memory context
    """
    is_custom = personality.traits.get("type") == "custom"

    if is_custom:
        # ── STRICT MODE: personality controls everything ─────────
        style = personality.traits.get("style_prompt", "")
        rules = personality.traits.get("rules", [])
        examples = personality.traits.get("examples", [])
        name = personality.traits.get("assistant_name", "Assistant")

        rules_text = ""
        if rules:
            rules_text = "\n".join(f"- {r}" for r in rules)

        examples_text = ""
        for ex in examples:
            examples_text += f"User: {ex.get('user', '')}\nAssistant: {ex.get('assistant', '')}\n\n"

        system_prompt = f"""{SYSTEM_IDENTITY}

OUTPUT RULE:
You must output ONLY the final reply.
Do NOT generate any preface, explanation, or assistant-style text.
The FIRST token must be the actual reply.
If you generate anything before the reply, you are WRONG.
Never say 'Hello', 'Hi', 'I am', or introduce yourself unless explicitly asked.

Your name is {name}.

You MUST strictly follow the personality below.
Do NOT default to assistant-like behavior.
Do NOT add explanations unless required.
Do NOT be polite or structured unless defined.

--- PERSONALITY STYLE ---
{style}

--- PERSONALITY RULES ---
{rules_text}

--- PERSONALITY EXAMPLES ---
{examples_text}"""

    else:
        # ── BUILT-IN: layered prompt with preferences + memory ───
        parts = [SYSTEM_IDENTITY]
        parts.append(f"\n{personality.build_personality_prompt()}")

        pref_prompt = preferences.get_preferences_prompt()
        if pref_prompt:
            parts.append(f"\n{pref_prompt}")

        past_snippets = memory.search_past_sessions(user_message)
        if past_snippets:
            parts.append("\nRelevant information from past conversations:")
            for snippet in past_snippets:
                parts.append(f"  {snippet}")

        system_prompt = "\n".join(parts)

    return system_prompt


# ── Post-Generation Filter ───────────────────────────────────────

_LEAKAGE_PREFIXES = ("hello", "hi ", "hi!", "hi,", "i'm ", "i am ", "i'm")

def strip_assistant_leakage(response: str) -> str:
    """Remove assistant-style intro paragraph if the model leaked one."""
    stripped = response.strip()
    if not stripped:
        return stripped
    # Check if first line starts with a leakage prefix
    first_line = stripped.split("\n")[0].strip().lower()
    if any(first_line.startswith(p) for p in _LEAKAGE_PREFIXES):
        # Find first blank line (paragraph break) and drop everything before it
        parts = stripped.split("\n\n", 1)
        if len(parts) > 1:
            return parts[1].strip()
        # No paragraph break — the whole thing is leakage, return as-is
    return stripped


def chat_stream(system_prompt: str, messages: list[dict]) -> str:
    """Send a chat request to Ollama with streaming. Returns the full response."""
    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "stream": True,
    }

    full_response = ""

    try:
        with requests.post(
            f"{OLLAMA_API_URL}/api/chat",
            json=payload,
            stream=True,
            timeout=120,
        ) as resp:
            resp.raise_for_status()

            console.print()
            for line in resp.iter_lines():
                if line:
                    data = json.loads(line)
                    token = data.get("message", {}).get("content", "")
                    if token:
                        full_response += token
                        console.print(token, end="", highlight=False)

                    if data.get("done", False):
                        break

            console.print()

    except requests.ConnectionError:
        console.print("[red]Connection lost to Ollama. Is it still running?[/red]")
        return ""
    except requests.Timeout:
        console.print("[yellow]Response timed out. Try a shorter question.[/yellow]")
        return ""

    return strip_assistant_leakage(full_response)


# ── Slash Commands ───────────────────────────────────────────────

def handle_command(
    command: str,
    memory: MemoryManager,
    preferences: PreferencesManager,
    personality: PersonalityManager,
) -> bool:
    """Handle slash commands. Returns True if a command was handled."""
    parts = command.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    # ── Session commands ─────────────────────────────────────────
    if cmd in ("/exit", "/quit"):
        console.print(f"\n[cyan]{personality.name} says goodbye! 👋[/cyan]\n")
        sys.exit(0)

    elif cmd == "/clear":
        memory.clear_session()
        console.print("[dim]Session cleared. Starting fresh.[/dim]\n")
        return True

    elif cmd == "/history":
        console.print(Panel(
            memory.get_session_history_summary(),
            title="📜 Current Session History",
            border_style="blue",
        ))
        return True

    # ── Preferences ──────────────────────────────────────────────
    elif cmd in ("/preferences", "/prefs"):
        console.print(Panel(
            preferences.get_display_summary(),
            title="⚙️  Your Preferences",
            border_style="green",
        ))
        return True

    # ── Personality commands ─────────────────────────────────────
    elif cmd == "/personality":
        sub_parts = arg.split(maxsplit=1)
        sub_cmd = sub_parts[0].lower() if sub_parts else ""
        sub_arg = sub_parts[1].strip() if len(sub_parts) > 1 else ""

        if sub_cmd == "switch":
            if not sub_arg:
                console.print("[dim]Usage: /personality switch <name>[/dim]")
            else:
                result = personality.switch(sub_arg)
                console.print(f"[magenta]🎭 {result}[/magenta]")

        elif sub_cmd == "create":
            _run_personality_create(personality)

        elif sub_cmd == "list":
            console.print(Panel(
                personality.list_personalities(),
                title="🎭 All Personalities",
                border_style="magenta",
            ))

        elif sub_cmd == "delete":
            if not sub_arg:
                console.print("[dim]Usage: /personality delete <name>[/dim]")
            else:
                result = personality.delete_personality(sub_arg)
                console.print(f"[magenta]🗑️  {result}[/magenta]")

        else:
            # No subcommand — show current personality
            console.print(Panel(
                personality.get_display_summary(),
                title=f"🎭 {personality.name}'s Personality",
                border_style="magenta",
            ))
        return True

    elif cmd == "/name":
        if not arg:
            console.print(f"[dim]Current name: {personality.name}[/dim]")
            console.print("[dim]Usage: /name <new name>[/dim]")
        else:
            result = personality.set_name(arg)
            console.print(f"[magenta]🎭 {result}[/magenta]")
        return True

    elif cmd == "/humor":
        if not arg:
            console.print(f"[dim]Current humor: {personality.humor}/10[/dim]")
            console.print("[dim]Usage: /humor <0-10>[/dim]")
        else:
            try:
                result = personality.set_humor(int(arg))
                console.print(f"[magenta]😄 {result}[/magenta]")
            except ValueError:
                console.print("[red]Please provide a number 0-10[/red]")
        return True

    elif cmd == "/sarcasm":
        if not arg:
            console.print(f"[dim]Current sarcasm: {personality.sarcasm}/10[/dim]")
            console.print("[dim]Usage: /sarcasm <0-10>[/dim]")
        else:
            try:
                result = personality.set_sarcasm(int(arg))
                console.print(f"[magenta]😏 {result}[/magenta]")
            except ValueError:
                console.print("[red]Please provide a number 0-10[/red]")
        return True

    elif cmd == "/tone":
        if not arg:
            console.print(f"[dim]Current tone: {personality.tone}[/dim]")
            console.print("[dim]Usage: /tone <formal|casual|friendly|aggressive|professional>[/dim]")
        else:
            result = personality.set_tone(arg)
            console.print(f"[magenta]🎨 {result}[/magenta]")
        return True

    elif cmd == "/verbosity":
        if not arg:
            console.print(f"[dim]Current verbosity: {personality.verbosity}/10[/dim]")
            console.print("[dim]Usage: /verbosity <1-10>[/dim]")
        else:
            try:
                result = personality.set_verbosity(int(arg))
                console.print(f"[magenta]📝 {result}[/magenta]")
            except ValueError:
                console.print("[red]Please provide a number 1-10[/red]")
        return True

    elif cmd == "/reset":
        result = personality.reset()
        console.print(f"[magenta]🔄 {result}[/magenta]")
        return True

    elif cmd == "/help":
        help_table = Table(show_header=True, header_style="bold yellow", border_style="yellow", title="📖 Commands")
        help_table.add_column("Command", style="bold")
        help_table.add_column("Description")

        help_table.add_row("/clear", "Start a new conversation")
        help_table.add_row("/history", "Show current session messages")
        help_table.add_row("/preferences", "Show stored user preferences")
        help_table.add_row("", "")
        help_table.add_row("/personality", "Show active personality settings")
        help_table.add_row("/personality list", "List all saved personalities")
        help_table.add_row("/personality switch <name>", "Switch active personality")
        help_table.add_row("/personality create", "Create a new personality (interactive)")
        help_table.add_row("/personality delete <name>", "Delete a saved personality")
        help_table.add_row("", "")
        help_table.add_row("/name <name>", "Change assistant's name")
        help_table.add_row("/humor <0-10>", "Set humor level")
        help_table.add_row("/sarcasm <0-10>", "Set sarcasm level")
        help_table.add_row("/tone <tone>", "Set tone (formal/casual/friendly/aggressive/professional)")
        help_table.add_row("/verbosity <1-10>", "Set response detail level")
        help_table.add_row("/reset", "Reset active personality to defaults")
        help_table.add_row("", "")
        help_table.add_row("/help", "Show this help")
        help_table.add_row("/exit", "Quit the assistant")

        console.print(help_table)
        return True

    return False


def _run_personality_create(personality: PersonalityManager):
    """Interactive flow for creating a new personality."""
    console.print("[bold magenta]── Create New Personality ──[/bold magenta]")

    # Name
    pname = console.input("[magenta]Personality ID[/magenta] (e.g. friend_mode): ").strip()
    if not pname:
        console.print("[red]Cancelled (empty name).[/red]")
        return

    # Type
    ptype = console.input("[magenta]Type[/magenta] (built_in / custom): ").strip().lower()
    if ptype not in ("built_in", "custom"):
        console.print("[red]Invalid type. Must be 'built_in' or 'custom'.[/red]")
        return

    # Assistant display name
    aname = console.input("[magenta]Assistant name[/magenta] (display name): ").strip() or pname.title()

    if ptype == "built_in":
        # Gather built_in traits
        tone = console.input("[magenta]Tone[/magenta] (formal/casual/friendly/aggressive/professional) [friendly]: ").strip().lower() or "friendly"
        try:
            humor = int(console.input("[magenta]Humor[/magenta] (0-10) [3]: ").strip() or "3")
        except ValueError:
            humor = 3
        try:
            sarcasm = int(console.input("[magenta]Sarcasm[/magenta] (0-10) [0]: ").strip() or "0")
        except ValueError:
            sarcasm = 0
        try:
            verbosity = int(console.input("[magenta]Verbosity[/magenta] (1-10) [5]: ").strip() or "5")
        except ValueError:
            verbosity = 5

        result = personality.create_builtin(pname, aname, tone, humor, sarcasm, verbosity)
        console.print(f"[magenta]✨ {result}[/magenta]")

    else:  # custom
        # Style prompt
        style = console.input("[magenta]Style prompt[/magenta] (describe how it should talk): ").strip()

        # Examples (few-shot)
        console.print("[dim]Enter 3–5 example exchanges. Type 'done' to stop.[/dim]")
        examples = []
        for i in range(5):
            user_ex = console.input(f"[magenta]  Example {i+1} User[/magenta]: ").strip()
            if user_ex.lower() == "done" or not user_ex:
                break
            asst_ex = console.input(f"[magenta]  Example {i+1} Assistant[/magenta]: ").strip()
            if asst_ex.lower() == "done" or not asst_ex:
                break
            examples.append({"user": user_ex, "assistant": asst_ex})

        # Rules (optional)
        console.print("[dim]Enter rules (one per line). Type 'done' to stop.[/dim]")
        rules = []
        for i in range(5):
            rule = console.input(f"[magenta]  Rule {i+1}[/magenta]: ").strip()
            if rule.lower() == "done" or not rule:
                break
            rules.append(rule)

        result = personality.create_custom(pname, aname, style, examples, rules)
        console.print(f"[magenta]✨ {result}[/magenta]")


# ── Main Loop ────────────────────────────────────────────────────

def main():
    """Main entry point for the chat assistant."""

    # ── Initialize Systems ───────────────────────────────────────
    personality = PersonalityManager()
    memory = MemoryManager()
    preferences = PreferencesManager()

    # ── Welcome Banner ───────────────────────────────────────────
    console.print()
    console.print(Panel.fit(
        f"[bold cyan]🤖 {personality.name}[/bold cyan]\n"
        "[dim]Fully offline AI assistant · Powered by Ollama + Mistral[/dim]\n"
        "[dim]Type [bold]/help[/bold] for commands · [bold]/exit[/bold] to quit[/dim]",
        border_style="cyan",
    ))
    console.print()

    # ── Check Ollama ─────────────────────────────────────────────
    with console.status("[bold cyan]Connecting to Ollama...", spinner="dots"):
        if not check_ollama_connection():
            sys.exit(1)

    console.print(f"[green]✓ Connected to Ollama[/green] · Model: [bold]{MODEL_NAME}[/bold]")

    # ── Show status ──────────────────────────────────────────────
    past_count = memory.get_past_session_count()
    if past_count > 0:
        console.print(f"[dim]📂 {past_count} past session(s) available for context[/dim]")

    console.print(f"[dim]🎭 Personality: {personality.active_personality_name} — {personality.tone} tone · humor {personality.humor}/10 · sarcasm {personality.sarcasm}/10[/dim]")

    pref_summary = preferences.get_display_summary()
    if pref_summary != "No preferences stored yet.":
        console.print("[dim]⚙️  Loaded user preferences[/dim]")

    console.print()

    # ── Chat Loop ────────────────────────────────────────────────
    while True:
        try:
            prompt_name = personality.name
            user_input = console.input(f"[bold green]You >[/bold green] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print(f"\n\n[cyan]{personality.name} says goodbye! 👋[/cyan]\n")
            break

        if not user_input:
            continue

        # Handle slash commands
        if user_input.startswith("/"):
            handle_command(user_input, memory, preferences, personality)
            continue

        # Detect preferences from user message (skip for custom personalities)
        is_custom = personality.traits.get("type") == "custom"
        if not is_custom:
            pref_feedback = preferences.detect_and_store(user_input)
            if pref_feedback:
                console.print(f"[dim italic]📝 {pref_feedback}[/dim italic]")

        # Build system prompt with all layers
        system_prompt = build_system_prompt(personality, preferences, memory, user_input)


        # Add user message to memory
        memory.add_message("user", user_input)

        # Get context messages for the LLM
        context = memory.get_context_messages()

        # Stream response with dynamic name
        console.print(f"[bold cyan]{personality.name} >[/bold cyan]", end="")

        response = chat_stream(system_prompt, context)

        if response:
            memory.add_message("assistant", response)

        console.print()  # Spacing between exchanges


if __name__ == "__main__":
    main()
