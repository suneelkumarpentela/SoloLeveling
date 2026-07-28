# Scoring spec (schema v2)

The authoritative description of how tasks, points and the streak work.
`engine.py` implements exactly this; if the two disagree, this file is wrong and
should be corrected rather than the code quietly diverging.

## Task pools

| Pool | Who edits it | Recurs? | Cadence? |
|---|---|---|---|
| `static_tasks` (`s*`) | **Notion only**, by hand | forever | yes, `every_days` |
| `dynamic_daily` (`d*`) | Telegram | one-off, carries until done | no — always due |
| `dynamic_weekly` (`w*`) | Telegram | one-off, due by Sunday | no |

Telegram can never create, edit or delete an `s*` task. `/add` mints `d*`,
`/week` mints `w*`, and `/drop` only accepts `d*` / `w*` ids.

## Static-task cadence

Each static task carries `every_days` (default 1) and `next_due`. It appears on
the checklist only when `next_due` has arrived; `next_due: null` means due now.

| Outcome | Effect |
|---|---|
| completed | `next_due = that day + every_days` |
| first miss | `next_due = that day + 1` — a last-chance day, flagged ⚠ |
| second (or any later) miss | streak breaks (if not already broken), `next_due = that day + 1` |

**Breaking the streak earns no cadence breathing room.** Once a task starts
being missed, it is asked about every single day — same grace-day rule
whether it's the first miss or the fifth — until it is actually completed.
Only a completion resumes the full `every_days` interval. This is deliberate:
falling behind on a task should make it more visible, not less.

Worked example, `every_days: 2`:

```
day1  due          done   → next due day3
day1  due          miss   → misses=1, back on day2 as a last chance
day2  last chance  miss   → misses=2, STREAK BREAKS, still due day3 (no
                            breathing room from the break)
day2  last chance  done   → next due day4  (measured from the day it was done)
```

`every_days: 1` reproduces plain daily behaviour exactly — miss, warned
last-chance day, second miss breaks. The daily rule is just the N=1 case.

**A task that is not due is absent from the checklist entirely**, so it is not
in the denominator and cannot affect completion or the streak. That is the whole
point of the cadence.

A missing, null, zero, negative or non-numeric `every_days` falls back to 1, so
a hand-edit in Notion cannot produce a task that silently never appears.

**Both `every_days` and `next_due` are capped at 90 days out** (`engine.
MAX_CADENCE_DAYS`). This is the failure mode that matters most, because it is
invisible: an `every_days` typo (`9999` instead of `2`) or a `next_due`
hand-edited to the wrong year would otherwise strand a task for years with no
error — it would just quietly stop being asked about. Past the cap, `is_due`
treats the value as a data error and forces the task due rather than honouring
it.

## Points

- Completing any daily task (`s*` or `d*`): **+1**
- Completing a weekly task (`w*`): **+2**

Points are cumulative and never reset.

## Miss counters

Every `s*` and `d*` task carries `consecutive_misses`:

- completed → `consecutive_misses = 0`, `last_completed = <date>`
- missed → `consecutive_misses += 1`
- at `== 1` → next morning's message flags it **⚠ last day to keep the streak**
- at `>= 2` → **breaks the streak**, then the counter resets to 0 so one
  abandoned task cannot pin the streak at zero forever

## Streak

Evaluated once per day, during the morning run, against the previous day's
snapshot. In precedence order:

1. **Break** — any daily task reached 2 consecutive misses, **or** it is Sunday
   and a weekly task is still open → `streak = 0`
2. **Grow** — everything due was completed → `streak += 1`
3. **Hold** — otherwise unchanged

So a non-breaking but incomplete day neither grows nor resets the streak.
Percentage-based scoring (the old 100% / >50% / ≤50% rules) is retired.

## Lifecycle

- A completed `d*` task is **removed** from `dynamic_daily` — it was a one-off.
- An incomplete `d*` task **carries** to the next day.
- On Sunday, after evaluation, `dynamic_weekly` is emptied for the new week.

## Daily cycle

```
~08:00 IST   fetch replies → score yesterday → build today → render → send
~22:00 IST   fetch replies → apply adds/removes only → re-send today's checklist
```

(The cron triggers actually fire at 05:00/19:00 IST to absorb GitHub's
scheduling delay — see README.md "GitHub cron drifts." These times are the
intended delivery target, not the trigger time.)

The 22:00 run never scores. It re-sends the same snapshot built at 08:00 and
invites additions — unless that snapshot isn't actually for today yet (the
morning run hasn't happened), in which case it skips rather than re-sending
a stale, already-scored day under today's date. All scoring happens the
following morning, so replies sent
any time between 22:00 and 08:00 are picked up together.

## Reply grammar

Four slash commands, case-insensitive, any order. Several commands may appear
in one message (one per line), and a payload may span lines.

| Command | Meaning |
|---|---|
| `/done s1 d2 w1` | mark complete — routed by id prefix |
| `/done all` | mark every daily item complete |
| `/done none` | reset — nothing marked |
| `/undo s1` | un-mark something |
| `/add call dentist` | new `d*` task |
| `/week file taxes` | new `w*` task |
| `/drop d3 w1` | delete a dynamic task (`d*`/`w*` only) |

Register these with BotFather so they appear as a tappable autocomplete menu:

```
done - mark items complete
undo - un-mark an item
add  - add a daily task
week - add a task for this week
drop - remove a dynamic task
```

### Completions accumulate

Marks build up across the day, so ticking things off as you do them works:

```
12:00  /done s1        →  {s1}
23:00  /done s2 s3     →  {s1, s2, s3}
```

Per id, the **last mention wins** — a later `/done` re-marks something `/undo`
removed, and vice versa. `/done all` followed by `/undo s3` means everything
except s3. `/done none` resets the whole day.

This replaced a last-statement-wins rule, which silently discarded the noon
mark in the example above unless it happened to be repeated at 23:00.

### Separators

**Commas separate ids. Newlines and semicolons separate task text. Commas do
not.** Ids cannot contain commas so splitting them is safe; task descriptions
frequently do, and splitting those turns "call mom, then dentist" into two
junk tasks.

```
/done s1, s3, d1              three ids
/add call mom, then dentist   ONE task
/add call mom; buy milk       two tasks
```

### Categories

A trailing `#tag` sets the category: `/add morning walk #health`.

### Pasted checklists

A message containing `- [x] ... (id)` lines is read as a ticked checklist.
Ticked ids are complete, unticked are not. **A paste is treated as a complete
statement**, so paste the whole checklist rather than a fragment — copying the
evening message gives you the full list, which is the intended flow.

### Precedence and error handling

- `/done` — the last statement wins. A weekly-only `/done w1` never clobbers an
  earlier `/done all`.
- `/add`, `/week`, `/drop` — accumulate across messages.
- `/start`, `/help`, `/list`, `/stats`, `/settings` — ignored as Telegram
  boilerplate.
- Anything else, including a malformed `/done the knee thing`, is collected in
  `unparsed` and **reported back in the next message**. It is never silently
  read as "nothing was done" — that would zero a day's score on a typo.
