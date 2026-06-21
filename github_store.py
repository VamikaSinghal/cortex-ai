"""
cortex/github_store.py
----------------------
Push extracted context notes to a GitHub repo via the GitHub REST API.
Each insight/decision/question becomes a .md file.
Each commit message encodes source + timestamp for full provenance.
"""

import base64
import os
import re
import requests
from datetime import datetime
from typing import Optional

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
CORTEX_REPO = os.environ.get("CORTEX_REPO", "")  # e.g. "vamikasinghal/cortex-brain"
GITHUB_API = "https://api.github.com"
BRANCH = os.environ.get("CORTEX_BRANCH", "main")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _safe_filename(text: str, max_len: int = 50) -> str:
    """Turn arbitrary text into a safe filename slug."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:max_len]


def _get_existing_sha(filepath: str) -> Optional[str]:
    """Get the SHA of a file if it already exists (needed for updates)."""
    url = f"{GITHUB_API}/repos/{CORTEX_REPO}/contents/{filepath}"
    resp = requests.get(url, headers=_headers(), params={"ref": BRANCH})
    if resp.status_code == 200:
        return resp.json().get("sha")
    return None


def push_file(filepath: str, content: str, commit_message: str) -> bool:
    """
    Create or update a file in the GitHub repo.

    Args:
        filepath: Path within the repo, e.g. "notes/insights/2026-06-20-insight.md"
        content: Markdown content to write
        commit_message: Git commit message

    Returns:
        True if successful
    """
    if not GITHUB_TOKEN or not CORTEX_REPO:
        raise EnvironmentError("GITHUB_TOKEN and CORTEX_REPO must be set in environment")

    url = f"{GITHUB_API}/repos/{CORTEX_REPO}/contents/{filepath}"
    sha = _get_existing_sha(filepath)

    payload = {
        "message": commit_message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": BRANCH,
    }
    if sha:
        payload["sha"] = sha  # Required for updates

    resp = requests.put(url, headers=_headers(), json=payload)
    return resp.status_code in (200, 201)


def _build_note_frontmatter(note_type: str, source: str, timestamp: str, extra: dict = None) -> str:
    lines = [
        "---",
        f"type: {note_type}",
        f"source: {source}",
        f"timestamp: {timestamp}",
    ]
    if extra:
        for k, v in extra.items():
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def save_extracted_context(extracted: dict, raw_text: str = "") -> list[str]:
    """
    Save all extracted context items to the GitHub repo.

    Args:
        extracted: Output from ingest.extract_context()
        raw_text: Original raw text (stored as reference excerpt)

    Returns:
        List of file paths that were successfully saved
    """
    source = extracted.get("_source", "unknown")
    timestamp = extracted.get("_timestamp", datetime.now().isoformat())
    date_str = timestamp[:10]  # YYYY-MM-DD
    commit_prefix = f"ingest({source}): {timestamp}"

    saved = []

    # ── 1. Key Insights ──────────────────────────────────────────────────────
    for i, insight in enumerate(extracted.get("KEY_INSIGHTS", [])):
        slug = _safe_filename(insight)
        filepath = f"notes/insights/{date_str}-{slug}.md"
        frontmatter = _build_note_frontmatter("insight", source, timestamp)
        content = f"""{frontmatter}

# {insight}

## Source
`{source}` · {timestamp}

## Raw Excerpt
> {raw_text[:300].strip()}{"..." if len(raw_text) > 300 else ""}
"""
        if push_file(filepath, content, f"{commit_prefix} | insight {i+1}"):
            saved.append(filepath)

    # ── 2. Decisions ─────────────────────────────────────────────────────────
    for i, decision in enumerate(extracted.get("DECISIONS", [])):
        slug = _safe_filename(decision)
        filepath = f"notes/decisions/{date_str}-{slug}.md"
        frontmatter = _build_note_frontmatter("decision", source, timestamp)
        content = f"""{frontmatter}

# Decision: {decision}

## Source
`{source}` · {timestamp}
"""
        if push_file(filepath, content, f"{commit_prefix} | decision {i+1}"):
            saved.append(filepath)

    # ── 3. Open Questions ─────────────────────────────────────────────────────
    for i, question in enumerate(extracted.get("OPEN_QUESTIONS", [])):
        slug = _safe_filename(question)
        filepath = f"notes/questions/{date_str}-{slug}.md"
        frontmatter = _build_note_frontmatter("open-question", source, timestamp)
        content = f"""{frontmatter}

# ❓ {question}

**Status:** open

## Source
`{source}` · {timestamp}
"""
        if push_file(filepath, content, f"{commit_prefix} | question {i+1}"):
            saved.append(filepath)
        # Optionally also file as a GitHub Issue (see open_github_issue below)

    # ── 4. People ─────────────────────────────────────────────────────────────
    for person in extracted.get("PEOPLE", []):
        name = person.get("name", "unknown") if isinstance(person, dict) else str(person)
        ctx = person.get("context", "") if isinstance(person, dict) else ""
        slug = _safe_filename(name)
        filepath = f"notes/people/{slug}.md"

        # For people, we append to existing note rather than overwrite
        existing_sha = _get_existing_sha(filepath)
        if existing_sha:
            # Append new context entry
            get_resp = requests.get(
                f"{GITHUB_API}/repos/{CORTEX_REPO}/contents/{filepath}",
                headers=_headers()
            )
            if get_resp.status_code == 200:
                existing_content = base64.b64decode(get_resp.json()["content"]).decode("utf-8")
                new_entry = f"\n## {timestamp} · {source}\n{ctx}\n"
                content = existing_content + new_entry
            else:
                content = _person_note(name, ctx, source, timestamp)
        else:
            content = _person_note(name, ctx, source, timestamp)

        if push_file(filepath, content, f"{commit_prefix} | person: {name}"):
            saved.append(filepath)

    # ── 5. Action Items ──────────────────────────────────────────────────────
    if extracted.get("ACTION_ITEMS"):
        filepath = f"notes/actions/{date_str}-{_safe_filename(source)}-actions.md"
        frontmatter = _build_note_frontmatter("action-items", source, timestamp)
        items_md = "\n".join(f"- [ ] {item}" for item in extracted["ACTION_ITEMS"])
        content = f"""{frontmatter}

# Action Items — {date_str}

**Source:** `{source}`

{items_md}
"""
        if push_file(filepath, content, f"{commit_prefix} | actions"):
            saved.append(filepath)

    # ── 6. Session summary ───────────────────────────────────────────────────
    if extracted.get("SUMMARY"):
        filepath = f"notes/sessions/{date_str}-{_safe_filename(source)}-summary.md"
        frontmatter = _build_note_frontmatter("session-summary", source, timestamp)

        # Link to related notes
        related = []
        for f in saved:
            label = f.split("/")[-1].replace(".md", "")
            related.append(f"- [{label}](../{f})")
        related_md = "\n".join(related) if related else "_none_"

        content = f"""{frontmatter}

# Session Summary — {source} · {date_str}

{extracted["SUMMARY"]}

## Captured Notes
{related_md}

## Stats
- Insights: {len(extracted.get("KEY_INSIGHTS", []))}
- Decisions: {len(extracted.get("DECISIONS", []))}
- Open questions: {len(extracted.get("OPEN_QUESTIONS", []))}
- Action items: {len(extracted.get("ACTION_ITEMS", []))}
- People: {len(extracted.get("PEOPLE", []))}
"""
        if push_file(filepath, content, f"{commit_prefix} | summary"):
            saved.append(filepath)

    return saved


def open_github_issue(title: str, body: str, labels: list[str] = None) -> Optional[str]:
    """
    Open a GitHub Issue for an open question.
    Returns the issue URL or None on failure.
    """
    url = f"{GITHUB_API}/repos/{CORTEX_REPO}/issues"
    payload = {
        "title": title,
        "body": body,
        "labels": labels or ["open-question"],
    }
    resp = requests.post(url, headers=_headers(), json=payload)
    if resp.status_code == 201:
        return resp.json().get("html_url")
    return None


def _person_note(name: str, context: str, source: str, timestamp: str) -> str:
    frontmatter = _build_note_frontmatter("person", source, timestamp, {"name": name})
    return f"""{frontmatter}

# {name}

## {timestamp} · {source}
{context}
"""


# ── Repo initialisation ───────────────────────────────────────────────────────

def init_repo_structure() -> list[str]:
    """
    Create the base folder structure in the GitHub repo by pushing .gitkeep files.
    Run this once on first setup.
    """
    folders = [
        "notes/insights",
        "notes/decisions",
        "notes/questions",
        "notes/people",
        "notes/actions",
        "notes/sessions",
        "notes/projects",
    ]
    created = []
    for folder in folders:
        filepath = f"{folder}/.gitkeep"
        if push_file(filepath, "", f"init: create {folder}"):
            created.append(folder)
            print(f"  ✅ {folder}")
        else:
            print(f"  ⚠️  {folder} (already exists or error)")
    return created


if __name__ == "__main__":
    print("Initialising Cortex repo structure...")
    init_repo_structure()
    print("Done. Check your GitHub repo.")
