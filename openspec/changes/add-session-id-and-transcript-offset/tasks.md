## 1. Update Intake API

- [ ] 1.1 Add `TURN_OPTIONAL_INT_FIELDS = ("started_at", "ended_at")` constant in `intake_api.py` and update `validate_transcript()` to verify these fields are integers when present but not require them
- [ ] 1.2 Read `session_id` from the multipart form body (string, optional; default `None`) and `transcript_offset` (integer string, optional; default `0`) in `intake_batch()`
- [ ] 1.3 Validate `transcript_offset`: must be a non-negative integer if provided; return 400 with a clear error if non-numeric
- [ ] 1.4 Clamp `transcript_offset` to `max(0, min(transcript_offset, len(turns)))` so an out-of-range value never causes an IndexError downstream
- [ ] 1.5 Extend the `meta` dict written to `meta.json` to include `session_id` (string or `null`) and `transcript_offset` (int)

## 2. Update Summarisation Stage

- [ ] 2.1 In `summarize_batch()` in `summarize_transcript.py`, read `transcript_offset` from `meta.json` (default `0` if key absent, for backward compat with legacy batches)
- [ ] 2.2 After `join_images_to_nodes()`, apply the offset filter: if `transcript_offset > 0` and `turns` is non-empty and `transcript_offset < len(turns)`, compute `cutoff = turns[transcript_offset]["timestamp"]` and remove nodes where `node.get("occurred_at", 0) < cutoff`
- [ ] 2.3 Write `transcript_offset` into the updated `meta.json` at the end of `summarize_batch()` so the value is visible in pipeline status outputs

## 3. Tests

- [ ] 3.1 Add `test_session_id_stored_in_meta` to `tests/test_intake.py`: post a batch with `session_id="test-sess-001"` and assert `meta.json["session_id"] == "test-sess-001"`
- [ ] 3.2 Add `test_session_id_null_when_omitted`: post without `session_id` and assert `meta.json["session_id"] is None`
- [ ] 3.3 Add `test_transcript_offset_stored_in_meta`: post with `transcript_offset=3` and assert `meta.json["transcript_offset"] == 3`
- [ ] 3.4 Add `test_transcript_offset_defaults_to_zero`: post without `transcript_offset` and assert `meta.json["transcript_offset"] == 0`
- [ ] 3.5 Add `test_started_at_ended_at_accepted`: post a turn with `started_at` and `ended_at` present and assert 202 (no 400)
- [ ] 3.6 Add `test_started_at_must_be_int_if_present`: post a turn with `started_at = "not-a-number"` and assert 400
- [ ] 3.7 Add unit tests for `summarize_batch` offset filter in `tests/test_summarize.py`: mock `meta.json` with `transcript_offset=1`, run `summarize_batch`, assert nodes with `occurred_at` before `turns[1]["timestamp"]` are absent from `intent_graph.json`
- [ ] 3.8 Update `tests/mock_android_upload.py` to include `session_id` and `transcript_offset` fields and `started_at`/`ended_at` in transcript turns, mirroring the updated Android client payload
