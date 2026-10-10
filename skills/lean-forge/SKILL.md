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
4. **Act on the outcome:**

   | `status` | What you do |
   |---|---|
   | `committed` | Continue with the next unit. |
   | `blocked` | Read `stage` and `findings`, `hits` or `reason`. Fix the cause and run commit again. |
   | `needs_direction` | Ask the human the `questions` with AskUserQuestion, offering the options each question names. Act on the answer, then rerun. |
   | `awaiting_approval` | Approval is configured for this change. Tell the human to run `lean-forge approve <run>`. |
   | `halted` | Stop and report the reason. Never clear a halt. |
   | `error` | Read `reason` (configuration, provider login, a reviewer that tried to write) and fix the cause. |
   | `noop` | Nothing to commit or land. |

5. **Land.** When the units are done, run `lean-forge land`. It rebases onto the remote base under a
   lock, re-runs the gates and fast-forward pushes. A rebase conflict returns `needs_direction`:
   resolve it yourself when the right combination is clear from the code, otherwise ask.
6. **Clean up** the worktree once the outcome is `landed`.

## Rules

- Never run `git commit` or `git push` yourself. The hook denies it in lean-forge repositories.
- Never edit lean-forge state under the git directory (`lean-forge/` evidence, runs and halt), and
  never clear a halt.
- Never weaken a gate, a test or a reviewer prompt to get a PASS. The reviewer blocks it and the
  round is wasted.
- Reruns are cheap and safe: evidence for an unchanged tree is reused, so a rerun skips gates and
  reviews that already passed.
- `lean-forge runs` is the dashboard. `lean-forge runs <id>` shows every step with time and cost.
- `lean-forge review` reviews the current state without committing. `lean-forge config` shows the
  resolved configuration and which ref it came from.
