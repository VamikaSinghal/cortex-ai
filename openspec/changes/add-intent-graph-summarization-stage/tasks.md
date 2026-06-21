## 1. TTC Compression Client

- [x] 1.1 Create `summarize_transcript.py` with a `compress_transcript(text, *, timeout=10) -> str` function
- [x] 1.2 Call the TTC compression API with the accuracy-preserving operating point (`accuracy_mode=True` or equivalent)
- [x] 1.3 On any TTC exception or timeout: log a WARNING, return the original `text` — do not raise
- [x] 1.4 If `TTC_API_KEY` is missing from the environment: log a WARNING and skip compression (return original text); add `TTC_API_KEY=...` to `.env.example`

## 2. Sonnet Summarization

- [x] 2.1 Create `summarize_batch(batch_id) -> dict` in `summarize_transcript.py`
- [x] 2.2 Read `staging/<batch_id>/transcript.json`; convert turns to a compact text representation for the prompt
- [x] 2.3 Compress the transcript text via `compress_transcript()` before constructing the Sonnet prompt
- [x] 2.4 Call `claude-sonnet-4-6` asking for a JSON array of intent-graph nodes (Goal, Interruption, Commitment, ObjectLocation); include the schema definition in the system prompt
- [x] 2.5 Each node in the prompt response MUST include: `id`, `node_type`, `description`, `speaker`, and the appropriate time field(s) (`started_at`+`ended_at` for Goal; `occurred_at` for Interruption, Commitment, ObjectLocation); `related_images` starts as `[]`
- [x] 2.6 Parse the Sonnet response JSON; on parse failure, regex-extract the first `[...]` block; if both fail, write `[]` and set `summarization_status: "failed"` — do not raise
- [x] 2.7 On empty transcript (`[]`): skip the Sonnet call entirely; write `[]` to `intent_graph.json`, set `summarization_status: "complete"`, `node_count: 0`

## 3. Timestamp Join

- [x] 3.1 Create `join_images_to_nodes(nodes, image_records, point_window_secs=60) -> list` as a pure function (no I/O, no LLM calls) in `summarize_transcript.py`
- [x] 3.2 For spanned nodes (those with `started_at` and `ended_at`): attach image `id` if `started_at <= observed_at <= ended_at` (inclusive both ends)
- [x] 3.3 For point-in-time nodes (`occurred_at` present): attach image `id` if `abs(observed_at - occurred_at) <= point_window_secs`
- [x] 3.4 Log unmatched image ids at DEBUG level (images whose `observed_at` falls outside every node's window)
- [x] 3.5 After Sonnet extraction, read `staging/<batch_id>/descriptions.json` (if absent, treat as empty list); call `join_images_to_nodes` and update nodes in place

## 4. Persistence

- [x] 4.1 Write joined nodes to `staging/<batch_id>/intent_graph.json` as a JSON array
- [x] 4.2 Update `staging/<batch_id>/meta.json` with `summarization_status` (`"complete"` or `"failed"`) and `node_count`

## 5. Smoke Test

> Run with: `python tests/smoke_summarize.py <batch_id>` where `<batch_id>` has already been through the intake and image-description stages.
> One-shot shortcut: `python tests/smoke_summarize.py --run-intake` — stages a new batch, describes images, then summarizes, all in sequence.

- [x] 5.1 Create `tests/smoke_summarize.py` that:
  - Accepts a `batch_id` argument, or `--run-intake` to run intake + image-description + summarization inline
  - Calls `summarize_batch(batch_id)` directly
  - Reads `staging/<batch_id>/intent_graph.json` and asserts at least one node is present
  - Asserts at least one node has a non-empty `related_images` list referencing one of the staged image ids
  - Reads `staging/<batch_id>/meta.json` and asserts `summarization_status == "complete"`
  - Prints each node's type + description (truncated to 120 chars) for manual inspection
  - Prints `✅ Smoke test passed` on success
- [x] 5.2 Document smoke test invocation in this file (done — see above)

## 6. Unit Tests

> Approach: mock the Anthropic client and TTC HTTP call for all LLM and network scenarios; `join_images_to_nodes` tested as a pure function with no mocking.

- [x] 6.1 **Typed nodes extracted** — mock Sonnet to return a Goal + Interruption → assert both node types present with non-empty `description` and `speaker`
- [x] 6.2 **No images in batch** — `descriptions.json` absent; mock Sonnet → all nodes have `related_images: []`, no crash
- [x] 6.3 **TTC fails — fallback exercised** — mock TTC to raise `requests.Timeout` → assert Sonnet is still called with the original (uncompressed) transcript; summarization completes
- [x] 6.4 **Empty transcript** — `transcript.json` is `[]` → Sonnet is NOT called; `intent_graph.json` is `[]`; `meta.json` has `summarization_status: "complete"`, `node_count: 0`
- [x] 6.5 **Sonnet returns invalid JSON** → `intent_graph.json` is `[]`; `summarization_status: "failed"` (no crash)
- [x] 6.6 **join_images_to_nodes — spanned node boundary inclusive** — pure unit test: Goal `[T, T+30]`, image at `T` and image at `T+30` → both joined; image at `T+31` → not joined
- [x] 6.7 **join_images_to_nodes — point-in-time window** — pure unit test: Interruption at `T`, `point_window_secs=60` → image at `T+60` joined; image at `T+61` not joined
- [x] 6.8 **join_images_to_nodes — unmatched image** — pure unit test: image `observed_at` outside every node window → appears in no `related_images` list (test that the function returns without error; unmatched logging is tested via log capture)
- [x] 6.9 **join_images_to_nodes — no images** — pure unit test: empty `image_records` → all nodes have `related_images: []`
