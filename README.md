# lean-forge

An autonomous, mechanical gate chain for agent-written code, packaged as a Claude Code plugin.

Agents write code quickly. Checking it is the slow part. lean-forge makes the checking mechanical and
leaves the decisions to agents. Every unit of work goes through the project's gates and an independent
reviewer in a fresh context, with a capped fix loop. The commit is exactly the tree that was reviewed,
and branches land by a locked rebase and a fast-forward push. A human is asked only for direction,
never for permission to commit.

lean-forge is the lean successor to [forge-plugin](https://github.com/nixlim/forge-plugin). It keeps
the parts that caught real defects (independent review, a hard iteration cap, structured verdicts,
gates read from committed configuration) and drops the heavyweight guarantees. The core is under
1,200 lines of standard-library Python.

## Requirements

- Python 3.11 or newer, and git.
- The `claude` CLI (Claude Code) and/or the `codex` CLI, logged in, for the agent roles you route to
  them.

## Install

As a Claude Code plugin:

```bash
claude plugin marketplace add nixlim/lean-forge-plugin
claude plugin install lean-forge@lean-forge
```

Then ask Claude Code to "set up lean-forge" in a repository. The `lean-forge-init` skill writes the
configuration from what the repository's CI already runs.

Without Claude Code, clone this repository and put `bin/lean-forge` on your `PATH`.

## Quick start

```bash
lean-forge init                          # writes .lean-forge/config.toml; fill [gates]
lean-forge commit -m "Adopt lean-forge"  # the first commit reads config from the working tree
lean-forge land                          # pushes to origin/main

git worktree add -b agent/topic .worktrees/topic origin/main && cd .worktrees/topic
# ...edit...
lean-forge commit -m "Explain why and what"
lean-forge land
lean-forge runs                          # the dashboard: status, time and cost per run
```

## How it works

**Primitives.** classify, gates, scan, stage, review, fix, implement, approve, commit, land, eval and
halt. Each acts on an explicit git tree id. Each records append-only evidence under
`<git-common-dir>/lean-forge/`, which is shared by every worktree and never committed.

**One guarantee.** The `commit` primitive refuses unless evidence for that exact tree shows that
every gate passed, the secrets scan was clean, the required reviewer said PASS, and an approval
exists when approval is configured. Ordering comes from these preconditions rather than from an
engine, so the primitives compose in any order and unsafe orders simply fail. Commits use
`git commit-tree` and a compare-and-swap `update-ref`, so a commit is exactly the reviewed tree and a
moved HEAD fails instead of racing.

**`commit`** stages the whole worktree, then repeats until PASS or the cap:

1. Run the gates. A failure becomes the round's findings.
2. Scan for secrets. A hit blocks: the session removes it.
3. Review with a fresh reviewer. Control-class paths get `review-final`; everything else gets
   `review-cheap`.
4. On BLOCK, the fix agent addresses the findings and the next round starts.

The cap defaults to 8 rounds. Reaching it returns `needs_direction`.

**`land`** checks that every commit ahead of the remote base went through `commit`, using
rebase-stable patch ids. Then, under a lock in the git common directory, it rebases onto the remote
base, reloads the configuration from the base it just fetched, re-runs that base's gates on the
result and fast-forward pushes. History stays linear. A conflict
aborts the rebase and returns `needs_direction`.

**Reruns are free of wasted work.** Gates and reviews that already passed for an unchanged tree are
skipped. A failure at any point means "fix or answer, then rerun". There is no state machine and no
recovery protocol.

## Configuration

`.lean-forge/config.toml` is read from the remote base tip as last fetched
(`refs/remotes/<remote>/<branch>`), then HEAD, then the working tree. `commit` does not fetch, so it
uses whatever base the last fetch brought; `land` fetches first and judges with the fresh base. The
working tree is used only for the very first commit. A candidate therefore never relaxes the gates
or routes that judge it. `.lean-forge/local.toml` is uncommitted and may override a route's `model`
and `effort` only, so each developer picks their own models; any other key there is an error.
`lean-forge config` prints the resolved configuration and the ref it came from.

| Section | Keys | Default |
|---|---|---|
| `[gates]` | name = shell command; all must exit 0 | none |
| `[control]` | `paths`: prefixes that get `review-final` | config, CI, agent guidance |
| `[review]` | `max_iterations`, `autofix`, `focus` | 8, true, "" |
| `[approval]` | `mode`: `none`, `control` or `all` | `none` |
| `[land]` | `remote`, `branch`, `review` (`none` or `final`) | origin, main, none |
| `[routes.<role>]` | `provider` (`claude`, `codex`, `command`), `model`, `effort` | Claude |
| `[guard]` | `enabled`: the hook that denies raw git commit and push | true |
| `[timeouts]` | `gate`, `agent`, `git` (ls-remote, fetch, push) in seconds | 1800, 1800, 120 |

Roles are `review-cheap`, `review-final`, `fix` and `implement`. A sensible split is a cheap first
pass on Codex and the binding review on a strong Claude model.

## Outcomes

Every verb prints one JSON outcome. The exit code follows the status:

| `status` | Exit | Meaning |
|---|---|---|
| `committed`, `landed`, `reviewed`, `evaluated`, `done`, `noop` | 0 | Done |
| `blocked`, `error`, `halted` | 1 | Fix the cause or wait for the operator, then rerun |
| `needs_direction` | 2 | A human must answer `questions` |
| `awaiting_approval` | 3 | Approval is configured: `lean-forge approve <run>` |

`needs_direction` comes from:

- `commit`: the review-and-fix iteration cap, a reviewer's question, or a fix agent's question.
- `approve`: the same as `commit`, because approving re-enters the commit.
- `review`: a reviewer's question.
- `implement` or `fix`: the writing agent's question.
- `land`: a final reviewer's question, a commit made outside the chain, or a rebase conflict.

## Agents

Reviewers are read-only. On Claude, they get Read, Grep and Glob plus Bash limited by exact-match
permission rules to a fixed set of complete git read commands (`git status`, `git diff`, `git diff
HEAD`, `git diff --cached`, `git diff --stat HEAD`, `git log --oneline -n 30`, `git ls-files`) and the
configured gate commands verbatim, so no flag such as `--output` can be appended. They run in
`--safe-mode`, so user hooks and plugins do not load. On Codex, they run in the read-only sandbox.
Either way, the engine compares the checkout before and after and rejects a reviewer that changed it.
Running a gate command executes the candidate's code, exactly as the gate step itself does; that is
the one thing a reviewer can run beyond git reads. Writing agents (`fix`, `implement`) also get Edit
and Write, and the engine rejects any that moved HEAD. On Claude a writer can edit the project code a
gate runs, so its containment is that check rather than a sandbox; use Codex's workspace-write
sandbox where you need OS-level containment. A gate string with shell operators (`&&`, `;`) may not
match its exact rule; such a gate still runs in the chain, but agents cannot run it themselves. The
`command` provider runs any program that reads a JSON request on stdin and prints the result; the
tests use it.

Reviewer prompts and the adversarial review constitution live in `prompts/` and `rules/`. The
project's `[review].focus` and its AGENTS.md or CLAUDE.md are appended to every prompt, read from the
same committed ref as the configuration, so a candidate cannot rewrite the guidance that judges it.

## Evals

Golden tasks under `.lean-forge/evals/<id>.md` have front matter `role` and `expected`, plus a fenced
`diff` block that applies to HEAD. `lean-forge eval` stages each diff in a scratch worktree and
reviews it afresh, writing `<id>.result`. `lean-forge eval --check` fails unless every recorded
result matches, so it can be a gate.

## Operator controls

- `lean-forge halt --on "reason"` stops every commit and land in every worktree until
  `lean-forge halt --off`.
- With `approval.mode = "control"` or `"all"`, `commit` returns `awaiting_approval`, and
  `lean-forge approve <run>` approves and commits.

## Deliberately out of scope

- Crash recovery. An interrupted run is rerun.
- Cross-session authorization markers.
- Enforcement against a hostile local process. lean-forge assumes cooperative agents. The hook is a
  guard rail, not a sandbox.
- Distinguishing a commit that went through the chain from one made with the same patch by hand.
  Provenance is checked by patch id.

## Development

```bash
python3 -m unittest discover -s tests   # end-to-end tests with real git repos and a fake agent
ruff check lean_forge tests bin/lean-forge
```

lean-forge is developed with itself. See `.lean-forge/config.toml` and AGENTS.md.

## License

MIT. `rules/review-constitution.md` derives from the MockServer AI-SDLC system under Apache-2.0.
See NOTICE.
