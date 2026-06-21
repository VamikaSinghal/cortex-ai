# 🧠 Cortex — Universal Context Layer

> *One layer. Every AI knows you.*

Built at UC Berkeley AI Hackathon 2026.

---

## The Problem

Every AI you talk to starts from zero. Every app you use forgets what the others know. Your second brain is already there — it's just scattered across 8 silos.

## What Cortex Does

Cortex is a **persistent context layer** that:

1. **Captures** everything — select any text in any app, press `Cmd+Shift+V`, it's saved
2. **Processes** with Claude — extracts entities, decisions, insights, open questions
3. **Stores** in a GitHub repo (version-controlled markdown) + Redis vector search
4. **Exposes as an MCP server** — so Claude, and any AI, can call `get_context(query)` and instantly know your full history

## Demo

You walk up to the judges and say:

*"Three weeks ago I was in a meeting discussing a startup idea. Watch what happens when I ask Claude about it now."*

Claude — through Cortex's MCP — pulls the transcript, the Slack thread, the Notion note, and a ChatGPT conversation where you refined the model. It surfaces them as one coherent answer with timestamps and sources.

*"And it's been doing this passively — I didn't tag anything, I didn't organize anything. It just knew."*

---

## Architecture

```
CAPTURE LAYER
  Global hotkey (Cmd+Shift+V) — any app, any text
  Claude Desktop (auto-saves via MCP + Project instructions)
  Omi wearable — ambient audio transcripts
  Meta Ray-Bans — visual context via VisionClaw
          ↓
PROCESSING LAYER — Claude API
  Extract: entities, decisions, insights, open questions
  Tag: source, timestamp, topic, people
  Link: connect related ideas across sources
          ↓
STORAGE LAYER
  GitHub repo     — markdown files, version-controlled, browsable
  Redis Stack     — vector embeddings for semantic search
  SQLite          — metadata index
          ↓
QUERY LAYER — MCP Server
  get_context(query)         → semantic search across all memory
  get_recent(hours)          → what happened lately
  get_about_person(name)     → everything about someone
  get_open_questions()       → unresolved threads → GitHub Issues
```

---

## Project Structure

```
cortex/
├── ingest.py          # Claude extraction pipeline (raw text → structured context)
├── github_store.py    # Push notes to GitHub repo via API
├── redis_store.py     # Voyage AI embeddings + Redis vector search
├── mcp_server.py      # MCP server — exposes Cortex to any AI
├── capture.py         # Global hotkey menu bar app (Cmd+Shift+V)
├── webhook.py         # FastAPI webhook for ChatGPT/Gemini live capture
├── requirements.txt
├── .env.example
└── SETUP.md           # Full setup guide
```

---

## Quickstart

```bash
git clone https://github.com/VamikaSinghal/cortex-ai
cd cortex-ai

python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Fill in: ANTHROPIC_API_KEY, GITHUB_TOKEN, CORTEX_REPO, VOYAGE_API_KEY

# Start Redis
docker run -d -p 6379:6379 --name cortex-redis redis/redis-stack

# Init GitHub repo structure + Redis index
python github_store.py
python redis_store.py

# Start the MCP server (connect to Claude Desktop via claude_desktop_config.json)
python mcp_server.py

# Start the global capture tool (menu bar app)
python capture.py
```

See [SETUP.md](SETUP.md) for the full step-by-step guide including Claude Desktop config and Claude Project setup.

---

## Tech Stack

| Layer | Tool |
|---|---|
| AI / reasoning | Claude (Anthropic API) |
| Embeddings | Voyage AI `voyage-3` |
| Vector search | Redis Stack |
| Note storage | GitHub repo (markdown) |
| MCP server | Python `mcp` SDK |
| Menu bar app | `rumps` + `pynput` |
| Wearable audio | Omi |
| Wearable vision | Meta Ray-Bans + VisionClaw |
| Observability | Arize Phoenix |

---

## Prize Tracks

- 🏆 **Ddoski's Toolbox** — ultimate productivity/knowledge tool
- 🤖 **Anthropic** — Claude as reasoning engine, MCP server is Claude-native
- 🔴 **Redis** — vector search is the core memory retrieval mechanism
- 📊 **Arize** — telemetry on every context retrieval call

---

## Team

Built by Vamika Singhal at UC Berkeley AI Hackathon 2026.
