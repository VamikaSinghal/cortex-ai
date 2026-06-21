## Context

Recall captures images and conversation audio during a session. The diarized-turn-transcription change produces turn-level JSON (`{ speaker, text, started_at, ended_at }`). This change defines how a client submits a completed batch — images + transcript — for async processing. Nothing downstream exists yet; this is the intake gate.

## Goals / Non-Goals

**Goals:**
- Define the multipart request shape and validation rules
- Stage raw batch bytes + metadata durably so later stages can consume them
- Return a `batch_id` quickly (no LLM calls in the hot path)
- Survive a malformed or oversized request without 500ing

**Non-Goals:**
- Auth (no auth for hackathon demo — stated explicitly, not silently omitted)
- Image description, summarization, relevance scoring (later changes)
- Image retention/deletion policy (blocked on `add-relevance-filter-and-image-pruning`)
- Partial batch acceptance

## Decisions

**Staging store: local filesystem**
Redis is already used for vector search (Cortex pattern), but for raw image bytes a flat staging directory is simpler and faster to stand up. Each batch gets a folder: `staging/<batch_id>/`. Images are written as files; transcript is written as `transcript.json`; a `meta.json` records timestamps and counts. The next-stage worker reads from this directory. Trade-off: not durable across machine restarts, but fine for a hackathon demo that runs on one machine.

**`batch_id` generation: UUID4**
Collision-free, no coordination needed, opaque to the client. Format: `batch_<uuid4>` (e.g. `batch_a3f2...`).

**Timestamp extraction: filename-first, fallback to form field**
Parse UNIX timestamp from the image filename (e.g. `1718900000.jpg` or `img_1718900000.png`). If the filename has no parseable integer, check for a companion form field named `<fieldname>_ts`. If neither works, reject 400 naming the specific image. This keeps the common case zero-friction (just name your files with timestamps).

**Validation: all-or-nothing**
No partial batch acceptance. If any image lacks a timestamp or the transcript schema is wrong, the whole request is rejected. Partial batches create ambiguous downstream state.

**Request size limits (defaults — revisit if time allows):**
- Max images per batch: 20
- Max image size: 5 MB each
- Max transcript size: 500 KB

## Risks / Trade-offs

- [Filesystem staging not durable] → Acceptable for demo; document that restarting the server clears staged batches.
- [No auth] → Known, stated. Risk: anyone who knows the endpoint can submit batches during the demo. Mitigation: run on localhost only during demo.
- [Timestamp extraction via filename heuristic] → Could fail for unexpected naming conventions. Mitigation: companion form field fallback + clear 400 on failure.
