"""The push-counter hook.

The hook decides when the quality gates fire, so it needs the same coverage as
the library. Two groups: payload handling (which pushes count), and size
measurement (which pushes are large), the latter against real git repositories
because the logic reads the remote-tracking ref's reflog.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".claude", "hooks", "push-counter.py",
)

PUSH_OK = {"stdout": "", "stderr": "To https://x\n   a..b main -> main"}


def fire(project_dir, count_before=0, command="git push origin main", response=None):
    """Run the hook as the harness would, returning (stdout, counter, returncode)."""
    state = os.path.join(project_dir, ".claude", "state")
    os.makedirs(state, exist_ok=True)
    counter = os.path.join(state, "push-count")
    if count_before is not None:
        with open(counter, "w") as handle:
            handle.write(str(count_before))

    done = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "tool_response": PUSH_OK if response is None else response,
            }
        ),
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)},
    )
    value = Path(counter).read_text().strip() if os.path.exists(counter) else None
    return done.stdout.strip(), value, done.returncode


# --------------------------------------------------------------------------
# Which pushes count
# --------------------------------------------------------------------------


def test_successful_push_increments(tmp_path):
    _, count, code = fire(tmp_path, 3)
    assert count == "4"
    assert code == 0


def test_tenth_push_asks_for_the_checkpoint(tmp_path):
    out, count, _ = fire(tmp_path, 9)
    assert count == "10"
    assert "push-checkpoint" in out


def test_counter_starts_at_one_when_absent(tmp_path):
    _, count, _ = fire(tmp_path, None)
    assert count == "1"


@pytest.mark.parametrize(
    "label, kwargs",
    [
        ("rejected", {"response": {"stderr": "! [rejected] main -> main\nerror: failed"}}),
        ("up to date", {"response": {"stdout": "Everything up-to-date"}}),
        ("interrupted", {"response": {"stderr": "", "interrupted": True}}),
        ("dry run", {"command": "git push --dry-run origin main"}),
        ("not a push", {"command": "git status"}),
    ],
)
def test_non_pushes_do_not_count(tmp_path, label, kwargs):
    out, count, code = fire(tmp_path, 9, **kwargs)
    assert count == "9", f"{label} should not have counted"
    assert out == ""
    assert code == 0


def test_other_tools_are_ignored(tmp_path):
    state = tmp_path / ".claude" / "state"
    state.mkdir(parents=True)
    (state / "push-count").write_text("9")
    done = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps({"tool_name": "Read", "tool_input": {"command": "git push"}}),
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    assert (state / "push-count").read_text().strip() == "9"
    assert done.returncode == 0


def test_malformed_payload_does_not_crash(tmp_path):
    done = subprocess.run(
        [sys.executable, HOOK], input="not json", capture_output=True, text=True
    )
    assert done.returncode == 0
    assert "Traceback" not in done.stderr


def test_non_git_directory_does_not_crash(tmp_path):
    out, count, code = fire(tmp_path, 0)
    assert code == 0
    assert count == "1"


# --------------------------------------------------------------------------
# Which pushes are large
# --------------------------------------------------------------------------


def git(cwd, *args):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A working clone with a real remote, so the reflog behaves normally."""
    bare, work = tmp_path / "remote.git", tmp_path / "work"
    git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    git(tmp_path, "clone", "-q", str(bare), str(work))
    git(work, "config", "user.email", "t@t.test")
    git(work, "config", "user.name", "T")
    # As in the real repo: the counter must never enter a diff, or it would
    # inflate every push's measured size by one line.
    (work / ".gitignore").write_text(".claude/state/\n")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "gitignore")
    git(work, "push", "-q", "-u", "origin", "main")
    return work


def push_lines(repo, name, lines):
    (repo / name).write_text("x\n" * lines)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", f"add {name}")
    git(repo, "push", "-q", "origin", "main")


def test_small_push_does_not_ask_for_verify(repo):
    push_lines(repo, "small.txt", 5)
    out, _, _ = fire(repo, 0)
    assert "verify" not in out


def test_large_push_asks_for_verify_with_the_exact_count(repo):
    push_lines(repo, "big.txt", 250)
    out, _, _ = fire(repo, 0)
    assert "verify" in out
    assert "250 changed lines" in out


def test_threshold_is_strictly_more_than_100(repo):
    push_lines(repo, "exactly.txt", 100)
    out, _, _ = fire(repo, 0)
    assert "verify" not in out, "100 lines is not *more than* 100"

    push_lines(repo, "over.txt", 101)
    out, _, _ = fire(repo, 0)
    assert "verify" in out


def test_deletions_count_toward_the_threshold(repo):
    push_lines(repo, "doomed.txt", 200)
    (repo / "doomed.txt").unlink()
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "remove")
    git(repo, "push", "-q", "origin", "main")
    out, _, _ = fire(repo, 0)
    assert "verify" in out


def test_both_gates_can_fire_on_one_push(repo):
    push_lines(repo, "big.txt", 300)
    out, count, _ = fire(repo, 9)
    assert "verify" in out
    assert "push-checkpoint" in out
    assert count == "10"


def test_failed_large_push_fires_nothing(repo):
    push_lines(repo, "big.txt", 300)
    out, count, _ = fire(repo, 5, response={"stderr": "! [rejected] main -> main"})
    assert out == ""
    assert count == "5"
