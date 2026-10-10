"""Run log: one directory per workflow run with a JSONL step log and an outcome. It is the dashboard."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from lean_forge import git


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
        self.log("start", workflow=workflow, branch=git.branch(root), head=git.head(root), cwd=str(root), **args)

    def log(self, step: str, **data) -> None:
        self.cost_usd += float(data.get("cost_usd") or 0)
        self.tokens += int(data.get("tokens") or 0)
        entry = {"t": round(time.time() - self.started, 2), "step": step, **data}
        with open(self.dir / "log.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    def finish(self, status: str, **data) -> dict:
        outcome = {
            "run": self.id, "workflow": self.workflow, "status": status,
            "cost_usd": round(self.cost_usd, 4), "tokens": self.tokens,
            "seconds": round(time.time() - self.started, 1), **data,
        }
        self.log("finish", status=status)
        (self.dir / "outcome.json").write_text(json.dumps(outcome, indent=2, sort_keys=True) + "\n")
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
