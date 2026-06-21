## ADDED Requirements

### Requirement: Process images in observed_at order
The worker SHALL sort all images in a staged batch by their `observed_at` timestamp ascending before any Haiku calls are made.

#### Scenario: Multiple images processed oldest-first
- **WHEN** a batch contains images with `observed_at` values `[T+5, T, T+2]`
- **THEN** the worker processes them in order `T`, `T+2`, `T+5`

#### Scenario: Output records reflect sorted order
- **WHEN** descriptions are written to `descriptions.json`
- **THEN** records appear in ascending `observed_at` order

### Requirement: Tag first image in batch
The image with the lowest `observed_at` in the batch SHALL have `is_first_in_batch: true` in its output record. All other records SHALL have `is_first_in_batch: false`.

#### Scenario: Exactly one record tagged as first
- **WHEN** a batch of N images is processed
- **THEN** exactly one record in `descriptions.json` has `is_first_in_batch: true`

#### Scenario: Batch of one image
- **WHEN** a batch contains exactly one image
- **THEN** that image's record has `is_first_in_batch: true`

### Requirement: Call Haiku with standard thinking and redaction prompt
Each image SHALL be described by a call to `claude-haiku-4-5` via the Messages API. The call MUST use standard (non-extended) thinking. The system prompt MUST include verbatim:

> Do not include: names or identifying features of people; on-screen text, document content, or displayed UI; contact information, account numbers, or credentials.

#### Scenario: Description record produced for valid image
- **WHEN** Haiku returns a successful response for an image
- **THEN** the output record has a non-empty `description`, `redaction_applied: true`, and `error: null`

#### Scenario: observed_at carried through
- **WHEN** an image with `observed_at: 1718900000` is described
- **THEN** the output record has `observed_at: 1718900000`

### Requirement: Per-image failure isolation
If Haiku fails for a single image (API error, timeout, or content refusal), the worker SHALL record the failure against that image's id and continue processing the remaining images. The batch MUST NOT abort.

#### Scenario: One image fails, rest succeed
- **WHEN** Haiku fails for image B in a batch [A, B, C]
- **THEN** records for A and C have non-empty descriptions; B's record has `description: null` and a non-empty `error` string

#### Scenario: All images fail
- **WHEN** Haiku fails for every image in a batch
- **THEN** `descriptions.json` contains one failure record per image and `meta.json` has `description_status: "failed"`

### Requirement: Batch status written to meta.json
After processing, the worker SHALL update `staging/<batch_id>/meta.json` with `description_status`, `described_count`, and `failed_count`.

#### Scenario: All succeed
- **WHEN** all N images are described successfully
- **THEN** `meta.json` has `description_status: "complete"`, `described_count: N`, `failed_count: 0`

#### Scenario: Partial failure
- **WHEN** M of N images fail
- **THEN** `meta.json` has `description_status: "partial"`, `described_count: N-M`, `failed_count: M`

#### Scenario: Total failure
- **WHEN** all images fail
- **THEN** `meta.json` has `description_status: "failed"` (not `"complete"` or `"partial"`)

### Requirement: Output persisted to descriptions.json
The worker SHALL write all output records (success and failure) to `staging/<batch_id>/descriptions.json` as a JSON array.

#### Scenario: File exists after processing
- **WHEN** the worker finishes processing a batch
- **THEN** `staging/<batch_id>/descriptions.json` exists and contains one record per image
