"""
Read/write the single JSON code block on the "Daily Reminders Data" Notion page.

This runs on a GitHub Actions runner (which has unrestricted internet), NOT
inside Claude Cowork. It talks to api.notion.com directly using an internal
integration token.

PRIVACY: this repo is public, so Actions logs are world-readable. Nothing in
here may ever print checklist text, reply text, or any page content. Counts
and status codes only.
"""

import json
import os
import sys

import requests

NOTION_VERSION = "2022-06-28"
API = "https://api.notion.com/v1"

# Notion rejects any single rich_text item longer than 2000 characters, so the
# JSON payload has to be split across several items in the same block.
RICH_TEXT_LIMIT = 1900


def _headers():
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        sys.exit("NOTION_TOKEN is not set")
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _find_code_block(page_id):
    """Return (block_id, text) for the first code block on the page."""
    url = f"{API}/blocks/{page_id}/children?page_size=100"
    while url:
        r = requests.get(url, headers=_headers(), timeout=30)
        r.raise_for_status()
        data = r.json()

        for block in data.get("results", []):
            if block.get("type") == "code":
                text = "".join(
                    rt.get("plain_text", "")
                    for rt in block["code"].get("rich_text", [])
                )
                return block["id"], text

        if data.get("has_more") and data.get("next_cursor"):
            url = f"{API}/blocks/{page_id}/children?page_size=100&start_cursor={data['next_cursor']}"
        else:
            url = None

    sys.exit("No code block found on the Notion page")


def load():
    """Return (block_id, parsed_dict)."""
    page_id = os.environ.get("NOTION_PAGE_ID")
    if not page_id:
        sys.exit("NOTION_PAGE_ID is not set")

    block_id, text = _find_code_block(page_id)
    try:
        return block_id, json.loads(text)
    except json.JSONDecodeError as exc:
        # Deliberately does not echo the malformed text - public logs.
        sys.exit(f"Notion code block is not valid JSON (at char {exc.pos})")


def save(block_id, payload):
    """Write payload back into the code block, chunked to Notion's limit."""
    # Round-trip guard: never write something we cannot read back.
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    json.loads(text)

    chunks = [
        text[i : i + RICH_TEXT_LIMIT] for i in range(0, len(text), RICH_TEXT_LIMIT)
    ]
    body = {
        "code": {
            "language": "json",
            "rich_text": [
                {"type": "text", "text": {"content": chunk}} for chunk in chunks
            ],
        }
    }

    r = requests.patch(
        f"{API}/blocks/{block_id}", headers=_headers(), json=body, timeout=30
    )
    if not r.ok:
        sys.exit(f"Notion write failed: HTTP {r.status_code}")
    print(f"notion: wrote {len(text)} chars across {len(chunks)} chunk(s)")
