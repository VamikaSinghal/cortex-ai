## 1. Route and Handler

- [x] 1.1 Register `POST /api/intake/batch` route in the server
- [x] 1.2 Wire multipart/form-data parser (e.g. `python-multipart` or framework equivalent)
- [x] 1.3 Return `405 Method Not Allowed` for non-POST requests to this path

## 2. Request Size Limits

- [x] 2.1 Reject request if `images[]` count > 20 → 400 with message
- [x] 2.2 Reject any single image > 5 MB → 400 naming the image
- [x] 2.3 Reject `transcript` field > 500 KB → 400 with message
- [x] 2.4 Catch malformed multipart parse errors → 400 (not 500)

## 3. Image Timestamp Extraction

- [x] 3.1 Implement `extract_timestamp(filename, field_name, form_data) -> int | None`: try longest parseable integer in filename, then `<field_name>_ts` companion field
- [x] 3.2 For each uploaded image: call extractor; if None → 400 `"image '<filename>' missing timestamp"`

## 4. Transcript Validation

- [x] 4.1 Parse `transcript` field as JSON; on failure → 400 `"transcript must be valid JSON"`
- [x] 4.2 Assert top-level is a list; on failure → 400
- [x] 4.3 For each turn at index `i`, assert presence of `speaker`, `text`, `started_at`, `ended_at`; on missing field → 400 `"transcript[i] missing required field: <field>"`
- [x] 4.4 Assert `started_at` and `ended_at` are integers; on failure → 400 naming index and field

## 5. Staging Persistence

- [x] 5.1 Generate `batch_id = "batch_" + str(uuid4())`
- [x] 5.2 Create directory `staging/<batch_id>/`
- [x] 5.3 Write each image to `staging/<batch_id>/<timestamp>_<original_filename>`
- [x] 5.4 Write `transcript.json` to `staging/<batch_id>/transcript.json`
- [x] 5.5 Write `meta.json` to `staging/<batch_id>/meta.json` with `{ batch_id, image_count, received_at }` (ISO timestamp)

## 6. Response

- [x] 6.1 On success: return `202 Accepted` with body `{ "batch_id": "<batch_id>" }`
- [x] 6.2 Ensure all 400 responses include a JSON body with an `"error"` string (never a bare string or HTML)

## 7. Smoke Test

> Run with: `python tests/smoke_intake.py` against a running server at `http://localhost:8000`

- [x] 7.1 Create `tests/smoke_intake.py` that:
  - Generates 2-3 small in-memory PNG images (1×1 pixel, programmatically created with Pillow or raw bytes)
  - Names them with real UNIX timestamps (e.g. `1718900000.png`, `1718900005.png`)
  - Builds a 2-turn transcript JSON `[{ "speaker": "A", "text": "...", "started_at": ..., "ended_at": ... }, ...]`
  - POSTs to `POST /api/intake/batch` as multipart/form-data
  - Asserts response status is 202
  - Asserts response body contains `batch_id` starting with `"batch_"`
  - Prints `✅ Smoke test passed: batch_id=<batch_id>` on success
- [x] 7.2 Document smoke test invocation in this file (done — see above)

## 8. Test Cases

> Approach: integration tests against a running test server instance (faster to write than mocking multipart; acceptable for hackathon).

- [x] 8.1 **Valid batch** — N images with timestamps + valid 2-turn transcript → 202 + `batch_id`
- [x] 8.2 **Missing image timestamp** — one image with no parseable timestamp → 400 naming that image's filename
- [x] 8.3 **Unparseable image timestamp** — filename is `photo.png` (no integers) → 400 naming the image
- [x] 8.4 **Missing transcript field** — turn missing `started_at` → 400 naming index and field
- [x] 8.5 **Transcript not JSON** — `transcript` is `"not json"` → 400
- [x] 8.6 **Empty images array** — no `images[]` fields → 400
- [x] 8.7 **Too many images** — 21 images → 400 citing count limit
- [x] 8.8 **Oversized image** — single image > 5 MB → 400 naming the image
- [x] 8.9 **Oversized transcript** — transcript field > 500 KB → 400
- [x] 8.10 **Malformed multipart** — wrong Content-Type or corrupt body → 400, not 500
- [x] 8.11 **Staged files exist** — after 202, assert `staging/<batch_id>/` contains images, `transcript.json`, `meta.json`
