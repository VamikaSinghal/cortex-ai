## ADDED Requirements

### Requirement: LLM judges each non-first image for relevance
For every image in a batch whose `is_first_in_batch` is `false`, the worker SHALL submit the image's description and its linked intent-graph node descriptions (if any) to a Claude model call. The model SHALL return a `decision` of `"keep"` or `"delete"` and a non-empty `reason` string for each image. The judgment MUST NOT be requested for the first-in-batch image — that image is always retained and the model is not asked about it.

#### Scenario: Redundant image marked delete
- **WHEN** an image description substantially duplicates the text of its linked intent-graph node
- **THEN** the model returns `decision: "delete"` with a non-empty reason

#### Scenario: Informative image marked keep
- **WHEN** an image description contains detail not captured in any linked node's text
- **THEN** the model returns `decision: "keep"` with a non-empty reason

#### Scenario: Unlinked image defaults to delete
- **WHEN** an image has no linked intent-graph nodes (`related_images` of every node does not include this image's id)
- **THEN** the model returns `decision: "delete"` (default for unlinked images — no node context implies nothing was happening worth capturing visually); reason MUST still be non-empty

#### Scenario: First-in-batch image is never submitted for judgment
- **WHEN** the batch contains one or more images
- **THEN** the image with `is_first_in_batch: true` is excluded from the judgment call input entirely

### Requirement: First-in-batch image is protected by a code-level guard
A dedicated function (not a prompt instruction) SHALL refuse to execute any deletion against an image whose `is_first_in_batch` field is `true`, regardless of the model's output. This guard MUST run before any file or record deletion occurs.

#### Scenario: Model anomalously returns delete for first image — guard blocks it
- **WHEN** the model output (via mock or real call) contains `decision: "delete"` for the first-in-batch image
- **THEN** the guard function refuses the deletion, logs an anomaly at WARNING level, and the image bytes and description record remain intact

#### Scenario: Batch of exactly one image — LLM call skipped entirely
- **WHEN** a batch contains exactly one image
- **THEN** that image is first-in-batch; the worker writes `pruning.json` with a single keep entry (reason: "only image in batch") and does NOT call the LLM

### Requirement: Decisions persisted to pruning.json with reasons
The worker SHALL write a `pruning.json` file to `staging/<batch_id>/` containing one record per non-first image with fields `id`, `decision`, and `reason`. The first-in-batch image SHALL also appear in `pruning.json` with `decision: "keep"` and an appropriate reason, even though the guard — not the model — determined this.

#### Scenario: pruning.json contains all images after run
- **WHEN** the worker finishes processing a batch
- **THEN** `staging/<batch_id>/pruning.json` exists and contains exactly one record per image in the batch

#### Scenario: Every record has a non-empty reason
- **WHEN** `pruning.json` is written
- **THEN** every record has a `reason` field with a non-empty string

### Requirement: Soft-delete execution
For images with `decision: "delete"` that pass the guard, the worker SHALL:
1. Delete the image byte file from `staging/<batch_id>/`
2. Set `deleted: true` on the corresponding record in `descriptions.json` (tombstone); the description text and all other fields MUST be preserved

#### Scenario: Deleted image bytes removed, record tombstoned
- **WHEN** a non-first image is marked delete and the guard does not block it
- **THEN** the image file no longer exists in `staging/<batch_id>/`; the `descriptions.json` record has `deleted: true` and its `description` field is unchanged

#### Scenario: Kept image bytes and record untouched
- **WHEN** an image is marked keep
- **THEN** the image file remains in `staging/<batch_id>/`; its `descriptions.json` record has no `deleted` field (or `deleted: false`)

### Requirement: Fail-safe on model error
If the relevance judgment call fails (API error, timeout, or unparseable response), the worker SHALL retain ALL images, write no deletions to `pruning.json` (or write keep for all with reason "model error — fail safe"), log the failure, and set `pruning_status: "failed"` in `meta.json`.

#### Scenario: Model call fails — no images deleted
- **WHEN** the Claude API raises an exception or returns an unparseable response
- **THEN** no image bytes are deleted; `meta.json` has `pruning_status: "failed"`; all `descriptions.json` records are unchanged

### Requirement: Batch status written to meta.json
After pruning, the worker SHALL update `staging/<batch_id>/meta.json` with `pruning_status` (`"complete"` or `"failed"`), `kept_count`, and `deleted_count`.

#### Scenario: Counts correct after successful run
- **WHEN** the worker completes without error
- **THEN** `meta.json` has `pruning_status: "complete"`, `kept_count` equal to images not deleted, `deleted_count` equal to images deleted
