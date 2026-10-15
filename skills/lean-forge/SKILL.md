---
name: lean-forge
description: Run work autonomously through the lean-forge gate chain in a repository that has .lean-forge/config.toml. Use whenever you are about to commit, push, land, review or delegate implementation in such a repository, or when the user says "commit this", "land it", "ship it", "run the gates", "review my changes" or "have codex implement X".
---

# Working under lean-forge

lean-forge is the only way changes are committed and landed in this repository. It runs the
project's gates, an independent reviewer and a capped fix loop, then commits the exact tree that was
reviewed. You orchestrate and decide. You never ask the human for permission to commit. You ask the
human only when the chain returns `needs_direction`, or when the task itself is ambiguous in a way
the repository cannot answer.

The CLI is `"${CLAUDE_PLUGIN_ROOT}/bin/lean-forge"`, written `lean-forge` below. Every verb prints one
JSON outcome on stdout and a one-line summary on stderr. Exit codes: 0 done, 1 blocked, error or
halted, 2 needs_direction, 3 awaiting_approval.

## The loop

1. **Work in your own worktree.** Start from the remote base, for example
   `git worktree add -b agent/<topic> .worktrees/<topic> origin/main`, and work only there. Never
   work in a worktree another session created.
2. **Implement the unit.** Do it yourself, or delegate with `lean-forge implement "<brief>"` (or
   `@brief.md`), which runs the configured implement route, Claude or Codex, in this worktree. Treat
   its summary as a claim: read the diff before moving on.
3. **Commit the unit.** `lean-forge commit -m "<why and what>"` commits the whole worktree state.
   A review round takes minutes, so run it in the background and wait for it rather than polling.
   Leave the worktree alone until it returns: one run per worktree at a time (see Rules).
4. **Act on the outcome:**

   | `status` | What you do |
   |---|---|
   | `committed` | Continue with the next unit. |
   | `blocked` | Read `stage` and `findings`, `hits` or `reason`. Fix the cause and run commit again. |
   | `needs_direction` | Ask the human the `questions` with AskUserQuestion, offering the options each question names. Act on the answer, then rerun. If the answer needs `approve`, `halt --off` or `land --allow-outside-commits`, ask the human to run it in their own terminal; the hook denies them to you. |
   | `awaiting_approval` | Approval is configured for this change. Tell the human to run `lean-forge approve <run>`. |
   | `halted` | Stop and report the reason. Never clear a halt. |
   | `error` | Read `reason` (configuration, provider login, a reviewer that tried to write, another run in flight in this worktree) and fix the cause. |
   | `noop` | Nothing to commit or land. |

5. **Land.** When the units are done, run `lean-forge land`. It rebases onto the remote base under a
   lock, re-runs the gates and fast-forward pushes. A rebase conflict returns `needs_direction`:
   resolve it yourself when the right combination is clear from the code, otherwise ask. A land that
   stops before its push leaves the branch as it found it: fix the cause with `lean-forge commit`,
   then rerun `land`.
6. **Clean up** once the outcome is `landed`: from the main checkout, `git worktree remove
   .worktrees/<topic>` and `git branch -d agent/<topic>`.

## Running several units at once

Give each independent unit its own worktree and branch, as in step 1, and run its `implement` and
`commit` in the background. Each worktree still takes one run at a time (see Rules).

- **Land each unit when it commits.** Lands take turns on a lock. Each one rebases onto whatever
  landed before it and re-runs the gates. A conflict returns `needs_direction`.
- **Unless `[land].review` is set, only the gates check units against each other.** Each review saw
  the base its unit started from. Units that touch the same code belong in one worktree, one after
  the other.
- **A land that waits more than five minutes for the lock returns `error`.** Rerun it.
- **Every unit runs its own agents and gates at the same time.** Run only as many as the machine and
  the budget can take.
- **Clean up each worktree as its unit lands,** as in step 6.

## Rules

- Never run `git commit` or `git push` yourself. The hook denies it in lean-forge repositories.
- Run one lean-forge run per worktree at a time. `implement`, `fix`, `review`, `commit`, `land` and
  `eval` hold their worktree until they print their outcome, and a second one there returns `error`
  naming the run in flight: wait for that outcome or stop that run, then rerun. Nothing stops your
  own edits, so leave the worktree alone while a run is in flight. Its implement and fix agents edit
  the same files, and the run would review and commit your half-finished changes. Stopping a run with
  SIGTERM, Ctrl-C or a closed terminal also stops its agents and gates. A run started under `nohup`
  (SIGHUP ignored) keeps running when its terminal closes. After SIGKILL the worktree is
  free, but its agents and gates may still be running and editing, so stop them first. Then read the
  diff before you continue, because an agent may have left edits behind. A `running` row in
  `lean-forge runs` is no proof the run is alive: a killed run shows `running` forever.
- Never edit lean-forge state under the git directory (`lean-forge/` evidence, runs and halt), and
  never clear a halt.
- Never weaken a gate, a test or a reviewer prompt to get a PASS. The reviewer blocks it and the
  round is wasted.
- Reruns are cheap and safe: evidence for an unchanged tree is reused, so a rerun skips gates and
  reviews that already passed.
- `lean-forge runs` is the dashboard. `lean-forge runs <id>` shows every step with time and cost.
- The reference is in `${CLAUDE_PLUGIN_ROOT}/docs/`: primitives, configuration, agents, records,
  parallel units, evals, operating and migrating from forge-plugin.
- After updating lean-forge or changing a reviewer route, run `lean-forge eval` if the repository has
  golden tasks. A result that differs from `expected` means the reviewers regressed; tell the human.
- `lean-forge review` reviews the current state without committing. `lean-forge config` shows the
  resolved configuration and which ref it came from.
