"""
Unit tests for summarize_transcript.py.

All LLM and TTC calls are mocked. join_images_to_nodes is tested as a pure function.

Run: pytest tests/test_summarize.py -v
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import summarize_transcript
from summarize_transcript import join_images_to_nodes, summarize_batch

T = 1718900000


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def staging(tmp_path, monkeypatch):
    monkeypatch.setattr(summarize_transcript, "STAGING_DIR", tmp_path)
    return tmp_path


def make_batch(staging_dir: Path, turns: list, image_records: list | None = None) -> str:
    from uuid import uuid4
    batch_id = f"batch_{uuid4()}"
    batch_dir = staging_dir / batch_id
    batch_dir.mkdir()
    (batch_dir / "transcript.json").write_text(json.dumps(turns), encoding="utf-8")
    meta = {"batch_id": batch_id, "image_count": len(image_records or []), "received_at": "2024-01-01T00:00:00+00:00"}
    (batch_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    if image_records is not None:
        (batch_dir / "descriptions.json").write_text(json.dumps(image_records), encoding="utf-8")
    return batch_id


def mock_sonnet_response(nodes: list) -> MagicMock:
    r = MagicMock()
    r.content = [MagicMock(text=json.dumps(nodes))]
    return r


GOAL_NODE = {
    "id": "n1",
    "node_type": "Goal",
    "description": "Finish the intake API before the demo.",
    "speaker": "Alice",
    "occurred_at": T,
    "related_images": [],
}

INTERRUPTION_NODE = {
    "id": "n2",
    "node_type": "Interruption",
    "description": "Phone rang unexpectedly.",
    "speaker": "Bob",
    "occurred_at": T + 5,
    "related_images": [],
}

SAMPLE_TURNS = [
    {"speaker": "Alice", "text": "I need to finish the intake API.", "timestamp": T},
    {"speaker": "Bob", "text": "I'll handle Redis.", "timestamp": T + 5},
]


# ── Test 6.1: Typed nodes extracted ──────────────────────────────────────────

def test_typed_nodes_extracted(staging):
    """Mock Sonnet returns Goal + Interruption → both types present with description/speaker."""
    batch_id = make_batch(staging, SAMPLE_TURNS)
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_sonnet_response([GOAL_NODE, INTERRUPTION_NODE])

    with patch.object(summarize_transcript, "compress_transcript", return_value="[compressed]"):
        meta = summarize_batch(batch_id, _client=mock_client)

    nodes = json.loads((staging / batch_id / "intent_graph.json").read_text())
    types = {n["node_type"] for n in nodes}
    assert "Goal" in types
    assert "Interruption" in types
    for n in nodes:
        assert n["description"]
        assert n["speaker"]
    assert meta["summarization_status"] == "complete"
    assert meta["node_count"] == 2


# ── Test 6.2: No images in batch ─────────────────────────────────────────────

def test_no_images_in_batch(staging):
    """descriptions.json absent → nodes have related_images: [], no crash."""
    batch_id = make_batch(staging, SAMPLE_TURNS, image_records=None)
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_sonnet_response([GOAL_NODE])

    with patch.object(summarize_transcript, "compress_transcript", return_value="[t]"):
        summarize_batch(batch_id, _client=mock_client)

    nodes = json.loads((staging / batch_id / "intent_graph.json").read_text())
    assert all(n["related_images"] == [] for n in nodes)


# ── Test 6.3: TTC fails — fallback exercised ─────────────────────────────────

def test_ttc_fail_fallback(staging):
    """TTC raises requests.Timeout → Sonnet still called with original transcript."""
    import requests as req_module
    batch_id = make_batch(staging, SAMPLE_TURNS)

    mock_client = MagicMock()
    captured_content = []

    def capture_create(**kwargs):
        captured_content.append(kwargs["messages"][0]["content"])
        return mock_sonnet_response([GOAL_NODE])

    mock_client.messages.create.side_effect = capture_create

    with patch.object(summarize_transcript.requests, "post", side_effect=req_module.Timeout("timeout")):
        with patch.dict("os.environ", {"TTC_API_KEY": "fake-key"}):
            summarize_batch(batch_id, _client=mock_client)

    # Sonnet was called
    assert mock_client.messages.create.called
    # Content includes original transcript text (not empty)
    assert captured_content[0]
    nodes = json.loads((staging / batch_id / "intent_graph.json").read_text())
    assert len(nodes) == 1


# ── Test 6.4: Empty transcript ────────────────────────────────────────────────

def test_empty_transcript(staging):
    """transcript.json is [] → Sonnet NOT called; intent_graph is []; status complete."""
    batch_id = make_batch(staging, [], image_records=[])
    mock_client = MagicMock()

    summarize_batch(batch_id, _client=mock_client)

    mock_client.messages.create.assert_not_called()
    nodes = json.loads((staging / batch_id / "intent_graph.json").read_text())
    assert nodes == []
    meta = json.loads((staging / batch_id / "meta.json").read_text())
    assert meta["summarization_status"] == "complete"
    assert meta["node_count"] == 0


# ── Test 6.5: Sonnet returns invalid JSON ─────────────────────────────────────

def test_sonnet_invalid_json(staging):
    """Sonnet returns non-JSON → intent_graph is []; status failed; no crash."""
    batch_id = make_batch(staging, SAMPLE_TURNS)
    mock_client = MagicMock()
    bad_response = MagicMock()
    bad_response.content = [MagicMock(text="Sorry, I cannot extract nodes from this.")]
    mock_client.messages.create.return_value = bad_response

    with patch.object(summarize_transcript, "compress_transcript", return_value="[t]"):
        meta = summarize_batch(batch_id, _client=mock_client)

    nodes = json.loads((staging / batch_id / "intent_graph.json").read_text())
    assert nodes == []
    assert meta["summarization_status"] == "failed"


# ── Tests 6.6–6.9: join_images_to_nodes (pure function) ──────────────────────

def _img(id_: str, observed_at: int) -> dict:
    return {"id": id_, "observed_at": observed_at, "description": "x", "error": None}


def _node(occurred_at: int, id_: str = "n1") -> dict:
    return {"id": id_, "node_type": "Goal", "description": "d", "speaker": "A",
            "occurred_at": occurred_at, "related_images": []}


def test_join_boundary_exclusive_upper():
    """Node at T, next at T+10: image at T+9 → node 0; image at T+10 → node 1."""
    nodes = [_node(T, "n0"), _node(T + 10, "n1")]
    images = [_img("a", T + 9), _img("b", T + 10)]
    result = join_images_to_nodes(nodes, images)
    by_id = {n["id"]: n for n in result}
    assert "a" in by_id["n0"]["related_images"]
    assert "b" in by_id["n1"]["related_images"]
    assert "b" not in by_id["n0"]["related_images"]


def test_join_last_node_90s_window():
    """Single node: image at T+90 joined; image at T+91 not joined."""
    nodes = [_node(T)]
    images = [_img("x", T + 90), _img("y", T + 91)]
    result = join_images_to_nodes(nodes, images, last_turn_window_secs=90)
    related = result[0]["related_images"]
    assert "x" in related
    assert "y" not in related


def test_join_unmatched_image_not_in_any_node():
    """Image before the first node's timestamp → not joined to any node."""
    nodes = [_node(T + 100)]
    images = [_img("early", T)]
    result = join_images_to_nodes(nodes, images)
    assert "early" not in result[0]["related_images"]


def test_join_no_images():
    """Empty image_records → all nodes have related_images: []."""
    nodes = [_node(T), _node(T + 5)]
    result = join_images_to_nodes(nodes, [])
    assert all(n["related_images"] == [] for n in result)


# ── Tests 6.10–6.12: transcript_offset node filter ───────────────────────────

def make_batch_with_offset(staging_dir: Path, turns: list, transcript_offset: int) -> str:
    from uuid import uuid4
    batch_id = f"batch_{uuid4()}"
    batch_dir = staging_dir / batch_id
    batch_dir.mkdir()
    (batch_dir / "transcript.json").write_text(json.dumps(turns), encoding="utf-8")
    meta = {
        "batch_id": batch_id,
        "image_count": 0,
        "received_at": "2024-01-01T00:00:00+00:00",
        "transcript_offset": transcript_offset,
    }
    (batch_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return batch_id


def test_offset_filter_drops_prior_nodes(staging):
    """transcript_offset=1: node at turns[0].timestamp is dropped; node at turns[1].timestamp kept."""
    turns = [
        {"speaker": "A", "text": "old turn", "timestamp": T},
        {"speaker": "B", "text": "new turn", "timestamp": T + 10},
    ]
    old_node = {**GOAL_NODE, "id": "old", "occurred_at": T}
    new_node = {**INTERRUPTION_NODE, "id": "new", "occurred_at": T + 10}

    batch_id = make_batch_with_offset(staging, turns, transcript_offset=1)
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_sonnet_response([old_node, new_node])

    with patch.object(summarize_transcript, "compress_transcript", return_value="[t]"):
        meta = summarize_batch(batch_id, _client=mock_client)

    nodes = json.loads((staging / batch_id / "intent_graph.json").read_text())
    ids = [n["id"] for n in nodes]
    assert "new" in ids, "new node must be retained"
    assert "old" not in ids, "old node must be filtered out"
    assert meta["node_count"] == 1
    assert meta["transcript_offset"] == 1


def test_offset_zero_keeps_all_nodes(staging):
    """transcript_offset=0: no filter applied; all nodes written."""
    turns = [
        {"speaker": "A", "text": "turn 0", "timestamp": T},
        {"speaker": "B", "text": "turn 1", "timestamp": T + 10},
    ]
    batch_id = make_batch_with_offset(staging, turns, transcript_offset=0)
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_sonnet_response([GOAL_NODE, INTERRUPTION_NODE])

    with patch.object(summarize_transcript, "compress_transcript", return_value="[t]"):
        meta = summarize_batch(batch_id, _client=mock_client)

    nodes = json.loads((staging / batch_id / "intent_graph.json").read_text())
    assert len(nodes) == 2
    assert meta["node_count"] == 2


def test_offset_absent_in_legacy_meta_keeps_all_nodes(staging):
    """meta.json without transcript_offset key: defaults to 0, all nodes written."""
    batch_id = make_batch(staging, SAMPLE_TURNS)  # make_batch writes no transcript_offset
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_sonnet_response([GOAL_NODE, INTERRUPTION_NODE])

    with patch.object(summarize_transcript, "compress_transcript", return_value="[t]"):
        meta = summarize_batch(batch_id, _client=mock_client)

    nodes = json.loads((staging / batch_id / "intent_graph.json").read_text())
    assert len(nodes) == 2
    assert meta["transcript_offset"] == 0
