## Why

The Cortex summarisation pipeline extracts duplicate intent-graph nodes because every batch upload replays the full session transcript from turn 1. On a 10-turn session this produces ~55 node extractions for ~10 distinct pieces of information — each a billable Sonnet call and a redundant Redis entry that degrades semantic search recall. The fix requires two new optional fields in the intake API, with a corresponding filter in the summarisation stage.

## What Changes

- `POST /api/intake/batch` accepts two new optional multipart fields: `session_id` (string UUID) and `transcript_offset` (integer, default `0`).
- `meta.json` is extended to store `session_id` and `transcript_offset` for downstream stages.
- `transcript` turn validation is updated: `started_at` and `ended_at` are accepted as optional integer fields (the Android client now sends them; they must not cause a 400 if present).
- `summarize_batch()` reads `transcript_offset` from `meta.json` and filters extracted nodes so only nodes whose `occurred_at` falls at or after `turns[transcript_offset]["timestamp"]` are persisted — prior turns supply context to Sonnet but do not generate new node entries.

## Capabilities

### New Capabilities

- `batch-session-tracking`: `session_id` and `transcript_offset` fields accepted by the intake endpoint and stored in `meta.json`, enabling per-session batch grouping and offset-aware node filtering.

### Modified Capabilities

- `haiku-image-description`: No requirement change — image description pipeline is unaffected.

## Impact

- **`intake_api.py`**: New optional fields; `TURN_OPTIONAL_FIELDS` guard for `started_at`/`ended_at`; `meta.json` schema extended.
- **`summarize_transcript.py`**: `summarize_batch()` reads `transcript_offset` from `meta.json`; post-join node filter added.
- **API contract**: Fully backward-compatible — callers that omit `session_id` and `transcript_offset` see no change in behaviour.
- **Cost**: Eliminates O(N²) duplicate Sonnet calls; on a 10-turn session reduces node extractions from ~55 to ~10.
