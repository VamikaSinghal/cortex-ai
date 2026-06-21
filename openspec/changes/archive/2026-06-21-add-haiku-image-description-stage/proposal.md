## Why

Raw images from the intake stage must never reach permanent storage. This stage converts each staged image into a redacted text description via Claude Haiku, fulfilling Recall's core privacy thesis. The text output — not the image — is what persists and flows to downstream stages.

## What Changes

- New worker: reads a staged batch from `staging/<batch_id>/`, processes each image through Claude Haiku, writes a `SceneDescription` record per image
- Records are written back to the batch's staging directory as `descriptions.json`
- Partial-failure handling: a single image failure records an error against that image's id and continues; the batch status reflects partial vs. full success
- Images processed in `observed_at` order (oldest first); first image tagged `is_first_in_batch: true`
- **No LLM model choice**: Claude Haiku only, standard (non-extended) thinking. Extended thinking is explicitly disabled — this is a bounded image-description task running once per image across potentially dozens of images in a batch; extended thinking adds latency and cost with no benefit.
- **Known limitation**: Redaction is prompt-level, not verified. Same limitation already documented for the intent graph's SceneDescription node — the model is instructed to omit the categories below but this is not cryptographically enforced.

## Capabilities

### New Capabilities

- `haiku-image-description`: Consume a staged batch, call Haiku per image in `observed_at` order, produce a `SceneDescription`-shaped record per image with per-image failure handling.

### Modified Capabilities

_None._

## Impact

- New worker module (`describe_images.py` or equivalent)
- Reads from `staging/<batch_id>/` written by `add-image-transcript-intake-api`
- Writes `descriptions.json` + updates `meta.json` batch status back to same staging dir
- Adds `anthropic` SDK call (already in `requirements.txt`)
- Does not delete any images — pruning is out of scope (final change)
