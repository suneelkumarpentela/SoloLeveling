# Handoff prompt for Claude Code

Open Claude Code with this folder as the working directory:

```
cd "C:\Users\sunee\Desktop\tasks\daily_reminders\telegram-bridge"
claude
```

Then paste everything below the line.

Have these four values ready. Do not paste them into a file — Claude Code will
prompt you, or you can enter them into `gh` interactively.

| Value | Where to get it |
|---|---|
| Telegram bot token | @BotFather → `/newbot` |
| Telegram chat ID | message the bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `chat.id` |
| Notion integration secret | <https://www.notion.so/my-integrations> → New integration → Read + Update content |
| Notion page ID | `3a3be74c7b1581efa8bee554770ead56` (already known) |

**Before running:** in Notion, open the **Daily Reminders Data** page → `···` →
**Connections** → add your new integration. The token alone will not grant
access, and this is the most commonly missed step.

---

I have a complete GitHub Actions project in this directory that bridges a
Telegram bot to a Notion page. Please get it live on GitHub.

Repo layout already in place:

```
.github/workflows/bridge.yml   3 daily UTC crons + workflow_dispatch
scripts/fetch_updates.py       getUpdates -> Notion telegram_inbox
scripts/send_outbox.py         Notion telegram_outbox -> sendMessage
scripts/notion_store.py        reads/writes the JSON code block on the page
state/offset.json              Telegram update offset (starts at 0)
README.md                      full design notes
```

Please do the following:

1. Verify `gh auth status`; if not authenticated, run `gh auth login` and walk
   me through it.
2. Initialise the repo on branch `main`, commit everything, and create a
   **public** GitHub repo named `telegram-daily-bridge`, then push.
3. Set these four repository secrets with `gh secret set`, prompting me for each
   value rather than putting them on the command line or in any file:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `NOTION_TOKEN`
   - `NOTION_PAGE_ID` (use `3a3be74c7b1581efa8bee554770ead56`)
4. Set the repo's Actions workflow permissions to **read and write** so the
   workflow can commit the advanced offset back. Use the API if `gh` supports
   it, otherwise tell me the exact settings page to click through.
5. Trigger a manual run with `gh workflow run bridge.yml`, wait for it, then
   show me the logs with `gh run view --log`.

Expected on a clean first run: `0 update(s) since offset 0` and
`outbox: 0 pending of 0 total`.

6. Then ask me to send a test message to the bot. Re-run the workflow and
   confirm the logs report one appended message and that `state/offset.json`
   advanced in a new commit.

Important constraints:

- **This repo is public.** Git history and Actions logs are world-readable.
  The scripts deliberately never print message text or Notion content. Do not
  add any logging that echoes user content, and never commit a token.
- Do not modify the streak or scoring logic — that lives in a separate Claude
  Cowork scheduled task, not in this repo.
- If a script fails, report the HTTP status rather than dumping the response
  body, for the same privacy reason.
