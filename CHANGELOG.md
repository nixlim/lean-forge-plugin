# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- AGENTS.md raises the core-size guideline from about 1,200 to about 1,500 lines, by operator
  decision on 2026-10-11, after the per-worktree run lock took `lean_forge/` to 1,212 lines. It stays
  a guideline, not a limit: growth past about 1,500 is still the signal to stop and simplify. The
  README said the core was under 1,200 lines, which is no longer true; it now says about 1,200.
- The lean-forge skill makes one run per worktree at a time a rule: before starting another
  `implement`, `fix`, `commit` or `land` in a worktree, or editing its files by hand, wait for the
  previous run's outcome or stop it. Two overlapping runs in one worktree reviewed and committed each
  other's half-finished changes. `docs/parallel.md` no longer says the commit checks prevent this:
  they stop a tree that changes after review, not a reviewed tree that holds another run's edits.
- The init skill asks which model reviews ordinary code, defaulting to Opus, because `review-cheap`
  is the only review for paths outside `[control].paths`. It no longer calls Codex a good cheap
  first pass. `docs/configuration.md` explains the three ways to give ordinary code a stronger
  review.
- The README is now a short overview. The reference moved to `docs/`: primitives, configuration,
  agents, records and the dashboard, parallel units, evals, operating, and migrating from
  forge-plugin.
- The skills tell the agent that a human runs `approve`, `halt --off` and
  `land --allow-outside-commits`, because the hook denies them to agents.
- AGENTS.md says to re-score the reviewers with `lean-forge eval` from the trusted copy after a
  reviewer change lands. The `evals` gate stays: it stops a recorded result that differs from its
  expectation from landing.
- The Claude hook reserves `approve`, `halt --off` and `land --allow-outside-commits` for a human in
  their own terminal.
  An agent can still make a local commit outside the chain, but it can no longer land one.
- The commit primitive derives the required reviewer and control status from the tree's changed paths.
- Approval evidence is keyed by both tree and reviewed base.
- `init` adds `.worktrees/` to `.gitignore`.
- Reviewer and writer checks now detect changes to `.lean-forge/local.toml`.
- The README describes the core as about 1,200 lines and lists every `needs_direction` source.
- `commit` refuses while `[gates]` is empty, before any reviewer runs, so the secrets scan and a
  reviewer are never the only checks on a `commit`. `land` is unchanged: it re-runs the base's gates,
  whatever they are, and does not run the scan.
- Migration: a repository that committed the template unchanged has empty `[gates]` on its base, and
  `commit` now returns `needs_direction` there. Add the gates in a config-only `git commit` and land
  it with `lean-forge land --allow-outside-commits`.

### Added

- A per-worktree run lock. `implement`, `fix`, `review`, `commit`, `land` and `eval` hold a kernel
  lock in their worktree's own git directory until they write their outcome, and a second run in that
  worktree returns `error` naming the run in flight. Other worktrees are unaffected. The lock dies
  with its process, so it never goes stale; after SIGKILL the run's agents and gates may still be
  running. `eval --check` does not take it, so it still works as a gate. The land lock now uses the
  same helper and names the run that holds it.
- The lean-forge skill covers running several units at once and removing their worktrees after they
  land, and says to run `lean-forge eval` after updating lean-forge or changing a reviewer route.
- The init skill migrates a forge-plugin repository. It asks about open forge runs and an enabled
  forge plugin, rewrites CLAUDE.md and AGENTS.md, ports gates from `forge-project.md` and deletes
  forge's tracked files in the adoption commit.
- An end-to-end bootstrap test for a first commit and push to an empty bare remote.
- Three golden review tasks covering a weakened commit guarantee, a clean docs edit, and a prompt injection.

### Fixed

- A landed outcome now carries the land review's findings and summary
  ([#2](https://github.com/nixlim/lean-forge-plugin/issues/2)). The land review records no evidence
  so that it is never served from cache, and the run log kept only the number of findings, so the
  notes of a review that passed were lost.
- A land that stops before its push now puts the branch back on the commits it started from
  ([#1](https://github.com/nixlim/lean-forge-plugin/issues/1)). It used to leave the rebased
  commits in place. When the rebase had changed the lines around a hunk, for example a new bullet
  that another unit landed at the top of a changelog, the commit's patch id changed. The next land
  then refused it, and every commit on top of it, as made outside the chain, and only a human could
  land them.
- The test suite removes its temporary directories, and `eval` removes its scratch directory. Both
  were left in `/tmp`, one per test and one per fixture, and on a shared host thousands of them used
  up the tmpfs inodes.
- The init skill says that `init` also adds `.worktrees/` to `.gitignore` and never overwrites an
  existing configuration.
- Stopping a run (SIGTERM or Ctrl-C) now kills its gate or agent process group instead of orphaning it.
- A closed terminal (SIGHUP) now stops a run the same way, so the run's agents no longer keep editing a
  worktree whose lock has already been released. A run started under `nohup` (SIGHUP ignored) keeps
  running.
- The hook recognises operator-only verbs however lean-forge is invoked (`python3 bin/lean-forge approve`).
- The configuration template lives in `templates/config.toml` instead of being duplicated in the CLI.
  `templates/` is a control path, so edits to the shipped defaults still get review-final.
- Option abbreviations are refused (`halt --of` no longer means `halt --off`), so the hook's exact
  match on operator-only options cannot be sidestepped.
- Eval runs record each reviewer's summary and findings in the run log, so a recorded result can be
  audited for being right for the right reason.

### Added (after the follow-ups landed)

- Recorded results for the three golden tasks, scored against the landed code with the real lanes
  (Codex gpt-5.6-terra for review-cheap, Claude Opus 5.5 for review-final), and an `evals` gate in
  this repository's configuration (`lean-forge eval --check`).

## [0.1.0] - 2026-10-10

### Added

- `lean-forge` CLI (Python 3.11+ standard library only) with the verbs `commit`, `land`, `review`,
  `implement`, `fix`, `eval`, `approve`, `runs`, `halt`, `init`, `config` and `hook`.
- `commit` runs the configured gates, a secrets scan and an independent review loop over the whole
  worktree (fresh reviewer every round, structured verdict, cap of 8, fix agent on BLOCK), then
  commits exactly the reviewed tree.
- `land` rebases onto the remote base under a lock, re-runs the gates on the result and
  fast-forward pushes; commits made outside the chain and rebase conflicts ask for direction.
- Evidence keyed by git tree id gates the effectful primitives, so they compose in any order.
- Optional human approval (`approval.mode = none | control | all`, default `none`).
- Providers: Claude Code (read-only reviewers via exact-match tool rules: fixed git read commands
  and the gate commands verbatim), Codex
  (read-only sandbox for reviewers) and a `command` provider for custom agents and tests.
- Golden-task evals for the reviewer lanes (`eval`, `eval --check`).
- Claude Code plugin packaging: a PreToolUse hook that denies raw git commit and push in lean-forge
  repositories, and the `lean-forge` and `lean-forge-init` skills.
