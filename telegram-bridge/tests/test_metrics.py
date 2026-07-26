"""Unit tests for metric computation. Run: python -m pytest tests/ -q"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import engine  # noqa: E402
import metrics  # noqa: E402


def state_with_history():
    return {
        "static_tasks": [
            {"id": "s1", "text": "Brush", "type": "do", "category": "health", "consecutive_misses": 0},
            {"id": "s2", "text": "Productivity", "type": "do", "category": "work", "consecutive_misses": 0},
            {"id": "s3", "text": "Knee exercise", "type": "do", "category": "health", "consecutive_misses": 1},
        ],
        "dynamic_daily": [
            {"id": "d1", "text": "call dentist", "category": "health",
             "added_on": "2026-07-20", "consecutive_misses": 0}
        ],
        "dynamic_weekly": [],
        "weekly_log": [
            {"week_start": "2026-07-13", "week_end": "2026-07-19", "total": 3, "completed": 2, "missed": 1}
        ],
        "history": [
            # Mon            all three done
            {"date": "2026-07-20", "completed": 3, "total": 3, "streak": 1, "broke": False,
             "done": ["s1", "s2", "s3"], "missed": []},
            # Tue            s3 missed
            {"date": "2026-07-21", "completed": 2, "total": 3, "streak": 1, "broke": False,
             "done": ["s1", "s2"], "missed": ["s3"]},
            # Wed            s3 recovered
            {"date": "2026-07-22", "completed": 3, "total": 3, "streak": 2, "broke": False,
             "done": ["s1", "s2", "s3"], "missed": []},
            # Thu            s3 missed again, not recovered
            {"date": "2026-07-23", "completed": 2, "total": 3, "streak": 2, "broke": False,
             "done": ["s1", "s2"], "missed": ["s3"]},
            # Fri            s3 missed twice running
            {"date": "2026-07-24", "completed": 2, "total": 3, "streak": 0, "broke": True,
             "done": ["s1", "s2"], "missed": ["s3"]},
        ],
        "streak": 0,
        "best_streak": 2,
        "total_points": 12,
    }


def test_accuracy_windows():
    st = state_with_history()
    m = metrics.compute(st, "2026-07-24")
    # 12 completed of 15 due
    assert m["accuracy_lifetime_pct"] == 80
    assert m["days_logged"] == 5


def test_accuracy_window_excludes_old_rows():
    st = state_with_history()
    st["history"].insert(0, {"date": "2026-01-01", "completed": 0, "total": 10,
                             "streak": 0, "broke": True, "done": [], "missed": []})
    m = metrics.compute(st, "2026-07-24")
    assert m["accuracy_7d_pct"] == 80, "7-day window must ignore the January row"
    assert m["accuracy_lifetime_pct"] == 48, "lifetime must include it"


def test_per_task_reliability_identifies_the_culprit():
    st = state_with_history()
    m = metrics.compute(st, "2026-07-24")
    per = m["per_task"]
    assert per["s1"]["reliability_pct"] == 100
    assert per["s3"]["reliability_pct"] == 40  # 2 of 5
    assert per["s3"]["current_misses"] == 1

    worst = metrics.weakest_task(m)
    assert worst["id"] == "s3", "weakest task should surface the failing habit"


def test_longest_run_per_task():
    st = state_with_history()
    per = metrics.compute(st, "2026-07-24")["per_task"]
    assert per["s1"]["longest_run"] == 5
    assert per["s3"]["longest_run"] == 1


def test_recovery_rate():
    st = state_with_history()
    r = metrics.compute(st, "2026-07-24")["recovery"]
    # s3 missed on 21 (recovered 22), missed 23 (not recovered 24) -> 1 of 2
    assert r["occasions"] == 2
    assert r["recovered"] == 1
    assert r["rate_pct"] == 50


def test_dynamic_task_age_flags_procrastination():
    st = state_with_history()
    ages = metrics.compute(st, "2026-07-26")["open_task_ages"]
    assert ages[0]["id"] == "d1"
    assert ages[0]["age_days"] == 6


def test_day_of_week_pattern():
    st = state_with_history()
    dow = metrics.compute(st, "2026-07-24")["day_of_week"]
    assert dow["Mon"]["completion_pct"] == 100
    assert dow["Fri"]["completion_pct"] == 67


def test_load_vs_completion_buckets():
    st = state_with_history()
    load = metrics.compute(st, "2026-07-24")["load_vs_completion"]
    assert "1-4" in load
    assert load["1-4"]["days"] == 5
    assert "9+" not in load, "empty buckets should be omitted"


def test_category_balance():
    st = state_with_history()
    cats = metrics.compute(st, "2026-07-24")["category_balance"]
    assert cats["work"]["completion_pct"] == 100
    assert cats["health"]["completion_pct"] == 70  # s1 5/5 + s3 2/5


def test_weekly_commitment_rate():
    st = state_with_history()
    w = metrics.compute(st, "2026-07-24")["weekly_commitment"]
    assert w["committed"] == 3 and w["completed"] == 2
    assert w["rate_pct"] == 67


def test_headline_is_one_line():
    st = state_with_history()
    line = metrics.headline(metrics.compute(st, "2026-07-24"))
    assert "\n" not in line
    assert "12 pts" in line and "80%" in line


def test_metrics_never_mutate_state():
    st = state_with_history()
    import copy
    before = copy.deepcopy(st)
    metrics.compute(st, "2026-07-24")
    assert st == before


def test_empty_state_does_not_crash():
    st = {"history": [], "static_tasks": [], "dynamic_daily": [], "dynamic_weekly": []}
    m = metrics.compute(st, "2026-07-26")
    assert m["accuracy_lifetime_pct"] == 0
    assert metrics.weakest_task(m) is None


# ─────────────────── integration: engine writes what metrics needs ───────────


def test_engine_records_ids_metrics_can_consume():
    st = {
        "static_tasks": [
            {"id": "s1", "text": "A", "type": "do", "category": "work", "consecutive_misses": 0},
            {"id": "s2", "text": "B", "type": "do", "category": "health", "consecutive_misses": 0},
        ],
        "dynamic_daily": [], "dynamic_weekly": [], "history": [],
        "streak": 0, "best_streak": 0, "total_points": 0,
    }
    engine.build_today(st, "2026-07-27")
    engine.score_day(st, engine.parse_directives(["/done s1"]))

    row = st["history"][-1]
    assert row["done"] == ["s1"] and row["missed"] == ["s2"]

    m = metrics.compute(st, "2026-07-27")
    assert m["per_task"]["s2"]["reliability_pct"] == 0
    assert m["category_balance"]["work"]["completion_pct"] == 100


def test_category_parsed_from_add_directive():
    st = {"static_tasks": [], "dynamic_daily": [], "dynamic_weekly": []}
    d = engine.parse_directives(["/add morning walk #health"])
    engine.apply_additions(st, d, "2026-07-27")
    assert st["dynamic_daily"][0]["text"] == "morning walk"
    assert st["dynamic_daily"][0]["category"] == "health"


def test_weekly_log_written_on_sunday_reset():
    st = {
        "static_tasks": [{"id": "s1", "text": "A", "type": "do", "consecutive_misses": 0}],
        "dynamic_daily": [], "dynamic_weekly": [], "history": [],
        "streak": 0, "best_streak": 0, "total_points": 0,
    }
    engine.apply_additions(st, {"week_add": ["taxes", "adr"]}, "2026-07-28")
    engine.build_today(st, "2026-08-02")  # Sunday
    engine.score_day(st, engine.parse_directives(["/done all", "/done w1"]))

    log = st["weekly_log"][-1]
    assert log["total"] == 2 and log["completed"] == 1 and log["missed"] == 1
    assert metrics.compute(st, "2026-08-02")["weekly_commitment"]["rate_pct"] == 50
