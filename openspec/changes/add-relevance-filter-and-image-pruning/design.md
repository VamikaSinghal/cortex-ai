## Context

This is the final stage of the Recall pipeline. At this point, `staging/<batch_id>/` contains:
- `descriptions.json` — one SceneDescription record per image, with `is_first_in_batch` set and `related_images` populated by the join step
- `intent_graph.json` — typed intent-graph nodes from the summarization stage
- The original image byte files

The pipeline's privacy thesis requires that raw images not reach permanent storage. The description stage converted images to text; this stage removes the image bytes whose text was redundant with what the intent graph already captured.

**TTC vs. this stage (restated for design history):** The Token Company API was used in `add-intent-graph-summarization-stage` solely to reduce token count on the transcript before the Sonnet call. It has no role here. The relevance judgment in this stage is an explicit Claude reasoning call — an entirely separate mechanism with a different purpose.

## Goals / Non-Goals

**Goals:**
- One Claude call per batch (not per image) to minimize cost and latency
- Hard-coded first-image guard that cannot be overridden by model output
- Soft-delete: image bytes gone, text records tombstoned (not hard-deleted)
- Fail-safe: model error → retain everything
- Full audit trail in `pruning.json`

**Non-Goals:**
- Writing to Redis or GitHub (downstream from this stage)
- Re-running TTC compression
- Multi-turn or extended-thinking calls — single-turn structured judgment only

## Decisions

**Single batch call, not per-image calls**
All non-first images are submitted in one Claude call with a JSON-structured input. Alternative (one call per image) is 3–20× more expensive and adds latency proportional to batch size. The batch approach loses some per-image isolation but gains predictable cost.

**Model: `claude-sonnet-4-6`**
Same tier as the summarization stage. This judgment requires reasoning about redundancy across node text and image descriptions — not a bounded classification task Haiku can handle reliably.

**Prompt output: JSON array of `{id, decision, reason}`**
Same parse strategy as `summarize_transcript.py`: ask for a raw JSON array, use regex fallback to extract `[...]` block, treat parse failure as model error (fail-safe). Keeps implementation consistent across stages.

**Unlinked images default to delete**
An image with no linked intent-graph node means nothing noteworthy was happening at that timestamp (or the node extraction missed it). Defaulting to delete is consistent with the pipeline's goal of retaining only contextually meaningful frames. This default is stated in the prompt and enforced as a prompt instruction — but it is not in the guard (the guard only protects the first image).

**Soft-delete: tombstone in descriptions.json**
Set `"deleted": true` on the record in-place; preserve all other fields. Rationale: during demo prep you will need to inspect *why* images were deleted (check `pruning.json` reasons) and correlate with the descriptions that were tombstoned. Hard-deleting the text record would make that impossible without re-running the full pipeline. Soft-delete adds ~20 bytes per record and is the right call for a hackathon.

**First-image guard as a separate function: `is_protected(record) -> bool`**
`is_protected` returns `True` iff `record.get("is_first_in_batch") is True`. The deletion loop calls this before any `unlink()` or tombstone write. Even if the model returns `delete` for the first image (anomaly), `is_protected` blocks it and logs at WARNING. This is the only place the guard logic lives — not duplicated in the prompt or elsewhere.

**Batch of 1 image: skip LLM call entirely**
If there is only one image, it is by definition first-in-batch. Writing `pruning.json` with a single keep entry (reason: "only image in batch") costs nothing and avoids a model call that would be vacuous. This is a minor optimization but keeps the implementation clean — no empty judgment call.

**`pruning_status` in meta.json**
`"complete"` on success (even if all images were deleted), `"failed"` on model error. Kept consistent with `description_status` and `summarization_status` patterns from prior stages.

## Risks / Trade-offs

- [Model returns malformed JSON] → Regex fallback; if both fail → fail-safe (retain all). Same pattern as prior stages, proven in smoke tests.
- [Model deletes too aggressively] → Soft-delete and `pruning.json` reasons enable manual review during demo prep without re-running the pipeline.
- [First-image guard bypassed by prompt injection in image descriptions] → Guard is in code, not prompt. Model output can claim whatever it wants; the guard ignores `delete` for `is_first_in_batch: true` regardless.
- [Batch call token ceiling] → A batch of 20 images × ~200-word descriptions + intent-graph nodes fits well within Sonnet's 200K context. Not a practical concern for hackathon demo batches.
