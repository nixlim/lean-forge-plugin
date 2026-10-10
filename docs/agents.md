# Agents

## Roles

| Role | When it runs | Writes? |
|---|---|---|
| `review-cheap` | `commit` review of a candidate with no control paths | No |
| `review-final` | `commit` review of a candidate that touches a control path; the land review when `[land].review = "final"`; `eval` for fixtures that name it | No |
| `fix` | After a BLOCK in `commit`, when `autofix` is on; `lean-forge fix` | Yes |
| `implement` | `lean-forge implement "<brief>"` | Yes |

A PASS from `review-final` also satisfies a non-control candidate. A `review-cheap` PASS never
satisfies a control candidate.

Each review starts in a fresh context: a new process with no memory of earlier rounds. Reviewers
return a structured verdict with `verdict` (PASS or BLOCK), `findings`, `summary` and `questions`.
Writers return `summary` and `questions`. Any question stops the chain with `needs_direction`.

## What a reviewer sees

- The candidate diff, inline (very large diffs are truncated; the reviewer reads the rest with git).
- The role prompt from `prompts/` and, for reviewers, the adversarial review constitution from
  `rules/review-constitution.md`.
- The project's `[review].focus`, AGENTS.md and CLAUDE.md, read from the same committed ref as the
  configuration, so a candidate cannot rewrite the guidance that judges it. At the very first commit
  they come from the working tree.

The reviewer does not see the commit message.

## Providers

- **claude** runs `claude -p` in `--safe-mode`, so user hooks and plugins do not load, with a JSON
  schema for the result, no session persistence, and an explicit tool list.
- **codex** runs `codex exec` with an output schema, `approval_policy=never`, an ephemeral session,
  and the read-only sandbox for reviewers or the workspace-write sandbox for writers.
- **command** runs any program that reads a JSON request on stdin and prints the result.

Every agent runs in its own process group with a timeout (`[timeouts].agent`). Stopping a run kills
the whole group.

## Read-only reviewers

- **On Claude**, reviewers get Read, Grep and Glob, plus Bash limited by exact-match permission rules
  to a fixed set of complete git read commands (`git status`, `git diff`, `git diff HEAD`,
  `git diff --cached`, `git diff --stat HEAD`, `git log --oneline -n 30`, `git ls-files`) and the
  configured gate commands verbatim. Rules are exact, never prefixes, so no flag such as `--output`
  can be appended.
- **On Codex**, reviewers run in the read-only sandbox.
- **Everywhere**, the engine compares the checkout, including `.lean-forge/local.toml`, before and
  after a reviewer, and rejects a reviewer that changed it.

Running a gate command executes the candidate's code, exactly as the gate step does. That is the one
thing a reviewer can run beyond git reads.

## Writers

`fix` and `implement` also get Edit and Write. The engine rejects a writer that moved HEAD or changed
`.lean-forge/local.toml`. On Claude a writer can edit the project code a gate runs, so its containment
is that check rather than a sandbox. Use Codex's workspace-write sandbox where you need OS-level
containment. Treat a writer's summary as a claim and read the diff.
