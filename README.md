# 🤖 Mini Jarvis — Fully Offline AI Assistant

A lightweight, fully customizable, 100% offline ChatGPT-like assistant powered by **Ollama + Mistral 7B**.

---

## Features

- 💬 **Natural chat** with streaming responses
- 🧠 **Conversation memory** — persists across sessions, searches past conversations
- 🎭 **Multiple personalities** — built-in (name, humor, sarcasm, tone) + custom (style, examples, rules)
- ⚙️ **User preference learning** — detects your name, preferred tone, language
- 🔒 **100% offline** — no API keys, no cloud, no internet after setup
- ✨ **Rich terminal UI** — colored output, panels, progress bars

---

## Quick Start

### Step 1: Install Ollama

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows — download from https://ollama.com/download
```

### Step 2: Start Ollama & Download the Model

```bash
# Start the Ollama server
ollama serve

# In a NEW terminal, pull Mistral 7B (~4.1GB download, one-time)
ollama pull mistral
```

**Verify it works:**
```bash
ollama run mistral "Say hello in one sentence"
```

### Step 3: Set Up Python

```bash
cd "Mini jarvis LLM"

# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate        # macOS/Linux
# venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt
```

### Step 4: Run Jarvis

```bash
python jarvis.py
```

You'll see:
```
╭─ 🤖 Jarvis ─────────────────────────────────╮
│ Fully offline AI assistant                    │
│ Type /help for commands · /exit to quit       │
╰───────────────────────────────────────────────╯
✓ Connected to Ollama · Model: mistral
🎭 Personality: friendly tone · humor 3/10 · sarcasm 0/10

You > Hello!
Jarvis > Hi there! How can I help you today?
```

---

## All Commands

| Command | What it does |
|---|---|
| `/help` | Show all commands |
| `/clear` | Start a new conversation |
| `/history` | Show current session messages |
| `/preferences` | Show your stored preferences |
| `/personality` | Show active personality settings |
| `/personality list` | List all saved personalities |
| `/personality switch <name>` | Switch active personality |
| `/personality create` | Create a new personality (interactive) |
| `/personality delete <name>` | Delete a saved personality |
| `/name <name>` | Change active personality's name |
| `/humor <0-10>` | Set humor level (0 = serious, 10 = max comedy) |
| `/sarcasm <0-10>` | Set sarcasm level (0 = none, 10 = dripping) |
| `/tone <tone>` | Set tone: `formal`, `casual`, `friendly`, `aggressive`, `professional` |
| `/verbosity <1-10>` | Set detail level (1 = ultra-brief, 10 = very detailed) |
| `/reset` | Reset active personality to defaults |
| `/exit` | Quit |

---

## Personality System

Two personality types:

### 1. Built-in (default)
Uses configurable numeric traits: name, humor, sarcasm, tone, verbosity.

### 2. Custom
Uses a style prompt, few-shot examples, and optional rules.

### Managing Personalities

```
/personality list              → See all saved personalities
/personality switch friend_mode → Switch active personality
/personality create            → Interactive creation wizard
/personality delete old_one    → Delete a personality
```

### Creating a Custom Personality

```
You > /personality create
── Create New Personality ──
Personality ID: friend_mode
Type (built_in / custom): custom
Assistant name: Buddy
Style prompt: Talk like a close friend. Use informal language and be supportive.
Enter 3–5 example exchanges. Type 'done' to stop.
  Example 1 User: How are you?
  Example 1 Assistant: Doing great bro! 😄 What about you?
  Example 2 User: I failed my test
  Example 2 Assistant: That sucks 😔 But one test doesn't define you!
  Example 3 User: done
Enter rules (one per line). Type 'done' to stop.
  Rule 1: Always be supportive
  Rule 2: Use emojis occasionally
  Rule 3: done
✨ Custom personality 'friend_mode' created!

You > /personality switch friend_mode
🎭 Switched to 'friend_mode' (custom)
```

### Storage (`data/personality.json`)

```json
{
  "active_personality": "default",
  "personalities": {
    "default": {
      "type": "built_in",
      "assistant_name": "Jarvis",
      "humor": 3,
      "sarcasm": 0,
      "tone": "friendly",
      "verbosity": 5
    },
    "friend_mode": {
      "type": "custom",
      "assistant_name": "Buddy",
      "style_prompt": "Talk like a close friend. Use informal language and be supportive.",
      "examples": [
        {"user": "How are you?", "assistant": "Doing great bro! 😄"}
      ],
      "rules": ["Always be supportive", "Use emojis occasionally"]
    }
  }
}
```

Edit this file directly or use slash commands — both work.

---

## Memory System (How It Works)

### Current Session
- Every message (yours and the assistant's) is saved automatically
- The last 20 messages are included as context in each new prompt
- Use `/history` to view the current session

### Past Sessions
- Each session is saved as a JSON file in `data/conversations/`
- When you ask a question, the system searches past sessions for relevant context
- Matching snippets (up to 3) are injected into the system prompt
- This gives the assistant context from past conversations

### User Preferences
- The assistant auto-detects preferences from your messages:
  - Say **"Call me Gabe"** → it remembers your name
  - Say **"I prefer concise answers"** → it adjusts style
  - Say **"Reply in Spanish"** → it switches language
- Saved to `data/preferences.json`, persists forever

---

## Project Structure

```
Mini jarvis LLM/
├── jarvis.py           # Main chat application + commands
├── personality.py      # Personality engine (name, humor, sarcasm, tone)
├── memory.py           # Conversation memory + cross-session search
├── preferences.py      # Auto-detect user preferences
├── config.py           # Constants (model, paths, limits, base prompt)
├── requirements.txt    # Python dependencies
├── README.md           # This file
├── data/
│   ├── conversations/  # Saved session files (auto-created)
│   ├── preferences.json
│   └── personality.json
└── venv/               # Python virtual environment
```

---

## Optimization

| Tip | How |
|---|---|
| **Low RAM (< 8GB)** | Use `ollama pull phi3:mini` then set `MODEL_NAME = "phi3:mini"` in `config.py` |
| **GPU acceleration** | Ollama auto-detects your GPU — no config needed |
| **Faster responses** | Lower `/verbosity` to 2-3 for shorter answers |
| **Context limit** | Reduce `MAX_CURRENT_MESSAGES` in `config.py` (default: 20) |

---

## Fully Offline

After setup, **zero internet required**:
- Ollama runs a local server at `http://localhost:11434`
- Model weights stored locally (~4.1GB)
- Memory, preferences, personality all stored in local JSON files
- No API keys, no cloud, no telemetry

**Test:** Turn off WiFi → run `python jarvis.py` → works identically.

---

## Troubleshooting

### "Cannot connect to Ollama"
```bash
# Start the Ollama server
ollama serve
# Then retry
python jarvis.py
```

### "Model not found"
```bash
# Pull the model
ollama pull mistral
# Verify
ollama list
```

### "command not found: ollama"
```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh
```

### "pip: command not found" or "No module named venv"
```bash
# macOS
brew install python3

# Ubuntu/Debian
sudo apt install python3 python3-venv python3-pip
```

### Slow responses
- Use a smaller model: `ollama pull phi3:mini`, update `config.py`
- Reduce `MAX_CURRENT_MESSAGES` to 10 in `config.py`
- Lower verbosity: `/verbosity 2`

### Want to change the model?
1. Pull a new model: `ollama pull <model-name>`
2. Edit `MODEL_NAME` in `config.py`
3. Restart `python jarvis.py`

Popular offline models:
- `mistral` — Best balance of quality and speed (recommended)
- `phi3:mini` — Smallest, fastest, good for < 8GB RAM
- `llama3` — Strong reasoning, needs 8GB+ RAM
- `gemma2` — Google's model, good quality
