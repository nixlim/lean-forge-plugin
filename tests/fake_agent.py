"""Scripted stand-in for every agent role, used through the `command` provider.

LEAN_FORGE_FAKE names a JSON file mapping role -> list of responses; each call pops the first one
for its role and appends the role to `calls`, its system prompt to `systems` and its prompt to `prompts`. A
response may carry `_write` (path -> content) and `_delete` (paths) to simulate an agent editing
the worktree, and `_git` (an argv after `git`) to simulate an agent running git, such as a commit.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

request = json.load(sys.stdin)
script_path = Path(os.environ["LEAN_FORGE_FAKE"])
script = json.loads(script_path.read_text())
role = request["role"]
queue = script.get(role) or []
if not queue:
    print(f"fake agent: no scripted response left for {role}", file=sys.stderr)
    sys.exit(3)
response = queue.pop(0)
script.setdefault("calls", []).append(role)
script.setdefault("systems", []).append(request["system"])
script.setdefault("prompts", []).append(request["prompt"])
script_path.write_text(json.dumps(script))
cwd = Path(request["cwd"])
for path, content in response.pop("_write", {}).items():
    (cwd / path).write_text(content)
for path in response.pop("_delete", []):
    (cwd / path).unlink(missing_ok=True)
if "_git" in response:
    subprocess.run(["git", *response.pop("_git")], cwd=cwd, check=True, capture_output=True)
print(json.dumps(response))
