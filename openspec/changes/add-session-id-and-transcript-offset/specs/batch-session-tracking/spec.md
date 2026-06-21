## ADDED Requirements

### Requirement: Intake API accepts optional session_id field
The intake endpoint SHALL accept an optional `session_id` string field in the multipart body. When present, the value SHALL be stored in `meta.json` under the key `session_id`. When absent, `meta.json` SHALL store `null` for `session_id`. The field SHALL NOT be validated beyond being a non-empty string if provided.

#### Scenario: session_id present and stored
- **WHEN** a client posts a valid batch with `session_id = "sess-abc-123"`
- **THEN** the server returns 202 and `meta.json` contains `"session_id": "sess-abc-123"`

#### Scenario: session_id omitted
- **WHEN** a client posts a valid batch without a `session_id` field
- **THEN** the server returns 202 and `meta.json` contains `"session_id": null`

#### Scenario: session_id does not affect existing validation
- **WHEN** a client posts a batch with an invalid image or malformed transcript alongside a valid `session_id`
- **THEN** the server returns the appropriate 4xx error (session_id does not suppress other validation failures)

---

### Requirement: Intake API accepts optional transcript_offset field
The intake endpoint SHALL accept an optional `transcript_offset` integer field in the multipart body representing the index of the first new turn in the transcript array. When present, the value SHALL be stored in `meta.json` under the key `transcript_offset`. When absent, `meta.json` SHALL store `0` for `transcript_offset`.

#### Scenario: transcript_offset present and stored
- **WHEN** a client posts a valid batch with `transcript_offset = 3`
- **THEN** the server returns 202 and `meta.json` contains `"transcript_offset": 3`

#### Scenario: transcript_offset defaults to 0 when omitted
- **WHEN** a client posts a valid batch without a `transcript_offset` field
- **THEN** the server returns 202 and `meta.json` contains `"transcript_offset": 0`

#### Scenario: transcript_offset exceeding turn count is clamped
- **WHEN** a client posts a batch with `transcript_offset = 99` but the transcript contains only 5 turns
- **THEN** the server returns 202; `summarize_batch` treats offset as `0` (all turns are new) rather than raising an error

---

### Requirement: Transcript turns may include started_at and ended_at
The intake endpoint SHALL accept transcript turn objects that include `started_at` and `ended_at` integer fields in addition to the existing required fields (`speaker`, `text`, `timestamp`). Their presence SHALL NOT trigger a validation error. Their absence SHALL NOT trigger a validation error.

#### Scenario: Turn with started_at and ended_at accepted
- **WHEN** a client posts a transcript turn containing `{"speaker":"0","text":"hi","timestamp":1750000000,"started_at":1750000000,"ended_at":1750000001}`
- **THEN** the server returns 202 and the turn is stored in `transcript.json` as received

#### Scenario: Turn without started_at and ended_at still accepted
- **WHEN** a client posts a transcript turn containing only `{"speaker":"0","text":"hi","timestamp":1750000000}`
- **THEN** the server returns 202 and the turn is stored in `transcript.json` as received

---

### Requirement: Summarisation stage filters nodes by transcript_offset
The `summarize_batch()` function SHALL read `transcript_offset` from `meta.json`. After running Sonnet extraction and image-join, it SHALL remove any node whose `occurred_at` is strictly less than `turns[transcript_offset]["timestamp"]` before writing `intent_graph.json`. When `transcript_offset` is `0` or out of range, the filter SHALL be skipped and all extracted nodes SHALL be written.

#### Scenario: New nodes only written when offset is set
- **WHEN** `meta.json` has `transcript_offset = 2` and turns[2].timestamp = 1750000010 and Sonnet extracts nodes at t=1750000000, t=1750000010, t=1750000020
- **THEN** `intent_graph.json` contains only the nodes at t=1750000010 and t=1750000020

#### Scenario: All nodes written when offset is 0
- **WHEN** `meta.json` has `transcript_offset = 0` and Sonnet extracts 3 nodes
- **THEN** `intent_graph.json` contains all 3 nodes

#### Scenario: All nodes written when offset is absent (legacy batches)
- **WHEN** `meta.json` has no `transcript_offset` key (batch staged before this change)
- **THEN** `summarize_batch` treats offset as 0 and writes all extracted nodes unchanged
