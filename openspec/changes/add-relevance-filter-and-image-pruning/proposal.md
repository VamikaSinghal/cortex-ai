## Why

The description stage produces a text record for every image in a batch, but most frames captured during a session are redundant — slight time-shifts of the same scene, already fully described by the linked intent-graph node. This stage runs one explicit LLM judgment pass and deletes the bytes of images that add no information, so only meaningful visual context survives in staging.

**Context — TTC vs. this stage (critical distinction):** The Token Company's compression API was used in the prior stage (`add-intent-graph-summarization-stage`) to reduce token count on the raw transcript text before the Sonnet call. TTC is prompt compression middleware: it shrinks tokens while preserving meaning; it makes no keep/delete decisions and is not invoked again here. The relevance judgment in this stage is performed by a separate Claude model call that reasons over intent-graph nodes and image descriptions. These are two entirely different mechanisms with different roles. This distinction has been a point of confusion — it is stated here for anyone reading the OpenSpec history.

## What Changes

- New worker: reads `intent_graph.json` and `descriptions.json` from `staging/<batch_id>/`, feeds non-first images to a Claude judgment call, and executes keep/delete decisions
- Per-image decision record written to `staging/<batch_id>/pruning.json` — each entry includes the image `id`, `decision` (`keep` or `delete`), and a short `reason` (for debugging during demo prep)
- Soft-delete: image bytes removed from staging; the `SceneDescription` record in `descriptions.json` is tombstoned (`deleted: true`, original content preserved) rather than hard-deleted, so pruning decisions can be inspected post-run
- **Hard-coded first-image guard**: a dedicated function — not a prompt instruction — refuses to execute any deletion against the image flagged `is_first_in_batch: true`, regardless of model output. Logs anomaly if model somehow returns a delete decision for it.
- Fail-safe fallback: if the model call fails or times out, all images are retained and the failure is logged; nothing is deleted on an error

## Capabilities

### New Capabilities

- `relevance-filter-and-pruning`: LLM-driven per-image keep/delete judgment over intent-graph nodes and image descriptions; first-image guard; soft-delete execution; fail-safe on model error.

### Modified Capabilities

_None._

## Impact

- New worker module (`prune_images.py`)
- Reads `staging/<batch_id>/intent_graph.json` and `descriptions.json`; writes `pruning.json`; mutates `descriptions.json` (soft-delete tombstone); deletes image byte files from `staging/<batch_id>/`
- No new external API dependencies — uses `anthropic` SDK already in `requirements.txt`
- Completes the four-part pipeline: intake → image description → summarization → pruning
