# Telegram ↔ Notion bridge

The transport layer for the daily checklist system. Claude Cowork cannot reach
`api.telegram.org` (its sandbox runs a strict egress allowlist), but a GitHub
Actions runner can. So Actions acts as a dumb pipe in both directions, and
Notion stays the single source of truth.

```
07:30 IST  Actions  →  getUpdates            →  telegram_inbox  (Notion)
07:45 IST  Cowork   →  reads inbox, scores yesterday, builds today's list
                                              →  telegram_outbox (Notion)
08:00 IST  Actions  →  reads outbox          →  sendMessage
21:45 IST  Cowork   →  builds evening checklist → telegram_outbox
22:00 IST  Actions  →  reads outbox          →  sendMessage
```

Cowork never touches GitHub or Telegram. It only reads and writes the Notion
page through its existing connector. No credentials live in any Cowork prompt.

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

```bash
cd telegram-bridge
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

Repo → **Actions** → `telegram-bridge` → **Run workflow**.

The first run should log `0 update(s)` and `0 pending`. Then send your bot a
message, run it again, and confirm the text appears in `telegram_inbox` on the
Notion page and that `state/offset.json` advanced.

## How dedup works

Telegram assigns every update a monotonically increasing `update_id`.
`state/offset.json` stores the next unconsumed ID, and the workflow commits it
back after each run. The offset is only advanced *after* Notion has accepted the
write, so a failure mid-run causes a harmless re-fetch rather than lost
messages. A message can never be processed twice.

## Cost

Three ~30-second runs per day ≈ **60 minutes/month**. Public repos get unlimited
free Actions minutes; private repos get 2,000/month. Free either way.

## Gotchas

- **GitHub cron drifts.** Scheduled workflows can be delayed several minutes
  under load. The Cowork runs are staggered 15 minutes ahead of the sends to
  absorb this. Don't tighten that gap.
- **Scheduled workflows are disabled after 60 days of repo inactivity.** The
  daily offset commit keeps the repo active and avoids this.
- **Both scripts run on every trigger** and are individually idempotent, so an
  extra, missed, or delayed run is self-healing.
