"""
The daily runner. Two modes:

  morning   fetch-already-done -> score yesterday -> apply adds -> build today
            -> clear inbox -> queue the morning message
  evening   re-send today's snapshot. READ-ONLY with respect to task state.

Invariant: the morning run is the ONLY job that mutates tasks, points, streak
or the inbox. The evening run only reads and queues a message. This is what
keeps the 10PM fetch from double-applying /add directives.

Usage: python scripts/process_replies.py {morning|evening}

PRIVACY: public repo, public Actions logs. Never print message or task text.
"""

from __future__ import annotations

import datetime as dt
import sys
import time
import zoneinfo

import engine
import metrics
import notion_store

TZ = zoneinfo.ZoneInfo("Asia/Kolkata")


def today_str() -> str:
    return dt.datetime.now(TZ).date().isoformat()


def queue_message(state: dict, text: str) -> None:
    state.setdefault("telegram_outbox", []).append(
        {"id": f"m{int(time.time())}", "text": text, "sent": False}
    )


def run_morning(state: dict) -> None:
    today = today_str()
    snapshot_date = (state.get("today") or {}).get("date")

    inbox = state.get("telegram_inbox") or []
    directives = engine.parse_directives([m.get("text", "") for m in inbox])
    print(
        f"inbox: {len(inbox)} message(s), "
        f"{len(directives['unparsed'])} unparsed"
    )

    # ── score yesterday, guarded so a re-run cannot zero the day.
    #
    #    Two distinct guards are needed:
    #      1. the snapshot's day must be OVER (date < today). Without this, a
    #         second run the same morning would score the list this run just
    #         built, against an empty inbox, marking everything missed.
    #      2. that date must not already appear as last_processed_date.
    summary = None
    if not snapshot_date:
        print("score: skipped (no snapshot yet - first run)")
    elif snapshot_date >= today:
        print("score: skipped (snapshot is for today, day not over)")
    elif state.get("last_processed_date") == snapshot_date:
        print("score: skipped (already scored)")
    else:
        gap = (
            dt.date.fromisoformat(today) - dt.date.fromisoformat(snapshot_date)
        ).days
        if gap > 1:
            # A run was entirely missed (not just delayed): the days between
            # the snapshot and today have no history row at all. Scoring
            # still proceeds against the one snapshot we have - there is no
            # way to reconstruct what happened on the skipped day(s) - but
            # this must not pass silently.
            print(f"score: WARNING - {gap - 1} day(s) skipped with no run at all")
        summary = engine.score_day(state, directives)
        print(
            f"score: {summary['completed']}/{summary['total']} "
            f"streak={summary['streak']} broke={summary['broke']}"
        )

    # ── mutations only happen here, in the morning run
    changes = engine.apply_additions(state, directives, today)
    if any(changes.values()):
        print(
            f"tasks: +{len(changes['added_daily'])} daily, "
            f"+{len(changes['added_weekly'])} weekly, "
            f"-{len(changes['removed'])} removed"
        )

    engine.build_today(state, today)
    print(f"today: {len(state['today']['items'])} item(s) due")

    computed = metrics.compute(state, today)
    state["metrics"] = computed

    queue_message(state, engine.render_morning(state, summary, computed, directives))

    # ── clear ONLY if we actually scored. Otherwise a re-run would discard
    #    messages that are meant for tomorrow's scoring pass.
    if summary is not None:
        state["telegram_inbox"] = []
        print("inbox: cleared")
    else:
        print("inbox: retained (nothing was scored)")


def run_evening(state: dict) -> None:
    snapshot = state.get("today") or {}
    if not snapshot.get("items"):
        print("evening: no snapshot to re-send, skipping")
        return

    # Surface parse errors, but do not consume or mutate anything.
    inbox = state.get("telegram_inbox") or []
    directives = engine.parse_directives([m.get("text", "") for m in inbox])

    queue_message(state, engine.render_evening(state, directives))
    print(f"evening: queued checklist of {len(snapshot['items'])} item(s)")


def main() -> None:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    if mode not in ("morning", "evening"):
        sys.exit("usage: process_replies.py {morning|evening}")

    block_id, state = notion_store.load()

    if mode == "morning":
        run_morning(state)
    else:
        run_evening(state)

    notion_store.save(block_id, state)


if __name__ == "__main__":
    main()
