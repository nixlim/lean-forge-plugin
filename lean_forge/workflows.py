"""The blessed compositions of the primitives: commit a unit, land a branch, score evals.

Every workflow returns an outcome dict with a `status`:
committed | landed | evaluated | noop | blocked | needs_direction | awaiting_approval | halted | error.
Only `needs_direction` (and `awaiting_approval`, when approval is configured) needs a human.
Re-running a workflow is always safe: steps whose evidence already exists for the same tree are skipped.
"""

from __future__ import annotations

import fcntl
import json
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from lean_forge import config as configuration
from lean_forge import evidence, git
from lean_forge.primitives import Refused, approve, classify, commit, gates, halt_reason, review, scan, work
from lean_forge.providers import AgentError
from lean_forge.runs import Run

FAILURES = (Refused, AgentError, git.GitError, configuration.ConfigError)


def _fix_brief(findings: list[str], stage: str) -> str:
    listed = "\n".join(f"- {f}" for f in findings)
    return (f"The gate chain stopped this candidate at the {stage} step. The candidate is the uncommitted "
            f"change in this worktree (`git diff HEAD` plus untracked files). Address every finding below, "
            f"then run the project's gate commands to confirm.\n\nFindings:\n{listed}\n")


def commit_unit(root: Path, message: str, *, autofix: bool | None = None) -> dict:
    """Gates, secrets scan, and a capped review-and-fix loop over the whole worktree; then commit it."""
    run = Run(root, "commit", subject=message.strip().splitlines()[0][:120] if message.strip() else "")
    (run.dir / "message.txt").write_text(message)
    try:
        config, source = configuration.load(root)
        run.log("config", source=source)
        if reason := halt_reason(root):
            return run.finish("halted", reason=reason)
        if not message.strip():
            return run.finish("blocked", reason="empty commit message")
        autofix = config["review"]["autofix"] if autofix is None else autofix
        cap = int(config["review"]["max_iterations"])
        for iteration in range(1, cap + 1):
            tree = git.stage_all(root)
            paths = git.changed_paths(root, tree)
            if not paths:
                return run.finish("noop", reason="nothing to commit")
            unit = classify(config, paths)
            run.log("iteration", n=iteration, tree=tree, paths=len(paths), role=unit["role"])
            failures = gates(root, config, tree, run)
            if failures:
                stage, findings = "gates", [f"gate `{f['gate']}` failed (`{f['command']}`):\n{f['output']}" for f in failures]
            else:
                hits = scan(root, tree, run)
                if hits:
                    return run.finish("blocked", stage="scan", tree=tree, hits=hits,
                                      reason="possible credentials in the candidate; remove them and rerun")
                verdict = review(root, config, run, tree=tree, role=unit["role"])
                if verdict["questions"]:
                    return run.finish("needs_direction", stage="review", tree=tree, questions=verdict["questions"],
                                      findings=verdict["findings"], summary=verdict["summary"])
                if verdict["verdict"] == "PASS":
                    break
                stage, findings = "review", verdict["findings"]
            if not autofix:
                return run.finish("blocked", stage=stage, tree=tree, iteration=iteration, findings=findings)
            if iteration == cap:
                return run.finish("needs_direction", stage="cap", tree=tree, iteration=iteration, findings=findings,
                                  questions=[(f"The candidate still fails at the {stage} step after {cap} rounds. "
                                              "Rework the approach, accept the remaining findings, or drop the unit?")])
            fixed = work(root, config, run, role="fix", brief=_fix_brief(findings, stage))
            if fixed["questions"]:
                return run.finish("needs_direction", stage="fix", tree=tree, questions=fixed["questions"],
                                  summary=fixed["summary"], findings=findings)
        mode = config["approval"]["mode"]
        if (mode == "all" or (mode == "control" and unit["control"])) and not evidence.find(
                root, kind="approval", tree=tree, base=git.head(root) or git.EMPTY_TREE):
            return run.finish("awaiting_approval", tree=tree, base=git.head(root) or git.EMPTY_TREE,
                              role=unit["role"], control_paths=unit["control_paths"],
                              questions=[f"Approve commit of tree {tree[:12]}? Run `lean-forge approve {run.id}`."])
        sha = commit(root, config, tree=tree, message=message)
        return run.finish("committed", sha=sha, tree=tree, iterations=iteration, role=unit["role"])
    except FAILURES as exc:
        return run.finish("error", reason=str(exc))


def approve_run(root: Path, run_id: str, by: str) -> dict:
    """Record approval for the tree a commit run is waiting on, then re-enter the commit in the run's worktree.

    Any checkout of the repository may approve; the commit always happens where the run came from.
    """
    outcome_path = git.state_dir(root) / "runs" / run_id / "outcome.json"
    if not outcome_path.exists():
        return {"status": "error", "reason": f"no finished run {run_id}"}
    outcome = json.loads(outcome_path.read_text())
    if outcome.get("status") != "awaiting_approval":
        return {"status": "error", "reason": f"run {run_id} is {outcome.get('status')}, not awaiting_approval"}
    worktree = Path(json.loads((outcome_path.parent / "log.jsonl").read_text().splitlines()[0])["cwd"])
    try:
        if (git.head(worktree) or git.EMPTY_TREE) != (outcome.get("base") or git.EMPTY_TREE):
            return {"status": "error", "reason": f"{worktree} changed base since run {run_id}; rerun commit there"}
        if git.stage_all(worktree) != outcome["tree"]:
            return {"status": "error", "reason": f"{worktree} changed since run {run_id}; rerun commit there"}
    except (git.GitError, OSError) as exc:
        return {"status": "error", "reason": f"cannot check the worktree of run {run_id}: {exc}"}
    approve(worktree, outcome["tree"], by)
    return commit_unit(worktree, (outcome_path.parent / "message.txt").read_text())


@contextmanager
def _lock(path: Path, timeout: int = 300):
    with open(path, "w") as fh:
        deadline = time.time() + timeout
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > deadline:
                    raise Refused(f"land lock held by another session for {timeout}s") from None
                time.sleep(2)
        yield


def land(root: Path, *, allow_outside: bool = False) -> dict:
    """Rebase onto the remote base under a lock, re-run the gates on the result, fast-forward push."""
    run = Run(root, "land")
    try:
        config, source = configuration.load(root)
        run.log("config", source=source)
        if reason := halt_reason(root):
            return run.finish("halted", reason=reason)
        if git.git("status", "--porcelain", "--untracked-files=all", cwd=root).strip():
            return run.finish("blocked", reason="uncommitted changes; commit them with `lean-forge commit` first")
        if not git.branch(root):
            return run.finish("blocked", reason="detached HEAD; land from a branch")
        remote, base, net = config["land"]["remote"], config["land"]["branch"], config["timeouts"]["git"]
        exists = git.succeeds("ls-remote", "--exit-code", "--heads", remote, base, cwd=root, timeout=net)
        base_ref = f"refs/remotes/{remote}/{base}" if exists else None
        if exists:
            git.git("fetch", "-q", remote, base, cwd=root, timeout=net)
        ahead = git.git("rev-list", "--reverse", f"{base_ref}..HEAD" if base_ref else "HEAD", cwd=root).split()
        if not ahead:
            return run.finish("noop", reason=f"nothing ahead of {base_ref}")
        outside = [sha for sha in ahead if not evidence.find(root, kind="commit", patch_id=git.patch_id(sha, root))]
        if outside and not allow_outside:
            return run.finish("needs_direction", stage="provenance", commits=outside, questions=[(
                f"{len(outside)} commit(s) were made outside `lean-forge commit` and never went through the chain. "
                "Land them anyway (`lean-forge land --allow-outside-commits`), or redo them through the chain?")])
        with _lock(git.state_dir(root) / "land.lock"):
            pulled = 0
            if exists:
                git.git("fetch", "-q", remote, base, cwd=root, timeout=net)
                pulled = int(git.git("rev-list", "--count", f"HEAD..{base_ref}", cwd=root).strip())
                if pulled and not git.succeeds("rebase", "-q", base_ref, cwd=root):
                    git.git("rebase", "--abort", cwd=root, check=False)
                    return run.finish("needs_direction", stage="rebase", questions=[(
                        f"Rebasing onto {base_ref} conflicts. Resolve it in this worktree (rebase, then rerun "
                        "`lean-forge land`), or tell me how the two changes should combine.")])
            # The base just fetched is the authority: its gates and land settings judge the rebased tree.
            config, source = configuration.load(root)
            run.log("config", source=source, reloaded=True)
            tree = git.git("rev-parse", "HEAD^{tree}", cwd=root).strip()
            failures = gates(root, config, tree, run)
            if failures:
                return run.finish("blocked", stage="gates", tree=tree, failures=failures, pulled=pulled)
            if config["land"]["review"] != "none":
                # Never cached: a commit-time PASS for this tree reviewed one unit, not base...HEAD.
                since = git.git("merge-base", base_ref, "HEAD", cwd=root).strip() if base_ref else git.EMPTY_TREE
                verdict = review(root, config, run, tree=tree, role="review-final", record=False, since=since)
                if verdict["questions"]:
                    return run.finish("needs_direction", stage="land-review", questions=verdict["questions"],
                                      findings=verdict["findings"])
                if verdict["verdict"] != "PASS":
                    return run.finish("blocked", stage="land-review", findings=verdict["findings"])
            if not git.succeeds("push", "-q", remote, f"HEAD:refs/heads/{base}", cwd=root, timeout=net):
                return run.finish("blocked", stage="push", reason="the remote rejected the push (it may have moved); rerun land")
            sha = git.head(root)
            evidence.add(root, kind="land", sha=sha, tree=tree, onto=f"{remote}/{base}")
        return run.finish("landed", sha=sha, onto=f"{remote}/{base}", commits=len(ahead), pulled=pulled)
    except FAILURES as exc:
        return run.finish("error", reason=str(exc))


FIXTURE = re.compile(r"\A---\n(?P<meta>.*?)\n---\n.*?```diff\n(?P<diff>.*?)```", re.DOTALL)


def _fixtures(root: Path) -> list[tuple[Path, dict, str]]:
    found = []
    for path in sorted((root / ".lean-forge" / "evals").glob("*.md")):
        match = FIXTURE.search(path.read_text(encoding="utf-8"))
        meta = dict(line.split(":", 1) for line in match.group("meta").splitlines() if ":" in line) if match else {}
        meta = {k.strip(): v.strip() for k, v in meta.items()}
        found.append((path, meta, match.group("diff") if match else ""))
    return found


def evals(root: Path, ids: list[str] | None = None) -> dict:
    """Score golden tasks: stage each fixture's diff in a scratch worktree of HEAD and review it afresh."""
    run = Run(root, "eval")
    try:
        config, _ = configuration.load(root)
        results = {}
        for path, meta, diff in _fixtures(root):
            if ids and path.stem not in ids:
                continue
            valid = meta.get("role") in ("review-cheap", "review-final") and meta.get("expected") in ("PASS", "BLOCK")
            if not valid or not diff:
                results[path.stem] = {"error": "fixture needs role, expected and a ```diff block"}
                continue
            scratch = Path(tempfile.mkdtemp(prefix="lean-forge-eval-")) / "wt"
            git.git("worktree", "add", "-q", "--detach", str(scratch), "HEAD", cwd=root)
            try:
                git.git("apply", "--index", "-", cwd=scratch, stdin=diff)
                tree = git.git("write-tree", cwd=scratch).strip()
                verdict = review(root, config, run, tree=tree, role=meta["role"], cwd=scratch, record=False)
                path.with_suffix(".result").write_text(verdict["verdict"] + "\n")
                results[path.stem] = {"expected": meta["expected"], "got": verdict["verdict"]}
                run.log("eval", fixture=path.stem, expected=meta["expected"], got=verdict["verdict"],
                        summary=verdict["summary"], findings=verdict["findings"])
            finally:
                git.git("worktree", "remove", "--force", str(scratch), cwd=root, check=False)
        ok = all(r.get("got") == r.get("expected") for r in results.values())
        return run.finish("evaluated", results=results, ok=ok)
    except FAILURES as exc:
        return run.finish("error", reason=str(exc))


def eval_check(root: Path) -> dict:
    """Every fixture has a recorded result equal to its expectation. Usable as a gate command."""
    problems = []
    fixtures = _fixtures(root)
    for path, meta, _ in fixtures:
        if meta.get("role") not in ("review-cheap", "review-final") or meta.get("expected") not in ("PASS", "BLOCK"):
            problems.append(f"{path.stem}: fixture needs role (review-cheap or review-final) and expected (PASS or BLOCK)")
            continue
        result = path.with_suffix(".result")
        got = result.read_text().strip() if result.exists() else None
        if got != meta.get("expected"):
            problems.append(f"{path.stem}: expected {meta.get('expected')}, recorded {got}")
    return {"status": "ok" if not problems else "blocked", "fixtures": len(fixtures), "problems": problems}
