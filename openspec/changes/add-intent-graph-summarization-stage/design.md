## Context

The intake stage produces `transcript.json` (array of `{ speaker, text, started_at, ended_at }` turns) and the image-description stage produces `descriptions.json` (array of SceneDescription records). This stage is the third in the pipeline: it turns the flat transcript into a typed intent-graph and links nodes to image records by timestamp. No upstream stage has done any reasoning about intent; all prior stages are about data capture and format conversion.

The existing `ingest.py` extraction schema (`KEY_INSIGHTS`, `DECISIONS`, etc.) is for Cortex's general second-brain use case. The intent-graph schema defined here is specific to the Recall wearable pipeline — it models what a person was *doing* during a session, not what they were *talking about*. Do not conflate the two.

## Goals / Non-Goals

**Goals:**
- Compress transcript with TTC before sending to Sonnet (cost optimization, not correctness dependency)
- Extract Goal / Interruption / Commitment / ObjectLocation nodes from transcript via Sonnet
- Join nodes to image records via pure timestamp arithmetic
- Persist `intent_graph.json` and update `meta.json`

**Non-Goals:**
- Relevance scoring or filtering — that's `add-relevance-filter-and-image-pruning`
- Image deletion
- Writing to Redis or GitHub — this stage only writes to staging
- Extended thinking on Sonnet — not needed for one-shot structured extraction

## Decisions

**Intent-graph node schema (canonical definition for this pipeline)**

```json
{
  "id": "<batch_id>_<index>",
  "node_type": "Goal" | "Interruption" | "Commitment" | "ObjectLocation",
  "description": "<text>",
  "speaker": "<speaker string from transcript>",
  "related_images": ["<SceneDescription id>", ...],
  "started_at": 1718900000,
  "ended_at": 1718900030
}
```

Node-type-specific time fields:
- `Goal` → `started_at` + `ended_at` (spanned; derived from the transcript turns that express the goal)
- `Interruption` → `occurred_at` (point-in-time)
- `Commitment` → `occurred_at` (point-in-time; the moment the commitment was made)
- `ObjectLocation` → `occurred_at` (point-in-time)

`related_images` starts as `[]` in the Sonnet response and is populated by the join step.

**Model: `claude-sonnet-4-6`**
Sonnet over Haiku because extracting typed intent-graph nodes requires reasoning about speaker intent across turn boundaries — not just OCR-style extraction but semantic classification (is this a Goal or just a statement? does this Commitment have a due date implied?). Haiku is the wrong tier for this.

**Sonnet prompt strategy: ask for JSON array directly**
Ask Sonnet to return a JSON array of node objects. Do not use tool use or structured output — for a hackathon, parsing the raw JSON response (with a regex fallback identical to `ingest.py`) is faster to implement and debug. If Sonnet returns non-JSON, log the raw output and return an empty node list (not a crash).

**TTC integration: `compress_transcript(text) -> str`**
Calls the TTC API with `accuracy_mode=True` (accuracy-preserving, lighter compression). The function signature is `compress_transcript(text, *, timeout=10) -> str`. On any exception: log at WARNING level, return original `text`. TTC is never in the critical path for correctness.

**TTC operating point: accuracy-preserving**
TTC offers a range from maximum-compression to accuracy-positive. We pick the accuracy-preserving end. Rationale: for a one-shot hackathon demo, a degraded summarization because TTC over-compressed is harder to recover from than slightly higher per-call cost.

**Timestamp join: `join_images_to_nodes(nodes, image_records, point_window_secs=60) -> list`**
Pure function, no I/O, no LLM calls. Rules:
- Spanned nodes (have `started_at` and `ended_at`): join image if `started_at <= observed_at <= ended_at` (inclusive both ends)
- Point-in-time nodes (have `occurred_at`): join image if `|observed_at - occurred_at| <= point_window_secs`
- `point_window_secs` defaults to 60; caller can override (flagged as tunable in tasks)
- Images not matched by any node: logged at DEBUG level as unmatched (not silently dropped, not an error)

**`related_images` field name**
Uses `related_images` (not `related_entity` from the old Cortex schema). The new field name is more specific and avoids confusion with the general-purpose Cortex extraction.

**Persistence**
Nodes written to `staging/<batch_id>/intent_graph.json` as a JSON array. `meta.json` updated with:
```json
{
  "summarization_status": "complete" | "failed",
  "node_count": N
}
```
No partial status here — unlike the image-description stage, a Sonnet summarization failure is all-or-nothing (the transcript is a single call, not per-item).

## Risks / Trade-offs

- [Sonnet returns invalid JSON] → Fallback: regex-extract first `[...]` block; if that fails, write `[]` and set `summarization_status: "failed"`. Log raw response for debugging.
- [TTC API unavailable at demo time] → Fallback path is always exercised if TTC fails; demo still works with uncompressed transcript.
- [Transcript has no clear intent signals] → Sonnet returns fewer or zero nodes. `node_count: 0` with `status: "complete"` is valid output, not a failure.
- [Timestamp join produces false positives for long-spanning Goals] → Acceptable; the join is additive (attaches references, doesn't delete), and correctness of relevance filtering is deferred to the next change.
- [TTC API key not in .env.example] → Add `TTC_API_KEY` to `.env.example` and handle missing key gracefully (log warning, skip compression).
