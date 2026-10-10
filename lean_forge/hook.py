"""Claude Code PreToolUse hook: deny raw history writes and operator-only actions.

Deliberately crude. Any `git` word followed by a `commit` or `push` word in the same shell segment is
treated as a write, so it errs toward denial and models no shell grammar. Segments that invoke
lean-forge itself are exempt from the history rule, so a commit message that mentions git is fine.
Commits and pushes made by lean-forge run as subprocesses and never pass through this hook.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from lean_forge import config as configuration
from lean_forge import git

REASON = ("lean-forge: commits and pushes go through the gate chain in this repository. "
          "Use `lean-forge commit -m \"...\"` and `lean-forge land`.")
OPERATOR_REASON = ("lean-forge: approve and halt --off are operator actions "
                   "to be run by a human in their own terminal.")
SEGMENTS = re.compile(r"[;&|\n()`]|\$\(")
WRITES = ("commit", "push")


def _word(token: str) -> str:
    return token.strip("'\"").rsplit("/", 1)[-1]


def writes_history(command: str) -> bool:
    for segment in SEGMENTS.split(command):
        words = [_word(t) for t in segment.split()]
        if not words or words[0] == "lean-forge" or words[:2] in (["python3", "lean-forge"], ["python", "lean-forge"]):
            continue
        for i, word in enumerate(words):
            if word == "git" and any(w in WRITES for w in words[i + 1:]):
                return True
    return False


def operator_action(command: str) -> bool:
    """`approve` and `halt --off` belong to a human, however lean-forge is invoked."""
    for segment in SEGMENTS.split(command):
        words = [_word(t) for t in segment.split()]
        if "lean-forge" not in words:
            continue
        rest = words[words.index("lean-forge") + 1:]
        while rest and rest[0].startswith("-"):
            rest = rest[2:] if rest[0] == "-C" else rest[1:]
        if rest and (rest[0] == "approve" or (rest[0] == "halt" and "--off" in rest)):
            return True
    return False


def main(stdin=sys.stdin, stdout=sys.stdout) -> int:
    try:
        payload = json.load(stdin)
    except json.JSONDecodeError:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    operator = operator_action(command)
    if not operator and not writes_history(command):
        return 0
    try:
        root = git.toplevel(Path(payload.get("cwd") or "."))
    except (git.GitError, FileNotFoundError):
        return 0
    if not (root / configuration.CONFIG).exists():
        return 0
    try:
        enabled = configuration.load(root)[0]["guard"]["enabled"]
    except configuration.ConfigError:
        enabled = True
    if operator or enabled:
        json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                          "permissionDecisionReason": OPERATOR_REASON if operator else REASON}}, stdout)
    return 0
