"""
Metric computation. Pure functions over state - no network, no I/O.

Everything here is derived from `history` rows, which must carry the per-day
`done` / `missed` id lists. Aggregate counts alone are not enough to answer
"which habit am I actually failing", which is the point of most of these.

Nothing in this module mutates state.
"""

from __future__ import annotations

import datetime as dt

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
LOAD_BUCKETS = [(1, 4, "1-4"), (5, 6, "5-6"), (7, 8, "7-8"), (9, 99, "9+")]


def _pct(num: int, den: int) -> int:
    return round(100 * num / den) if den else 0


def _window(history: list[dict], as_of: str, days: int) -> list[dict]:
    cutoff = (dt.date.fromisoformat(as_of) - dt.timedelta(days=days - 1)).isoformat()
    return [h for h in history if h.get("date", "") >= cutoff]


def _accuracy(rows: list[dict]) -> int:
    return _pct(
        sum(r.get("completed", 0) for r in rows),
        sum(r.get("total", 0) for r in rows),
    )


def _task_catalog(state: dict) -> dict[str, dict]:
    """id -> {text, category, pool} for tasks that still exist."""
    out = {}
    for pool in ("static_tasks", "dynamic_daily", "dynamic_weekly"):
        for task in state.get(pool) or []:
            out[task["id"]] = {
                "text": task.get("text", ""),
                "category": task.get("category", "uncategorised"),
                "pool": pool,
            }
    return out


# ─────────────────────────────── individual metrics ──────────────────────────


def per_task_reliability(state: dict) -> dict:
    """completions / times-due per task, plus current misses and longest run."""
    history = sorted(state.get("history", []), key=lambda r: r.get("date", ""))
    catalog = _task_catalog(state)

    stats: dict[str, dict] = {}
    runs: dict[str, int] = {}

    for row in history:
        done = set(row.get("done") or [])
        missed = set(row.get("missed") or [])
        for task_id in done | missed:
            s = stats.setdefault(
                task_id, {"due": 0, "done": 0, "longest_run": 0}
            )
            s["due"] += 1
            if task_id in done:
                s["done"] += 1
                runs[task_id] = runs.get(task_id, 0) + 1
                s["longest_run"] = max(s["longest_run"], runs[task_id])
            else:
                runs[task_id] = 0

    for task_id, s in stats.items():
        s["reliability_pct"] = _pct(s["done"], s["due"])
        meta = catalog.get(task_id, {})
        s["text"] = meta.get("text", "(removed)")
        s["category"] = meta.get("category", "uncategorised")
        s["still_active"] = task_id in catalog
        s["current_misses"] = 0

    for pool in ("static_tasks", "dynamic_daily"):
        for task in state.get(pool) or []:
            if task["id"] in stats:
                stats[task["id"]]["current_misses"] = task.get("consecutive_misses", 0)

    return stats


def dynamic_task_age(state: dict, as_of: str) -> list[dict]:
    """How long each open one-off task has been carrying. Procrastination signal."""
    today = dt.date.fromisoformat(as_of)
    out = []
    for pool, label in (("dynamic_daily", "daily"), ("dynamic_weekly", "weekly")):
        for task in state.get(pool) or []:
            if task.get("done"):
                continue
            added = task.get("added_on")
            age = (today - dt.date.fromisoformat(added)).days if added else 0
            out.append(
                {"id": task["id"], "text": task.get("text", ""), "kind": label, "age_days": age}
            )
    return sorted(out, key=lambda t: -t["age_days"])


def recovery_rate(state: dict) -> dict:
    """When a task was missed and came up again next day, how often was it saved?

    This is the metric that tells you whether the warning actually works.
    """
    history = sorted(state.get("history", []), key=lambda r: r.get("date", ""))
    occasions = 0
    recovered = 0

    for prev, cur in zip(history, history[1:]):
        prev_missed = set(prev.get("missed") or [])
        cur_done = set(cur.get("done") or [])
        cur_missed = set(cur.get("missed") or [])
        for task_id in prev_missed & (cur_done | cur_missed):
            occasions += 1
            if task_id in cur_done:
                recovered += 1

    return {
        "occasions": occasions,
        "recovered": recovered,
        "rate_pct": _pct(recovered, occasions),
    }


def load_vs_completion(state: dict) -> dict:
    """Completion rate bucketed by how many items were on the list.

    If accuracy collapses in the high buckets, the checklist is too long.
    """
    buckets: dict[str, dict] = {label: {"days": 0, "completed": 0, "total": 0} for _, _, label in LOAD_BUCKETS}
    for row in state.get("history", []):
        total = row.get("total", 0)
        for lo, hi, label in LOAD_BUCKETS:
            if lo <= total <= hi:
                b = buckets[label]
                b["days"] += 1
                b["completed"] += row.get("completed", 0)
                b["total"] += total
                break
    for b in buckets.values():
        b["completion_pct"] = _pct(b["completed"], b["total"])
    return {k: v for k, v in buckets.items() if v["days"]}


def day_of_week_pattern(state: dict) -> dict:
    out: dict[str, dict] = {}
    for row in state.get("history", []):
        date_str = row.get("date")
        if not date_str:
            continue
        label = WEEKDAYS[dt.date.fromisoformat(date_str).weekday()]
        d = out.setdefault(label, {"days": 0, "completed": 0, "total": 0})
        d["days"] += 1
        d["completed"] += row.get("completed", 0)
        d["total"] += row.get("total", 0)
    for d in out.values():
        d["completion_pct"] = _pct(d["completed"], d["total"])
    return out


def weekly_commitment_rate(state: dict) -> dict:
    """Of the weekly tasks you took on, what fraction landed by Sunday?"""
    log = state.get("weekly_log", [])
    total = sum(r.get("total", 0) for r in log)
    completed = sum(r.get("completed", 0) for r in log)
    return {
        "weeks_logged": len(log),
        "committed": total,
        "completed": completed,
        "rate_pct": _pct(completed, total),
    }


def category_balance(state: dict) -> dict:
    """Completion split by category - the work-vs-health balance check."""
    catalog = _task_catalog(state)
    out: dict[str, dict] = {}
    for row in state.get("history", []):
        done = set(row.get("done") or [])
        missed = set(row.get("missed") or [])
        for task_id in done | missed:
            cat = catalog.get(task_id, {}).get("category", "uncategorised")
            c = out.setdefault(cat, {"due": 0, "done": 0})
            c["due"] += 1
            if task_id in done:
                c["done"] += 1
    for c in out.values():
        c["completion_pct"] = _pct(c["done"], c["due"])
    return out


# ──────────────────────────────── aggregate ──────────────────────────────────


def compute(state: dict, as_of: str) -> dict:
    history = state.get("history", [])
    return {
        "as_of": as_of,
        "points": state.get("total_points", 0),
        "days_logged": len(history),
        "accuracy_lifetime_pct": _accuracy(history),
        "accuracy_7d_pct": _accuracy(_window(history, as_of, 7)),
        "accuracy_30d_pct": _accuracy(_window(history, as_of, 30)),
        "streak": {
            "current": state.get("streak", 0),
            "best": state.get("best_streak", 0),
        },
        "per_task": per_task_reliability(state),
        "open_task_ages": dynamic_task_age(state, as_of),
        "recovery": recovery_rate(state),
        "load_vs_completion": load_vs_completion(state),
        "day_of_week": day_of_week_pattern(state),
        "weekly_commitment": weekly_commitment_rate(state),
        "category_balance": category_balance(state),
    }


def headline(metrics: dict) -> str:
    """The single line that goes in the morning message. Deliberately terse."""
    return (
        f"Score: {metrics['points']} pts · "
        f"Accuracy: {metrics['accuracy_lifetime_pct']}% "
        f"(7d {metrics['accuracy_7d_pct']}%) · "
        f"Streak: {metrics['streak']['current']} (best {metrics['streak']['best']})"
    )


def weakest_task(metrics: dict, min_due: int = 3) -> dict | None:
    """Lowest-reliability active task with enough data to be meaningful."""
    candidates = [
        {"id": tid, **s}
        for tid, s in metrics["per_task"].items()
        if s["still_active"] and s["due"] >= min_due
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda s: s["reliability_pct"])
