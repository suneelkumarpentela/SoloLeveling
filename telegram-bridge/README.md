# Telegram ↔ Notion daily checklist

A self-contained daily-checklist system. GitHub Actions is the only component
that can reach `api.telegram.org`, so it also does the scoring — there is no
separate Cowork step. Notion holds all state as a single JSON code block on
one page; the engine (`scripts/engine.py`, `scripts/metrics.py`,
`scripts/process_replies.py`) is pure Python with no I/O of its own, unit
tested in `tests/`. See `SPEC.md` for the scoring rules, including static-task
cadence (`every_days`) — not every task has to be due every day.

```
05:00 IST  Actions (morning)  →  getUpdates → telegram_inbox (Notion)
(targets       →  score yesterday, build today, queue message
 ~08:00)       →  sendMessage
19:00 IST  Actions (evening)  →  getUpdates → telegram_inbox (Notion)
(targets       →  re-send today's checklist (read-only)
 ~22:00)       →  sendMessage
```

The cron triggers fire at 05:00/19:00 IST, three hours ahead of the actual
08:00/22:00 delivery target — see "GitHub cron drifts" in Gotchas for why.

The morning and evening jobs live in the same workflow but are gated so only
one runs per trigger — see the comment at the top of `bridge.yml`. Only the
morning job mutates task state, points or the streak; the evening job only
reads and queues a message, and skips entirely if the morning run hasn't
built today's snapshot yet (see Gotchas).

## Privacy note — this repo is public

Public repos have world-readable git history **and world-readable Actions logs.**
So no checklist text, reply text, or Notion content is ever written to the repo
or printed to the logs. The repo holds only code plus `state/offset.json`, which
contains a single integer. The scripts log counts and HTTP status codes only.

Keep it that way if you edit them.

## Setup

### 1. Create the Telegram bot

In Telegram, message **@BotFather** → `/newbot` → follow the prompts.
Copy the token it gives you (looks like `123456789:AAE...`).

### 2. Find your chat ID

Send any message to your new bot, then open this in a browser:

```
https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
```

Find `"chat":{"id":123456789` — that number is your chat ID. Only this chat is
processed; messages from anyone else are ignored.

### 3. Create a Notion internal integration

This is separate from the Claude connector — the runner needs its own auth.

1. Go to <https://www.notion.so/my-integrations> → **New integration**
2. Name it (e.g. `telegram-bridge`), give it **Read** and **Update** content
   capabilities
3. Copy the **Internal Integration Secret**
4. Open the **Daily Reminders Data** page in Notion → `···` menu →
   **Connections** → add your new integration

Skipping step 4 is the most common failure — the token alone is not enough,
the page has to be explicitly shared with the integration.

### 4. Create the repo and push

The repo root is `daily_reminders/`, one level up from this folder — Actions
only discovers workflows in a repo-root `.github/workflows/`, so
`.github/workflows/bridge.yml` lives there rather than inside `telegram-bridge/`.
The workflow sets `working-directory: telegram-bridge` so its script and
state paths still resolve.

```bash
cd ..   # daily_reminders/
git init -b main
git add .
git commit -m "feat: telegram-notion bridge"
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

### 5. Add the secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from step 1 |
| `TELEGRAM_CHAT_ID` | from step 2 |
| `NOTION_TOKEN` | from step 3 |
| `NOTION_PAGE_ID` | `3a3be74c7b1581efa8bee554770ead56` |

### 6. Allow the workflow to commit

Repo → **Settings → Actions → General → Workflow permissions** →
select **Read and write permissions**. The workflow needs this to push the
advanced offset back.

### 7. Test it

Repo → **Actions** → `telegram-bridge` → **Run workflow**, choosing `morning`
or `evening`. The morning run should send a checklist message to your bot and
advance `state/offset.json`. Reply, then run `evening` to confirm the reply
lands in `telegram_inbox`.

## Local mirror

A separate Claude Cowork task, scheduled daily at 13:00 IST, reads the Notion
page and writes a read-only snapshot to `daily_tasks.json` at the
`daily_reminders/` repo root. It is **one-way**: Notion is always the source
of truth, nothing ever reads this file back, and the Cowork task is
explicitly forbidden from writing to Notion (the Actions jobs are the sole
writer there — a second writer would race them).

This file holds real personal task and health text, so it is **deliberately
untracked** — listed in `.gitignore`, enforced by `tests/test_repo_hygiene.py`
and the `ci` workflow (see Gotchas below). Any local edits to it are
overwritten the next time the Cowork task runs; don't rely on it for
anything but reading the latest snapshot.

## How dedup works

Telegram assigns every update a monotonically increasing `update_id`.
`state/offset.json` stores the next unconsumed ID, and the workflow commits it
back after each run. The offset is only advanced *after* Notion has accepted the
write, so a failure mid-run causes a harmless re-fetch rather than lost
messages. A message can never be processed twice.

## Cost

Two ~30-second runs per day ≈ **30 minutes/month**. Public repos get unlimited
free Actions minutes; private repos get 2,000/month. Free either way.

## Gotchas

- **GitHub cron drifts - a lot, not a little.** `schedule:` has no delivery
  guarantee, and in practice both jobs here landed 1-3.5 hours late
  (morning worse than evening), most likely because `:00`/`:15`/`:30`/`:45`
  are the most congested minutes on GitHub's shared scheduler and this
  repo's original slots (`:30`) sat right on one. The crons are now set
  three hours ahead of the real 08:00/22:00 IST target (see the comment at
  the top of `bridge.yml`) so a typical delay lands close to on time instead
  of hours late, and an undelayed run just arrives a bit early - the
  preferred failure direction here. If the delay pattern changes, re-measure
  with `gh run list --workflow=bridge.yml --json event,conclusion,createdAt`
  filtered to `event == "schedule"` before adjusting further.
  The morning guard itself compares real wall-clock dates (not the nominal
  schedule time), so a delayed run still scores correctly as long as it
  lands on the right calendar day. A run delayed past the *next* trigger's
  time, or a manual re-run performed between midnight and the next scheduled
  run, can still cause the day-over guard to fire earlier than a user's
  actual bedtime — avoid manually triggering the morning job overnight.
- **A fully skipped day is not backfilled.** If a run never happens at all for
  a whole day, that day gets no `history` row and is not scored — the next
  successful run logs a `WARNING` naming the gap, but there is no way to
  reconstruct what actually happened that day.
- **Scheduled workflows are disabled after 60 days of repo inactivity.** The
  daily offset commit keeps the repo active and avoids this.
- **The morning and evening jobs are gated by `if:`** so only one runs per
  trigger — see the comment at the top of `bridge.yml`. This matters because
  Notion's read-modify-write is not atomic; two jobs racing on the same page
  would silently lose one job's write.
- **The evening run refuses a stale snapshot.** If `today.date` in Notion
  isn't actually today (the morning run hasn't happened yet — badly delayed,
  or skipped), evening skips instead of re-sending yesterday's already-scored
  checklist relabelled as today's.
- **Dynamic task ids (`d*`, `w*`) are never reused**, even after the task is
  completed or dropped (`state["id_seq"]` tracks the next id). Reusing an id
  would splice two unrelated tasks' historical metrics together.
- **Static-task cadence (`every_days`) and `next_due` are capped at 90 days.**
  A typo in either field would otherwise strand a task invisibly for years —
  see SPEC.md.
- **`ci.yml` runs the full test suite on every push/PR**, including a
  hygiene check that fails the build if `daily_tasks.json` (the local Notion
  mirror) is ever staged, tracked, or appears in history. That workflow
  checks out full history (`fetch-depth: 0`) specifically so that check is
  meaningful — the `bridge.yml` workflow stays shallow since it doesn't need
  history.
