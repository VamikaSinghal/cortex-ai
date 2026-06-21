"""
cortex/ingest.py
----------------
Core extraction pipeline. Takes raw text from any source,
calls Claude to extract structured context, returns a dict.
"""

import json
import re
import os
from datetime import datetime
from anthropic import Anthropic

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

EXTRACTION_SYSTEM_PROMPT = """You are a context extraction engine for a personal second brain called Cortex.

Given any text (a conversation, document, message thread, voice transcript, etc.), extract:

- KEY_INSIGHTS: Important ideas, realizations, or knowledge worth remembering (list of strings)
- DECISIONS: Decisions made or conclusions reached (list of strings)
- OPEN_QUESTIONS: Unresolved questions or things to follow up on (list of strings)
- PEOPLE: People mentioned and key context about them — format as {"name": "...", "context": "..."} (list of objects)
- PROJECTS: Projects, products, or work items discussed — format as {"name": "...", "context": "..."} (list of objects)
- ACTION_ITEMS: Concrete next steps or todos (list of strings)
- SUMMARY: 2-3 sentence summary of the overall content (string)

Rules:
- Be concise — capture signal, not noise
- Only include items with real informational value
- Empty lists are fine if a category has nothing
- Return ONLY valid JSON, no markdown fences, no commentary

Example output:
{
  "KEY_INSIGHTS": ["Redis vector search is fast enough for real-time retrieval under 100ms"],
  "DECISIONS": ["Use GitHub instead of Obsidian for note storage"],
  "OPEN_QUESTIONS": ["Does Omi webhook support streaming or only batch?"],
  "PEOPLE": [{"name": "Sarah", "context": "My teammate handling the MCP server"}],
  "PROJECTS": [{"name": "Cortex", "context": "Universal context layer / second brain for AI"}],
  "ACTION_ITEMS": ["Set up Redis Stack with Docker before hacking starts"],
  "SUMMARY": "Discussed architecture for Cortex, a personal context layer. Decided on GitHub for storage and Redis for vector search."
}"""


def extract_context(raw_text: str, source: str = "unknown") -> dict:
    """
    Extract structured context from raw text using Claude.

    Args:
        raw_text: The raw content to process (conversation, doc, transcript, etc.)
        source: Where this came from (e.g. 'claude-chat', 'slack', 'omi', 'notion')

    Returns:
        Dict with keys: KEY_INSIGHTS, DECISIONS, OPEN_QUESTIONS, PEOPLE, PROJECTS, ACTION_ITEMS, SUMMARY
        Plus metadata: _source, _timestamp, _raw_length
    """
    if not raw_text or not raw_text.strip():
        return _empty_extraction(source)

    # Truncate very long inputs — Claude can handle ~180k tokens but we keep costs low
    MAX_INPUT_CHARS = 40_000
    truncated = raw_text[:MAX_INPUT_CHARS]
    if len(raw_text) > MAX_INPUT_CHARS:
        truncated += f"\n\n[... truncated {len(raw_text) - MAX_INPUT_CHARS} chars ...]"

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=2000,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"Source: {source}\nTimestamp: {datetime.now().isoformat()}\n\n---\n\n{truncated}\n\n---\n\nExtract context as JSON:"
        }]
    )

    raw_output = response.content[0].text.strip()

    # Parse JSON — handle cases where Claude wraps in markdown
    extracted = _parse_json_response(raw_output)

    # Add metadata
    extracted["_source"] = source
    extracted["_timestamp"] = datetime.now().isoformat()
    extracted["_raw_length"] = len(raw_text)

    return extracted


def _parse_json_response(text: str) -> dict:
    """Robustly parse JSON from Claude's response."""
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

    # Fallback: return minimal structure with raw text in summary
    return {
        "KEY_INSIGHTS": [],
        "DECISIONS": [],
        "OPEN_QUESTIONS": [],
        "PEOPLE": [],
        "PROJECTS": [],
        "ACTION_ITEMS": [],
        "SUMMARY": text[:500]  # Store raw output as summary
    }


def _empty_extraction(source: str) -> dict:
    return {
        "KEY_INSIGHTS": [],
        "DECISIONS": [],
        "OPEN_QUESTIONS": [],
        "PEOPLE": [],
        "PROJECTS": [],
        "ACTION_ITEMS": [],
        "SUMMARY": "",
        "_source": source,
        "_timestamp": datetime.now().isoformat(),
        "_raw_length": 0
    }


def format_extraction_summary(extracted: dict) -> str:
    """Human-readable summary of what was extracted."""
    lines = [f"📥 Captured from {extracted.get('_source', 'unknown')}"]

    counts = {
        "insights": len(extracted.get("KEY_INSIGHTS", [])),
        "decisions": len(extracted.get("DECISIONS", [])),
        "questions": len(extracted.get("OPEN_QUESTIONS", [])),
        "actions": len(extracted.get("ACTION_ITEMS", [])),
        "people": len(extracted.get("PEOPLE", [])),
        "projects": len(extracted.get("PROJECTS", [])),
    }

    parts = [f"{v} {k}" for k, v in counts.items() if v > 0]
    if parts:
        lines.append("→ " + ", ".join(parts))

    if extracted.get("SUMMARY"):
        lines.append(f'"{extracted["SUMMARY"][:120]}..."')

    return "\n".join(lines)


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sample = """
    Vamika: I'm thinking we should use GitHub instead of Obsidian for Cortex.
    The main reason is that GitHub gives us an API, version history, and it's shareable for the demo.

    Claude: That makes a lot of sense. GitHub Actions could also trigger your ingestion pipeline automatically.
    You could use GitHub Issues to track open questions — they map directly to your get_open_questions() MCP tool.

    Vamika: Yes exactly. And judges can just click the repo link. Let's go with GitHub.
    One thing I'm still not sure about is whether we need a graph visualization or if the file tree is enough.

    Claude: For the hackathon demo, I'd ship a simple D3.js graph in the chat UI rather than relying on GitHub's network graph.
    It'll look better and you control it.

    Vamika: Good call. Let's plan to do that in Phase 5.
    """

    result = extract_context(sample, source="claude-chat")
    print(json.dumps(result, indent=2))
    print("\n" + format_extraction_summary(result))
