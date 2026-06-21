## Context

The intake stage (`add-image-transcript-intake-api`) stages batches to `staging/<batch_id>/` with image files named `<timestamp>_<original_filename>`, `transcript.json`, and `meta.json`. This stage runs after intake and before transcript summarization. It must not block on single-image failures or delete anything.

## Goals / Non-Goals

**Goals:**
- Describe every image in a batch via Haiku in `observed_at` order
- Tag the earliest image `is_first_in_batch: true`
- Record per-image failures without aborting the batch
- Persist output to `staging/<batch_id>/descriptions.json`
- Update `meta.json` with batch-level status

**Non-Goals:**
- Image deletion or pruning (final change)
- Transcript summarization (next change)
- Extended thinking — explicitly out
- Any form of image storage beyond what intake already wrote

## Decisions

**Haiku model, standard thinking**
Use `claude-haiku-4-5` (latest Haiku). Set `thinking` to `{"type": "disabled"}` explicitly in the API call — don't rely on the default. Rationale: image description is bounded; extended thinking multiplies cost by image count with no quality gain for this task.

**Redaction prompt (verbatim from intent-graph SceneDescription constraint)**
The system prompt for each Haiku call MUST include:

> Do not include: names or identifying features of people; on-screen text, document content, or displayed UI; contact information, account numbers, or credentials.

This is the canonical redaction language shared across Recall. Do not paraphrase it — copy it verbatim so behavior stays consistent if the constraint is ever updated centrally.

**Output record shape**
```json
{
  "id": "<timestamp>_<original_filename>",
  "description": "<haiku output>",
  "observed_at": 1718900000,
  "redaction_applied": true,
  "is_first_in_batch": false,
  "error": null
}
```
On failure, `description` is `null` and `error` is a string (exception message or API stop reason).

**Batch status in meta.json**
After processing, update `meta.json` with:
```json
{
  "description_status": "complete" | "partial" | "failed",
  "described_count": N,
  "failed_count": M
}
```
- `complete`: all images succeeded
- `partial`: some succeeded, some failed
- `failed`: all images failed (do not silently report success)

**Image reading**
Reconstruct `observed_at` from the staged filename prefix (the integer before the first `_`). This avoids re-parsing the original form data.

**Ordering**
Sort staged image files by their `observed_at` prefix ascending before processing. This is the canonical order for all downstream stages.

## Risks / Trade-offs

- [Content refusal by Haiku] → Treated as per-image failure; `error` records the stop reason. Batch continues.
- [Prompt-level redaction not enforced] → Known, documented. Same limitation as the rest of Recall's intent graph.
- [Staging dir grows unbounded] → Out of scope; pruning change handles cleanup.
