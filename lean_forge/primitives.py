"""The forge primitives. Each acts on an explicit tree id and records evidence for it.

Effectful primitives (commit) refuse unless evidence for that exact tree says the gates passed,
the secrets scan was clean, the required reviewer said PASS, and, if approval is configured,
an approval exists. Everything else is a pure check or an agent call.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from lean_forge import evidence, git
from lean_forge.providers import VERDICT_SCHEMA, WORK_SCHEMA, AgentError, run_agent, run_bounded

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SECRET = re.compile(
    r"AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA|EC|OPENSSH|PGP|DSA) PRIVATE KEY|gh[pousr]_[A-Za-z0-9]{36}"
    r"|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|(?i:(?:api[_-]?key|secret|password|token)\s*[:=]\s*[\"'][^\"']{12,}[\"'])"
)
GUIDANCE_FILES = ("AGENTS.md", "CLAUDE.md")
MAX_INLINE_DIFF = 200_000


class Refused(RuntimeError):
    """A precondition failed. The message says what evidence is missing."""


def classify(config: dict, paths: list[str]) -> dict:
    control = [p for p in paths if any(p == c or p.startswith(c) for c in config["control"]["paths"])]
    return {"paths": paths, "control": bool(control), "control_paths": control,
            "role": "review-final" if control else "review-cheap"}


def gates(root: Path, config: dict, tree: str, run) -> list[dict]:
    """Run every configured gate in the worktree; return failures. Passing evidence short-circuits."""
    failures = []
    for name, command in config["gates"].items():
        if evidence.find(root, kind="gate", tree=tree, name=name, cmd=command, ok=True):
            run.log("gate", name=name, ok=True, cached=True)
            continue
        started = time.time()
        try:
            proc = run_bounded(command, shell=True, cwd=root, timeout=config["timeouts"]["gate"])
            ok, tail = proc.returncode == 0, (proc.stdout + proc.stderr)[-3000:]
        except subprocess.TimeoutExpired:
            ok, tail = False, f"timed out after {config['timeouts']['gate']}s"
        evidence.add(root, kind="gate", tree=tree, name=name, cmd=command, ok=ok, run=run.id)
        run.log("gate", name=name, ok=ok, seconds=round(time.time() - started, 1))
        if not ok:
            failures.append({"gate": name, "command": command, "output": tail})
    return failures


def scan(root: Path, tree: str, run) -> list[str]:
    """Where the candidate adds lines that look like credentials, as `path:line` (never the match itself).

    Eval fixtures are exempt by design.
    """
    base = git.head(root) or git.EMPTY_TREE
    diff = git.git("diff", "--no-renames", "-U0", base, tree, "--", ".", ":(exclude).lean-forge/evals", cwd=root)
    hits, path, lineno = [], None, 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("@@"):
            lineno = int(re.search(r"\+(\d+)", line).group(1))
        elif line.startswith("+"):
            if SECRET.search(line):
                hits.append(f"{path}:{lineno}")
            lineno += 1
    evidence.add(root, kind="scan", tree=tree, base=base, ok=not hits, run=run.id)
    run.log("scan", ok=not hits, hits=len(hits))
    return hits


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else None


def _clip(text: str, limit: int = 40_000) -> str:
    return text if len(text) <= limit else text[:limit] + "\n[truncated]"


def _system(role_prompt: str, root: Path, config: dict, with_constitution: bool) -> str:
    """Guidance comes from the same committed ref as the config, so a candidate cannot rewrite its judge."""
    parts = [_clip(_read(PLUGIN_ROOT / "prompts" / role_prompt))]
    if with_constitution:
        parts.append(_clip(_read(PLUGIN_ROOT / "rules" / "review-constitution.md")))
    if config["review"].get("focus"):
        parts.append("## Project review focus\n\n" + config["review"]["focus"])
    ref = config.get("source_ref")
    for name in GUIDANCE_FILES:
        text = git.show(ref, name, root) if ref else _read(root / name)
        if text:
            parts.append(f"## Project guidance ({name})\n\n" + _clip(text))
    return "\n\n".join(parts)


def _agent(config: dict, role: str, cwd: Path, run, *, system: str, prompt: str, schema: dict, write: bool) -> dict:
    route = config["routes"].get(role) or {}
    if not route:
        raise AgentError(f"no route configured for {role}")
    started = time.time()
    result = run_agent(route, role=role, system=system, prompt=prompt, schema=schema, write=write,
                       bash_prefixes=list(config["gates"].values()), cwd=cwd,
                       timeout=config["timeouts"]["agent"])
    run.log("agent", role=role, route=result["route"], cost_usd=result["cost_usd"], tokens=result["tokens"],
            seconds=round(time.time() - started, 1))
    return result["output"]


def review(root: Path, config: dict, run, *, tree: str, role: str, since: str | None = None,
           cwd: Path | None = None, record: bool = True) -> dict:
    """Independent read-only review of exactly `git diff <since> <tree>`; `since` defaults to HEAD.

    The reviewer sees the very tree the evidence is keyed by, never a later state of the index.
    `record=False` (evals, land) neither reads nor writes evidence, so the review always runs afresh.
    Evidence is keyed by the tree and the HEAD it was reviewed against: the same tree on another base is
    another change (a revert of a later commit, say). A cached PASS is reused only if it asked no questions.
    """
    cwd = cwd or root
    base = git.head(root) or git.EMPTY_TREE
    cached = record and evidence.find(root, kind="review", tree=tree, base=base, role=role, verdict="PASS", questions=[])
    if cached:
        run.log("review", role=role, verdict="PASS", cached=True)
        return {key: cached[key] for key in ("verdict", "findings", "summary", "questions")}
    since = since or base
    diff = git.git("diff", "--no-renames", since, tree, cwd=cwd)
    shown = diff if len(diff) <= MAX_INLINE_DIFF else diff[:MAX_INLINE_DIFF] + "\n[diff truncated; read the rest with git]"
    prompt = (f"Review this candidate. It is exactly `git diff --no-renames {since} {tree}` in this checkout.\n\n"
              f"```diff\n{shown}\n```\n")
    before = git.snapshot(cwd)
    verdict = _agent(config, role, cwd, run, system=_system(f"{role}.md", root, config, True),
                     prompt=prompt, schema=VERDICT_SCHEMA, write=False)
    if git.snapshot(cwd) != before:
        raise AgentError(f"{role} modified the checkout; a reviewer must be read-only")
    if record:
        evidence.add(root, kind="review", tree=tree, base=base, role=role, run=run.id, **verdict)
    run.log("review", role=role, verdict=verdict["verdict"], findings=len(verdict["findings"]),
            questions=len(verdict["questions"]))
    return verdict


def work(root: Path, config: dict, run, *, role: str, brief: str) -> dict:
    """A writing agent (`fix` or `implement`) edits the worktree. It never commits or moves HEAD."""
    head_before = git.head(root)
    output = _agent(config, role, root, run, system=_system(f"{role}.md", root, config, False),
                    prompt=brief, schema=WORK_SCHEMA, write=True)
    if git.head(root) != head_before:
        raise AgentError(f"{role} moved HEAD; writing agents edit files only")
    return output


def approve(root: Path, tree: str, by: str) -> dict:
    return evidence.add(root, kind="approval", tree=tree, by=by)


def required_evidence(root: Path, config: dict, tree: str, role: str, control: bool) -> list[str]:
    """What is missing before `tree` may be committed on the current HEAD. Empty means commit is allowed."""
    base = git.head(root) or git.EMPTY_TREE
    missing = [f"gate {name} on {tree[:12]}" for name, cmd in config["gates"].items()
               if not evidence.find(root, kind="gate", tree=tree, name=name, cmd=cmd, ok=True)]
    if not evidence.find(root, kind="scan", tree=tree, base=base, ok=True):
        missing.append("clean secrets scan")
    roles = ["review-final"] if role == "review-final" else ["review-cheap", "review-final"]
    if not any(evidence.find(root, kind="review", tree=tree, base=base, role=r, verdict="PASS", questions=[])
               for r in roles):
        missing.append(f"{role} PASS")
    mode = config["approval"]["mode"]
    if (mode == "all" or (mode == "control" and control)) and not evidence.find(root, kind="approval", tree=tree):
        missing.append("operator approval")
    return missing


def commit(root: Path, config: dict, *, tree: str, role: str, control: bool, message: str) -> str:
    """Commit exactly `tree` on HEAD, with a compare-and-swap so a moved HEAD fails instead of racing."""
    missing = required_evidence(root, config, tree, role, control)
    if missing:
        raise Refused("commit refused; missing evidence: " + ", ".join(missing))
    if git.stage_all(root) != tree:
        raise Refused("the worktree changed after review; rerun commit")
    parent = git.head(root)
    sha = git.git("commit-tree", tree, *(["-p", parent] if parent else []), "-F", "-", cwd=root, stdin=message).strip()
    git.git("update-ref", "-m", "lean-forge commit", "HEAD", sha, parent or "0" * 40, cwd=root)
    evidence.add(root, kind="commit", tree=tree, sha=sha, patch_id=git.patch_id(sha, root))
    return sha


def halt_reason(root: Path) -> str | None:
    path = git.state_dir(root) / "HALT"
    return (path.read_text().strip() or "halted") if path.exists() else None
