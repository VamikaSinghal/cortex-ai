## 1. Haiku API Call Wrapper

- [x] 1.1 Create `describe_images.py` with a `describe_image(image_bytes, observed_at) -> dict` function
- [x] 1.2 Call `claude-haiku-4-5` via the Anthropic Messages API with the image as a base64-encoded `image` content block
- [x] 1.3 Set thinking to standard (non-extended) — pass `thinking={"type": "disabled"}` explicitly in the API call
- [x] 1.4 Include verbatim redaction system prompt: _"Do not include: names or identifying features of people; on-screen text, document content, or displayed UI; contact information, account numbers, or credentials."_
- [x] 1.5 On success: return `{ id, description, observed_at, redaction_applied: True, is_first_in_batch: False, error: None }`
- [x] 1.6 On API error, timeout, or content refusal (stop_reason != "end_turn"): return `{ id, description: None, observed_at, redaction_applied: True, is_first_in_batch: False, error: "<message>" }` — do not raise

## 2. Batch Worker

- [x] 2.1 Create `process_batch(batch_id) -> dict` function that reads from `staging/<batch_id>/`
- [x] 2.2 Glob image files from `staging/<batch_id>/`, reconstruct `observed_at` from filename prefix (integer before first `_`)
- [x] 2.3 Sort images by `observed_at` ascending before any API calls
- [x] 2.4 Set `is_first_in_batch: True` on the record for the lowest `observed_at` image
- [x] 2.5 Call `describe_image()` per image in sorted order; collect all records (success and failure)
- [x] 2.6 Write records to `staging/<batch_id>/descriptions.json` as a JSON array
- [x] 2.7 Compute `described_count` and `failed_count` from records
- [x] 2.8 Determine `description_status`: `"complete"` if `failed_count == 0`; `"failed"` if `described_count == 0`; otherwise `"partial"`
- [x] 2.9 Update `staging/<batch_id>/meta.json` with `description_status`, `described_count`, `failed_count`

## 3. Smoke Test

> Run with: `python tests/smoke_describe.py <batch_id>` where `<batch_id>` was returned by a prior intake smoke test run.
> One-shot shortcut: `python tests/smoke_describe.py --run-intake` — runs intake internally then immediately describes the resulting batch.

- [x] 3.1 Create `tests/smoke_describe.py` that:
  - Accepts a `batch_id` argument, or `--run-intake` to stage a new batch inline (reusing `smoke_intake.py`'s PNG helpers)
  - Calls `process_batch(batch_id)` directly (no HTTP layer needed for this stage)
  - Reads `staging/<batch_id>/descriptions.json` and asserts:
    - One record per image
    - Every record has a non-empty `description` (no failures on valid images)
    - Every record has the correct `observed_at` matching the filename prefix
    - Exactly one record has `is_first_in_batch: True`
  - Reads `staging/<batch_id>/meta.json` and asserts `description_status == "complete"`
  - Prints each description (truncated to 120 chars) so the caller can spot-check redaction
  - Prints `✅ Smoke test passed` on success
- [x] 3.2 Document smoke test invocation in this file (done — see above)

## 4. Test Cases

> Approach: unit tests with mocked Anthropic client for failure scenarios; real API call only in smoke test and the redaction spot-check (manual).

- [x] 4.1 **Normal batch** — 3 images with distinct timestamps → 3 records in `observed_at` order, all descriptions non-empty, exactly one `is_first_in_batch: True`
- [x] 4.2 **Single image fails** — mock Haiku to raise an exception for image B in [A, B, C] → A and C have descriptions; B has `description: null` and non-empty `error`; `meta.json` has `description_status: "partial"`, `described_count: 2`, `failed_count: 1`
- [x] 4.3 **All images fail** — mock Haiku to raise for every image → all records have `description: null`; `meta.json` has `description_status: "failed"`, `described_count: 0`
- [x] 4.4 **Redaction spot-check** (manual / live API) — submit a real image with a visible face and on-screen text; print the description and manually confirm names, face descriptions, and on-screen text are absent. Note in test output: _"This is a spot-check, not a guarantee — redaction is prompt-level."_
- [x] 4.5 **Batch of exactly 1 image** → single record has both `is_first_in_batch: True` and a non-empty description; `meta.json` has `description_status: "complete"`, `described_count: 1`, `failed_count: 0`
- [x] 4.6 **Ordering assertion** — stage images with out-of-order filenames (`T+5`, `T`, `T+2`) → records in `descriptions.json` appear in order `T`, `T+2`, `T+5`
