"""
Send any unsent messages queued in telegram_outbox on the Notion page.

Claude Cowork writes messages into telegram_outbox; this script delivers them
and flips sent=true. Marking happens after a successful send, so a crash mid-run
can at worst re-send, never silently drop.

PRIVACY: public repo, public Actions logs. Never print message text.
"""

import os
import sys
import time

import requests

import notion_store

TELEGRAM_TIMEOUT = 30
# Telegram hard-caps a single message at 4096 characters.
MAX_LEN = 4000


def send_message(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")

    for start in range(0, len(text), MAX_LEN):
        chunk = text[start : start + MAX_LEN]
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            },
            timeout=TELEGRAM_TIMEOUT,
        )
        if not r.ok:
            # Surface the API error code but not the message body.
            raise RuntimeError(f"sendMessage failed: HTTP {r.status_code}")
        if not r.json().get("ok"):
            raise RuntimeError("sendMessage returned ok=false")
        time.sleep(0.4)  # stay well under Telegram's rate limit


def main():
    block_id, data = notion_store.load()
    outbox = data.get("telegram_outbox") or []

    pending = [m for m in outbox if isinstance(m, dict) and not m.get("sent")]
    print(f"outbox: {len(pending)} pending of {len(outbox)} total")
    if not pending:
        return

    sent_count = 0
    for msg in pending:
        text = msg.get("text") or ""
        if not text.strip():
            msg["sent"] = True
            continue
        try:
            send_message(text)
        except RuntimeError as exc:
            print(f"outbox: stopping early - {exc}")
            break
        msg["sent"] = True
        msg["sent_at"] = int(time.time())
        sent_count += 1

    # Keep the last 20 sent messages for traceability, drop older ones so the
    # Notion block does not grow without bound.
    kept_sent = [m for m in outbox if m.get("sent")][-20:]
    still_pending = [m for m in outbox if not m.get("sent")]
    data["telegram_outbox"] = kept_sent + still_pending

    notion_store.save(block_id, data)
    print(f"outbox: sent {sent_count} message(s)")


if __name__ == "__main__":
    main()
