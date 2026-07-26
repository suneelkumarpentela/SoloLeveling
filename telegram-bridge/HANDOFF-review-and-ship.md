# Claude Code handoff — review, test, ship, schedule

Open Claude Code at the repo root (one level **above** this folder):

```
cd "C:\Users\sunee\Desktop\tasks\daily_reminders"
claude
```

Then paste everything below the line.

---

I have a Telegram + Notion daily-checklist system in `telegram-bridge/`. The
scoring logic was just refactored substantially and I need you to review it with
fresh eyes, fix what's wrong, then ship it and schedule it.

**Do not take my description below on trust.** It was written by the person who
wrote the code, so it may be wrong in exactly the places that matter. Read the
source and verify independently.

## What the system does

- **08:00 IST** — pull Telegram replies from overnight, score yesterday's
  checklist, build today's, queue a message, clear the inbox.
- **22:00 IST** — pull replies, re-send today's checklist. Read-only.
- Notion holds all state as a single JSON code block on one page. GitHub Actions
  is the only component that can reach `api.telegram.org`.

Read `telegram-bridge/SPEC.md` first — it is the authoritative rule reference —
then `README.md` for the architecture.

## Files

```
telegram-bridge/
  SPEC.md                     scoring + grammar rules (authoritative)
  README.md                   architecture and setup
  scripts/engine.py           parsing, scoring, rendering (pure, no I/O)
  scripts/metrics.py          8 metrics (pure, no I/O)
  scripts/process_replies.py  the runner: morning | evening
  scripts/notion_store.py     Notion read/write, chunked to 2000-char limit
  scripts/fetch_updates.py    getUpdates -> Notion inbox, offset in repo
  scripts/send_outbox.py      Notion outbox -> sendMessage
  state/offset.json           Telegram update offset
  tests/                      61 tests, all passing via `pytest tests/ -q`
```

## Step 1 — Verify the suite

Run `cd telegram-bridge && python -m pytest tests/ -q`. Confirm 61 pass. If any
fail, stop and report before changing anything.

## Step 2 — Fresh-eyes review

Review `engine.py`, `metrics.py` and `process_replies.py` for correctness bugs,
not style. The system mutates cumulative state (points, streak, per-task miss
counters) once a day with no human checking the arithmetic, so a silent error
compounds for weeks before it's noticed. Prioritise anything that could corrupt
state or lose user input.

**Known-suspicious areas — check these specifically, and look for others:**

1. **`/drop` still penalises you.** If a dynamic task is dropped tonight, next
   morning `score_day` runs against yesterday's snapshot which still contains it,
   so it is recorded as missed and can contribute to a streak break. Decide
   whether an explicitly dropped task should be excluded from scoring, and if so
   fix it.

2. **`/done` on an id not in today's snapshot** is silently ignored. Should it be
   reported as unparsed instead?

3. **Date-boundary risk.** `process_replies.today_str()` reads the clock in
   `Asia/Kolkata`; GitHub cron is UTC. Morning fires 02:30 UTC, evening 16:30
   UTC. Work out what happens if a run is delayed by hours (GitHub cron drifts
   under load) and crosses an IST midnight. Does the `snapshot_date >= today`
   guard still hold?

4. **Unbounded growth.** `history` gains a row per day and `metrics` is rewritten
   each run. Notion caps a block's `rich_text` array at 100 elements and
   `notion_store.py` chunks at 1900 chars, so the ceiling is ~190k chars. Confirm
   the real headroom and say whether archival is needed and when.

5. **Re-run and missed-run safety.** There are two guards in `run_morning`:
   the snapshot's day must be over, and its date must not equal
   `last_processed_date`. Try to break them. Specifically: what happens if a day
   is skipped entirely (no run at all), and what happens on a manual re-run
   hours after the scheduled one?

6. **Concurrency.** The workflow has a `concurrency` group, but Notion
   read-modify-write is not atomic. Is there any path where two runs interleave?

7. **First-run and empty-state paths.** No snapshot, empty task list, empty
   inbox, all-static-tasks-deleted. Should not crash or mis-score.

Write a regression test for every real bug you find, then fix it. Tests must fail
before the fix and pass after.

## Step 3 — Fix the wiring (these are known-broken)

**a. The workflow does not call the runner.** `bridge.yml` still runs only
`fetch_updates.py` and `send_outbox.py` on three crons. It never invokes
`process_replies.py`, so nothing is ever scored. Rewrite it as two jobs:

```
morning   02:30 UTC (08:00 IST)   fetch_updates -> process_replies morning -> send_outbox
evening   16:30 UTC (22:00 IST)   fetch_updates -> process_replies evening -> send_outbox
```

Keep `workflow_dispatch`, the `concurrency` group, `contents: write`, and the
step that commits `state/offset.json` only when it changed.

**b. Workflow location and paths.** The repo root is `daily_reminders/`, but the
workflow currently sits at `telegram-bridge/.github/workflows/bridge.yml`.
Actions only discovers workflows in a **repo-root** `.github/workflows/`. Move it
and set `working-directory: telegram-bridge` so script and state paths resolve.
Verify `state/offset.json` is still found and committable.

**c. Schema migration — REQUIRED, the system cannot run without it.** The Notion
page is still on the old schema and the new code expects a different shape. It
must be migrated in place, preserving all history:

| Old | New |
|---|---|
| `weekly_tasks` | `dynamic_weekly` |
| — | `dynamic_daily: []` |
| — | `consecutive_misses: 0` on every static task |
| — | `category` on every static task |
| `last_day_qualified` | delete (percentage rules are retired) |
| — | `weekly_log: []` |
| — | `metrics: {}` |

Preserve exactly: `history` (4 rows), `streak: 3`, `best_streak: 3`,
`total_points: 19`, `static_tasks` texts and `type` values.

Write this as `scripts/migrate_v2.py` — idempotent, safe to run twice, and it
must **print the before/after JSON and require an explicit confirmation flag**
before writing. Do not run it against the live page without showing me the diff
first.

Note: the 4 existing history rows have no `done`/`missed` id lists, so per-task
metrics will be blank for those days. That is expected — confirm the code handles
it without crashing rather than back-filling fake data.

**d. Stale inbox.** The live page's `telegram_inbox` holds three test messages
(`/start`, `Hi Sun Jinwoo`, `Solo Leveling start`). The first real run would
report two of them as unparsed. Clear the inbox as part of the migration.

## Step 4 — Ship

Only after tests pass and the review is done:

1. Show me a summary of every bug found and how you fixed it.
2. `git add`, commit with a message describing the refactor, push to `main`.
3. **This repo is public.** Before pushing, confirm no token, chat id, or task
   text is in the diff or in any log statement. The scripts deliberately log
   counts and HTTP status codes only — keep it that way.

## Step 5 — Schedule and smoke-test

1. Confirm the four secrets exist: `gh secret list`. Expect
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `NOTION_TOKEN`, `NOTION_PAGE_ID`.
2. Confirm workflow permissions are read/write (needed for the offset commit).
3. Run the migration, showing me the diff first and waiting for my confirmation.
4. `gh workflow run` the morning job manually. Then `gh run view --log`.
   Expected: a message arrives in Telegram containing today's checklist, the
   metrics line, and the five-line command reference.
5. Ask me to reply `/done s1` in Telegram. Trigger the evening job. Confirm the
   reply lands in `telegram_inbox` and the offset advanced in a new commit.
6. Trigger the morning job again and confirm it does **not** re-score (the guard
   should log `snapshot is for today, day not over`).

Report at the end: tests passing, bugs found and fixed, what is scheduled, and
anything you think is still fragile.
