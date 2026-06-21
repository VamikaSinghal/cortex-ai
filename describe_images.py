"""
cortex/describe_images.py
-------------------------
Stage 1 worker: describe each staged image via Claude Haiku.

Usage (module):
    from describe_images import process_batch
    meta = process_batch("batch_<uuid>")

Run smoke test:
    python tests/smoke_describe.py --run-intake
"""

import base64
import json
from pathlib import Path
from uuid import uuid4

import anthropic

STAGING_DIR = Path("staging")
MODEL = "claude-haiku-4-5"
REDACTION_SYSTEM_PROMPT = (
    "Do not include: names or identifying features of people; "
    "on-screen text, document content, or displayed UI; "
    "contact information, account numbers, or credentials."
)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _media_type(filename: str) -> str:
    return _MEDIA_TYPES.get(Path(filename).suffix.lower(), "image/png")


def describe_image(
    image_bytes: bytes,
    observed_at: int,
    image_id: str = None,
    _client: anthropic.Anthropic = None,
) -> dict:
    """
    Describe a single image via Claude Haiku.
    Returns a SceneDescription-shaped record. Never raises.
    """
    if image_id is None:
        image_id = f"{observed_at}_{uuid4().hex[:8]}"
    if _client is None:
        _client = anthropic.Anthropic()

    base = {
        "id": image_id,
        "observed_at": observed_at,
        "redaction_applied": True,
        "is_first_in_batch": False,
    }

    try:
        encoded = base64.standard_b64encode(image_bytes).decode("utf-8")
        response = _client.messages.create(
            model=MODEL,
            max_tokens=1024,
            thinking={"type": "disabled"},
            system=REDACTION_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": _media_type(image_id),
                                "data": encoded,
                            },
                        },
                        {"type": "text", "text": "Describe what is happening in this image."},
                    ],
                }
            ],
        )

        if response.stop_reason != "end_turn":
            return {**base, "description": None, "error": f"unexpected stop_reason: {response.stop_reason}"}

        description = response.content[0].text if response.content else ""
        return {**base, "description": description, "error": None}

    except Exception as exc:
        return {**base, "description": None, "error": str(exc)}


def process_batch(batch_id: str) -> dict:
    """
    Process all images in staging/<batch_id>/ with Claude Haiku.
    Writes descriptions.json and updates meta.json. Returns updated meta.
    """
    batch_dir = STAGING_DIR / batch_id

    image_files = [
        f for f in batch_dir.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    ]

    def get_observed_at(path: Path) -> int:
        return int(path.name.split("_")[0])

    image_files.sort(key=get_observed_at)

    client = anthropic.Anthropic()
    records = []
    for i, img_path in enumerate(image_files):
        observed_at = get_observed_at(img_path)
        record = describe_image(
            img_path.read_bytes(),
            observed_at,
            image_id=img_path.name,
            _client=client,
        )
        if i == 0:
            record["is_first_in_batch"] = True
        records.append(record)

    (batch_dir / "descriptions.json").write_text(
        json.dumps(records, indent=2), encoding="utf-8"
    )

    described_count = sum(1 for r in records if r["error"] is None)
    failed_count = sum(1 for r in records if r["error"] is not None)

    if failed_count == 0:
        description_status = "complete"
    elif described_count == 0:
        description_status = "failed"
    else:
        description_status = "partial"

    meta_path = batch_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["description_status"] = description_status
    meta["described_count"] = described_count
    meta["failed_count"] = failed_count
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return meta
