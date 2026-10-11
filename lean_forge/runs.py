"""Run log: one directory per workflow run with a JSONL step log and an outcome. It is the dashboard."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from pathlib import Path

from lean_forge import git
from lean_forge.primitives import Refused


class Run:
    def __init__(self, root: Path, workflow: str, **args) -> None:
        self.root = root
        self.workflow = workflow
        self.id = time.strftime("%Y%m%d-%H%M%S-") + os.urandom(2).hex()
        self.dir = git.state_dir(root) / "runs" / self.id
        self.dir.mkdir(parents=True)
        self.cost_usd = 0.0
        self.tokens = 0
        self.started = time.time()
        self.held = []
        self.log("start", workflow=workflow, branch=git.branch(root), head=git.head(root), cwd=str(root), **args)

    def log(self, step: str, **data) -> None:
        self.cost_usd += float(data.get("cost_usd") or 0)
        self.tokens += int(data.get("tokens") or 0)
        entry = {"t": round(time.time() - self.started, 2), "step": step, **data}
        with open(self.dir / "log.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    def hold(self, path: Path | None = None, *, wait: int = 0, what: str = "this worktree") -> None:
        """Flock `path` (default: one run per worktree, in its own git dir) until the outcome is written, or refuse.

        The kernel's lock is the state: the file only names the holder, and a killed run's lock dies with its process."""
        path = path or Path(git.git("rev-parse", "--path-format=absolute", "--git-dir", cwd=self.root).strip()) / "lean-forge.lock"
        fh = None
        try:
            fh = open(path, "a+")  # append mode, so a refused run cannot wipe the holder's name
            for attempt in range(wait // 2 + 1):
                time.sleep(2 if attempt else 0)
                with contextlib.suppress(BlockingIOError):
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
            else:
                with fh:
                    fh.seek(0)
                    raise Refused(f"{what} is held by run {fh.read().strip() or '?'}; wait for its outcome or stop it, then rerun")
            fh.truncate(0)
            print(self.id, file=fh, flush=True)
        except OSError as exc:
            if fh:
                fh.close()
            raise Refused(f"cannot lock {path}: {exc}") from None
        self.held.append(fh)

    def finish(self, status: str, **data) -> dict:
        outcome = {
            "run": self.id, "workflow": self.workflow, "status": status,
            "cost_usd": round(self.cost_usd, 4), "tokens": self.tokens,
            "seconds": round(time.time() - self.started, 1), **data,
        }
        self.log("finish", status=status)
        (self.dir / "outcome.json").write_text(json.dumps(outcome, indent=2, sort_keys=True) + "\n")
        for fh in self.held:  # released only once the outcome exists
            fh.close()
        return outcome


def recent(root: Path, limit: int = 20) -> list[dict]:
    base = git.state_dir(root) / "runs"
    rows = []
    for run_dir in sorted(base.glob("*"), reverse=True)[:limit] if base.exists() else []:
        outcome = run_dir / "outcome.json"
        rows.append(json.loads(outcome.read_text()) if outcome.exists() else {"run": run_dir.name, "status": "running"})
    return rows


def load(root: Path, run_id: str) -> tuple[dict | None, list[dict]]:
    run_dir = git.state_dir(root) / "runs" / run_id
    if not run_dir.exists():
        raise FileNotFoundError(f"no run {run_id}")
    outcome = json.loads((run_dir / "outcome.json").read_text()) if (run_dir / "outcome.json").exists() else None
    steps = [json.loads(line) for line in (run_dir / "log.jsonl").read_text().splitlines() if line]
    return outcome, steps
