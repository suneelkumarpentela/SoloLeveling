"""
Runner-level tests: inbox lifecycle, re-run safety, and the
"morning mutates / evening is read-only" invariant.

These are the behaviours that silently corrupt data when they go wrong, so they
are tested at the runner level rather than only in the engine.
"""

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import process_replies as runner  # noqa: E402


def fresh_state(snapshot_date=None, items=None):
    st = {
        "static_tasks": [
            {"id": "s1", "text": "Brush", "type": "do", "category": "health",
             "consecutive_misses": 0, "last_completed": None},
            {"id": "s2", "text": "Productivity", "type": "do", "category": "work",
             "consecutive_misses": 0, "last_completed": None},
        ],
        "dynamic_daily": [],
        "dynamic_weekly": [],
        "telegram_inbox": [],
        "telegram_outbox": [],
        "history": [],
        "weekly_summaries": [],
        "streak": 0,
        "best_streak": 0,
        "total_points": 0,
        "last_processed_date": None,
    }
    if snapshot_date:
        st["today"] = {
            "date": snapshot_date,
            "items": items if items is not None else [
                {"ref": "s1", "text": "Brush", "kind": "static", "type": "do",
                 "category": "health", "warn": False, "done": False},
                {"ref": "s2", "text": "Productivity", "kind": "static", "type": "do",
                 "category": "work", "warn": False, "done": False},
            ],
        }
    else:
        st["today"] = {"date": None, "items": []}
    return st


def inbox(st, *texts):
    st["telegram_inbox"] = [
        {"message_id": i, "date": 0, "text": t} for i, t in enumerate(texts, 1)
    ]


def last_sent(st):
    return st["telegram_outbox"][-1]["text"]


# ───────────────────────── inbox lifecycle ──────────────────────────────────


def test_morning_scores_then_clears_inbox(monkeypatch):
    monkeypatch.setattr(runner, "today_str", lambda: "2026-07-28")
    st = fresh_state("2026-07-27")
    inbox(st, "/done s1")

    runner.run_morning(st)

    assert st["history"][-1]["done"] == ["s1"]
    assert st["history"][-1]["missed"] == ["s2"]
    assert st["telegram_inbox"] == [], "inbox must be cleared after scoring"
    assert st["last_processed_date"] == "2026-07-27"


def test_rerunning_morning_does_not_zero_the_day(monkeypatch):
    """The bug that clear-before-fetch would cause."""
    monkeypatch.setattr(runner, "today_str", lambda: "2026-07-28")
    st = fresh_state("2026-07-27")
    inbox(st, "/done s1 s2")
    runner.run_morning(st)

    points_after_first = st["total_points"]
    streak_after_first = st["streak"]
    history_len = len(st["history"])
    assert points_after_first == 2 and streak_after_first == 1

    # Second run the same morning: inbox is empty and the offset has moved.
    runner.run_morning(st)

    assert st["total_points"] == points_after_first, "must not re-score"
    assert st["streak"] == streak_after_first, "must not touch the streak"
    assert len(st["history"]) == history_len, "must not append a second row"


def test_inbox_retained_when_nothing_was_scored(monkeypatch):
    """A re-run must not discard messages meant for tomorrow."""
    monkeypatch.setattr(runner, "today_str", lambda: "2026-07-28")
    st = fresh_state("2026-07-27")
    inbox(st, "/done s1")
    runner.run_morning(st)

    # Message arrives after the run, then the workflow is triggered again.
    inbox(st, "/add buy milk")
    runner.run_morning(st)
    assert len(st["telegram_inbox"]) == 1, \
        "unscored messages must survive a redundant run"


def test_missed_run_gap_is_logged(monkeypatch, capsys):
    """If a whole day was skipped (no run at all), that gap must be visible
    in the logs rather than silently scoring the stale snapshot as if it were
    a normal single day behind."""
    monkeypatch.setattr(runner, "today_str", lambda: "2026-07-30")
    st = fresh_state("2026-07-27")  # 3 days behind: the 28th and 29th never ran
    inbox(st, "/done s1")

    runner.run_morning(st)

    assert "WARNING" in capsys.readouterr().out
    assert st["history"][-1]["date"] == "2026-07-27"


def test_first_ever_run_builds_without_scoring(monkeypatch):
    monkeypatch.setattr(runner, "today_str", lambda: "2026-07-28")
    st = fresh_state()  # no snapshot
    inbox(st, "/done s1")

    runner.run_morning(st)

    assert st["history"] == [], "nothing to score on the first run"
    assert st["streak"] == 0
    assert len(st["today"]["items"]) == 2
    assert st["telegram_inbox"] != [], "inbox kept for the next real scoring pass"


# ─────────────────── morning mutates / evening read-only ─────────────────────


def test_evening_never_mutates_task_state(monkeypatch):
    monkeypatch.setattr(runner, "today_str", lambda: "2026-07-28")
    st = fresh_state("2026-07-28")
    inbox(st, "/done s1", "/add buy milk", "/week file taxes")

    before = copy.deepcopy({k: v for k, v in st.items() if k != "telegram_outbox"})
    runner.run_evening(st)
    after = {k: v for k, v in st.items() if k != "telegram_outbox"}

    assert before == after, "evening run must be read-only apart from the outbox"


def test_evening_queues_the_current_snapshot(monkeypatch):
    monkeypatch.setattr(runner, "today_str", lambda: "2026-07-28")
    st = fresh_state("2026-07-28")
    runner.run_evening(st)
    msg = last_sent(st)
    assert "Evening check-in (2026-07-28)" in msg
    assert "- [ ] Brush (s1)" in msg


def test_no_double_add_across_evening_then_morning(monkeypatch):
    """The reason the evening run must not apply additions."""
    monkeypatch.setattr(runner, "today_str", lambda: "2026-07-28")
    st = fresh_state("2026-07-27")
    inbox(st, "/add buy milk")

    runner.run_evening(st)          # sees the add, must ignore it
    assert st["dynamic_daily"] == []

    runner.run_morning(st)          # applies it exactly once
    assert [t["text"] for t in st["dynamic_daily"]] == ["buy milk"]


def test_evening_with_no_snapshot_is_a_noop(monkeypatch):
    monkeypatch.setattr(runner, "today_str", lambda: "2026-07-28")
    st = fresh_state()
    runner.run_evening(st)
    assert st["telegram_outbox"] == []


# ──────────────────────────── message content ────────────────────────────────


def test_morning_message_carries_metrics_and_errors(monkeypatch):
    monkeypatch.setattr(runner, "today_str", lambda: "2026-07-28")
    st = fresh_state("2026-07-27")
    inbox(st, "/done s1", "did the other thing too")

    runner.run_morning(st)
    msg = last_sent(st)

    assert "Score:" in msg and "Accuracy:" in msg and "Streak:" in msg
    assert "not understood" in msg
    assert st["metrics"]["accuracy_lifetime_pct"] == 50


def test_daytime_reply_survives_to_be_scored_next_morning(monkeypatch):
    """Full two-day cycle: noon reply must still count."""
    monkeypatch.setattr(runner, "today_str", lambda: "2026-07-27")
    st = fresh_state("2026-07-26")
    runner.run_morning(st)              # builds the 27th
    assert st["today"]["date"] == "2026-07-27"

    inbox(st, "/done s1")               # noon on the 27th, fetched at 22:00
    runner.run_evening(st)              # read-only, inbox untouched
    assert len(st["telegram_inbox"]) == 1

    st["telegram_inbox"].append(
        {"message_id": 99, "date": 0, "text": "/done s1 s2"}   # 23:00 reply
    )

    monkeypatch.setattr(runner, "today_str", lambda: "2026-07-28")
    runner.run_morning(st)

    row = st["history"][-1]
    assert row["date"] == "2026-07-27"
    assert sorted(row["done"]) == ["s1", "s2"], "last /done statement wins"
    assert st["telegram_inbox"] == []
