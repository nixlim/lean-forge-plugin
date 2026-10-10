"""lean-forge command line. Every verb prints one JSON outcome on stdout and a one-line summary on stderr.

Exit codes: 0 done, 1 blocked/error/halted, 2 needs_direction, 3 awaiting_approval.
"""

from __future__ import annotations

import argparse
import getpass
import json
import signal
import sys
from pathlib import Path

from lean_forge import __version__, git, hook, runs, workflows
from lean_forge import config as configuration
from lean_forge.primitives import classify, halt_reason, review, work
from lean_forge.providers import AgentError
from lean_forge.runs import Run

EXIT = {"needs_direction": 2, "awaiting_approval": 3}
OK = {"committed", "landed", "evaluated", "noop", "ok", "reviewed", "done", "halt"}
FAILURES = (AgentError, git.GitError, configuration.ConfigError)

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "config.toml"


def _text(value: str) -> str:
    return Path(value[1:]).read_text() if value.startswith("@") else value


def _agent_verb(root: Path, role: str, brief: str) -> dict:
    run = Run(root, role)
    try:
        config, _ = configuration.load(root)
        if reason := halt_reason(root):
            return run.finish("halted", reason=reason)
        out = work(root, config, run, role=role, brief=brief)
        return run.finish("needs_direction" if out["questions"] else "done", **out)
    except FAILURES as exc:
        return run.finish("error", reason=str(exc))


def _review_verb(root: Path, role: str | None) -> dict:
    run = Run(root, "review")
    try:
        config, _ = configuration.load(root)
        tree = git.stage_all(root)
        unit = classify(config, git.changed_paths(root, tree))
        verdict = review(root, config, run, tree=tree, role=role or unit["role"])
        return run.finish("needs_direction" if verdict["questions"] else "reviewed", tree=tree, **verdict)
    except FAILURES as exc:
        return run.finish("error", reason=str(exc))


def _init(root: Path) -> dict:
    path = root / configuration.CONFIG
    created = not path.exists()
    if created:
        path.parent.mkdir(exist_ok=True)
        path.write_text(TEMPLATE.read_text())
    ignore = root / ".gitignore"
    lines = ignore.read_text().splitlines() if ignore.exists() else []
    missing = [pattern for pattern in (configuration.LOCAL, ".worktrees/") if pattern not in lines]
    if missing:
        ignore.write_text("\n".join([*lines, *missing]) + "\n")
    return {"status": "ok", "config": str(path), "created": created,
            "next": "fill [gates] with the commands CI runs, then lean-forge commit -m 'Adopt lean-forge'"}


def _halt(root: Path, on: str | None, off: bool) -> dict:
    path = git.state_dir(root) / "HALT"
    if on:
        path.write_text(on + "\n")
    elif off:
        path.unlink(missing_ok=True)
    reason = halt_reason(root)
    return {"status": "halt", "halted": reason is not None, "reason": reason}


def _runs(root: Path, run_id: str | None) -> dict:
    if run_id:
        outcome, steps = runs.load(root, run_id)
        return {"status": "ok", "outcome": outcome, "steps": steps}
    rows = runs.recent(root)
    for row in rows:
        note = row.get("reason") or (row.get("sha") or "")[:12]
        print(f"{row['run']}  {row.get('workflow', '?'):9} {row['status']:17} ${row.get('cost_usd', 0):>7.2f}"
              f"  {row.get('seconds', 0):>6}s  {note}", file=sys.stderr)
    return {"status": "ok", "runs": rows}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lean-forge", description="Autonomous, mechanical gate chain for agent-written code.",
                                allow_abbrev=False)  # the hook matches option names exactly
    p.add_argument("-C", dest="cwd", default=".", help="run as if started in this directory")
    sub = p.add_subparsers(dest="verb", required=True)
    c = sub.add_parser("commit", allow_abbrev=False, help="gates, scan, review loop, then commit the whole worktree")
    c.add_argument("-m", "--message", required=True, help="commit message, or @file")
    c.add_argument("--no-autofix", action="store_true", help="stop at the first BLOCK instead of fixing")
    lnd = sub.add_parser("land", allow_abbrev=False, help="rebase onto the remote base, re-run gates, fast-forward push")
    lnd.add_argument("--allow-outside-commits", action="store_true", help="land commits made outside the chain")
    r = sub.add_parser("review", allow_abbrev=False, help="review the current worktree state without committing")
    r.add_argument("--role", choices=["review-cheap", "review-final"])
    sub.add_parser("implement", allow_abbrev=False, help="run the implement agent on a brief (text or @file)").add_argument("text")
    sub.add_parser("fix", allow_abbrev=False, help="run the fix agent on findings (text or @file)").add_argument("text")
    e = sub.add_parser("eval", allow_abbrev=False, help="score golden tasks in .lean-forge/evals, or --check recorded results")
    e.add_argument("ids", nargs="*")
    e.add_argument("--check", action="store_true")
    a = sub.add_parser("approve", allow_abbrev=False, help="approve the tree a commit run is waiting on, then commit it")
    a.add_argument("run_id")
    rs = sub.add_parser("runs", allow_abbrev=False, help="recent runs (the dashboard), or one run's steps")
    rs.add_argument("run_id", nargs="?")
    h = sub.add_parser("halt", allow_abbrev=False, help="show, set (--on) or clear (--off) the operator halt")
    h.add_argument("--on", metavar="REASON")
    h.add_argument("--off", action="store_true")
    for verb, what in (("init", "write .lean-forge/config.toml"), ("config", "print the resolved config and its source"),
                       ("hook", "Claude Code PreToolUse hook; reads the hook payload on stdin"),
                       ("version", "print the version")):
        sub.add_parser(verb, allow_abbrev=False, help=what)
    return p


def _dispatch(args, root: Path) -> dict:
    if args.verb == "commit":
        return workflows.commit_unit(root, _text(args.message), autofix=False if args.no_autofix else None)
    if args.verb == "land":
        return workflows.land(root, allow_outside=args.allow_outside_commits)
    if args.verb == "review":
        return _review_verb(root, args.role)
    if args.verb in ("implement", "fix"):
        return _agent_verb(root, args.verb, _text(args.text))
    if args.verb == "eval":
        return workflows.eval_check(root) if args.check else workflows.evals(root, args.ids or None)
    if args.verb == "approve":
        return workflows.approve_run(root, args.run_id, getpass.getuser())
    if args.verb == "runs":
        return _runs(root, args.run_id)
    if args.verb == "halt":
        return _halt(root, args.on, args.off)
    if args.verb == "init":
        return _init(root)
    try:
        config, source = configuration.load(root)
        return {"status": "ok", "source": source, "config": config}
    except configuration.ConfigError as exc:
        return {"status": "error", "reason": str(exc)}


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # unwinds so agent process groups are killed
    args = parser().parse_args(argv)
    if args.verb == "hook":
        return hook.main()
    if args.verb == "version":
        print(__version__)
        return 0
    try:
        root = git.toplevel(Path(args.cwd))
    except (git.GitError, FileNotFoundError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}))
        return 1
    try:
        outcome = _dispatch(args, root)
    except FileNotFoundError as exc:  # `runs <unknown id>`, `-m @missing`
        outcome = {"status": "error", "reason": str(exc)}
    print(json.dumps(outcome, indent=2, sort_keys=True, default=str))
    status = outcome.get("status", "error")
    if args.verb not in ("runs", "config", "init", "halt"):
        detail = outcome.get("reason") or outcome.get("sha") or "; ".join(outcome.get("questions", []))
        print(f"lean-forge {args.verb}: {status} {detail}".rstrip(), file=sys.stderr)
    return 0 if status in OK else EXIT.get(status, 1)
