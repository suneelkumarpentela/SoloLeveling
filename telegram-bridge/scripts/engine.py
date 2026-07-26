"""
Deterministic scoring engine. Pure functions over a state dict - no network,
no I/O, no clock reads except what callers pass in. See SPEC.md.

Everything here is unit-tested in tests/test_engine.py. Keep it that way:
this is the part of the system that must never quietly drift.
"""

from __future__ import annotations

import datetime as dt
import re

DAILY_POOLS = ("static_tasks", "dynamic_daily")
WARN_TEXT = "⚠ last day to keep the streak"

# ─────────────────────────────── reply parsing ───────────────────────────────

_ID_RE = re.compile(r"^[sdw]\d+$", re.IGNORECASE)

# "/add buy milk" or "/done@mybot s1" - the @suffix appears in group chats.
_CMD_RE = re.compile(r"^/(\w+)(?:@\w+)?\s*(.*)$", re.IGNORECASE)

# "- [x] Brush (s1)" - a pasted, ticked checklist.
_CHECKBOX_RE = re.compile(
    r"^\s*[-*]?\s*\[([ xX])\]\s*.*?\(([sdw]\d+)\)\s*$", re.IGNORECASE
)

# Telegram/BotFather boilerplate. Benign, not a syntax error worth reporting.
_BENIGN_CMDS = {"start", "help", "list", "stats", "settings"}

KNOWN_CMDS = {"done", "undo", "add", "week", "drop"}

# IDs are comma-safe; free text is not. So commas split ids, but only newlines
# and semicolons split task descriptions - otherwise "call mom, then dentist"
# silently becomes two junk tasks.
_ID_SPLIT = re.compile(r"[,;\s]+")
_TEXT_SPLIT = re.compile(r"[;\n]+")


def _blocks(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Split a message into (command, payload) pairs, plus any stray lines.

    Handles both several commands in one message and a multi-line payload
    belonging to a single command. A line that appears before any command is
    recognised (so there is nothing for it to be a continuation of) is a
    stray line - it must be reported, not silently discarded, per the same
    principle as any other unparsed input.
    """
    out: list[list[str]] = []
    stray: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        m = _CMD_RE.match(stripped)
        if m:
            out.append([m.group(1).lower(), m.group(2).strip()])
        elif out:
            out[-1][1] += "\n" + stripped
        elif stripped:
            stray.append(stripped)
    return [(c, p.strip()) for c, p in out], stray


def _route_ids(raw: str) -> tuple[list[str], list[str]]:
    """Split an id list into (daily, weekly) by prefix."""
    ids = [t.lower() for t in _ID_SPLIT.split(raw) if t.strip()]
    valid = [i for i in ids if _ID_RE.match(i)]
    return (
        [i for i in valid if i[0] in ("s", "d")],
        [i for i in valid if i[0] == "w"],
    )


def _mark(result: dict, key: str, ids: list[str]) -> None:
    """Add ids to an accumulating set, cancelling any earlier /undo of them."""
    for task_id in ids:
        if task_id not in result[key]:
            result[key].append(task_id)
        if task_id in result["undone"]:
            result["undone"].remove(task_id)


def parse_directives(texts: list[str]) -> dict:
    """Fold raw Telegram message bodies into one directive set.

    Completions ACCUMULATE across the day - ticking s1 at noon and s2 at 23:00
    yields both. Per id, the last mention wins: a later /done re-marks something
    /undo removed, and vice versa. "/done none" resets everything marked.

    add / week_add / remove also accumulate. Anything unrecognised lands in
    `unparsed` and is reported back, never read as "nothing was done".
    """
    result = {
        "done": [],
        "week_done": [],
        "undone": [],
        "add": [],
        "week_add": [],
        "remove": [],
        "all_done": False,
        "unparsed": [],
    }

    for raw in texts:
        text = (raw or "").strip()
        if not text:
            continue

        # ── a pasted checklist enumerates everything, so it is a complete
        #    statement: ticked = done, unticked = not done.
        lines = text.splitlines()
        checks = [
            (m.group(1).lower() == "x", m.group(2).lower())
            for m in (_CHECKBOX_RE.match(line) for line in lines)
            if m
        ]
        if checks:
            for ticked, tid in checks:
                key = "done" if tid[0] in ("s", "d") else "week_done"
                if ticked:
                    _mark(result, key, [tid])
                else:
                    # An unticked box in a paste is an explicit "not done".
                    if tid in result[key]:
                        result[key].remove(tid)
                    if tid not in result["undone"]:
                        result["undone"].append(tid)
            # A line that isn't part of the checklist (e.g. a command tacked
            # on to the paste) is not consumed by it - fall through and let
            # the normal command parsing below see it.
            leftover = "\n".join(
                ln for ln in lines if not _CHECKBOX_RE.match(ln.strip()) and ln.strip()
            )
            if not leftover:
                continue
            text = leftover

        blocks, stray = _blocks(text)
        if stray:
            result["unparsed"].append("\n".join(stray))
        if not blocks:
            continue

        for cmd, payload in blocks:
            if cmd in _BENIGN_CMDS:
                continue
            if cmd not in KNOWN_CMDS:
                result["unparsed"].append(f"/{cmd} {payload}".strip())
                continue

            if cmd == "done":
                flag = payload.lower().strip(" .!")
                if flag in ("all", "all done"):
                    result["all_done"] = True
                    result["undone"] = []
                elif flag in ("none", ""):
                    # Explicit full reset of everything marked so far.
                    result["all_done"] = False
                    result["done"] = []
                    result["week_done"] = []
                    result["undone"] = []
                else:
                    daily, weekly = _route_ids(payload)
                    if not daily and not weekly:
                        # e.g. "/done the knee thing" - report it, and leave
                        # earlier marks intact rather than reading it as zero.
                        result["unparsed"].append(f"/done {payload}")
                    else:
                        _mark(result, "done", daily)
                        _mark(result, "week_done", weekly)

            elif cmd == "undo":
                daily, weekly = _route_ids(payload)
                if not daily and not weekly:
                    result["unparsed"].append(f"/undo {payload}")
                for task_id in daily + weekly:
                    key = "done" if task_id[0] in ("s", "d") else "week_done"
                    if task_id in result[key]:
                        result[key].remove(task_id)
                    if task_id not in result["undone"]:
                        result["undone"].append(task_id)

            elif cmd == "add":
                result["add"].extend(
                    p.strip() for p in _TEXT_SPLIT.split(payload) if p.strip()
                )

            elif cmd == "week":
                result["week_add"].extend(
                    p.strip() for p in _TEXT_SPLIT.split(payload) if p.strip()
                )

            elif cmd == "drop":
                daily, weekly = _route_ids(payload)
                # Telegram may never remove a static task.
                result["remove"].extend(
                    i for i in daily + weekly if not i.startswith("s")
                )

    return result


# ──────────────────────────────── state helpers ──────────────────────────────


def _all_daily_tasks(state: dict) -> list[dict]:
    out = []
    for pool in DAILY_POOLS:
        out.extend(state.get(pool) or [])
    return out


def _find_daily(state: dict, task_id: str) -> dict | None:
    for task in _all_daily_tasks(state):
        if task["id"] == task_id:
            return task
    return None


def _next_id(state: dict, prefix: str) -> str:
    """Monotonically increasing <prefix><n>, tracked in state['id_seq'].

    Ids are never reused, even after their task is completed or dropped:
    per-task metrics are keyed by id and accumulate across the task's whole
    history, so reissuing a finished task's id to an unrelated new task would
    silently splice two different tasks' reliability stats together under one
    identity.
    """
    seq = state.setdefault("id_seq", {})
    n = seq.get(prefix, 0) + 1
    seq[prefix] = n
    return f"{prefix}{n}"


def _split_category(raw: str) -> tuple[str, str]:
    """'call dentist #health' -> ('call dentist', 'health')."""
    m = re.match(r"^(.*?)\s+#(\w+)\s*$", raw.strip())
    if m:
        return m.group(1).strip(), m.group(2).lower()
    return raw.strip(), "uncategorised"


def is_sunday(date_str: str) -> bool:
    return dt.date.fromisoformat(date_str).weekday() == 6


def week_bounds(date_str: str) -> tuple[str, str]:
    d = dt.date.fromisoformat(date_str)
    monday = d - dt.timedelta(days=d.weekday())
    return monday.isoformat(), (monday + dt.timedelta(days=6)).isoformat()


# ───────────────────────────────── scoring ───────────────────────────────────


def score_day(state: dict, directives: dict) -> dict:
    """Score state['today'] against directives. Mutates state. Returns a summary.

    Caller must ensure this runs at most once per today.date (guard on
    last_processed_date).
    """
    today = state.get("today") or {}
    date_str = today.get("date")
    items = today.get("items") or []
    if not date_str:
        return {"scored": False, "reason": "no snapshot"}

    undone = {i.lower() for i in (directives.get("undone") or [])}
    if directives.get("all_done"):
        # "/done all" then "/undo s3" means everything except s3.
        done_ids = {item["ref"] for item in items} - undone
    else:
        done_ids = {i.lower() for i in (directives.get("done") or [])} - undone

    # A task explicitly dropped tonight is cancelled, not failed: exclude it
    # from scoring entirely rather than recording a miss against it.
    dropped = {i.lower() for i in (directives.get("remove") or [])}
    scored_items = [item for item in items if item["ref"] not in dropped]

    # Surface ids that match nothing real, so a typo (e.g. "s9" for "s3") is
    # reported rather than silently doing nothing.
    if not directives.get("all_done"):
        valid_refs = {item["ref"] for item in items}
        for uid in sorted(
            i for i in (directives.get("done") or []) if i.lower() not in valid_refs
        ):
            directives.setdefault("unparsed", []).append(f"/done {uid} (no such task)")
    valid_weekly = {t["id"] for t in state.get("dynamic_weekly") or []}
    for uid in sorted(
        i for i in (directives.get("week_done") or []) if i.lower() not in valid_weekly
    ):
        directives.setdefault("unparsed", []).append(f"/done {uid} (no such task)")

    points = 0
    completed = 0
    finished_dynamic: list[str] = []
    done_log: list[str] = []
    missed_log: list[str] = []

    for item in scored_items:
        task = _find_daily(state, item["ref"])
        hit = item["ref"] in done_ids
        item["done"] = hit

        if hit:
            completed += 1
            points += 1
            done_log.append(item["ref"])
            if task is not None:
                task["consecutive_misses"] = 0
                task["last_completed"] = date_str
                if item.get("kind") == "static":
                    # Cadence restarts from the day it was actually done.
                    task["next_due"] = shift(date_str, every_days(task))
            if item.get("kind") == "dynamic":
                finished_dynamic.append(item["ref"])
        else:
            missed_log.append(item["ref"])
            if task is not None:
                misses = task.get("consecutive_misses", 0) + 1
                task["consecutive_misses"] = misses
                if item.get("kind") == "static" and misses == 1:
                    # First miss: bring it back tomorrow as a last chance,
                    # regardless of cadence.
                    task["next_due"] = shift(date_str, 1)

    # Completed one-off dynamic tasks leave the pool.
    if finished_dynamic:
        state["dynamic_daily"] = [
            t for t in (state.get("dynamic_daily") or [])
            if t["id"] not in finished_dynamic
        ]

    # Weekly completions.
    week_done = {i.lower() for i in (directives.get("week_done") or [])} - undone
    weekly_completed = 0
    for task in state.get("dynamic_weekly") or []:
        if task["id"] in week_done and not task.get("done"):
            task["done"] = True
            weekly_completed += 1
            points += 2

    # ── streak, in precedence order: break > grow > hold
    breakers = [t for t in _all_daily_tasks(state) if t.get("consecutive_misses", 0) >= 2]

    sunday = is_sunday(date_str)
    weekly_open = [t for t in (state.get("dynamic_weekly") or []) if not t.get("done")]
    weekly_missed = len(weekly_open) if sunday else 0

    total = len(scored_items)
    broke = bool(breakers) or bool(weekly_missed)

    if broke:
        state["streak"] = 0
        # Fresh start so one abandoned task cannot pin the streak at zero.
        # Only static tasks carry a cadence - dynamic tasks must never gain a
        # next_due. Membership in the current static_tasks pool is the
        # unambiguous discriminator (not id prefix or key presence, either of
        # which a hand-added static task lacking "every_days" could evade).
        static_ids = {t["id"] for t in state.get("static_tasks") or []}
        for task in breakers:
            task["consecutive_misses"] = 0
            if task["id"] in static_ids:
                # Breaking the streak does not earn the task any breathing
                # room - it reappears tomorrow, same as a first miss. Only an
                # actual completion resumes the full every_days cadence; a
                # task you keep missing is asked about daily until you catch
                # up on it, not once every `every_days`.
                task["next_due"] = shift(date_str, 1)
    elif total > 0 and completed == total:
        state["streak"] = state.get("streak", 0) + 1
    # else: hold

    state["best_streak"] = max(state.get("best_streak", 0), state.get("streak", 0))
    state["total_points"] = state.get("total_points", 0) + points

    # Per-day id lists are what make per-task reliability, recovery rate,
    # day-of-week and category metrics computable. Do not reduce to counts.
    state.setdefault("history", []).append(
        {
            "date": date_str,
            "completed": completed,
            "total": total,
            "streak": state["streak"],
            "broke": broke,
            "done": done_log,
            "missed": missed_log,
        }
    )
    state["last_processed_date"] = date_str

    weekly_reset = 0
    if sunday:
        weekly_all = state.get("dynamic_weekly") or []
        weekly_reset = len(weekly_all)
        week_start, week_end = week_bounds(date_str)
        state.setdefault("weekly_log", []).append(
            {
                "week_start": week_start,
                "week_end": week_end,
                "total": weekly_reset,
                "completed": sum(1 for t in weekly_all if t.get("done")),
                "missed": weekly_missed,
            }
        )
        state["dynamic_weekly"] = []

    _rollup_week(state, date_str)

    return {
        "scored": True,
        "date": date_str,
        "completed": completed,
        "total": total,
        "points": points,
        "streak": state["streak"],
        "best_streak": state["best_streak"],
        "total_points": state["total_points"],
        "broke": broke,
        "break_ids": [t["id"] for t in breakers],
        "weekly_completed": weekly_completed,
        "weekly_missed": weekly_missed,
        "weekly_reset": weekly_reset,
        "sunday": sunday,
    }


def _rollup_week(state: dict, date_str: str) -> None:
    start, end = week_bounds(date_str)
    rows = [
        h for h in state.get("history", [])
        if start <= h.get("date", "") <= end
    ]
    if not rows:
        return
    total_completed = sum(r.get("completed", 0) for r in rows)
    total_possible = sum(r.get("total", 0) for r in rows)
    entry = {
        "week_start": start,
        "week_end": end,
        "days_logged": len(rows),
        "total_completed": total_completed,
        "total_possible": total_possible,
        "avg_completion_pct": (
            round(100 * total_completed / total_possible) if total_possible else 0
        ),
    }
    summaries = state.setdefault("weekly_summaries", [])
    for i, existing in enumerate(summaries):
        if existing.get("week_start") == start:
            summaries[i] = entry
            return
    summaries.append(entry)


# ─────────────────────────── mutations from Telegram ─────────────────────────


def apply_additions(state: dict, directives: dict, date_str: str) -> dict:
    """Apply add / week-add / remove. Safe to run on any cycle."""
    added_daily, added_weekly, removed = [], [], []

    for raw in directives.get("add") or []:
        text, category = _split_category(raw)
        task_id = _next_id(state, "d")
        state.setdefault("dynamic_daily", []).append(
            {
                "id": task_id,
                "text": text,
                "category": category,
                "added_on": date_str,
                "consecutive_misses": 0,
                "last_completed": None,
            }
        )
        added_daily.append(task_id)

    for raw in directives.get("week_add") or []:
        text, category = _split_category(raw)
        task_id = _next_id(state, "w")
        state.setdefault("dynamic_weekly", []).append(
            {
                "id": task_id,
                "text": text,
                "category": category,
                "added_on": date_str,
                "done": False,
            }
        )
        added_weekly.append(task_id)

    for task_id in directives.get("remove") or []:
        for pool in ("dynamic_daily", "dynamic_weekly"):
            before = len(state.get(pool) or [])
            state[pool] = [t for t in (state.get(pool) or []) if t["id"] != task_id]
            if len(state[pool]) < before:
                removed.append(task_id)

    return {"added_daily": added_daily, "added_weekly": added_weekly, "removed": removed}


MAX_CADENCE_DAYS = 90  # sanity cap: no cadence, and no next_due (however it got
# set - computed or hand-edited in Notion), may push a task further out than
# this. Without a ceiling, a typo (every_days: 9999, or a next_due with the
# wrong year) would strand a task silently for years - no error, it just
# stops appearing. This is the worst failure mode of the feature precisely
# because it is invisible, so it is capped rather than trusted.


def every_days(task: dict) -> int:
    """Cadence of a static task. Absent or invalid means daily."""
    try:
        n = int(task.get("every_days", 1))
    except (TypeError, ValueError):
        return 1
    return max(1, min(n, MAX_CADENCE_DAYS))


def is_due(task: dict, date_str: str) -> bool:
    """A static task is due once its next_due date has arrived.

    next_due of None means "never scheduled yet", i.e. due immediately - which
    is also what every task looks like straight after migration. A next_due
    further out than MAX_CADENCE_DAYS is treated as a data error rather than
    honoured, so it can never strand a task indefinitely.
    """
    next_due = task.get("next_due")
    if not next_due:
        return True
    if next_due > shift(date_str, MAX_CADENCE_DAYS):
        return True
    return next_due <= date_str


def shift(date_str: str, days: int) -> str:
    return (dt.date.fromisoformat(date_str) + dt.timedelta(days=days)).isoformat()


def build_today(state: dict, date_str: str) -> None:
    """Snapshot today's due list.

    Dynamic tasks are due every day. Static tasks follow their own cadence:
    they reappear every `every_days`, except after a first miss, when they
    reappear the next day as a last chance (see SPEC.md).
    """
    items = []
    for task in state.get("static_tasks") or []:
        if not is_due(task, date_str):
            continue
        items.append(
            {
                "ref": task["id"],
                "text": task["text"],
                "kind": "static",
                "type": task.get("type", "do"),
                "category": task.get("category", "uncategorised"),
                "every_days": every_days(task),
                "warn": task.get("consecutive_misses", 0) == 1,
                "done": False,
            }
        )
    for task in state.get("dynamic_daily") or []:
        items.append(
            {
                "ref": task["id"],
                "text": task["text"],
                "kind": "dynamic",
                "type": "do",
                "category": task.get("category", "uncategorised"),
                "warn": task.get("consecutive_misses", 0) == 1,
                "done": False,
            }
        )
    state["today"] = {"date": date_str, "items": items}


# ───────────────────────────────── rendering ─────────────────────────────────

_HINT = "/done s1 d2 w1 · /undo s1 · /add <task> · /week <task> · /drop d3"


def _example_ids(state: dict) -> dict:
    """Build examples from the user's real ids, so the help is copy-pasteable."""
    items = (state.get("today") or {}).get("items") or []
    refs = [i["ref"] for i in items]
    dynamic = [t["id"] for t in (state.get("dynamic_daily") or [])]
    weekly = [t["id"] for t in (state.get("dynamic_weekly") or []) if not t.get("done")]
    return {
        "two": " ".join(refs[:2]) if refs else "s1 s2",
        "one": refs[0] if refs else "s1",
        "drop": (dynamic or weekly or ["d1"])[0],
    }


def render_help(state: dict, compact: bool = False) -> list[str]:
    """Action reference. Goes at the END so the checklist stays at the top.

    Kept deliberately tight - five short lines - because a help block that
    pushes the checklist out of view defeats the purpose of the message.
    """
    ex = _example_ids(state)
    if compact:
        return ["", f"/done {ex['two']} · /undo {ex['one']} · /add · /week · /drop"]
    return [
        "",
        "──────────",
        f"/done {ex['two']} — mark done",
        f"/undo {ex['one']} — un-mark",
        "/add <task> — add to today",
        "/week <task> — add to this week",
        f"/drop {ex['drop']} — remove an added task",
    ]


def render_unparsed(directives: dict) -> list[str]:
    """Tell the user what didn't register, rather than dropping it silently."""
    bad = directives.get("unparsed") or []
    if not bad:
        return []
    first = bad[0]
    if len(first) > 60:
        first = first[:57] + "..."
    noun = "message" if len(bad) == 1 else "messages"
    # The syntax hint is already appended at the end of every message, so it is
    # deliberately not repeated here.
    return ["", f'{len(bad)} {noun} not understood — e.g. "{first}"']


def _lines(items):
    out = []
    for item in items:
        line = f"- {item['text']} ({item['ref']})"
        if item.get("every_days", 1) > 1:
            line += f" · every {item['every_days']}d"
        if item.get("warn"):
            line += f" {WARN_TEXT}"
        out.append(line)
    return out


def render_morning(
    state: dict,
    summary: dict | None,
    metrics: dict | None = None,
    directives: dict | None = None,
) -> str:
    items = state["today"]["items"]
    parts = [f"Good morning! Today's list ({state['today']['date']}):"]

    # Not segregated by do/avoid - the task text itself says which it is.
    lines = _lines(items)
    if lines:
        parts += [""] + lines

    weekly = [
        f"- {t['text']} ({t['id']})"
        for t in (state.get("dynamic_weekly") or [])
        if not t.get("done")
    ]
    if weekly:
        parts += ["", "This week (by Sunday):"] + weekly

    if summary and summary.get("scored"):
        parts += ["", f"Yesterday: {summary['completed']}/{summary['total']} done."]
        if summary["broke"]:
            why = (
                "a weekly task went unfinished"
                if summary["weekly_missed"]
                else f"missed twice: {', '.join(summary['break_ids'])}"
            )
            parts.append(f"Streak reset ({why}). Points: {summary['total_points']}.")
        else:
            parts.append(
                f"Streak: {summary['streak']} (best: {summary['best_streak']}). "
                f"Points: {summary['total_points']}."
            )
        if summary.get("weekly_reset"):
            parts.append(
                f"Weekly list reset: {summary['weekly_completed']} done, "
                f"{summary['weekly_missed']} missed."
            )

    if metrics:
        import metrics as _m  # local import keeps engine importable standalone

        parts += ["", _m.headline(metrics)]

        # One procrastination nudge, only when it has actually gone stale.
        stale = [t for t in metrics.get("open_task_ages", []) if t["age_days"] >= 3]
        if stale:
            worst = stale[0]
            parts.append(
                f"Oldest open item: {worst['text']} ({worst['id']}) — "
                f"{worst['age_days']} days."
            )

    if directives:
        parts += render_unparsed(directives)

    parts += render_help(state)
    return "\n".join(parts)


def render_evening(state: dict, directives: dict | None = None) -> str:
    items = state["today"]["items"]
    parts = [f"Evening check-in ({state['today']['date']}) — mark what you did:"]

    for item in items:
        line = f"- [ ] {item['text']} ({item['ref']})"
        if item.get("warn"):
            line += f" {WARN_TEXT}"
        parts.append(line)

    weekly = [
        f"- [ ] {t['text']} ({t['id']})"
        for t in (state.get("dynamic_weekly") or [])
        if not t.get("done")
    ]
    if weekly:
        parts += ["", "This week (by Sunday):"] + weekly

    if directives:
        parts += render_unparsed(directives)

    # Evening gets the one-liner: the full reference already went out at 8AM.
    parts += render_help(state, compact=True)
    return "\n".join(parts)
