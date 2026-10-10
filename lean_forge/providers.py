"""Launch a headless agent for one role and return its schema-valid structured output.

Providers: `claude` (Claude Code -p), `codex` (codex exec), and `command` (any program that reads a
JSON request on stdin and prints the JSON result; used for tests and custom agents). Read-only
agents get Read/Grep/Glob plus Bash limited to exact-match rules on Claude (a fixed set of complete
git read commands and the configured gate commands, verbatim, so no flag can be appended), and the
read-only OS sandbox on Codex. Writers additionally get Edit/Write (Claude) or the workspace-write
sandbox (Codex). A gate command runs project code, so on Claude a writer that edits that code can
run anything; its containment there is the engine's HEAD check, not a sandbox.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import signal
import subprocess
import tempfile
from pathlib import Path

# Complete commands, matched exactly: a prefix rule would admit `--output=<file>`, `-O<pager>` and the like.
READ_BASH = ["git status", "git diff", "git diff HEAD", "git diff --cached", "git diff --stat HEAD",
             "git log --oneline -n 30", "git ls-files"]

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "BLOCK"]},
        "findings": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "findings", "summary", "questions"],
    "additionalProperties": False,
}

WORK_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "questions"],
    "additionalProperties": False,
}


class AgentError(RuntimeError):
    pass


def _env() -> dict:
    env = dict(os.environ)
    for key in ("CLAUDECODE", "CLAUDE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT"):
        env.pop(key, None)
    return env


def run_bounded(args, *, cwd: Path, timeout: int, input: str | None = None, **kw) -> subprocess.CompletedProcess:
    """subprocess.run in a session of its own. A timeout, or the run itself being stopped, kills the whole
    process tree instead of orphaning it."""
    with subprocess.Popen(args, cwd=cwd, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
                          **kw) as proc:
        try:
            out, err = proc.communicate(input, timeout=timeout)
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise
    return subprocess.CompletedProcess(args, proc.returncode, out, err)


def run_agent(route: dict, *, role: str, system: str, prompt: str, schema: dict, write: bool,
              bash_prefixes: list[str], cwd: Path, timeout: int) -> dict:
    """Return {"output": dict, "cost_usd": float | None, "tokens": int | None, "route": str}."""
    provider = route.get("provider")
    runner = {"claude": _claude, "codex": _codex, "command": _command}.get(provider)
    if runner is None:
        raise AgentError(f"route for {role} has unknown provider {provider!r}")
    try:
        result = runner(route, role, system, prompt, schema, write, bash_prefixes, cwd, timeout)
    except subprocess.TimeoutExpired as exc:
        raise AgentError(f"{role} agent timed out after {timeout}s") from exc
    output = result["output"]
    missing = [key for key in schema["required"] if key not in output]
    if missing:
        raise AgentError(f"{role} agent output lacks {', '.join(missing)}")
    result["route"] = f"{provider}/{route.get('model', route.get('command', ''))}"
    return result


def _claude(route, role, system, prompt, schema, write, bash_prefixes, cwd, timeout) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        commands = READ_BASH + bash_prefixes
        system_file = Path(tmp) / "system.md"
        system_file.write_text(system + "\n\nYour shell accepts only these exact commands, verbatim: "
                               + "; ".join(f"`{c}`" for c in commands) + "\n", encoding="utf-8")
        editors = ["Edit", "Write"] if write else []
        allowed = ["Read", "Grep", "Glob", *editors] + [f"Bash({c})" for c in commands]
        argv = [
            route.get("executable", "claude"), "-p", "--safe-mode", "--strict-mcp-config",
            "--output-format", "json", "--model", route["model"],
            "--system-prompt-file", str(system_file),
            "--tools", ",".join(["Read", "Grep", "Glob", "Bash", *editors]),
            "--allowedTools", *allowed,
            "--permission-prompts", "none", "--no-session-persistence",
            "--json-schema", json.dumps(schema),
        ]
        if route.get("effort"):
            argv += ["--effort", route["effort"]]
        proc = run_bounded(argv, input=prompt, cwd=cwd, timeout=timeout, env=_env())
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AgentError(f"{role} (claude) returned no JSON: {(proc.stderr or proc.stdout)[-800:]}") from exc
    output = data.get("structured_output")
    if data.get("is_error") or not isinstance(output, dict):
        raise AgentError(f"{role} (claude) failed: {data.get('subtype')} {str(data.get('result'))[-800:]}")
    usage = data.get("usage") or {}
    tokens = sum(int(usage.get(k) or 0) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                                  "cache_creation_input_tokens"))
    return {"output": output, "cost_usd": data.get("total_cost_usd"), "tokens": tokens}


def _codex(route, role, system, prompt, schema, write, bash_prefixes, cwd, timeout) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        schema_file, out_file = Path(tmp) / "schema.json", Path(tmp) / "out.json"
        schema_file.write_text(json.dumps(schema), encoding="utf-8")
        argv = [
            route.get("executable", "codex"), "exec", "--json", "-o", str(out_file),
            "--output-schema", str(schema_file), "-s", "workspace-write" if write else "read-only",
            "-c", "approval_policy=never", "-c", f"model={route['model']}",
            "--skip-git-repo-check", "--ephemeral", "-C", str(cwd),
        ]
        if route.get("effort"):
            argv += ["-c", f"model_reasoning_effort={route['effort']}"]
        proc = run_bounded([*argv, "-"], input=f"{system}\n\n---\n\n{prompt}", cwd=cwd, timeout=timeout, env=_env())
        if proc.returncode != 0 or not out_file.exists():
            raise AgentError(f"{role} (codex) failed: {(proc.stderr or proc.stdout)[-800:]}")
        try:
            output = json.loads(out_file.read_text())
        except json.JSONDecodeError as exc:
            raise AgentError(f"{role} (codex) final message is not JSON") from exc
    tokens = 0
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed":
            usage = event.get("usage") or {}
            tokens += int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
    return {"output": output, "cost_usd": None, "tokens": tokens}


def _command(route, role, system, prompt, schema, write, bash_prefixes, cwd, timeout) -> dict:
    request = {"role": role, "system": system, "prompt": prompt, "schema": schema, "write": write,
               "cwd": str(cwd)}
    proc = run_bounded(shlex.split(route["command"]), input=json.dumps(request), cwd=cwd, timeout=timeout)
    if proc.returncode != 0:
        raise AgentError(f"{role} (command) exited {proc.returncode}: {proc.stderr[-800:]}")
    try:
        output = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AgentError(f"{role} (command) printed no JSON") from exc
    cost = output.pop("_cost_usd", None)
    return {"output": output, "cost_usd": cost, "tokens": output.pop("_tokens", None)}
