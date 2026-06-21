## Why

Recall needs a single entry point that accepts a captured image batch and its paired conversation transcript together, so downstream stages have a complete, consistent unit to process. Without this endpoint, each stage would need its own intake logic and there's no staging layer to decouple capture from processing.

## What Changes

- New route: `POST /api/intake/batch` (multipart/form-data)
- Validates images (each must carry a UNIX timestamp) and transcript (must match turn schema)
- Stages the raw batch to a persistent store and returns `202 Accepted` with a `batch_id`
- No LLM calls, no intent-graph writes — intake only
- **Known limitation**: No auth on this endpoint. Acceptable for hackathon demo; revisit before any real deployment.
- Basic request-size limits enforced to prevent demo-killing oversized requests

## Capabilities

### New Capabilities

- `batch-intake`: Accept, validate, and stage a multipart batch of images + transcript. Issues a `batch_id` for async downstream processing.

### Modified Capabilities

_None._

## Impact

- Adds a new API route and handler
- Adds staging persistence (filesystem or Redis, TBD in design)
- No changes to existing diarized-turn-transcription or intent-graph code
- Downstream pipeline stages (`add-haiku-image-description-stage`, `add-intent-graph-summarization-stage`, `add-relevance-filter-and-image-pruning`) depend on the `batch_id` and staging format defined here
