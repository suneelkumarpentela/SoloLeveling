"""Unit tests for the v1 -> v2 schema migration. Run: python -m pytest tests/ -q"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import migrate_v2  # noqa: E402


def old_schema_state():
    return {
        "static_tasks": [
            {"id": "s1", "text": "Brush before 8 AM", "type": "do"},
            {"id": "s2", "text": "No corn", "type": "avoid"},
            {"id": "s5", "text": "Soak dry fruits", "type": "do"},
        ],
        "pending_additions": [],
        "today": {
            "date": "2026-07-26",
            "items": [
                {"id": "s1", "text": "Brush before 8 AM", "type": "do", "done": True},
                {"id": "s2", "text": "No corn", "type": "avoid", "done": False},
                {"id": "s5", "text": "Soak dry fruits", "type": "do", "done": False},
            ],
        },
        "weekly_tasks": [
            {"id": "w1", "text": "Telegram integration", "done": False},
            {"id": "w17847476900", "text": "file taxes", "done": False},
        ],
        "telegram_inbox": [{"message_id": 1, "date": 0, "text": "/start"}],
        "telegram_outbox": [{"text": "old test message", "sent": True}],
        "history": [
            {"date": "2026-07-21", "completed": 3, "total": 4, "streak": 0},
        ],
        "weekly_summaries": [],
        "streak": 3,
        "best_streak": 3,
        "total_points": 19,
        "last_day_qualified": True,
        "last_processed_date": "2026-07-25",
    }


def test_already_migrated_detects_v2_schema():
    assert not migrate_v2.already_migrated(old_schema_state())
    assert migrate_v2.already_migrated({"dynamic_weekly": []})


def test_weekly_tasks_renumbered_into_dynamic_weekly():
    new = migrate_v2.migrate(old_schema_state())
    ids = [t["id"] for t in new["dynamic_weekly"]]
    assert ids == ["w1", "w2"], "legacy epoch-based ids must be renumbered cleanly"
    assert new["dynamic_weekly"][1]["text"] == "file taxes"
    assert "weekly_tasks" not in new


def test_id_seq_seeded_past_renumbered_weekly_tasks():
    new = migrate_v2.migrate(old_schema_state())
    assert new["id_seq"] == {"d": 0, "w": 2}


def test_static_tasks_gain_new_fields():
    new = migrate_v2.migrate(old_schema_state())
    for task in new["static_tasks"]:
        assert task["consecutive_misses"] == 0
        assert task["category"] == "uncategorised"
        assert task["last_completed"] is None
    # originals must be untouched
    assert new["static_tasks"][0]["text"] == "Brush before 8 AM"


def test_retired_fields_are_removed():
    new = migrate_v2.migrate(old_schema_state())
    assert "last_day_qualified" not in new
    assert "pending_additions" not in new


def test_history_streak_and_points_are_preserved_exactly():
    new = migrate_v2.migrate(old_schema_state())
    assert new["history"] == [
        {"date": "2026-07-21", "completed": 3, "total": 4, "streak": 0}
    ]
    assert new["streak"] == 3
    assert new["best_streak"] == 3
    assert new["total_points"] == 19


def test_today_snapshot_reshaped_with_ref_key_and_done_preserved():
    new = migrate_v2.migrate(old_schema_state())
    items = {i["ref"]: i for i in new["today"]["items"]}
    assert items["s1"]["done"] is True, "prior completion must survive the reshape"
    assert items["s2"]["done"] is False
    assert items["s1"]["kind"] == "static"
    assert "id" not in items["s1"], "new items are keyed by 'ref', not 'id'"


def test_inbox_and_outbox_cleared():
    new = migrate_v2.migrate(old_schema_state())
    assert new["telegram_inbox"] == []
    assert new["telegram_outbox"] == []


def test_migration_is_idempotent_on_the_result():
    new = migrate_v2.migrate(old_schema_state())
    assert migrate_v2.already_migrated(new)


def test_cadence_map_seeds_every_days_and_next_due():
    new = migrate_v2.migrate(old_schema_state())
    tasks = {t["id"]: t for t in new["static_tasks"]}
    assert tasks["s5"]["every_days"] == 2, "CADENCE override must apply"
    assert tasks["s5"]["next_due"] is None
    assert tasks["s1"]["every_days"] == 1, "anything not in CADENCE is daily"


def test_cadence_is_seeded_before_today_is_rebuilt():
    """Regression risk called out explicitly: if build_today() ran before
    every_days/next_due were set on the tasks, the rebuilt snapshot would
    bake in the wrong (default) cadence for the very first post-migration
    message. Confirm the item in the rebuilt "today" reflects the real
    seeded cadence, not the default."""
    new = migrate_v2.migrate(old_schema_state())
    item = next(i for i in new["today"]["items"] if i["ref"] == "s5")
    assert item["every_days"] == 2, \
        "the rebuilt snapshot must see the seeded cadence, not the pre-seed default"
