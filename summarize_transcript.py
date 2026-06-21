"""
cortex/summarize_transcript.py
-------------------------------
Stage 2 worker: summarize a staged batch's transcript into typed intent-graph
nodes (Goal, Interruption, Commitment, ObjectLocation) via Claude Sonnet, then
join each node to contemporaneous image records by timestamp.

Usage:
    from summarize_transcript import summarize_batch
    meta = summarize_batch("batch_<uuid>")
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import anthropic
import requests

logger = logging.getLogger(__name__)

STAGING_DIR = Path("staging")
MODEL = "claude-sonnet-4-6"

# TTC endpoint — override via TTC_API_URL env var if the endpoint changes
TTC_API_URL = os.getenv("TTC_API_URL", "https://api.thetokencompany.com/v1/compress")

SUMMARIZATION_SYSTEM_PROMPT = """You are an intent extraction engine for a personal AI assistant called Recall.

Given a conversation transcript, extract a flat, typed event log of intent-graph nodes.
Return ONLY a valid JSON array — no markdown fences, no explanation.

All node types share the same schema — each node has a single timestamp (occurred_at):

Goal — something the speaker is trying to accomplish:
  {"id": "<unique>", "node_type": "Goal", "description": "...", "speaker": "...", "occurred_at": <unix_int>, "related_images": []}

Interruption — an unexpected break or disruption:
  {"id": "<unique>", "node_type": "Interruption", "description": "...", "speaker": "...", "occurred_at": <unix_int>, "related_images": []}

Commitment — a promise or action item made during the conversation:
  {"id": "<unique>", "node_type": "Commitment", "description": "...", "speaker": "...", "occurred_at": <unix_int>, "related_images": []}

ObjectLocation — where something is or should be placed:
  {"id": "<unique>", "node_type": "ObjectLocation", "description": "...", "speaker": "...", "occurred_at": <unix_int>, "related_images": []}

Rules:
- Only extract nodes that are clearly present in the transcript
- Use the exact UNIX timestamp from the transcript turn in which the event occurs
- An empty array [] is valid if no intent nodes are identifiable
- ALWAYS return valid JSON — the caller cannot handle non-JSON responses"""


# ── TTC Compression ───────────────────────────────────────────────────────────

def compress_transcript(text: str, *, timeout: int = 10) -> str:
    """
    Compress transcript text using The Token Company API.
    Uses accuracy-preserving mode. Falls back to original text on any failure.
    """
    api_key = os.getenv("TTC_API_KEY")
    if not api_key:
        logger.warning("TTC_API_KEY not set — skipping transcript compression")
        return text

    try:
        resp = requests.post(
            TTC_API_URL,
            json={"text": text, "accuracy_mode": True},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json().get("compressed_text", text)
    except Exception as exc:
        logger.warning("TTC compression failed (%s) — using original transcript", exc)
        return text


# ── Timestamp Join ────────────────────────────────────────────────────────────

def join_images_to_nodes(
    nodes: list,
    image_records: list,
    last_turn_window_secs: int = 90,
) -> list:
    """
    Pure function: attach related_images to each node by turn-boundary arithmetic.

    Nodes are sorted by occurred_at. Each node's window:
      - Non-last node i: [nodes[i].occurred_at, nodes[i+1].occurred_at)  (exclusive end)
      - Last node:       [node.occurred_at, node.occurred_at + last_turn_window_secs]

    Images that fall before the first node or after the last node's window are unmatched.
    """
    if not nodes:
        return nodes

    sorted_nodes = sorted(nodes, key=lambda n: n.get("occurred_at", 0))
    matched_image_ids: set[str] = set()

    for i, node in enumerate(sorted_nodes):
        t = node["occurred_at"]
        is_last = i == len(sorted_nodes) - 1

        if is_last:
            joined = [
                img["id"] for img in image_records
                if t <= img["observed_at"] <= t + last_turn_window_secs
            ]
        else:
            next_t = sorted_nodes[i + 1]["occurred_at"]
            joined = [
                img["id"] for img in image_records
                if t <= img["observed_at"] < next_t
            ]

        node["related_images"] = joined
        matched_image_ids.update(joined)

    for img in image_records:
        if img["id"] not in matched_image_ids:
            logger.debug("Unmatched image (no node window covers it): %s", img["id"])

    return nodes


# ── Sonnet Summarization ──────────────────────────────────────────────────────

def _format_transcript(turns: list) -> str:
    """Convert transcript turns to a compact timestamped text for the prompt."""
    lines = []
    for turn in turns:
        lines.append(
            f"[T={turn['timestamp']}] {turn['speaker']}: {turn['text']}"
        )
    return "\n".join(lines)


def _parse_nodes(raw: str) -> list | None:
    """Parse a JSON array from raw Sonnet output. Returns None on failure."""
    raw = raw.strip()
    # Strip markdown fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
    raw = raw.strip()

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    # Fallback: find first [...] block
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

    return None


def summarize_batch(batch_id: str, _client: anthropic.Anthropic = None) -> dict:
    """
    Summarize the transcript for a staged batch into intent-graph nodes.
    Reads transcript.json and descriptions.json; writes intent_graph.json;
    updates meta.json. Returns the updated meta dict.
    """
    batch_dir = STAGING_DIR / batch_id

    # Read transcript
    turns = json.loads((batch_dir / "transcript.json").read_text(encoding="utf-8"))

    # Short-circuit: empty transcript
    if not turns:
        nodes: list = []
        summarization_status = "complete"
    else:
        # Format + compress
        transcript_text = _format_transcript(turns)
        compressed_text = compress_transcript(transcript_text)

        # Call Sonnet
        if _client is None:
            _client = anthropic.Anthropic()

        response = _client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SUMMARIZATION_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Extract intent-graph nodes from this transcript:\n\n{compressed_text}"
                    ),
                }
            ],
        )

        raw_output = response.content[0].text if response.content else ""
        nodes = _parse_nodes(raw_output)

        if nodes is None:
            logger.warning(
                "Sonnet returned unparseable output for %s; raw: %.200s",
                batch_id,
                raw_output,
            )
            nodes = []
            summarization_status = "failed"
        else:
            summarization_status = "complete"

    # Read image records for join (absent → empty list)
    descriptions_path = batch_dir / "descriptions.json"
    if descriptions_path.exists():
        image_records = json.loads(descriptions_path.read_text(encoding="utf-8"))
        # Filter to successful records only
        image_records = [r for r in image_records if r.get("description") is not None]
    else:
        image_records = []

    # Timestamp join — images assigned by turn boundaries; last node gets 90 s tail
    nodes = join_images_to_nodes(nodes, image_records)

    # Read meta for transcript_offset (defaults to 0 for legacy batches without the key)
    meta_path = batch_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    transcript_offset = int(meta.get("transcript_offset", 0))

    # Filter: only persist nodes introduced in the new turns (those at or after the
    # first new turn's timestamp). Prior turns supply Sonnet context but must not
    # generate duplicate nodes in the intent graph.
    if transcript_offset > 0 and turns and transcript_offset < len(turns):
        cutoff = turns[transcript_offset]["timestamp"]
        nodes = [n for n in nodes if n.get("occurred_at", 0) >= cutoff]

    # Persist
    (batch_dir / "intent_graph.json").write_text(
        json.dumps(nodes, indent=2), encoding="utf-8"
    )

    meta["summarization_status"] = summarization_status
    meta["node_count"] = len(nodes)
    meta["transcript_offset"] = transcript_offset
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return meta
