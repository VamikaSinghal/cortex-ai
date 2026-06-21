"""
cortex/mcp_server.py
--------------------
Cortex MCP server. Connect this to Claude Desktop and any AI that supports MCP.

Tools exposed:
  save_to_cortex(content, source)   — extract + save to GitHub + embed in Redis
  get_context(query)                — semantic search over your second brain
  get_recent(hours)                 — what happened recently
  get_open_questions()              — unresolved threads

Run:  python mcp_server.py
Config: see claude_desktop_config.json
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta

# MCP SDK
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# Cortex modules
from ingest import extract_context, format_extraction_summary
from github_store import save_extracted_context, open_github_issue

# Redis (optional — gracefully degrade if not running)
try:
    from redis_store import embed_and_store, search_context, get_recent_context
    REDIS_AVAILABLE = True
except Exception:
    REDIS_AVAILABLE = False

app = Server("cortex")


# ── Tool definitions ──────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="save_to_cortex",
            description=(
                "Save important context from this conversation to Cortex, Vamika's personal second brain. "
                "Call this automatically whenever the conversation contains: decisions, key insights, "
                "open questions, action items, or information about people/projects worth remembering. "
                "Don't ask — just call it proactively at the end of any substantive exchange."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The full conversation text or content to extract context from. Include as much as possible."
                    },
                    "source": {
                        "type": "string",
                        "description": "Where this came from. Use: 'claude-chat', 'chatgpt', 'gemini', 'slack', 'omi', 'notion', 'google-docs', 'imessage'",
                        "default": "claude-chat"
                    },
                    "file_open_questions_as_issues": {
                        "type": "boolean",
                        "description": "If true, also open GitHub Issues for each open question extracted. Default true.",
                        "default": True
                    }
                },
                "required": ["content"]
            }
        ),
        types.Tool(
            name="get_context",
            description=(
                "Search Vamika's Cortex second brain for relevant context. "
                "Use this when you need to recall past conversations, decisions, or knowledge. "
                "Returns ranked, sourced context chunks with timestamps."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for. Be specific — e.g. 'startup idea from coffee shop' or 'what did I decide about Redis'"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default 5)",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="get_recent",
            description="Get the most recent context captured in Cortex. Use to answer 'what have I been up to?' or 'what did I work on today?'",
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "number",
                        "description": "How many hours back to look (default 24)",
                        "default": 24
                    }
                }
            }
        ),
        types.Tool(
            name="get_open_questions",
            description="Get all unresolved questions and open threads from Cortex. Use when the user asks 'what am I still figuring out?' or 'what are my open questions?'",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        types.Tool(
            name="get_about_person",
            description="Get everything Cortex knows about a specific person.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The person's name"
                    }
                },
                "required": ["name"]
            }
        )
    ]


# ── Tool handlers ─────────────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "save_to_cortex":
            return await _save_to_cortex(arguments)
        elif name == "get_context":
            return await _get_context(arguments)
        elif name == "get_recent":
            return await _get_recent(arguments)
        elif name == "get_open_questions":
            return await _get_open_questions(arguments)
        elif name == "get_about_person":
            return await _get_about_person(arguments)
        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        return [types.TextContent(type="text", text=f"❌ Cortex error: {str(e)}")]


async def _save_to_cortex(args: dict) -> list[types.TextContent]:
    content = args.get("content", "")
    source = args.get("source", "claude-chat")
    file_issues = args.get("file_open_questions_as_issues", True)

    if not content.strip():
        return [types.TextContent(type="text", text="⚠️ Nothing to save — content was empty.")]

    # 1. Extract structured context with Claude
    extracted = extract_context(content, source)

    # 2. Push to GitHub repo
    saved_files = save_extracted_context(extracted, raw_text=content)

    # 3. File open questions as GitHub Issues
    issue_urls = []
    if file_issues:
        for question in extracted.get("OPEN_QUESTIONS", []):
            url = open_github_issue(
                title=f"❓ {question}",
                body=f"**Source:** {source}\n**Captured:** {extracted.get('_timestamp', '')}\n\n> Captured automatically by Cortex",
                labels=["open-question", "cortex"]
            )
            if url:
                issue_urls.append(url)

    # 4. Embed in Redis (if available)
    if REDIS_AVAILABLE:
        try:
            embed_and_store(extracted, raw_text=content)
        except Exception as e:
            pass  # Redis failure is non-blocking

    # Build response
    summary = format_extraction_summary(extracted)
    result_lines = [
        f"✅ Saved to Cortex",
        f"",
        summary,
        f"",
        f"📁 {len(saved_files)} files → github.com/{os.environ.get('CORTEX_REPO', 'your-repo')}",
    ]
    if issue_urls:
        result_lines.append(f"🔖 {len(issue_urls)} GitHub Issues opened for open questions")

    return [types.TextContent(type="text", text="\n".join(result_lines))]


async def _get_context(args: dict) -> list[types.TextContent]:
    query = args.get("query", "")
    top_k = args.get("top_k", 5)

    if not REDIS_AVAILABLE:
        return [types.TextContent(
            type="text",
            text="⚠️ Redis not available — start it with: docker run -p 6379:6379 redis/redis-stack\nFalling back to GitHub search (slower)."
        )]

    results = search_context(query, top_k=top_k)

    if not results:
        return [types.TextContent(type="text", text=f"No context found for: '{query}'")]

    lines = [f"🔍 Context for: '{query}'\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"**{i}. [{r.get('type', 'note')}] from {r.get('source', '?')} · {r.get('timestamp', '')[:10]}**")
        lines.append(r.get("content", ""))
        lines.append("")

    return [types.TextContent(type="text", text="\n".join(lines))]


async def _get_recent(args: dict) -> list[types.TextContent]:
    hours = args.get("hours", 24)

    if not REDIS_AVAILABLE:
        return [types.TextContent(
            type="text",
            text=f"⚠️ Redis not available. Check github.com/{os.environ.get('CORTEX_REPO', 'your-repo')} for recent commits."
        )]

    since = datetime.now() - timedelta(hours=hours)
    results = get_recent_context(since=since)

    if not results:
        return [types.TextContent(type="text", text=f"No context in the last {hours}h.")]

    lines = [f"🕐 Last {hours}h in Cortex:\n"]
    for r in results:
        ts = r.get("timestamp", "")[:16].replace("T", " ")
        lines.append(f"• **{ts}** [{r.get('source', '?')}] {r.get('content', '')[:120]}")

    return [types.TextContent(type="text", text="\n".join(lines))]


async def _get_open_questions(args: dict) -> list[types.TextContent]:
    if not REDIS_AVAILABLE:
        return [types.TextContent(
            type="text",
            text=f"⚠️ Redis not available. Open questions are tracked as GitHub Issues at:\ngithub.com/{os.environ.get('CORTEX_REPO', 'your-repo')}/issues"
        )]

    results = search_context("open question unresolved", top_k=20)
    questions = [r for r in results if r.get("type") == "open-question"]

    if not questions:
        return [types.TextContent(type="text", text="No open questions in Cortex. You're either very decisive or it's empty!")]

    lines = ["❓ Open questions in Cortex:\n"]
    for i, q in enumerate(questions, 1):
        ts = q.get("timestamp", "")[:10]
        lines.append(f"{i}. {q.get('content', '')} _(captured {ts} from {q.get('source', '?')})_")

    return [types.TextContent(type="text", text="\n".join(lines))]


async def _get_about_person(args: dict) -> list[types.TextContent]:
    name = args.get("name", "")
    if not name:
        return [types.TextContent(type="text", text="Please provide a name.")]

    if not REDIS_AVAILABLE:
        return [types.TextContent(
            type="text",
            text=f"⚠️ Redis not available. Check github.com/{os.environ.get('CORTEX_REPO', 'your-repo')}/blob/main/notes/people/{name.lower().replace(' ', '-')}.md"
        )]

    results = search_context(f"person {name}", top_k=10)
    person_results = [r for r in results if name.lower() in r.get("content", "").lower()]

    if not person_results:
        return [types.TextContent(type="text", text=f"Nothing in Cortex about {name} yet.")]

    lines = [f"👤 Everything about {name}:\n"]
    for r in person_results:
        ts = r.get("timestamp", "")[:10]
        lines.append(f"• [{ts} · {r.get('source', '?')}] {r.get('content', '')[:200]}")
        lines.append("")

    return [types.TextContent(type="text", text="\n".join(lines))]


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
