"""
cortex/prune_images.py
-----------------------
Stage 3 (final) worker: LLM-driven relevance judgment and image pruning.

Reads staging/<batch_id>/descriptions.json and intent_graph.json.
Judges each non-first image for relevance via Claude Sonnet.
Executes soft-delete (tombstone + byte removal) for images marked delete.
Writes pruning.json and updates meta.json.

Usage:
    from prune_images import prune_batch
    meta = prune_batch("batch_<uuid>")
"""

import json
import logging
import re
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)

STAGING_DIR = Path("staging")
MODEL = "claude-sonnet-4-6"
_FAIL_SAFE_REASON = "model error — fail safe"

JUDGMENT_SYSTEM_PROMPT = """You are a relevance filter for a personal AI assistant's visual memory pipeline.

Given a set of intent-graph nodes and a set of image descriptions, judge whether each image should be KEPT or DELETED from the memory store.

Keep an image if its visual description adds meaningful detail not already captured in the text of its linked intent-graph nodes.

Delete an image if:
- Its description is redundant with the text of its linked nodes (the node text already captures the key information)
- It is not linked to any intent-graph node (default: delete — no context means no reason to retain the visual frame)

Return ONLY a valid JSON array — no markdown fences, no explanation:
[{"id": "<image_id>", "decision": "keep" | "delete", "reason": "<brief reason for debugging>"}]

Every image submitted must appear in your response exactly once. Reasons must be non-empty."""


# ── First-image guard ─────────────────────────────────────────────────────────

def is_protected(record: dict) -> bool:
    """Returns True iff this record is the first-in-batch image (never deletable)."""
    return record.get("is_first_in_batch") is True


# ── Judgment call ─────────────────────────────────────────────────────────────

def _build_judgment_prompt(descriptions: list, nodes: list) -> str:
    lines = ["INTENT-GRAPH NODES:"]
    if nodes:
        for node in nodes:
            node_type = node.get("node_type", "Unknown")
            desc = node.get("description", "")
            node_id = node.get("id", "?")
            if "started_at" in node and "ended_at" in node:
                time_info = f"span {node['started_at']}–{node['ended_at']}"
            elif "occurred_at" in node:
                time_info = f"at {node['occurred_at']}"
            else:
                time_info = ""
            lines.append(f"  [{node_id}] {node_type}: \"{desc}\" ({time_info})")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("IMAGES TO JUDGE (one response entry per image):")

    # Build reverse map: image_id → node_ids that reference it
    image_to_nodes: dict[str, list[str]] = {}
    for node in nodes:
        for img_id in node.get("related_images", []):
            image_to_nodes.setdefault(img_id, []).append(node.get("id", "?"))

    for desc in descriptions:
        img_id = desc.get("id", "?")
        img_desc = desc.get("description") or "(no description)"
        linked = image_to_nodes.get(img_id, [])
        lines.append(f"  id: \"{img_id}\"")
        lines.append(f"  description: \"{img_desc}\"")
        lines.append(f"  linked_nodes: {json.dumps(linked)}")
        lines.append("")

    return "\n".join(lines)


def _parse_decisions(raw: str) -> list | None:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
    raw = raw.strip()

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

    return None


def judge_images(
    descriptions: list,
    nodes: list,
    _client: anthropic.Anthropic = None,
) -> list:
    """
    Ask Claude Sonnet to judge each non-first image for relevance.
    Returns list of {id, decision, reason} dicts.
    On any failure: returns keep-all with _FAIL_SAFE_REASON (does not raise).
    """
    if not descriptions:
        return []

    if _client is None:
        _client = anthropic.Anthropic()

    prompt_text = _build_judgment_prompt(descriptions, nodes)

    try:
        response = _client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=JUDGMENT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt_text}],
        )
        raw = response.content[0].text if response.content else ""
        result = _parse_decisions(raw)
        if result is None:
            raise ValueError(f"unparseable response: {raw[:200]}")
        return result

    except Exception as exc:
        logger.warning(
            "Relevance judgment failed (%s) — fail safe: keeping all images", exc
        )
        return [
            {"id": d["id"], "decision": "keep", "reason": _FAIL_SAFE_REASON}
            for d in descriptions
        ]


# ── Deletion execution ────────────────────────────────────────────────────────

def execute_pruning(
    batch_id: str,
    decisions: list,
    descriptions: list,
    _client=None,
) -> dict:
    """
    Execute keep/delete decisions against staged files and description records.
    Soft-delete: tombstone record (deleted: true) + remove image byte file.
    The first-image guard is enforced here regardless of model output.
    Returns {deleted, blocked} counts.
    """
    batch_dir = STAGING_DIR / batch_id
    desc_by_id = {d["id"]: d for d in descriptions}
    deleted = 0
    blocked = 0

    for decision in decisions:
        if decision.get("decision") != "delete":
            continue

        img_id = decision.get("id")
        record = desc_by_id.get(img_id)

        if record is None:
            logger.warning("Decision references unknown image id: %s", img_id)
            continue

        if is_protected(record):
            logger.warning(
                "ANOMALY: guard blocked deletion of first-in-batch image: %s", img_id
            )
            blocked += 1
            continue

        # Tombstone the description record
        record["deleted"] = True

        # Delete image byte file
        image_file = batch_dir / img_id
        if image_file.exists():
            image_file.unlink()

        deleted += 1

    return {"deleted": deleted, "blocked": blocked}


# ── Batch orchestrator ────────────────────────────────────────────────────────

def prune_batch(batch_id: str, _client: anthropic.Anthropic = None) -> dict:
    """
    Orchestrate the full pruning stage for a batch.
    Reads descriptions.json + intent_graph.json; judges; executes; persists.
    Returns updated meta dict.
    """
    batch_dir = STAGING_DIR / batch_id

    descriptions: list = json.loads(
        (batch_dir / "descriptions.json").read_text(encoding="utf-8")
    )

    graph_path = batch_dir / "intent_graph.json"
    nodes: list = json.loads(graph_path.read_text(encoding="utf-8")) if graph_path.exists() else []

    first_record = next((d for d in descriptions if d.get("is_first_in_batch")), None)
    non_first = [d for d in descriptions if not d.get("is_first_in_batch")]

    if _client is None:
        _client = anthropic.Anthropic()

    decisions = judge_images(non_first, nodes, _client=_client)

    # Detect fail-safe: judge_images returned sentinel keeps for all non-first images
    is_fail_safe = (
        len(non_first) > 0
        and len(decisions) == len(non_first)
        and all(d.get("reason") == _FAIL_SAFE_REASON for d in decisions)
    )

    if not is_fail_safe:
        execute_pruning(batch_id, decisions, descriptions, _client=_client)

    # Write updated descriptions (with any tombstones)
    (batch_dir / "descriptions.json").write_text(
        json.dumps(descriptions, indent=2), encoding="utf-8"
    )

    # Build full pruning log (first-in-batch entry prepended)
    all_decisions = []
    if first_record:
        all_decisions.append({
            "id": first_record["id"],
            "decision": "keep",
            "reason": "first in batch — always retained",
        })
    all_decisions.extend(decisions)

    (batch_dir / "pruning.json").write_text(
        json.dumps(all_decisions, indent=2), encoding="utf-8"
    )

    kept_count = sum(1 for d in all_decisions if d.get("decision") == "keep")
    deleted_count = sum(1 for d in all_decisions if d.get("decision") == "delete")
    pruning_status = "failed" if is_fail_safe else "complete"

    meta_path = batch_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["pruning_status"] = pruning_status
    meta["kept_count"] = kept_count
    meta["deleted_count"] = deleted_count
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return meta
