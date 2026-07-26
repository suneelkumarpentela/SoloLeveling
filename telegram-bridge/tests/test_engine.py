"""Unit tests for the scoring engine. Run: python -m pytest tests/ -q"""

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import engine  # noqa: E402


def base_state():
    return {
        "static_tasks": [
            {"id": "s1", "text": "Brush", "type": "do", "consecutive_misses": 0, "last_completed": None},
            {"id": "s2", "text": "Productivity", "type": "do", "consecutive_misses": 0, "last_completed": None},
            {"id": "s3", "text": "Knee exercise", "type": "do", "consecutive_misses": 0, "last_completed": None},
            {"id": "s4", "text": "No corn", "type": "avoid", "consecutive_misses": 0, "last_completed": None},
        ],
        "dynamic_daily": [],
        "dynamic_weekly": [],
        "today": {"date": None, "items": []},
        "history": [],
        "weekly_summaries": [],
        "streak": 0,
        "best_streak": 0,
        "total_points": 0,
        "last_processed_date": None,
    }


def run_day(state, date, reply_texts):
    """Score `date` from replies, then build the next snapshot."""
    d = engine.parse_directives(reply_texts)
    summary = engine.score_day(state, d)
    engine.apply_additions(state, d, date)
    return summary


# ─────────────────────────────── parsing ─────────────────────────────────────


def test_parses_directives_across_messages():
    d = engine.parse_directives(
        ["/done s1, s3", "/add call dentist; buy milk", "/week file taxes"]
    )
    assert d["done"] == ["s1", "s3"]
    assert d["add"] == ["call dentist", "buy milk"]
    assert d["week_add"] == ["file taxes"]


def test_done_accumulates_across_the_day():
    """The noon mark must survive the evening reply. Regression: it did not."""
    d = engine.parse_directives(["/done s1", "/add a", "/done s2,s3", "/add b"])
    assert d["done"] == ["s1", "s2", "s3"], "noon mark must not be discarded"
    assert d["add"] == ["a", "b"]


def test_undo_removes_a_mark():
    d = engine.parse_directives(["/done s1 s2", "/undo s1"])
    assert d["done"] == ["s2"]
    assert d["undone"] == ["s1"]


def test_done_after_undo_remarks_it():
    d = engine.parse_directives(["/done s1", "/undo s1", "/done s1"])
    assert d["done"] == ["s1"]
    assert d["undone"] == []


def test_all_done_then_undo_means_everything_except():
    st = base_state()
    engine.build_today(st, "2026-07-27")
    run_day(st, "2026-07-27", ["/done all", "/undo s3"])
    row = st["history"][-1]
    assert row["missed"] == ["s3"]
    assert row["completed"] == 3


def test_done_none_resets_everything_marked():
    d = engine.parse_directives(["/done s1 s2", "/done w1", "/done none"])
    assert d["done"] == [] and d["week_done"] == [] and d["all_done"] is False


def test_all_done_flag():
    assert engine.parse_directives(["/done all"])["all_done"] is True


def test_prose_is_reported_not_read_as_a_completion():
    d = engine.parse_directives(["Hi Sun Jinwoo", "Solo Leveling start", "/start"])
    assert d["done"] == [] and d["all_done"] is False
    # /start is Telegram boilerplate, not a typo worth reporting back
    assert d["unparsed"] == ["Hi Sun Jinwoo", "Solo Leveling start"]


def test_malformed_done_is_reported_and_preserves_earlier_marks():
    d = engine.parse_directives(["/done s1 s2", "/done the knee thing"])
    assert d["done"] == ["s1", "s2"], "a typo must not discard real marks"
    assert d["unparsed"] == ["/done the knee thing"]


def test_malformed_undo_is_reported():
    d = engine.parse_directives(["/done s1", "/undo the knee thing"])
    assert d["done"] == ["s1"]
    assert d["unparsed"] == ["/undo the knee thing"]


def test_weekly_only_done_does_not_wipe_daily_marks():
    d = engine.parse_directives(["/done all", "/done w1"])
    assert d["all_done"] is True
    assert d["week_done"] == ["w1"]


def test_unknown_command_is_reported():
    d = engine.parse_directives(["/dnoe s1"])
    assert d["unparsed"] == ["/dnoe s1"]


def test_several_commands_in_one_message():
    d = engine.parse_directives(["/done s1 s2\n/add buy milk\n/week file taxes"])
    assert d["done"] == ["s1", "s2"]
    assert d["add"] == ["buy milk"]
    assert d["week_add"] == ["file taxes"]


def test_multiline_add_payload():
    d = engine.parse_directives(["/add call dentist\nbuy milk\nfix bike"])
    assert d["add"] == ["call dentist", "buy milk", "fix bike"]


def test_comma_does_not_split_task_text():
    d = engine.parse_directives(["/add call mom, then the dentist"])
    assert d["add"] == ["call mom, then the dentist"], \
        "commas separate ids, not free text"


def test_pasted_ticked_checklist():
    d = engine.parse_directives(
        [
            "- [x] Brush (s1)\n"
            "- [ ] Knee exercise (s3)\n"
            "- [x] No corn (s4)\n"
            "- [x] file taxes (w1)"
        ]
    )
    assert d["done"] == ["s1", "s4"]
    assert d["week_done"] == ["w1"]


def test_botname_suffix_is_stripped():
    d = engine.parse_directives(["/done@myremindbot s1 s2"])
    assert d["done"] == ["s1", "s2"]


def test_unparsed_is_surfaced_in_the_message():
    lines = engine.render_unparsed({"unparsed": ["did the knee thing"]})
    assert any("not understood" in line for line in lines)
    assert any("did the knee thing" in line for line in lines)
    assert engine.render_unparsed({"unparsed": []}) == []


def test_help_uses_real_ids_not_placeholders():
    st = base_state()
    engine.apply_additions(st, {"add": ["pay bill"]}, "2026-07-27")
    engine.build_today(st, "2026-07-27")
    msg = engine.render_morning(st, None)

    assert "/done s1 s2 — mark done" in msg, "example must use the user's own ids"
    assert "/drop d1 — remove an added task" in msg
    assert "<task>" in msg, "add/week still need a placeholder, they take free text"


def test_help_falls_back_when_there_are_no_tasks():
    st = {"static_tasks": [], "dynamic_daily": [], "dynamic_weekly": [],
          "today": {"date": "2026-07-27", "items": []}}
    lines = engine.render_help(st)
    assert any("/done s1 s2" in line for line in lines)


def test_help_sits_at_the_end_so_the_checklist_stays_on_top():
    st = base_state()
    engine.build_today(st, "2026-07-27")
    msg = engine.render_morning(st, None)
    assert msg.index("Brush (s1)") < msg.index("mark done")


def test_evening_uses_the_compact_one_liner():
    st = base_state()
    engine.build_today(st, "2026-07-27")
    morning = engine.render_morning(st, None)
    evening = engine.render_evening(st)

    assert "— mark done" in morning, "morning carries the full reference"
    assert "— mark done" not in evening, "evening must not repeat it"
    assert "/done s1 s2 ·" in evening

    # The help must stay a small fraction of the message.
    assert len(engine.render_help(st)) <= 7
    assert len(engine.render_help(st, compact=True)) == 2


def test_telegram_cannot_remove_a_static_task():
    d = engine.parse_directives(["/drop s1, d2, w3"])
    assert d["remove"] == ["d2", "w3"]


def test_stray_prose_before_a_command_is_still_reported():
    """Regression: a leading non-command line was silently swallowed instead
    of being reported, when it preceded a real command in the same message."""
    d = engine.parse_directives(["hey by the way\n/add buy milk"])
    assert d["add"] == ["buy milk"]
    assert d["unparsed"] == ["hey by the way"], \
        "leading prose must not be silently dropped just because a command follows"


def test_checklist_paste_with_trailing_command_still_applies_the_command():
    """Regression: any line in a message that didn't match the checkbox
    pattern was discarded without a trace once the message was recognised as
    a pasted checklist, even if it was a real command."""
    d = engine.parse_directives(
        ["- [x] Brush (s1)\n- [ ] Knee exercise (s3)\n/add buy milk"]
    )
    assert d["done"] == ["s1"]
    assert d["undone"] == ["s3"]
    assert d["add"] == ["buy milk"], \
        "a command alongside a pasted checklist must still be applied"


# ─────────────────────────────── streak rules ────────────────────────────────


def test_full_completion_grows_streak():
    st = base_state()
    engine.build_today(st, "2026-07-27")  # Monday
    s = run_day(st, "2026-07-27", ["/done all"])
    assert (s["completed"], s["total"]) == (4, 4)
    assert st["streak"] == 1
    assert st["total_points"] == 4


def test_incomplete_but_non_breaking_day_holds_streak():
    st = base_state()
    st["streak"] = 5
    st["best_streak"] = 5
    engine.build_today(st, "2026-07-27")
    run_day(st, "2026-07-27", ["/done s1"])
    # 1 of 4 done, but nothing missed twice yet -> hold, not grow, not reset
    assert st["streak"] == 5
    assert st["total_points"] == 1


def test_two_consecutive_misses_breaks_streak_then_counter_resets():
    st = base_state()
    st["streak"] = 3

    engine.build_today(st, "2026-07-27")
    run_day(st, "2026-07-27", ["/done s1,s2,s4"])  # s3 missed once
    assert st["streak"] == 3, "one miss must not break"
    assert engine._find_daily(st, "s3")["consecutive_misses"] == 1

    engine.build_today(st, "2026-07-28")
    assert any(i["ref"] == "s3" and i["warn"] for i in st["today"]["items"]), \
        "s3 should be flagged on its last-chance day"

    run_day(st, "2026-07-28", ["/done s1,s2,s4"])  # s3 missed twice
    assert st["streak"] == 0
    assert engine._find_daily(st, "s3")["consecutive_misses"] == 0, \
        "counter must reset so one task cannot pin the streak at zero"


def test_completion_resets_miss_counter():
    st = base_state()
    engine.build_today(st, "2026-07-27")
    run_day(st, "2026-07-27", ["/done s1,s2,s4"])
    assert engine._find_daily(st, "s3")["consecutive_misses"] == 1
    engine.build_today(st, "2026-07-28")
    run_day(st, "2026-07-28", ["/done all"])
    assert engine._find_daily(st, "s3")["consecutive_misses"] == 0
    assert engine._find_daily(st, "s3")["last_completed"] == "2026-07-28"


# ─────────────────────────── dynamic task lifecycle ─────────────────────────


def test_dropped_dynamic_task_is_excluded_from_scoring():
    """Regression: a task dropped the same night it was due still got scored
    against yesterday's snapshot (which pre-dates the drop), recording it as
    missed and letting it contribute to a streak break."""
    st = base_state()
    engine.apply_additions(st, {"add": ["call dentist"]}, "2026-07-26")
    engine.build_today(st, "2026-07-27")
    assert any(i["ref"] == "d1" for i in st["today"]["items"])

    d = engine.parse_directives(["/done s1,s2,s4", "/drop d1"])
    summary = engine.score_day(st, d)
    engine.apply_additions(st, d, "2026-07-27")

    row = st["history"][-1]
    assert row["total"] == 4, "a dropped task must not count toward total due"
    assert "d1" not in row["missed"], "a dropped task must not be recorded as missed"
    assert "d1" not in row["done"]
    assert summary["broke"] is False, "dropping a task must not break the streak"
    assert st["dynamic_daily"] == [], "the dropped task must leave the pool"


def test_done_on_nonexistent_id_is_reported_not_silently_dropped():
    """Regression: '/done s9' for a task that doesn't exist in today's
    snapshot was silently a no-op - indistinguishable from a typo that just
    never got applied, with no feedback to the user."""
    st = base_state()
    engine.build_today(st, "2026-07-27")
    d = engine.parse_directives(["/done s1,s2,s4,s9"])  # s9 does not exist

    engine.score_day(st, d)

    row = st["history"][-1]
    assert row["missed"] == ["s3"], "real scoring must be unaffected"
    assert any("s9" in u for u in d["unparsed"]), \
        "an id that matches no task must be surfaced, not silently ignored"


def test_done_week_on_nonexistent_id_is_reported():
    st = base_state()
    engine.apply_additions(st, {"week_add": ["file taxes"]}, "2026-07-27")
    engine.build_today(st, "2026-07-27")
    d = engine.parse_directives(["/done w9"])  # only w1 exists

    engine.score_day(st, d)

    assert any("w9" in u for u in d["unparsed"])


def test_dynamic_daily_carries_until_done_then_disappears():
    st = base_state()
    engine.apply_additions(st, {"add": ["call dentist"]}, "2026-07-27")
    assert st["dynamic_daily"][0]["id"] == "d1"

    engine.build_today(st, "2026-07-28")
    assert any(i["ref"] == "d1" for i in st["today"]["items"])
    run_day(st, "2026-07-28", ["/done s1,s2,s3,s4"])  # d1 missed

    engine.build_today(st, "2026-07-29")
    assert any(i["ref"] == "d1" for i in st["today"]["items"]), "should carry over"

    run_day(st, "2026-07-29", ["/done all"])
    assert st["dynamic_daily"] == [], "completed one-off should leave the pool"


def test_ids_never_collide_after_removal():
    st = base_state()
    engine.apply_additions(st, {"add": ["a", "b"]}, "2026-07-27")
    assert [t["id"] for t in st["dynamic_daily"]] == ["d1", "d2"]
    engine.apply_additions(st, {"remove": ["d1"]}, "2026-07-27")
    engine.apply_additions(st, {"add": ["c"]}, "2026-07-27")
    ids = [t["id"] for t in st["dynamic_daily"]]
    assert len(ids) == len(set(ids)) == 2


def test_removed_dynamic_id_is_never_reused():
    """Regression: ids were assigned by scanning for the lowest unused gap in
    the CURRENT pools, so a completed-and-removed task's id (e.g. d1) was
    handed to the next unrelated /add. That silently conflates two different
    tasks' historical metrics under one id."""
    st = base_state()
    engine.apply_additions(st, {"add": ["call dentist"]}, "2026-07-27")
    assert st["dynamic_daily"][0]["id"] == "d1"

    engine.build_today(st, "2026-07-28")
    run_day(st, "2026-07-28", ["/done all"])  # d1 completed, leaves the pool
    assert st["dynamic_daily"] == []

    engine.apply_additions(st, {"add": ["buy groceries"]}, "2026-07-29")
    assert st["dynamic_daily"][0]["id"] != "d1", \
        "a completed task's id must never be reissued to an unrelated task"


# ──────────────────────────────── weekly ─────────────────────────────────────


def test_open_weekly_task_on_sunday_breaks_streak_and_resets_list():
    st = base_state()
    st["streak"] = 9
    engine.apply_additions(st, {"week_add": ["file taxes"]}, "2026-07-27")
    engine.build_today(st, "2026-08-02")  # Sunday
    s = run_day(st, "2026-08-02", ["/done all"])
    assert s["sunday"] is True
    assert st["streak"] == 0, "unfinished weekly task must break the streak"
    assert st["dynamic_weekly"] == [], "weekly list resets for the new week"


def test_weekly_done_awards_two_points_and_does_not_break():
    st = base_state()
    engine.apply_additions(st, {"week_add": ["file taxes"]}, "2026-07-27")
    engine.build_today(st, "2026-08-02")  # Sunday
    before = st["total_points"]
    run_day(st, "2026-08-02", ["/done all", "/done w1"])
    assert st["streak"] == 1
    assert st["total_points"] == before + 4 + 2  # 4 daily + weekly bonus


def test_weekly_not_evaluated_midweek():
    st = base_state()
    st["streak"] = 2
    engine.apply_additions(st, {"week_add": ["file taxes"]}, "2026-07-27")
    engine.build_today(st, "2026-07-29")  # Wednesday
    run_day(st, "2026-07-29", ["/done all"])
    assert st["streak"] == 3, "open weekly task must not matter midweek"
    assert len(st["dynamic_weekly"]) == 1


# ──────────────────────────── rendering / safety ─────────────────────────────


def test_morning_message_shows_warning_and_ids():
    st = base_state()
    engine.build_today(st, "2026-07-27")
    run_day(st, "2026-07-27", ["/done s1,s2,s4"])
    engine.build_today(st, "2026-07-28")
    msg = engine.render_morning(st, {"scored": False})
    assert "Knee exercise (s3)" in msg
    assert engine.WARN_TEXT in msg
    assert "No corn (s4)" in msg


def test_morning_message_does_not_segregate_do_and_avoid():
    """The task text itself says what kind of action it is - no headers."""
    st = base_state()
    engine.build_today(st, "2026-07-27")
    msg = engine.render_morning(st, None)
    assert "Avoid:" not in msg
    assert "Do:" not in msg
    assert "No corn (s4)" in msg and "Brush (s1)" in msg


def test_scoring_does_not_mutate_static_task_text():
    st = base_state()
    original = copy.deepcopy(st["static_tasks"])
    engine.build_today(st, "2026-07-27")
    run_day(st, "2026-07-27", ["/done all"])
    assert [t["text"] for t in st["static_tasks"]] == [t["text"] for t in original]
    assert len(st["static_tasks"]) == 4


def test_no_snapshot_is_a_no_op():
    st = base_state()
    s = engine.score_day(st, engine.parse_directives(["/done all"]))
    assert s["scored"] is False
    assert st["streak"] == 0 and st["history"] == []
