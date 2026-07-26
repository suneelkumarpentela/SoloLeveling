"""
Repo hygiene, not scoring correctness. `daily_tasks.json` is a one-way local
mirror of the Notion page, written daily by a separate Claude Cowork task (see
README.md "Local mirror"). It holds real personal task/health text and this
repo is public, so it must never be staged, tracked, or appear in history -
a `.gitignore` entry alone is one `git add -f` away from a permanent leak.

Run: python -m pytest tests/ -q
"""

import os
import subprocess

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
MIRROR_FILE = "daily_tasks.json"


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_daily_tasks_json_is_gitignored():
    gitignore_path = os.path.join(REPO_ROOT, ".gitignore")
    with open(gitignore_path, encoding="utf-8") as f:
        assert MIRROR_FILE in f.read(), \
            f"{MIRROR_FILE} must be listed in the repo-root .gitignore"


def test_daily_tasks_json_has_never_been_committed():
    # Requires full history (fetch-depth: 0 in CI) - a shallow clone would
    # make this pass vacuously, which is worse than not checking at all.
    depth = _git("rev-list", "--count", "HEAD")
    assert int(depth) > 1, \
        "history looks shallow - this check needs fetch-depth: 0 to mean anything"

    hits = _git("log", "--all", "--oneline", "--", MIRROR_FILE)
    assert hits == "", (
        f"{MIRROR_FILE} appears in git history - it must never be committed "
        "to this public repo. If this fires, treat it as a live leak: rotate "
        "any exposed data source and scrub history, don't just delete the file."
    )


def test_daily_tasks_json_is_not_currently_staged_or_tracked():
    tracked = _git("ls-files", "--", MIRROR_FILE)
    assert tracked == "", f"{MIRROR_FILE} must never be a tracked file"

    status = _git("status", "--porcelain", "--", MIRROR_FILE)
    # "??" (untracked) is fine - anything staged (A/M/etc. in column 1) is not.
    assert status == "" or status.startswith("??"), \
        f"{MIRROR_FILE} must not be staged for commit"
