# lean-forge

An autonomous, mechanical gate chain for agent-written code, packaged as a Claude Code plugin.

Agents write code quickly. Checking it is the slow part. lean-forge makes the checking mechanical and
leaves the decisions to agents. Every unit of work goes through the project's gates, a secrets scan
and an independent reviewer in a fresh context, with a capped fix loop. The commit is exactly the
tree that was reviewed, and branches land by a locked rebase and a fast-forward push. A human is
asked only for direction, never for permission to commit.

lean-forge is the lean successor to [forge-plugin](https://github.com/nixlim/forge-plugin). It keeps
the parts that caught real defects (independent review, a hard iteration cap, structured verdicts,
gates read from committed configuration) and drops the heavyweight guarantees. The core is about
1,200 lines of standard-library Python.

## Requirements

- Python 3.11 or newer, and git.
- The `claude` CLI (Claude Code) and/or the `codex` CLI, logged in, for the agent roles you route to
  them.

## Install

```bash
claude plugin marketplace add nixlim/lean-forge-plugin
claude plugin install lean-forge@lean-forge
```

Then ask Claude Code to "set up lean-forge" in a repository. The `lean-forge-init` skill writes the
configuration from what the repository's CI already runs. It also migrates a repository that used
forge-plugin. Without Claude Code, clone this repository and put `bin/lean-forge` on your `PATH`.

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

## Documentation

- [Primitives and workflows](docs/primitives.md): what each verb does, the one guarantee, outcomes.
- [Configuration](docs/configuration.md): every setting, where it is read from, gates.
- [Agents](docs/agents.md): the four roles, providers, read-only reviewers, what reviewers see.
- [Records and the dashboard](docs/records.md): evidence, run journals, what is and isn't kept.
- [Running units in parallel](docs/parallel.md): fan-out, landing, cleanup, limits.
- [Evals](docs/evals.md): golden tasks that check the reviewers.
- [Operating](docs/operating.md): halt, approval, human-only actions, the hook, what is out of scope.
- [Migrating from forge-plugin](docs/migrating-from-forge-plugin.md).

## Development

```bash
python3 -m unittest discover -s tests   # end-to-end tests with real git repos and a fake agent
ruff check lean_forge tests bin/lean-forge
```

lean-forge is developed with itself. See `.lean-forge/config.toml` and AGENTS.md.

## License

MIT. `rules/review-constitution.md` derives from the MockServer AI-SDLC system under Apache-2.0.
See NOTICE.
