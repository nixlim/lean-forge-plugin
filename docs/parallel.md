# Running units in parallel

lean-forge has no orchestrator or scheduler of its own. Every verb is a separate process. The
orchestrator is your Claude Code session, or any script, launching `lean-forge implement` and
`lean-forge commit` in several worktrees at once in the background.

## The pattern

1. Split the work into independent units that touch different code.
2. Give each unit its own worktree and branch from the remote base:
   `git worktree add -b agent/<topic> .worktrees/<topic> origin/main`.
3. In each worktree, run `implement` (or do the work) and then `commit`, in the background.
4. Land each unit when it commits.
5. Once a unit has landed, remove it from the main checkout:
   `git worktree remove .worktrees/<topic>` and `git branch -d agent/<topic>`.

## Why it is safe

- **Shared state is locked or unique.** Evidence appends take a file lock, and run ids are effectively
  unique (a collision fails rather than sharing a directory).
- **Commits cannot race.** Each worktree commits to its own branch, with a compare-and-swap on HEAD.
- **Lands take turns.** They queue on one lock. Each land fetches, rebases onto whatever landed
  before it, re-runs the gates on the result and fast-forward pushes. A conflict returns
  `needs_direction`.
- **`halt` stops every worktree at once.**

## What to watch

- **One run per worktree.** Two runs in one worktree share the index and the files. The
  compare-and-swap and the "worktree changed after review" check make the commit fail rather than
  commit the wrong tree, but the agents overwrite each other's edits.
- **Changes that are fine apart but break together.** Each unit is reviewed against the base it
  started from. When another unit lands first, `land` re-runs the gates but not the review, so only a
  gate can catch the clash. Set `[land].review = "final"` to re-review at land, at the cost of one
  `review-final` per land. Units that touch the same code belong in one worktree, one after the
  other.
- **The land lock times out after five minutes** with "land lock held by another session". With slow
  gates and several lands queued, some fail and need a rerun.
- **There are no concurrency limits.** Five parallel units means five reviewers or fixers at once and
  five gate runs competing for CPU. On a shared host, wrap heavy gate commands in your host's slot
  tool (for example `sem-run`). Agents can only be throttled by routing them through the `command`
  provider with a wrapper script.
- **Provenance belongs to the clone.** Commit and land each unit from the same clone; see
  [records](records.md).
