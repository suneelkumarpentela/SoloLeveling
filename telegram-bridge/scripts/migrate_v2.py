"""
One-time schema migration: v1 (weekly_tasks, percentage scoring) -> v2
(dynamic_weekly/dynamic_daily, consecutive-miss scoring). See SPEC.md for the
v2 rules this unlocks.

Idempotent: running this against an already-migrated page is a no-op.

Safety: ALWAYS prints the full before/after JSON and refuses to write unless
invoked with --confirm. Do not run it against the live page without reading
that diff. This is a LOCAL, interactive, one-time tool - it prints full
checklist/task text, so it must never run inside GitHub Actions, where logs
are public.

Usage:
    python scripts/migrate_v2.py            # dry run: prints before/after only
    python scripts/migrate_v2.py --confirm   # writes the migrated state to Notion
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import sys

import engine
import notion_store

TODAY = dt.date.today().isoformat()

# Static-task cadence, seeded at migration. Anything not listed is daily.
# Edit `every_days` directly in Notion afterwards - this map only sets the
# starting value.
CADENCE = {
    "s5": 2,  # Soak dry fruits - every other day
}


def already_migrated(state: dict) -> bool:
    return "weekly_tasks" not in state and "dynamic_weekly" in state


def migrate(old: dict) -> dict:
    """Pure function: old-schema dict -> new-schema dict. No I/O."""
    new = copy.deepcopy(old)

    # weekly_tasks -> dynamic_weekly, renumbered sequentially. The live page
    # has a mix of a hand-written "w1" and epoch-timestamp-based ids from an
    # earlier ad hoc process; renumbering gives the new monotonic id_seq
    # (below) a small, sane starting point instead of inheriting those.
    old_weekly = new.pop("weekly_tasks", [])
    dynamic_weekly = []
    for i, task in enumerate(old_weekly, start=1):
        dynamic_weekly.append(
            {
                "id": f"w{i}",
                "text": task["text"],
                "category": task.get("category", "uncategorised"),
                "added_on": task.get("added_on", TODAY),
                "done": task.get("done", False),
            }
        )
    new["dynamic_weekly"] = dynamic_weekly
    new["dynamic_daily"] = []

    for task in new.get("static_tasks", []):
        task.setdefault("consecutive_misses", 0)
        task.setdefault("category", "uncategorised")
        task.setdefault("last_completed", None)
        # Cadence: every_days drives when the task reappears, next_due is the
        # date it next becomes due. None means "due now", which is correct for
        # every task at migration time.
        task.setdefault("every_days", CADENCE.get(task["id"], 1))
        task.setdefault("next_due", None)

    new.pop("last_day_qualified", None)
    new.pop("pending_additions", None)  # dead field: unused by the v2 engine

    new.setdefault("weekly_log", [])
    new.setdefault("metrics", {})

    # Ids are never reused post-migration (see engine._next_id) - seed the
    # counter past anything already issued so a future /add or /week can
    # never collide with history.
    new["id_seq"] = {"d": 0, "w": len(dynamic_weekly)}

    # The live "today" snapshot predates the new item shape (old items key
    # completions by "id"; the v2 engine uses "ref" plus kind/category/warn).
    # Rebuild it with the real build_today so the shape is guaranteed
    # correct, then carry over any completions already marked true.
    old_today = old.get("today") or {}
    old_done_refs = {i["id"] for i in (old_today.get("items") or []) if i.get("done")}
    if old_today.get("date"):
        engine.build_today(new, old_today["date"])
        for item in new["today"]["items"]:
            if item["ref"] in old_done_refs:
                item["done"] = True

    # These are test artifacts from bridge setup/testing, not real user data.
    new["telegram_inbox"] = []
    new["telegram_outbox"] = []

    return new


def main() -> None:
    if os.environ.get("GITHUB_ACTIONS"):
        sys.exit(
            "migrate_v2.py prints full task/checklist content - refusing to "
            "run in CI, where logs are public. Run it locally."
        )

    confirm = "--confirm" in sys.argv

    block_id, old_state = notion_store.load()

    if already_migrated(old_state):
        print("migrate_v2: already on schema v2, nothing to do")
        return

    new_state = migrate(old_state)

    print("\n" + "=" * 20 + " BEFORE " + "=" * 20)
    print(json.dumps(old_state, indent=2, ensure_ascii=False))
    print("\n" + "=" * 20 + " AFTER " + "=" * 20)
    print(json.dumps(new_state, indent=2, ensure_ascii=False))

    if not confirm:
        print("\nDry run only - re-run with --confirm to write this to Notion.")
        return

    notion_store.save(block_id, new_state)
    print("\nmigrate_v2: written to Notion")


if __name__ == "__main__":
    main()
