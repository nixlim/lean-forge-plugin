"""Append-only evidence, keyed by git tree id. Facts, never state: nothing is updated or deleted.

Effectful primitives check evidence for the exact tree they act on. That is the one guarantee
lean-forge keeps: it lets the primitives compose in any order while unsafe orders simply fail.
"""

from __future__ import annotations

import fcntl
import json
import time
from pathlib import Path

from lean_forge import git


def _path(root: Path) -> Path:
    return git.state_dir(root) / "evidence.jsonl"


def add(root: Path, **record) -> dict:
    record["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(_path(root), "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def find(root: Path, **match) -> dict | None:
    """Most recent record whose fields equal every given key, or None."""
    path = _path(root)
    if not path.exists():
        return None
    found = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if all(record.get(key) == value for key, value in match.items()):
                found = record
    return found
