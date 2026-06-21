"""
Unit tests for prune_images.py.

All LLM calls are mocked. is_protected and the deletion guard are tested
directly without mocking.

Run: pytest tests/test_prune.py -v
"""

import json
import struct
import sys
import zlib
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import logging

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import prune_images
import describe_images
import summarize_transcript
from prune_images import (
    execute_pruning,
    is_protected,
    judge_images,
    prune_batch,
    _FAIL_SAFE_REASON,
)

T = 1718900000


# ── Helpers ───────────────────────────────────────────────────────────────────

def minimal_png(rgb: tuple = (100, 149, 237)) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", c)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" + bytes(rgb))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def make_desc(id_: str, desc: str, is_first: bool = False, observed_at: int = T) -> dict:
    return {
        "id": id_,
        "description": desc,
        "observed_at": observed_at,
        "redaction_applied": True,
        "is_first_in_batch": is_first,
        "error": None,
        "related_images": [],
    }


def make_node(id_: str, desc: str, related: list = None) -> dict:
    return {
        "id": id_,
        "node_type": "Goal",
        "description": desc,
        "speaker": "Alice",
        "occurred_at": T,
        "related_images": related or [],
    }


def make_batch(staging: Path, descriptions: list, nodes: list = None, write_images: bool = True) -> str:
    from uuid import uuid4
    batch_id = f"batch_{uuid4()}"
    batch_dir = staging / batch_id
    batch_dir.mkdir()
    (batch_dir / "descriptions.json").write_text(json.dumps(descriptions), encoding="utf-8")
    (batch_dir / "intent_graph.json").write_text(json.dumps(nodes or []), encoding="utf-8")
    (batch_dir / "transcript.json").write_text("[]", encoding="utf-8")
    meta = {"batch_id": batch_id, "image_count": len(descriptions), "received_at": "2024-01-01T00:00:00+00:00"}
    (batch_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    if write_images:
        for d in descriptions:
            (batch_dir / d["id"]).write_bytes(minimal_png())
    return batch_id


def mock_judgment(*decisions) -> MagicMock:
    """Build a mock client whose messages.create returns a JSON list of decisions."""
    r = MagicMock()
    r.content = [MagicMock(text=json.dumps(list(decisions)))]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = r
    return mock_client


@pytest.fixture
def staging(tmp_path, monkeypatch):
    monkeypatch.setattr(prune_images, "STAGING_DIR", tmp_path)
    return tmp_path


# ── Test 6.1: Redundant image → delete ───────────────────────────────────────

def test_redundant_image_deleted(staging):
    first = make_desc(f"{T}_first.png", "A desk scene", is_first=True, observed_at=T)
    second = make_desc(f"{T+1}_second.png", "A desk scene with a laptop", observed_at=T + 1)
    node = make_node("n1", "A desk scene with a laptop", related=[second["id"]])

    batch_id = make_batch(staging, [first, second], nodes=[node])
    client = mock_judgment({"id": second["id"], "decision": "delete", "reason": "redundant with node n1"})

    meta = prune_batch(batch_id, _client=client)

    pruning = json.loads((staging / batch_id / "pruning.json").read_text())
    desc = json.loads((staging / batch_id / "descriptions.json").read_text())

    second_prune = next(p for p in pruning if p["id"] == second["id"])
    assert second_prune["decision"] == "delete"

    second_desc = next(d for d in desc if d["id"] == second["id"])
    assert second_desc.get("deleted") is True

    assert not (staging / batch_id / second["id"]).exists()
    assert meta["deleted_count"] == 1


# ── Test 6.2: Informative image → keep ───────────────────────────────────────

def test_informative_image_kept(staging):
    first = make_desc(f"{T}_first.png", "Office overview", is_first=True, observed_at=T)
    second = make_desc(f"{T+1}_second.png", "Close-up of whiteboard diagram", observed_at=T + 1)

    batch_id = make_batch(staging, [first, second])
    client = mock_judgment({"id": second["id"], "decision": "keep", "reason": "shows whiteboard detail not in nodes"})

    prune_batch(batch_id, _client=client)

    desc = json.loads((staging / batch_id / "descriptions.json").read_text())
    second_desc = next(d for d in desc if d["id"] == second["id"])
    assert second_desc.get("deleted") is not True
    assert (staging / batch_id / second["id"]).exists()


# ── Test 6.3: Unlinked image → delete ────────────────────────────────────────

def test_unlinked_image_tombstoned(staging):
    first = make_desc(f"{T}_first.png", "Scene A", is_first=True, observed_at=T)
    unlinked = make_desc(f"{T+5}_unlinked.png", "Random hallway shot", observed_at=T + 5)

    batch_id = make_batch(staging, [first, unlinked], nodes=[])
    client = mock_judgment({"id": unlinked["id"], "decision": "delete", "reason": "no linked node"})

    prune_batch(batch_id, _client=client)

    desc = json.loads((staging / batch_id / "descriptions.json").read_text())
    unlinked_desc = next(d for d in desc if d["id"] == unlinked["id"])
    assert unlinked_desc.get("deleted") is True


# ── Test 6.4: First-in-batch guard blocks forced delete ──────────────────────

def test_first_image_guard_blocks_deletion(staging, caplog):
    first = make_desc(f"{T}_first.png", "Summary frame", is_first=True, observed_at=T)
    second = make_desc(f"{T+1}_second.png", "Redundant frame", observed_at=T + 1)

    batch_id = make_batch(staging, [first, second])

    # Model anomalously returns delete for both
    client = mock_judgment(
        {"id": first["id"], "decision": "delete", "reason": "anomalous model output"},
        {"id": second["id"], "decision": "delete", "reason": "redundant"},
    )

    with caplog.at_level(logging.WARNING, logger="prune_images"):
        prune_batch(batch_id, _client=client)

    # First image bytes still exist
    assert (staging / batch_id / first["id"]).exists()

    # First image record not tombstoned
    desc = json.loads((staging / batch_id / "descriptions.json").read_text())
    first_desc = next(d for d in desc if d["id"] == first["id"])
    assert first_desc.get("deleted") is not True

    # Anomaly logged
    assert any("ANOMALY" in r.message for r in caplog.records)

    # pruning.json still marks first as keep
    pruning = json.loads((staging / batch_id / "pruning.json").read_text())
    first_prune = next(p for p in pruning if p["id"] == first["id"])
    assert first_prune["decision"] == "keep"


# ── Test 6.5: Batch of exactly 1 image — LLM skipped ────────────────────────

def test_single_image_batch_skips_llm(staging):
    first = make_desc(f"{T}_solo.png", "Only image", is_first=True, observed_at=T)
    batch_id = make_batch(staging, [first])

    mock_client = MagicMock()

    meta = prune_batch(batch_id, _client=mock_client)

    mock_client.messages.create.assert_not_called()

    pruning = json.loads((staging / batch_id / "pruning.json").read_text())
    assert len(pruning) == 1
    assert pruning[0]["decision"] == "keep"
    assert pruning[0]["reason"]

    assert meta["pruning_status"] == "complete"
    assert meta["kept_count"] == 1
    assert meta["deleted_count"] == 0


# ── Test 6.6: Model error → fail-safe ────────────────────────────────────────

def test_model_error_fail_safe(staging):
    first = make_desc(f"{T}_first.png", "Frame A", is_first=True, observed_at=T)
    second = make_desc(f"{T+1}_second.png", "Frame B", observed_at=T + 1)
    batch_id = make_batch(staging, [first, second])

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("API unavailable")

    meta = prune_batch(batch_id, _client=mock_client)

    assert meta["pruning_status"] == "failed"

    # No files deleted
    assert (staging / batch_id / first["id"]).exists()
    assert (staging / batch_id / second["id"]).exists()

    # No tombstones
    desc = json.loads((staging / batch_id / "descriptions.json").read_text())
    assert all(d.get("deleted") is not True for d in desc)


# ── Test 6.7: End-to-end pipeline ─────────────────────────────────────────────

def test_end_to_end_pipeline(tmp_path, monkeypatch):
    """
    Full pipeline: intake (manual staging) → describe (mocked Haiku) →
    summarize (mocked Sonnet) → prune (mocked Sonnet judgment).
    Asserts surviving image set and that intent_graph.json is intact.
    """
    monkeypatch.setattr(prune_images, "STAGING_DIR", tmp_path)
    monkeypatch.setattr(describe_images, "STAGING_DIR", tmp_path)
    monkeypatch.setattr(summarize_transcript, "STAGING_DIR", tmp_path)

    from uuid import uuid4
    now = T
    batch_id = f"batch_{uuid4()}"
    batch_dir = tmp_path / batch_id
    batch_dir.mkdir()

    # Stage 3 images
    img_first = f"{now}_first.png"
    img_keep  = f"{now+5}_keep.png"
    img_del   = f"{now+10}_del.png"
    for name in [img_first, img_keep, img_del]:
        (batch_dir / name).write_bytes(minimal_png())

    transcript = [
        {"speaker": "Alice", "text": "Let's get the demo ready.", "timestamp": now},
    ]
    (batch_dir / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
    (batch_dir / "meta.json").write_text(json.dumps({
        "batch_id": batch_id, "image_count": 3, "received_at": "2024-01-01T00:00:00+00:00"
    }), encoding="utf-8")

    # --- Stage 1: describe (mock Haiku) ---
    haiku_mock = MagicMock()
    haiku_mock.messages.create.return_value = MagicMock(
        stop_reason="end_turn",
        content=[MagicMock(text="A scene with people working.")],
    )
    with patch.object(describe_images.anthropic, "Anthropic", return_value=haiku_mock):
        describe_images.process_batch(batch_id)

    descriptions = json.loads((batch_dir / "descriptions.json").read_text())
    assert len(descriptions) == 3

    # --- Stage 2: summarize (mock Sonnet) ---
    goal_node = {
        "id": "n1", "node_type": "Goal",
        "description": "Get the demo ready",
        "speaker": "Alice",
        "occurred_at": now,
        "related_images": [img_first, img_keep, img_del],
    }
    sonnet_mock = MagicMock()
    sonnet_mock.messages.create.return_value = MagicMock(
        content=[MagicMock(text=json.dumps([goal_node]))]
    )
    with patch.object(summarize_transcript, "compress_transcript", return_value="[t]"):
        with patch.object(summarize_transcript.anthropic, "Anthropic", return_value=sonnet_mock):
            summarize_transcript.summarize_batch(batch_id)

    nodes = json.loads((batch_dir / "intent_graph.json").read_text())
    assert len(nodes) == 1

    # --- Stage 3: prune (mock judgment) ---
    prune_mock = MagicMock()
    prune_mock.messages.create.return_value = MagicMock(content=[MagicMock(text=json.dumps([
        {"id": img_keep, "decision": "keep",   "reason": "adds detail"},
        {"id": img_del,  "decision": "delete", "reason": "redundant"},
    ]))])

    meta = prune_batch(batch_id, _client=prune_mock)

    # First image always kept
    assert (batch_dir / img_first).exists()
    # Keep image retained
    assert (batch_dir / img_keep).exists()
    # Delete image bytes gone
    assert not (batch_dir / img_del).exists()

    # Intent graph intact
    nodes_after = json.loads((batch_dir / "intent_graph.json").read_text())
    assert len(nodes_after) == 1

    assert meta["pruning_status"] == "complete"
    assert meta["kept_count"] == 2   # first + keep
    assert meta["deleted_count"] == 1
