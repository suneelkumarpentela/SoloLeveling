# Claude Code handoff — static-task cadence + local mirror

Open Claude Code at the repo root:

```
cd "C:\Users\sunee\Desktop\tasks\daily_reminders"
claude
```

Paste everything below the line.

---

Two changes landed in this repo since your last pass, plus one new external
component. Review all of it with fresh eyes, fix what's wrong, then push.

**Do not take this description on trust** — it was written alongside the code,
so it may be wrong exactly where it matters. Read the source and verify.

## Change 1 — static tasks now have a cadence (`every_days`)

Previously every daily task was due every day. Static tasks can now recur on
their own interval. `SPEC.md` has been rewritten with the rules; read it first.

The governing example, `every_days: 2`:

```
day1  due          done   → next due day3
day1  due          miss   → misses=1, back on day2 as a last chance (⚠ flagged)
day2  last chance  miss   → misses=2, STREAK BREAKS, next due day4
day2  last chance  done   → next due day4  (measured from the day it was done)
```

`every_days: 1` must reproduce the old daily behaviour exactly — the daily rule
is just the N=1 case. There are tests asserting this in `tests/test_frequency.py`.

Touched: `engine.py` (`every_days`, `is_due`, `shift`, `build_today`,
`score_day`, `_lines`), `migrate_v2.py`, `SPEC.md`, and a new
`tests/test_frequency.py`. 88 tests pass — verify that yourself first.

### Review these specifically

1. **`next_due` is the only scheduling state.** `last_completed` is now
   informational. Confirm nothing still derives due-ness from it, and that the
   two cannot disagree in a way that matters.

2. **The break path sets `next_due` inside the `breakers` loop**, guarded by
   `if "every_days" in task or task.get("id", "").startswith("s")`. That guard is
   ugly and I am not confident it is right — `breakers` contains dynamic tasks
   too, which must never get a `next_due`. Check it, and prefer a cleaner
   discriminator (the item's `kind`, or an explicit pool lookup).

3. **Can a task get stranded?** Look for any sequence — miss, drop, re-add,
   migration, hand-edit in Notion, clock skew — that leaves `next_due` set far in
   the future so the task silently never appears again. This is the worst failure
   mode of the feature because it is invisible: no error, the task just quietly
   stops being asked about. Consider whether a sanity clamp is warranted.

4. **Interaction with the missed-run gap warning.** If several days are skipped
   entirely, `next_due` dates fall in the past. Confirm those tasks all reappear
   on the next successful run and are not double-counted.

5. **Metrics.** `per_task` reliability divides by "times due", derived from
   history. With cadence, a task is due less often. Confirm the denominators are
   still right and that an off-cycle day is not counted as a miss anywhere.

## Change 2 — a Cowork task now writes a local mirror

A scheduled task outside this repo (Claude Cowork, daily at 13:00 IST) reads the
Notion page and writes a read-only snapshot to `daily_tasks.json` at the repo
root. It is one-way; Notion stays the source of truth.

**This file must never reach the public repo.** It is already in `.gitignore` and
has never been committed — I verified. Your job is to make sure it stays that
way:

- Confirm `.gitignore` still covers it and that `git log --all -- daily_tasks.json`
  is empty.
- Add a CI guard: a step (or a test) that fails the build if `daily_tasks.json`,
  or any file containing task text, is ever staged or tracked. A `.gitignore`
  entry is one careless `git add -f` away from leaking personal data into a
  public repo permanently.
- Document the mirror in `README.md`: what writes it, that it is one-way, that
  local edits are overwritten, and that it is deliberately untracked.

The Cowork task is explicitly forbidden from writing to Notion, because the
Actions jobs are the sole writer and a second writer would race them. Nothing in
this repo needs to change for that, but be aware of the constraint if you touch
the Notion access pattern.

## Change 3 — `migrate_v2.py` now seeds cadence

It writes `every_days` and `next_due: null` on every static task, with a
`CADENCE` map at the top holding overrides. Currently `{"s5": 2}` — soak dry
fruits, every other day.

Check: the migration calls `engine.build_today()` internally, which now filters
by `is_due`. Confirm the cadence defaults are set *before* that call, or the
rebuilt snapshot will be missing tasks. I believe the ordering is correct but it
is exactly the kind of thing that breaks silently.

The migration has still **not been run against the live page** — it is dry-run
until invoked with `--confirm`.

## What to do

1. `cd telegram-bridge && python -m pytest tests/ -q`. Confirm 88 pass. Stop and
   report if not.
2. Review the above. Write a failing regression test for every real bug, then fix
   it.
3. Add the CI guard against committing the mirror.
4. Update `README.md` for both the cadence and the mirror.
5. Report every bug found and how you fixed it, then commit and push.
6. **Before pushing**, confirm the diff contains no token, chat id, or task text.
   The repo is public and Actions logs are world-readable.

Do not run `migrate_v2.py --confirm` yourself. Show me the dry-run diff and let
me decide.
