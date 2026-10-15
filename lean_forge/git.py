"""Thin git helpers. Every call is an argv list, never a shell string."""

from __future__ import annotations

import subprocess
from pathlib import Path

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class GitError(RuntimeError):
    pass


def _run(args: tuple[str, ...], cwd, stdin: str | None, timeout: int | None) -> subprocess.CompletedProcess:
    """`timeout` bounds network calls (ls-remote, fetch, push); a hung remote becomes a GitError."""
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, input=stdin,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} timed out after {timeout}s; rerun") from exc


def git(*args: str, cwd: Path | str | None = None, check: bool = True, stdin: str | None = None,
        timeout: int | None = None) -> str:
    proc = _run(args, cwd, stdin, timeout)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout


def succeeds(*args: str, cwd: Path | str | None = None, timeout: int | None = None) -> bool:
    return _run(args, cwd, None, timeout).returncode == 0


def toplevel(cwd: Path | str | None = None) -> Path:
    return Path(git("rev-parse", "--show-toplevel", cwd=cwd).strip())


def state_dir(cwd: Path | str | None = None) -> Path:
    """Per-repository state shared by every worktree and never committed: <git-common-dir>/lean-forge."""
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=cwd).strip())
    path = common / "lean-forge"
    path.mkdir(exist_ok=True)
    return path


def head(cwd: Path | str | None = None) -> str | None:
    out = git("rev-parse", "-q", "--verify", "HEAD", cwd=cwd, check=False).strip()
    return out or None


def branch(cwd: Path | str | None = None) -> str | None:
    out = git("symbolic-ref", "-q", "--short", "HEAD", cwd=cwd, check=False).strip()
    return out or None


def show(ref: str, path: str, cwd: Path | str | None = None) -> str | None:
    """File contents at ref, or None when the ref or the path does not exist."""
    proc = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=cwd, capture_output=True, text=True, check=False)
    return proc.stdout if proc.returncode == 0 else None


def stage_all(cwd: Path | str) -> str:
    """Stage the whole worktree state and return its tree id."""
    git("add", "-A", cwd=cwd)
    return git("write-tree", cwd=cwd).strip()


def changed_paths(cwd: Path | str, tree: str) -> list[str]:
    base = head(cwd) or EMPTY_TREE
    out = git("diff-tree", "-r", "--name-only", "--no-renames", base, tree, cwd=cwd)
    return [line for line in out.splitlines() if line]


def snapshot(cwd: Path | str, *files: str) -> str:
    """Everything a read-only agent must leave unchanged, plus `files` such as the ignored local override."""
    return "\n".join([
        head(cwd) or "",
        git("write-tree", cwd=cwd).strip(),
        git("status", "--porcelain=v1", "--untracked-files=all", "--ignored=no", cwd=cwd),
        *(repr((Path(cwd) / f).read_bytes() if (Path(cwd) / f).exists() else None) for f in files),
    ])


def patch_id(sha: str, cwd: Path | str) -> str:
    """Hashes context lines too, so a rebase that changes the lines around a hunk changes it."""
    diff = git("show", "--no-renames", "--format=", sha, cwd=cwd)
    out = git("patch-id", "--stable", cwd=cwd, stdin=diff).split()
    return out[0] if out else f"empty:{git('rev-parse', f'{sha}^{{tree}}', cwd=cwd).strip()}"
