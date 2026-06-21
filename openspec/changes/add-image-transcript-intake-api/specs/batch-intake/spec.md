## ADDED Requirements

### Requirement: Accept multipart batch submission
The system SHALL expose `POST /api/intake/batch` accepting `multipart/form-data` with one or more image files under the field `images[]` and a JSON blob under the field `transcript`.

#### Scenario: Valid batch accepted
- **WHEN** a client posts a valid multipart request with ≥1 timestamped image and a conforming transcript
- **THEN** the server returns `202 Accepted` with body `{ "batch_id": "<batch_<uuid4>>" }`

#### Scenario: Empty images array rejected
- **WHEN** a client posts a request with no images
- **THEN** the server returns `400` with `{ "error": "images[] must contain at least one image" }`

#### Scenario: Batch exceeds image count limit
- **WHEN** a client posts more than 20 images
- **THEN** the server returns `400` with `{ "error": "batch exceeds max image count (20)" }`

### Requirement: Require UNIX timestamp per image
Every image in the batch MUST carry a UNIX timestamp (seconds since epoch) identifying when it was captured. The server SHALL extract the timestamp from the image filename (longest parseable integer in the name), falling back to a companion form field `<fieldname>_ts`. If neither yields a parseable integer, the request is rejected.

#### Scenario: Timestamp in filename
- **WHEN** an image is submitted with filename `1718900000.jpg`
- **THEN** the server extracts `1718900000` as the capture timestamp

#### Scenario: Timestamp via companion field
- **WHEN** an image field is named `img0` and a companion field `img0_ts` contains `"1718900000"`
- **THEN** the server uses `1718900000` as the capture timestamp

#### Scenario: Missing timestamp rejected
- **WHEN** an image has no parseable timestamp in its filename and no companion `_ts` field
- **THEN** the server returns `400` with `{ "error": "image '<filename>' missing timestamp" }`

### Requirement: Validate transcript schema
The `transcript` field MUST be a JSON array. Each element MUST have `speaker` (string), `text` (string), `started_at` (integer UNIX timestamp), and `ended_at` (integer UNIX timestamp). The entire batch is rejected if any turn is invalid.

#### Scenario: Valid transcript accepted
- **WHEN** `transcript` is `[{ "speaker": "A", "text": "hello", "started_at": 1718900000, "ended_at": 1718900005 }]`
- **THEN** the server accepts the transcript

#### Scenario: Missing required turn field rejected
- **WHEN** a turn object is missing `started_at`
- **THEN** the server returns `400` with `{ "error": "transcript[0] missing required field: started_at" }` (index and field named explicitly)

#### Scenario: Transcript not JSON rejected
- **WHEN** the `transcript` field is not valid JSON
- **THEN** the server returns `400` with `{ "error": "transcript must be valid JSON" }`

### Requirement: Enforce request size limits
The server SHALL reject requests that exceed the configured limits before any processing.

#### Scenario: Oversized individual image rejected
- **WHEN** any single image exceeds 5 MB
- **THEN** the server returns `400` with `{ "error": "image '<filename>' exceeds max size (5 MB)" }`

#### Scenario: Oversized transcript rejected
- **WHEN** the transcript field exceeds 500 KB
- **THEN** the server returns `400` with `{ "error": "transcript exceeds max size (500 KB)" }`

#### Scenario: Malformed multipart rejected cleanly
- **WHEN** the request body is not valid multipart/form-data
- **THEN** the server returns `400` (not `500`)

### Requirement: Stage batch to filesystem
On a valid request, the server SHALL persist the batch to `staging/<batch_id>/` before returning 202. The directory MUST contain: each image file (named by timestamp), `transcript.json`, and `meta.json` with `{ batch_id, image_count, received_at }`.

#### Scenario: Staged batch readable by next stage
- **WHEN** a 202 is returned for a batch
- **THEN** `staging/<batch_id>/` exists and contains all submitted images, `transcript.json`, and `meta.json`
