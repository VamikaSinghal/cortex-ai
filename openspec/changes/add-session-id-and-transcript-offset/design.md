## Context

The intake API (`intake_api.py`) currently stages batches independently with no concept of session membership. The summarisation stage (`summarize_transcript.py`) is called once per batch and sees the full transcript that was uploaded with that batch. Because the Android client never clears `accumulatedTurns` between uploads, every batch carries the full session history from turn 1 — the Sonnet call re-processes all prior turns on every upload, creating duplicate intent-graph nodes proportional to O(N²) in session length.

Two pieces of metadata are needed to eliminate this: a `session_id` to group batches from the same conversation, and a `transcript_offset` to tell the summarisation stage which turns are new.

Current state of relevant files:
- `intake_api.py`: accepts `images[]` + `transcript`; writes `meta.json` with `batch_id`, `image_count`, `received_at`.
- `summarize_transcript.py`: `summarize_batch()` reads `transcript.json` in full; all extracted nodes are written to `intent_graph.json` without any deduplication guard.

## Goals / Non-Goals

**Goals:**
- Accept `session_id` (optional string) and `transcript_offset` (optional int) in every `POST /api/intake/batch` call.
- Persist both values in `meta.json` so downstream pipeline stages can read them without re-parsing the multipart body.
- Filter the nodes written to `intent_graph.json` so only nodes whose `occurred_at` ≥ `turns[transcript_offset]["timestamp"]` are persisted, eliminating re-extraction of nodes from prior turns.
- Accept `started_at` and `ended_at` on transcript turns without rejecting them (the Android client now sends these fields).

**Non-Goals:**
- Cross-batch deduplication via Redis lookup (that would require a round-trip before staging; deferred to a future change).
- Modifying any Android client code (server-side fix only).
- Changing the Haiku image-description stage or the pruning stage.
- Backfilling `session_id` on existing staged batches.

## Decisions

### D1: `transcript_offset` stored in `meta.json`, not `transcript.json`

`transcript.json` is a clean record of what the client sent; mixing pipeline-control metadata into it complicates the single-responsibility contract. `meta.json` already holds pipeline state (`description_status`, `summarization_status`, etc.) and is the natural home.

Alternative considered: pass `transcript_offset` as a query parameter to `summarize_batch()` at call time. Rejected because it requires every call site to re-derive the offset; storing it in `meta.json` makes the pipeline self-contained and auditable.

### D2: Filter nodes after extraction, not before

The full transcript (including prior turns) is still passed to Sonnet. Sonnet uses prior turns for context when accurately dating and categorising new events. Only the write step is filtered — nodes extracted from prior turns are discarded rather than never computed.

Alternative considered: send only the new turns to Sonnet. Rejected because Sonnet's entity resolution and temporal reasoning quality degrades without the conversational context that prior turns provide.

### D3: `started_at` / `ended_at` treated as optional, not required

Making them required would break existing test clients and the smoke test script. The fields carry useful data (turn duration) but the pipeline currently uses only `timestamp` for temporal joins, so accepting-but-not-requiring is the safest path.

### D4: `session_id` stored as-is, no server-side generation

If the client omits `session_id`, `meta.json` stores `null`. The server does not generate a session ID on the client's behalf because there is no mechanism to return it back to the client within the existing 202 response schema. Future tooling can group batches by the field when present.

## Risks / Trade-offs

- **Off-by-one in `transcript_offset`**: If the client sends `transcript_offset = N` but the transcript array has fewer than N entries (e.g. due to the turn-count trim), the cutoff lookup will `IndexError`. Mitigation: clamp `transcript_offset` to `len(turns) - 1` before use and treat out-of-range as `0` (process all nodes).
- **Sonnet still runs on full transcript**: The cost of the Sonnet call itself is not reduced — only the write is filtered. The O(N²) redundancy in stored nodes is eliminated, but Sonnet token usage still grows with session length. Mitigation: the existing 200-turn trim in `intake_api.py` bounds transcript size and therefore token growth.
- **`session_id` is unauthenticated**: Any caller can claim any `session_id`. For the current single-user local deployment this is acceptable. Mitigation: documented as a future concern if multi-user support is added.

## Migration Plan

1. Deploy updated `intake_api.py` (new fields are optional — zero-downtime, backward-compatible).
2. Deploy updated `summarize_transcript.py` (reads `transcript_offset` from `meta.json`; absent key defaults to `0`, so all existing staged batches process unchanged).
3. No database migration needed — `meta.json` files gain new nullable keys.

## Open Questions

- Should `session_id` eventually be surfaced in the 202 response for round-trip validation by the client?
- Should future work extend the node filter to use `ended_at` for tighter boundary arithmetic when that field is present?
