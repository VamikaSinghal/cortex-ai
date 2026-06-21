## 1. First-Image Guard

- [x] 1.1 Create `prune_images.py` with `is_protected(record: dict) -> bool` — returns `True` iff `record.get("is_first_in_batch") is True`
- [x] 1.2 Ensure `is_protected` is the sole place the first-image guard logic lives (not duplicated in the prompt or the deletion loop)

## 2. Relevance Judgment Call

- [x] 2.1 Create `judge_images(descriptions, nodes, _client=None) -> list` — takes the list of non-first `SceneDescription` records and all intent-graph nodes; returns a list of `{id, decision, reason}` dicts
- [x] 2.2 Build the judgment prompt: include all intent-graph node descriptions and, for each non-first image, its `id`, `description`, and which node `id`s it is linked to via `related_images`; instruct the model to return a raw JSON array of `{id, decision, reason}` with `decision` in `{"keep", "delete"}`
- [x] 2.3 State in the prompt that images unlinked to any node default to `"delete"` (no node context = no reason to retain the visual frame)
- [x] 2.4 Call `claude-sonnet-4-6` with the judgment prompt; parse response as JSON array; on parse failure use regex to extract first `[...]` block
- [x] 2.5 On any exception or parse failure after fallback: log WARNING, return a keep-all list (`{id, decision: "keep", reason: "model error — fail safe"}` for every image) — do not raise
- [x] 2.6 If the batch has exactly one image (which is first-in-batch): skip the LLM call entirely; return `[]` (no non-first images to judge)

## 3. Deletion Execution

- [x] 3.1 Create `execute_pruning(batch_id, decisions, descriptions, _client=None) -> dict` that iterates over `decisions` and for each `"delete"` entry: (a) calls `is_protected` on the matching description record — if protected, log WARNING anomaly and skip; (b) otherwise delete the image byte file and tombstone the description record (`deleted: true`)
- [x] 3.2 Tombstone: set `"deleted": true` on the record in-place in the descriptions list; preserve all other fields (`id`, `description`, `observed_at`, etc.)
- [x] 3.3 After all decisions are processed, write the updated descriptions list back to `staging/<batch_id>/descriptions.json`

## 4. Batch Worker and Persistence

- [x] 4.1 Create `prune_batch(batch_id) -> dict` that orchestrates: load `descriptions.json` and `intent_graph.json`; separate first-in-batch image; call `judge_images`; add a keep entry for the first-in-batch image (`reason: "first in batch — always retained"`); call `execute_pruning`; write `pruning.json`; update `meta.json`
- [x] 4.2 Write `staging/<batch_id>/pruning.json` as a JSON array containing one `{id, decision, reason}` record per image (including the first-in-batch keep entry)
- [x] 4.3 Update `staging/<batch_id>/meta.json` with `pruning_status` (`"complete"` or `"failed"`), `kept_count`, and `deleted_count`
- [x] 4.4 On model error (fail-safe path): write `pruning.json` with keep for all images and set `pruning_status: "failed"` in `meta.json`

## 5. Smoke Test

> Run with: `python tests/smoke_prune.py <batch_id>` where `<batch_id>` has been through intake → description → summarization.
> One-shot shortcut: `python tests/smoke_prune.py --run-intake` — runs all four pipeline stages inline.

- [x] 5.1 Create `tests/smoke_prune.py` that:
  - Accepts a `batch_id` argument, or `--run-intake` to chain intake → `describe_batch` → `summarize_batch` → `prune_batch` inline
  - Calls `prune_batch(batch_id)` directly
  - Reads `staging/<batch_id>/pruning.json` and asserts every non-first image has a record with a non-empty `reason`
  - Asserts the first-in-batch image has `decision: "keep"` in `pruning.json`
  - Reads `staging/<batch_id>/meta.json` and asserts `pruning_status == "complete"`
  - Prints each pruning decision (id, decision, reason truncated to 80 chars) for manual inspection
  - Prints `✅ Smoke test passed` on success
- [x] 5.2 Document smoke test invocation in this file (done — see above)

## 6. Unit Tests

> Approach: mock the Anthropic client for LLM calls; `is_protected` and deletion guard tested without any mocking.

- [x] 6.1 **Redundant image → delete** — mock model returns `delete` for an image whose description echoes its node text; assert `pruning.json` has `decision: "delete"` and image file is removed from staging; description record has `deleted: true`
- [x] 6.2 **Informative image → keep** — mock model returns `keep`; assert image file still exists; description record has no `deleted: true`
- [x] 6.3 **Unlinked image → delete** — image with empty `related_images` across all nodes; mock model returns `delete`; assert tombstoned
- [x] 6.4 **First-in-batch guard — model forced delete is blocked** — include first-in-batch image in mock model response with `decision: "delete"`; assert `is_protected` blocks it, WARNING logged, image bytes and record intact
- [x] 6.5 **Batch of exactly 1 image — LLM skipped** — single image batch; assert `judge_images` is not called (or returns `[]`); `pruning.json` has one keep entry; `meta.json` has `pruning_status: "complete"`, `kept_count: 1`, `deleted_count: 0`
- [x] 6.6 **Model error → fail-safe** — mock model to raise an exception; assert no files deleted, all descriptions unchanged, `pruning_status: "failed"`
- [x] 6.7 **End-to-end pipeline** — run intake → describe (mocked Haiku) → summarize (mocked Sonnet) → prune (mocked judgment) in a single test using `tmp_path`; assert surviving image set matches expected keeps and intent-graph nodes are intact in `intent_graph.json`
