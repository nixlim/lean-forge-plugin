"""Configuration: committed, not working-tree, so a candidate can never relax the gates that judge it.

Resolution order for .lean-forge/config.toml: the remote base branch tip as last fetched
(refs/remotes/<remote>/<branch>; `land` fetches, then reloads), then HEAD, then the working tree
(bootstrap only, before anything is committed). An optional, uncommitted
.lean-forge/local.toml may override route `model` and `effort` only, so each developer can pick
models but never swap the program that judges a candidate.
"""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path

from lean_forge import git

CONFIG = ".lean-forge/config.toml"
LOCAL = ".lean-forge/local.toml"

DEFAULTS: dict = {
    "gates": {},
    "control": {"paths": [".lean-forge/", ".claude/", ".codex/", ".github/", "AGENTS.md", "CLAUDE.md"]},
    "routes": {
        "review-cheap": {"provider": "claude", "model": "claude-sonnet-5-5", "effort": "medium"},
        "review-final": {"provider": "claude", "model": "claude-opus-5-5", "effort": "high"},
        "fix": {"provider": "claude", "model": "claude-opus-5-5", "effort": "high"},
        "implement": {"provider": "claude", "model": "claude-opus-5-5", "effort": "high"},
    },
    "review": {"max_iterations": 8, "autofix": True, "focus": ""},
    "approval": {"mode": "none"},
    "land": {"remote": "origin", "branch": "main", "review": "none"},
    "guard": {"enabled": True},
    "timeouts": {"gate": 1800, "agent": 1800, "git": 120},
}
LOCAL_ROUTE_KEYS = ("model", "effort")


class ConfigError(RuntimeError):
    pass


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _parse(text: str | None, origin: str) -> dict | None:
    if text is None:
        return None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{CONFIG} at {origin} is not valid TOML: {exc}") from exc


def load(root: Path) -> tuple[dict, str]:
    """Return (config, source) where source names the ref the config was read from."""
    working = root / CONFIG
    hint = _parse(git.show("HEAD", CONFIG, root), "HEAD") or _parse(
        working.read_text() if working.exists() else None, "working tree") or {}
    if not isinstance(hint.get("land", {}), dict):
        raise ConfigError("land must be a table")
    land = {**DEFAULTS["land"], **hint.get("land", {})}
    for ref in (f"refs/remotes/{land['remote']}/{land['branch']}", "HEAD"):
        parsed = _parse(git.show(ref, CONFIG, root), ref)
        if parsed is not None:
            raw, source, trusted = parsed, ref, ref
            break
    else:
        if not working.exists():
            raise ConfigError(f"no {CONFIG}; run `lean-forge init` first")
        raw, source, trusted = _parse(working.read_text(), "working tree"), "working tree (bootstrap)", None
    config = _merge(DEFAULTS, raw)
    tables = [s for s in DEFAULTS if not isinstance(config[s], dict)]
    routes = config["routes"] if isinstance(config["routes"], dict) else {}
    tables += [f"routes.{r}" for r, route in routes.items() if not isinstance(route, dict)]
    if tables:
        raise ConfigError(f"{', '.join(tables)} must be tables")
    if not all(isinstance(cmd, str) for cmd in config["gates"].values()):
        raise ConfigError("every gate must be a shell command string")
    paths = config["control"].get("paths")
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise ConfigError("control.paths must be a list of strings")
    local = root / LOCAL
    if local.exists():
        parsed = _parse(local.read_text(), LOCAL) or {}
        overrides = parsed.get("routes", {})
        if set(parsed) - {"routes"} or not isinstance(overrides, dict):
            raise ConfigError(f"{LOCAL} may contain only [routes.<role>] tables with model and effort")
        for role, route in overrides.items():
            bad = sorted(set(route) - set(LOCAL_ROUTE_KEYS)) if isinstance(route, dict) else [role]
            if bad:
                raise ConfigError(f"{LOCAL} may set only {' and '.join(LOCAL_ROUTE_KEYS)} for a route; "
                                  f"routes.{role} sets {', '.join(bad)}")
        config["routes"] = _merge(config["routes"], overrides)
    loose = [f"routes.{r}.{k}" for r, route in config["routes"].items() for k in LOCAL_ROUTE_KEYS
             if k in route and not isinstance(route[k], str)]
    if loose:
        raise ConfigError(f"{', '.join(loose)} must be strings")
    positive = [f"{s}.{k}" for s, k in (("review", "max_iterations"), ("timeouts", "gate"), ("timeouts", "agent"),
                                        ("timeouts", "git")) if type(config[s][k]) is not int or config[s][k] < 1]
    if positive:
        raise ConfigError(f"{', '.join(positive)} must be positive integers")
    if not isinstance(config["review"]["autofix"], bool):
        raise ConfigError("review.autofix must be true or false")
    if config["approval"]["mode"] not in ("none", "control", "all"):
        raise ConfigError("approval.mode must be none, control or all")
    if config["land"]["review"] not in ("none", "final"):
        raise ConfigError("land.review must be none or final")
    config["source_ref"] = trusted  # the ref guidance is read from too; None only at bootstrap
    return config, source
