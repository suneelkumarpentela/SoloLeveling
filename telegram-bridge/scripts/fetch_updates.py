"""
Pull new Telegram replies and append them to telegram_inbox in Notion.

Dedup is handled by Telegram's update_id offset, persisted in state/offset.json
and committed back to the repo by the workflow. Because the offset only ever
moves forward and is committed after a successful Notion write, a message can
never be processed twice.

PRIVACY: public repo, public Actions logs. Never print message text.
"""

import json
import os
import pathlib
import sys

import requests

import notion_store

OFFSET_FILE = pathlib.Path(__file__).resolve().parent.parent / "state" / "offset.json"
TELEGRAM_TIMEOUT = 30


def telegram(method, **params):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set")
    r = requests.get(
        f"https://api.telegram.org/bot{token}/{method}",
        params=params,
        timeout=TELEGRAM_TIMEOUT,
    )
    if not r.ok:
        sys.exit(f"Telegram {method} failed: HTTP {r.status_code}")
    payload = r.json()
    if not payload.get("ok"):
        sys.exit(f"Telegram {method} returned ok=false")
    return payload["result"]


def read_offset():
    if not OFFSET_FILE.exists():
        return 0
    return json.loads(OFFSET_FILE.read_text()).get("offset", 0)


def write_offset(value):
    OFFSET_FILE.write_text(json.dumps({"offset": value}, indent=2) + "\n")


def main():
    allowed_chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not allowed_chat:
        sys.exit("TELEGRAM_CHAT_ID is not set")

    offset = read_offset()
    updates = telegram("getUpdates", offset=offset, timeout=0, allowed_updates='["message"]')
    print(f"telegram: {len(updates)} update(s) since offset {offset}")

    if not updates:
        return

    highest = offset
    new_messages = []
    skipped = 0

    for upd in updates:
        highest = max(highest, upd["update_id"] + 1)
        msg = upd.get("message")
        if not msg:
            continue
        # Requirement: process only my chat, ignore everyone else.
        if str(msg.get("chat", {}).get("id")) != str(allowed_chat):
            skipped += 1
            continue
        text = msg.get("text")
        if not text:
            continue
        new_messages.append(
            {
                "message_id": msg["message_id"],
                "date": msg["date"],
                "text": text,
            }
        )

    if skipped:
        print(f"telegram: ignored {skipped} message(s) from other chats")

    if new_messages:
        block_id, data = notion_store.load()
        inbox = data.get("telegram_inbox") or []
        known = {m.get("message_id") for m in inbox if isinstance(m, dict)}
        added = [m for m in new_messages if m["message_id"] not in known]
        data["telegram_inbox"] = inbox + added
        notion_store.save(block_id, data)
        print(f"notion: appended {len(added)} message(s) to telegram_inbox")
    else:
        print("notion: nothing to append")

    # Only advance the offset once Notion has the messages safely stored.
    if highest > offset:
        write_offset(highest)
        print(f"offset: {offset} -> {highest}")


if __name__ == "__main__":
    main()
