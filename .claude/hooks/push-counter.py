#!/usr/bin/env python3
"""PostToolUse hook: watch successful `git push` calls and fire quality gates.

Skills are model-invoked and cannot fire on an event, so the counting,
measuring, and triggering live here, in a hook the harness runs
deterministically after every Bash call.

Two independent gates, either or both of which can fire on one push:

* **push-checkpoint** -- every CHECKPOINT_EVERY successful pushes.
* **verify** -- any push moving more than LARGE_PUSH_LINES changed lines.

Exits 0 and stays silent for anything that is not a real, successful push, and
for any git command that does not cooperate. A quality gate is never worth
breaking a push over.
"""

import json
import os
import re
import subprocess
import sys

CHECKPOINT_EVERY = 10
LARGE_PUSH_LINES = 100

GIT_TIMEOUT = 10

# Markers that mean the push did not actually land.
FAILURE_MARKERS = re.compile(
    r"\b(rejected|fatal:|error:|failed to push|permission denied|could not read)",
    re.IGNORECASE,
)
# A push that moved nothing is not a push.
NOOP_MARKERS = re.compile(r"everything up[- ]to[- ]date", re.IGNORECASE)

SHORTSTAT = re.compile(r"(\d+) insertions?\(\+\)|(\d+) deletions?\(-\)")


def emit(context: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": context,
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")


def is_push(command: str) -> bool:
    if not re.search(r"\bgit\b[^|;&]*\bpush\b", command):
        return False
    return not ("--dry-run" in command or "-n " in command or "--help" in command)


def succeeded(response) -> bool:
    """A push counts only if nothing in the output says it failed or no-opped."""
    if isinstance(response, dict):
        if response.get("interrupted"):
            return False
        text = " ".join(
            str(response.get(key, "")) for key in ("stdout", "stderr", "output", "error")
        )
    else:
        text = str(response)

    if FAILURE_MARKERS.search(text):
        return False
    return not NOOP_MARKERS.search(text)


def git(project_dir: str, *args: str) -> str | None:
    """Run a git command, returning stripped stdout or None if it did not work."""
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def pushed_span(project_dir: str) -> tuple[str, str] | None:
    """The (old, new) commits this push moved the upstream branch across.

    Read from the remote-tracking ref's reflog, which git updates as part of a
    successful push. On a branch's first push there is no previous value, so
    fall back to the merge base with the default branch -- everything the branch
    added is what was pushed.
    """
    upstream = git(project_dir, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if not upstream:
        return None

    new = git(project_dir, "rev-parse", upstream)
    if not new:
        return None

    old = git(project_dir, "rev-parse", f"{upstream}@{{1}}")
    if not old or old == new:
        old = None
        for candidate in ("origin/HEAD", "origin/main", "origin/master"):
            old = git(project_dir, "merge-base", candidate, new)
            if old:
                break

    if not old or old == new:
        return None
    return old, new


def changed_lines(project_dir: str, old: str, new: str) -> int | None:
    """Insertions plus deletions between two commits."""
    stat = git(project_dir, "diff", "--shortstat", f"{old}..{new}")
    if stat is None:
        return None
    return sum(int(ins or dele or 0) for ins, dele in SHORTSTAT.findall(stat))


def bump_counter(state_dir: str) -> int | None:
    counter_path = os.path.join(state_dir, "push-count")
    try:
        os.makedirs(state_dir, exist_ok=True)
        try:
            with open(counter_path) as handle:
                count = int(handle.read().strip() or "0")
        except (FileNotFoundError, ValueError):
            count = 0

        count += 1
        with open(counter_path, "w") as handle:
            handle.write(f"{count}\n")
        return count
    except OSError:
        return None  # A read-only checkout should not break pushes.


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # Never break the session over a malformed payload.

    if payload.get("tool_name") != "Bash":
        return 0

    command = str(payload.get("tool_input", {}).get("command", ""))
    if not is_push(command):
        return 0
    if not succeeded(payload.get("tool_response", "")):
        return 0

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    count = bump_counter(os.path.join(project_dir, ".claude", "state"))

    triggers: list[str] = []

    if count is not None and count % CHECKPOINT_EVERY == 0:
        triggers.append(
            f"Push #{count} just landed, which is a multiple of {CHECKPOINT_EVERY}. "
            'Run the `push-checkpoint` skill now (Skill tool, skill: "push-checkpoint") '
            "before continuing with other work. It reviews everything since the last "
            "checkpoint and runs the test/lint pass."
        )

    span = pushed_span(project_dir)
    if span:
        lines = changed_lines(project_dir, *span)
        if lines is not None and lines > LARGE_PUSH_LINES:
            old, new = span
            triggers.append(
                f"That push moved {lines} changed lines (insertions + deletions), "
                f"over the {LARGE_PUSH_LINES}-line threshold for a large push. "
                'Run the `verify` skill now (Skill tool, skill: "verify") on the range '
                f"{old[:12]}..{new[:12]} before continuing with other work."
            )

    if triggers:
        emit("\n\n".join(triggers))
    return 0


if __name__ == "__main__":
    sys.exit(main())
