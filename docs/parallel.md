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
- **One run per worktree.** `implement`, `fix`, `review`, `commit`, `land` and `eval` hold a kernel
  lock in their worktree's own git directory until they write their outcome. A second run in that
  worktree returns `error` naming the run in flight. The lock dies with its process, so it never goes
  stale. `eval --check` does not take it, so it still works as a gate; a gate that runs one of the
  locked verbs in its own worktree is refused. `approve` stages the run's worktree before its commit
  takes the lock, so do not approve while another run is in flight there.
- **`halt` stops every worktree at once.**

## What to watch

- **Your own edits during a run.** The lock covers lean-forge runs, not your editor. A run's
  implement and fix agents edit the worktree, and the "worktree changed after review" check only
  refuses a tree that changes after its review. A run can still review, and then commit, half-finished
  edits made while it ran. Wait for a run's outcome, or stop it, before editing its worktree.
- **How a run is stopped matters.** SIGTERM, Ctrl-C or a closed terminal (SIGHUP) stops the run's
  agents and gates with it. A run started under `nohup` (SIGHUP ignored) keeps running when its
  terminal closes. After SIGKILL the worktree is free at once, but the run's agents and
  gates can outlive it and keep editing. Stop them before you rerun or edit there.
- **Changes that are fine apart but break together.** Each unit is reviewed against the base it
  started from. When another unit lands first, `land` re-runs the gates but not the review, so only a
  gate can catch the clash. Set `[land].review = "final"` to re-review at land, at the cost of one
  `review-final` per land. Units that touch the same code belong in one worktree, one after the
  other.
- **The land lock times out after five minutes** with "the land lock is held by run <id>". With slow
  gates and several lands queued, some fail and need a rerun.
- **There are no concurrency limits.** Five parallel units means five reviewers or fixers at once and
  five gate runs competing for CPU. On a shared host, wrap heavy gate commands in your host's slot
  tool (for example `sem-run`). Agents can only be throttled by routing them through the `command`
  provider with a wrapper script.
- **Provenance belongs to the clone.** Commit and land each unit from the same clone; see
  [records](records.md).
