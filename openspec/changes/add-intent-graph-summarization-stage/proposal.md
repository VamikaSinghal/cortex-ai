## Why

The image-description stage converts visual context to text, but the transcript — where actual intent lives — is still raw turn data. This stage extracts structured intent-graph nodes (Goal, Interruption, Commitment, ObjectLocation) from the transcript and links them to contemporaneous image records by timestamp, giving downstream stages a queryable, typed event log instead of a flat conversation blob.

## What Changes

- New worker: reads `transcript.json` from `staging/<batch_id>/`, optionally compresses it via The Token Company, then calls Claude Sonnet to extract typed intent-graph nodes
- Token Company integration as pre-processing middleware: reduces token count before the Sonnet call; falls back to uncompressed on failure — **TTC shrinks tokens while preserving meaning; it does not decide what is or isn't important** (that judgment belongs to the next change, `add-relevance-filter-and-image-pruning`)
- Timestamp join: a separate, pure function attaches `related_images` references to each node by comparing node time spans / point-in-time windows against `descriptions.json` image `observed_at` values
- Output persisted to `staging/<batch_id>/intent_graph.json`; `meta.json` updated with `summarization_status` and `node_count`
- **Model choice**: Claude Sonnet (`claude-sonnet-4-6`). Haiku is reserved for bounded image-description (prior stage); extracting conversational intent — identifying what a goal is, when it starts, whether something is a commitment vs. an observation — requires stronger reasoning. Sonnet is the right tier for one-shot structured extraction.
- **TTC operating point**: accuracy-preserving (lighter compression). For a hackathon demo, output quality matters more than per-call cost reduction.
- **Explicitly out of scope**: image deletion, relevance filtering, embedding or vector search writes — those are the final change.

## Capabilities

### New Capabilities

- `intent-graph-summarization`: Extract typed intent-graph nodes from a staged transcript via Sonnet, pre-compress transcript with TTC (with fallback), join nodes to contemporaneous image records by timestamp arithmetic, persist to `intent_graph.json`.

### Modified Capabilities

_None._

## Impact

- New worker module (`summarize_transcript.py`)
- Reads `staging/<batch_id>/transcript.json` (from intake) and `staging/<batch_id>/descriptions.json` (from image-description stage)
- Writes `staging/<batch_id>/intent_graph.json`; updates `meta.json`
- Adds TTC HTTP call (new dependency: `requests` already in `requirements.txt`)
- Adds `TTC_API_KEY` env var (new)
- Does not delete images or write to Redis/GitHub — those are downstream
