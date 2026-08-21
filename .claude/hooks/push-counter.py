#!/usr/bin/env python3
"""PostToolUse hook: count successful `git push` calls, fire a checkpoint every 10.

Skills are model-invoked and cannot fire on an event, so the counting and
triggering live here, in a hook the harness runs deterministically after every
Bash call. When the count reaches a multiple of CHECKPOINT_EVERY this asks
Claude to run the `push-checkpoint` skill.

Exits 0 and stays silent for anything that is not a real, successful push.
"""

import json
import os
import re
import sys

CHECKPOINT_EVERY = 10

# Markers that mean the push did not actually land.
FAILURE_MARKERS = re.compile(
    r"\b(rejected|fatal:|error:|failed to push|permission denied|could not read)",
    re.IGNORECASE,
)
# A push that moved nothing is not a push.
NOOP_MARKERS = re.compile(r"everything up[- ]to[- ]date", re.IGNORECASE)


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
    # Dry runs and help text never move a ref.
    if "--dry-run" in command or "-n " in command or "--help" in command:
        return False
    return True


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
    if NOOP_MARKERS.search(text):
        return False
    return True


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
    state_dir = os.path.join(project_dir, ".claude", "state")
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
    except OSError:
        return 0  # A read-only checkout should not break pushes.

    if count % CHECKPOINT_EVERY == 0:
        emit(
            f"Push #{count} just landed, which is a multiple of {CHECKPOINT_EVERY}. "
            "Run the `push-checkpoint` skill now (Skill tool, skill: \"push-checkpoint\") "
            "before continuing with other work. It reviews everything since the last "
            "checkpoint and runs the test/lint pass."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
