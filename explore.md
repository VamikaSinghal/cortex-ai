# Cortex — Codebase & Architecture Exploration

> Built at UC Berkeley AI Hackathon 2026. Team: Vamika Singhal, Aaron, Vincent, Ibrahim.

---

## What It Is

Cortex is a **personal second brain with universal AI access**. The thesis: every AI you talk to starts from zero. Cortex breaks that by maintaining a persistent, queryable memory layer that any AI can tap into via MCP.

One sentence: *select text anywhere → Cmd+Shift+V → every future AI knows about it.*

---

## File Map

```
cortex-ai/
├── capture.py                    # macOS menu bar app + global hotkey
├── ingest.py                     # Claude extraction pipeline (core brain)
├── github_store.py               # Persistence layer → GitHub REST API
├── redis_store.py                # Vector search layer → Redis Stack
├── mcp_server.py                 # MCP server exposing tools to Claude Desktop
├── CORTEX_PROJECT_SYSTEM_PROMPT.txt  # Claude Project system prompt (auto-save behavior)
├── requirements.txt
├── .env.example
└── SETUP.md
```

**Not yet implemented** (mentioned in README/SETUP but absent from repo):
- `webhook.py` — FastAPI server for Omi wearable + live ChatGPT capture
- `ingest_batch.py` — Batch ingestion from ChatGPT exports / Notion

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        CAPTURE LAYER                            │
│                                                                 │
│  Cmd+Shift+V      Claude Desktop MCP       [planned]            │
│  (clipboard)      (auto-save via prompt)   Omi webhook          │
│       │                   │                    │                │
└───────┴───────────────────┴────────────────────┘                │
                            │                                     │
                            ▼                                     │
┌─────────────────────────────────────────────────────────────────┤
│                     PROCESSING LAYER                            │
│                                                                 │
│   ingest.py → Claude claude-opus-4-5                           │
│                                                                 │
│   Input: raw text (any source)                                  │
│   Output: {                                                     │
│     KEY_INSIGHTS, DECISIONS, OPEN_QUESTIONS,                    │
│     PEOPLE, PROJECTS, ACTION_ITEMS, SUMMARY                     │
│   }                                                             │
└─────────────────────────────────────────────────────────────────┤
                            │
              ┌─────────────┴──────────────┐
              ▼                            ▼
┌─────────────────────┐      ┌─────────────────────────────────┐
│   GITHUB STORE      │      │         REDIS STORE             │
│   github_store.py   │      │         redis_store.py          │
│                     │      │                                 │
│  One .md file per   │      │  Each item embedded with        │
│  insight/decision/  │      │  Voyage AI voyage-3 (1024-dim)  │
│  question/etc.      │      │  Stored in Redis Stack          │
│                     │      │  KNN cosine similarity search   │
│  notes/             │      │                                 │
│  ├── insights/      │      │  Index: cortex_idx              │
│  ├── decisions/     │      │  Fields: content, source,       │
│  ├── questions/     │      │    type, tags, timestamp_unix,  │
│  ├── people/        │      │    embedding (FLOAT32)          │
│  ├── actions/       │      │                                 │
│  └── sessions/      │      │                                 │
│                     │      │                                 │
│  + GitHub Issues    │      │                                 │
│    for open ?s      │      │                                 │
└─────────────────────┘      └─────────────────────────────────┘
              │                            │
              └─────────────┬──────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      QUERY LAYER (MCP)                          │
│                      mcp_server.py                              │
│                                                                 │
│  save_to_cortex(content, source)   → extract + store           │
│  get_context(query, top_k)         → semantic search           │
│  get_recent(hours)                 → time-windowed retrieval    │
│  get_open_questions()              → unresolved threads         │
│  get_about_person(name)            → person-filtered search     │
│                                                                 │
│  Transport: stdio (Claude Desktop via claude_desktop_config.json)│
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Flow — Capture

```
User: selects text → Cmd+C → Cmd+Shift+V
                                   │
                          capture.py
                          ├── reads clipboard (pyperclip)
                          ├── detects frontmost app (osascript)
                          └── calls ingest.extract_context(text, source)
                                         │
                                 Claude claude-opus-4-5
                                 system prompt → JSON extraction
                                         │
                          ┌──────────────┴────────────────┐
                          ▼                               ▼
                 github_store.py                   redis_store.py
                 push_file() per item              embed_and_store()
                 → GitHub REST API                 → Voyage AI embed
                 → .md files committed             → Redis HSET
```

---

## Data Flow — Query

```
Claude Desktop asks: "what did I decide about Redis?"
                │
        mcp_server.py._get_context()
                │
        redis_store.search_context(query, top_k=5)
                │
        Voyage AI embeds the query (voyage-3)
                │
        Redis KNN search: *=>[KNN 5 @embedding $vec AS score]
                │
        Returns: [{content, source, type, timestamp, score}]
                │
        Claude synthesizes and responds
```

---

## Key Design Decisions

**GitHub as primary storage, Redis as search index**
GitHub gives version-controlled, human-readable, shareable markdown. Redis gives sub-100ms semantic search. They're complementary — GitHub is the source of truth, Redis is the retrieval index. Redis failure degrades gracefully (tools return fallback messages with GitHub links).

**Every extracted item stored individually in Redis**
Each insight, decision, question, etc. is a separate Redis hash with its own embedding. This enables fine-grained KNN search and type-filtered queries (`@type:{open-question}`).

**People notes are append-only**
`github_store.py` checks if a person note already exists and appends new context rather than overwriting. This builds up a growing profile over time.

**Open questions → GitHub Issues**
`mcp_server.py` (via `open_github_issue()`) files each open question as a GitHub Issue tagged `open-question`. Unresolved threads live in a trackable, closeable system.

**MCP tool is proactively self-calling**
The `save_to_cortex` tool description explicitly tells Claude to "call this automatically... Don't ask — just call it proactively." The Claude Project system prompt reinforces this with hard rules. The whole UX goal is zero friction.

---

## Extraction Schema (ingest.py)

Claude is asked to return structured JSON with these fields:

| Field | Type | Description |
|---|---|---|
| `KEY_INSIGHTS` | `string[]` | Important ideas worth remembering |
| `DECISIONS` | `string[]` | Conclusions reached |
| `OPEN_QUESTIONS` | `string[]` | Unresolved threads |
| `PEOPLE` | `{name, context}[]` | People mentioned + context |
| `PROJECTS` | `{name, context}[]` | Projects discussed |
| `ACTION_ITEMS` | `string[]` | Concrete next steps |
| `SUMMARY` | `string` | 2-3 sentence summary |
| `_source` | `string` | Added by pipeline (not Claude) |
| `_timestamp` | `string` | ISO timestamp |
| `_raw_length` | `int` | Original text length |

Input is truncated at 40,000 chars to control cost.

---

## Environment Variables

| Variable | Used In | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | `ingest.py` | Claude API for extraction |
| `GITHUB_TOKEN` | `github_store.py` | GitHub REST API auth |
| `CORTEX_REPO` | `github_store.py` | Target repo (e.g. `vamikasinghal/cortex-brain`) |
| `CORTEX_BRANCH` | `github_store.py` | Branch (default: `main`) |
| `VOYAGE_API_KEY` | `redis_store.py` | Voyage AI embeddings |
| `REDIS_URL` | `redis_store.py` | Redis connection (default: `redis://localhost:6379`) |

> Note: `.env.example` lists `OPENAI_API_KEY` but the actual embedding code in `redis_store.py` uses `VOYAGE_API_KEY` (voyageai client). The requirements.txt also lists `openai` but it's not used in the implemented files. This is a residual inconsistency from an earlier design.

---

## Tech Stack

| Layer | Tool |
|---|---|
| Extraction AI | Claude claude-opus-4-5 (Anthropic SDK) |
| Embeddings | Voyage AI `voyage-3` (1024-dim) |
| Vector search | Redis Stack (FLAT index, cosine similarity) |
| Persistence | GitHub REST API (markdown files) |
| MCP transport | `mcp` Python SDK (stdio) |
| macOS menu bar | `rumps` + `pynput` |
| HTTP | `requests` |

---

## Open Threads / Gaps

1. **`webhook.py` not built** — Omi wearable and live ChatGPT capture are designed but unimplemented. SETUP.md has the stub code.

2. **`ingest_batch.py` not built** — Bulk import from ChatGPT export JSON or Notion is planned but missing.

3. **`OPENAI_API_KEY` in `.env.example` but unused** — requirements.txt lists `openai`, `.env.example` documents it, but `redis_store.py` uses `voyageai`. Either the key should be removed or there's a planned OpenAI embeddings fallback.

4. **`PROJECTS` field extracted but never stored** — `ingest.py` extracts `PROJECTS` from text and it appears in the schema, but `github_store.py` and `redis_store.py` don't handle it (no `notes/projects/` writer, no embed loop). The `notes/projects/` folder is initialized in `init_repo_structure()` though.

5. **No Redis → GitHub sync** — If Redis goes down and is restarted, it starts empty. There's no mechanism to rebuild the vector index from GitHub (the source of truth).

6. **Graph visualization** — README mentions a D3.js knowledge graph as a Phase 5 goal. Not started.

7. **Arize Phoenix observability** — Listed in the tech stack table but no instrumentation in the code.
