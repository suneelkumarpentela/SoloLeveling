"""
Static-task cadence (`every_days`).

The governing example, for every_days = 2:

    day1  due    done  -> next due day3
    day1  due    miss  -> misses=1, due day2 as a last chance (warned)
    day2  grace  miss  -> misses=2, STREAK BREAK, next due day4

every_days = 1 must reproduce the previous daily behaviour exactly.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import engine  # noqa: E402
import metrics  # noqa: E402

import datetime as _dt

# Mon 27 Jul 2026 onwards. Built with real date arithmetic rather than string
# formatting, which silently produced "2026-07-210" on the first attempt.
D1, D2, D3, D4, D5 = (
    (_dt.date(2026, 7, 27) + _dt.timedelta(days=n)).isoformat() for n in range(5)
)


def state(every=2):
    return {
        "static_tasks": [
            {"id": "s1", "text": "Brush", "type": "do", "every_days": 1,
             "consecutive_misses": 0, "last_completed": None, "next_due": None},
            {"id": "s2", "text": "Knee exercise", "type": "do", "every_days": every,
             "consecutive_misses": 0, "last_completed": None, "next_due": None},
        ],
        "dynamic_daily": [], "dynamic_weekly": [], "history": [],
        "streak": 0, "best_streak": 0, "total_points": 0,
    }


def task(st, tid="s2"):
    return next(t for t in st["static_tasks"] if t["id"] == tid)


def day(st, date, done):
    engine.build_today(st, date)
    refs = [i["ref"] for i in st["today"]["items"]]
    engine.score_day(st, engine.parse_directives([f"/done {done}"] if done else []))
    return refs


def test_done_on_day_one_reappears_on_day_three():
    st = state(every=2)
    assert "s2" in day(st, D1, "s1 s2")
    assert task(st)["next_due"] == D3

    engine.build_today(st, D2)
    assert "s2" not in [i["ref"] for i in st["today"]["items"]], "not due on day 2"

    engine.build_today(st, D3)
    assert "s2" in [i["ref"] for i in st["today"]["items"]], "due again on day 3"


def test_missed_on_day_one_gets_a_last_chance_on_day_two():
    st = state(every=2)
    day(st, D1, "s1")                       # s2 missed
    assert task(st)["consecutive_misses"] == 1
    assert task(st)["next_due"] == D2, "grace day overrides the cadence"

    engine.build_today(st, D2)
    item = next(i for i in st["today"]["items"] if i["ref"] == "s2")
    assert item["warn"] is True


def test_missed_twice_breaks_streak_and_resumes_on_day_four():
    st = state(every=2)
    st["streak"] = 5

    day(st, D1, "s1")                       # miss 1
    assert st["streak"] == 5, "one miss must not break"

    day(st, D2, "s1")                       # miss 2 on the grace day
    assert st["streak"] == 0, "second consecutive miss breaks the streak"
    assert task(st)["consecutive_misses"] == 0, "counter resets after a break"
    assert task(st)["next_due"] == D4, "resumes cadence from the grace day"

    engine.build_today(st, D3)
    assert "s2" not in [i["ref"] for i in st["today"]["items"]]
    engine.build_today(st, D4)
    assert "s2" in [i["ref"] for i in st["today"]["items"]]


def test_completing_on_the_grace_day_restarts_the_cadence():
    st = state(every=2)
    day(st, D1, "s1")                       # miss
    day(st, D2, "s1 s2")                    # recovered on the grace day
    assert task(st)["consecutive_misses"] == 0
    assert task(st)["next_due"] == D4, "measured from the day it was done"


def test_every_days_one_matches_the_old_daily_behaviour():
    st = state(every=1)
    st["streak"] = 3
    day(st, D1, "s2")                       # s1 missed once
    assert st["streak"] == 3
    engine.build_today(st, D2)
    assert any(i["ref"] == "s1" and i["warn"] for i in st["today"]["items"])
    day(st, D2, "s2")                       # s1 missed twice
    assert st["streak"] == 0


def test_a_task_not_due_cannot_affect_the_streak():
    """The point of cadence: an off-cycle task is absent from the denominator."""
    st = state(every=2)
    day(st, D1, "s1 s2")                    # both done, s2 now due D3
    assert st["streak"] == 1

    refs = day(st, D2, "s1")                # only s1 is due; complete it
    assert refs == ["s1"], "s2 must not appear on day 2"
    assert st["streak"] == 2, "a full day of what was due still grows the streak"
    assert st["history"][-1]["total"] == 1


def test_missing_every_days_defaults_to_daily():
    st = state()
    del task(st)["every_days"]
    assert engine.every_days(task(st)) == 1


def test_garbage_every_days_defaults_to_daily():
    st = state()
    for bad in (None, "abc", 0, -3):
        task(st)["every_days"] = bad
        assert engine.every_days(task(st)) == 1


def test_null_next_due_is_treated_as_due_now():
    """Every task looks like this immediately after migration."""
    st = state(every=3)
    task(st)["next_due"] = None
    engine.build_today(st, D1)
    assert "s2" in [i["ref"] for i in st["today"]["items"]]


def test_cadence_shown_in_the_message_only_when_not_daily():
    st = state(every=2)
    engine.build_today(st, D1)
    msg = engine.render_morning(st, None)
    assert "Knee exercise (s2) · every 2d" in msg
    assert "Brush (s1)" in msg and "every 1d" not in msg


def test_dynamic_tasks_never_get_a_cadence():
    st = state(every=2)
    engine.apply_additions(st, {"add": ["call dentist"]}, D1)
    engine.build_today(st, D1)
    item = next(i for i in st["today"]["items"] if i["ref"].startswith("d"))
    assert item.get("every_days", 1) == 1
    assert "next_due" not in st["dynamic_daily"][0]


def test_breakers_guard_does_not_depend_on_the_every_days_key_or_id_prefix():
    """Regression: the breakers loop decided "is this a static task" by
    checking `"every_days" in task or id.startswith("s")`. A static task
    added by hand in Notion without an `every_days` key (legitimate: it just
    means "daily", same as if the key were present and 1) and an unconventional
    id would slip past BOTH checks, so it would break the streak but never get
    a `next_due`, silently reverting it to whatever the miss-handling code set
    moments earlier instead of the correct cadence-based date."""
    st = state(every=2)
    hand_added = {
        "id": "zz9",  # does not start with "s" - a static task can have any id
        "text": "Hand-added in Notion",
        "type": "do",
        "consecutive_misses": 0,
        "last_completed": None,
        "next_due": None,
        # deliberately no "every_days" key at all
    }
    st["static_tasks"].append(hand_added)

    day(st, D1, "s1 s2")            # zz9 missed: miss 1, next_due = D2
    day(st, D2, "s1 s2")            # zz9 missed again: miss 2, streak breaks

    task_zz9 = next(t for t in st["static_tasks"] if t["id"] == "zz9")
    assert task_zz9["consecutive_misses"] == 0, "breaker reset must still apply"
    assert task_zz9["next_due"] == D3, \
        "a static task missing 'every_days' must still get next_due recomputed " \
        "from its cadence (default 1), not be silently skipped by the guard"


def test_dynamic_task_reaching_two_misses_never_gets_a_next_due():
    """The other half of the same guard: dynamic tasks must never gain a
    next_due even when they break the streak."""
    st = state(every=2)
    engine.apply_additions(st, {"add": ["call dentist"]}, D1)

    engine.build_today(st, D1)
    engine.score_day(st, engine.parse_directives(["/done s1 s2"]))  # d1 missed
    engine.build_today(st, D2)
    engine.score_day(st, engine.parse_directives(["/done s1"]))     # d1 missed again, s2 not due

    d1 = next(t for t in st["dynamic_daily"] if t["id"] == "d1")
    assert "next_due" not in d1, "a dynamic task must never gain scheduling state"


def test_every_days_is_capped_so_a_typo_cannot_strand_a_task_for_years():
    """Regression: every_days only clamped its LOWER bound (missing/zero/
    negative -> 1). A hand-edit typo like every_days: 9999 would push
    next_due nearly 30 years out with no error, and the task would silently
    never appear again - "the worst failure mode... because it is invisible.\""""
    st = state(every=2)
    task(st)["every_days"] = 9999

    day(st, D1, "s1 s2")  # s2 completed with a runaway cadence value

    next_due = task(st)["next_due"]
    assert next_due <= engine.shift(D1, engine.MAX_CADENCE_DAYS), \
        "next_due must be capped, not pushed arbitrarily far into the future"


def test_per_task_reliability_denominator_only_counts_days_actually_due():
    """Verified correct, not a bug: history's done/missed lists are built from
    the already-cadence-filtered snapshot, so an off-cycle day is never
    counted as a miss and never inflates the "times due" denominator."""
    st = state(every=2)
    day(st, D1, "s1 s2")   # both due, both done -> s2 next due D3
    day(st, D2, "s1")      # only s1 due; s2 is off-cycle, absent entirely
    day(st, D3, "s1 s2")   # s2 due again, done

    per = metrics.compute(st, D3)["per_task"]
    assert per["s2"]["due"] == 2, "s2 was only actually due on D1 and D3"
    assert per["s2"]["reliability_pct"] == 100, "the off-cycle day must not count as a miss"


def test_a_wildly_future_next_due_is_treated_as_a_data_error_not_honoured():
    """Even a next_due written directly (e.g. a hand-edit typo of the date
    itself, not routed through every_days at all) must not strand a task
    indefinitely - is_due must refuse to honour a next_due this far out."""
    st = state(every=1)
    task(st)["next_due"] = "2099-01-01"  # obviously wrong, but a valid date

    engine.build_today(st, D1)
    assert "s2" in [i["ref"] for i in st["today"]["items"]], \
        "a next_due far beyond the sanity cap must not hide the task forever"
