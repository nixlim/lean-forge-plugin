# Primitives and workflows

lean-forge is a handful of primitives and two workflows, `commit` and `land`, written as plain Python
functions. There is no engine and no state machine. Each primitive acts on an explicit git tree id and
records append-only evidence (see [records](records.md)).

## Primitives

| Primitive | CLI verb | What it does |
|---|---|---|
| classify | | Picks the reviewer a tree needs: `review-final` if any changed path is under `[control].paths`, otherwise `review-cheap`. |
| stage | | `git add -A` and `write-tree`: the tree id every later step acts on. |
| gates | | Runs each configured gate in the worktree. A gate that passed for the same tree and command is skipped. |
| scan | | Flags added lines that look like credentials, reported as `path:line`, never the match. |
| review | `review` | A fresh reviewer judges exactly `git diff <base> <tree>`. `--role` forces a role. |
| fix | `fix` | The fix agent edits the worktree to address findings (text or `@file`). |
| implement | `implement` | The implement agent carries out a brief (text or `@file`) in the worktree. |
| approve | `approve <run>` | Records operator approval for the tree and base a commit run is waiting on, then commits. |
| commit | `commit -m` | The commit workflow below. `-m @file` reads the message from a file; `--no-autofix` stops at the first BLOCK. |
| land | `land` | The land workflow below. |
| eval | `eval` | Scores golden tasks ([evals](evals.md)). |
| halt | `halt` | Shows, sets (`--on "reason"`) or clears (`--off`) a stop on every commit and land. |

Other verbs: `runs` (the [dashboard](records.md)), `init` (writes the configuration template),
`config` (prints the resolved configuration and its source), `hook` (the Claude Code hook) and
`version`. Every verb accepts `-C <path>` to run as if started there.

## The one guarantee

The commit primitive refuses unless at least one gate is configured and evidence for that exact tree
shows all of these:

- every gate passed,
- the secrets scan was clean,
- the required reviewer said PASS with no open questions, against the current HEAD,
- an approval exists, when approval is configured for this change.

Ordering comes from these preconditions, not from an engine, so the primitives compose in any order
and unsafe orders simply fail. The required reviewer is derived from the tree's own changed paths. A
commit is made with `git commit-tree` and a compare-and-swap `update-ref`, so it is exactly the
reviewed tree, and a HEAD that moved meanwhile fails instead of racing.

## commit

`commit` checks for a halt, a non-empty message and at least one gate, then repeats until PASS or the
cap:

1. Stage the whole worktree.
2. Run the gates. A failure becomes the round's findings.
3. Scan for secrets. A hit returns `blocked`; remove it and rerun.
4. Review with a fresh reviewer. Control paths get `review-final`; everything else gets `review-cheap`.
5. On BLOCK, the fix agent addresses the findings and the next round starts.

The cap is `[review].max_iterations`, 8 by default. Reaching it returns `needs_direction`. A reviewer
or fix agent that asks a question also returns `needs_direction`. If approval is configured for the
change, a PASS returns `awaiting_approval` instead of committing.

The message is fixed when the run starts. If the fix agent changes the scope of the work, update the
message and rerun.

## land

1. Refuse uncommitted changes, a detached HEAD or a halt.
2. Fetch the remote base and list the commits ahead of it. Any commit whose patch id was not made by
   `commit` returns `needs_direction`. Only a human may answer by running
   `lean-forge land --allow-outside-commits`.
3. Under a lock in the git common directory, fetch again and rebase onto the remote base. A conflict
   aborts the rebase and returns `needs_direction`.
4. Reload the configuration from the base just fetched, and re-run its gates on the rebased tree.
5. If `[land].review = "final"`, run a fresh `review-final` over everything since the merge base.
6. Fast-forward push. History stays linear.

A land that waits more than five minutes for the lock returns `error`; rerun it.

## Reruns

Gates and reviews that already passed for an unchanged tree are skipped, so a rerun costs nothing
for the work already done. Any failure means "fix or answer, then rerun". There is no recovery
protocol.

## Outcomes

Every verb prints one JSON outcome on stdout and a one-line summary on stderr. The exit code follows
the status:

| `status` | Exit | Meaning |
|---|---|---|
| `committed`, `landed`, `reviewed`, `evaluated`, `done`, `noop`, `ok`, `halt` | 0 | Done (`ok` from `config`, `init`, `runs` and a clean `eval --check`; `halt` from the `halt` verb) |
| `blocked`, `error`, `halted` | 1 | Fix the cause or wait for the operator, then rerun (`eval --check` reports `blocked` on a mismatch) |
| `needs_direction` | 2 | A human must answer `questions` |
| `awaiting_approval` | 3 | Approval is configured: a human runs `lean-forge approve <run>` |

`needs_direction` comes from:

- `commit`: the iteration cap, a reviewer's question, a fix agent's question, or empty `[gates]` in
  the committed configuration.
- `approve`: the same as `commit`, because approving re-enters the commit.
- `review`: a reviewer's question.
- `implement` or `fix`: the writing agent's question.
- `land`: a final reviewer's question, a commit made outside the chain, or a rebase conflict.
