## ADDED Requirements

### Requirement: Extract typed intent-graph nodes from transcript
The worker SHALL call `claude-sonnet-4-6` with the transcript text and produce a list of zero or more typed intent-graph nodes of types `Goal`, `Interruption`, `Commitment`, and `ObjectLocation`. Each node MUST carry: `id` (string, unique within batch), `node_type`, `description`, `speaker`, and `related_images` (initially empty list, populated by the join step).

#### Scenario: Transcript with goal and interruption produces typed nodes
- **WHEN** the transcript contains a speaker articulating a plan and then being interrupted
- **THEN** the output includes at least one `Goal` node and at least one `Interruption` node, each with non-empty `description` and `speaker`

#### Scenario: Empty transcript produces empty node list without error
- **WHEN** `transcript.json` contains an empty array `[]`
- **THEN** `intent_graph.json` is written as `[]` and `meta.json` has `summarization_status: "complete"`, `node_count: 0`

#### Scenario: Batch status written after summarization
- **WHEN** summarization completes successfully
- **THEN** `meta.json` has `summarization_status: "complete"` and `node_count` equal to the number of nodes extracted

### Requirement: Pre-compress transcript with The Token Company before Sonnet call
The worker SHALL call the TTC compression API with the accuracy-preserving operating point on the raw transcript text before constructing the Sonnet prompt. TTC's role is strictly token-count reduction; it SHALL NOT be used to filter or judge content relevance.

#### Scenario: TTC succeeds — compressed text sent to Sonnet
- **WHEN** the TTC API returns compressed text
- **THEN** the compressed text (not the original) is sent to the Sonnet summarization call

#### Scenario: TTC fails or times out — uncompressed fallback
- **WHEN** the TTC API raises an exception or times out
- **THEN** the worker logs a warning and sends the original uncompressed transcript to Sonnet; the batch does NOT fail

### Requirement: Join image records to nodes by timestamp
After Sonnet extraction, the worker SHALL call a discrete join function that sets `related_images` on each node to the list of `id` values from `descriptions.json` whose `observed_at` falls within that node's time window. The join function SHALL be pure (no LLM calls, no I/O) and independently testable.

#### Scenario: Spanned node (Goal) — image within span is joined
- **WHEN** a `Goal` node has `started_at: T` and `ended_at: T+30`, and an image has `observed_at: T+15`
- **THEN** that image's `id` appears in the node's `related_images`

#### Scenario: Spanned node — image outside span is not joined
- **WHEN** a `Goal` node spans `[T, T+30]` and an image has `observed_at: T+31`
- **THEN** that image's `id` does NOT appear in that node's `related_images`

#### Scenario: Spanned node — boundary is inclusive on both ends
- **WHEN** an image has `observed_at` equal to exactly `started_at` or exactly `ended_at`
- **THEN** the image IS joined to the node

#### Scenario: Point-in-time node (Interruption) — image within default ±60s window is joined
- **WHEN** an `Interruption` node has `occurred_at: T` and an image has `observed_at: T+45`
- **THEN** that image's `id` appears in `related_images`

#### Scenario: Point-in-time node — image outside window is not joined
- **WHEN** an `Interruption` node has `occurred_at: T` and an image has `observed_at: T+61`
- **THEN** that image's `id` does NOT appear in `related_images` and the image is logged as unmatched

#### Scenario: No images in batch — join produces empty related_images without error
- **WHEN** `descriptions.json` is absent or contains no records
- **THEN** all nodes have `related_images: []` and no error is raised

#### Scenario: Image unmatched by any node — surfaced as unmatched
- **WHEN** an image's `observed_at` falls outside every node's time window
- **THEN** the image `id` is logged as unmatched (not silently dropped)

### Requirement: Persist output to intent_graph.json
The worker SHALL write all extracted (and joined) nodes to `staging/<batch_id>/intent_graph.json` as a JSON array.

#### Scenario: File written after processing
- **WHEN** the worker finishes
- **THEN** `staging/<batch_id>/intent_graph.json` exists and contains one object per extracted node
