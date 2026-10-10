# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- The Claude hook reserves `approve` and `halt --off` for a human in their own terminal.
- The commit primitive derives the required reviewer and control status from the tree's changed paths.
- Approval evidence is keyed by both tree and reviewed base.
- `init` adds `.worktrees/` to `.gitignore`.
- Reviewer and writer checks now detect changes to `.lean-forge/local.toml`.
- The README describes the core as about 1,200 lines and lists every `needs_direction` source.

### Added

- An end-to-end bootstrap test for a first commit and push to an empty bare remote.
- Three golden review tasks covering a weakened commit guarantee, a clean docs edit, and a prompt injection.

### Fixed

- Stopping a run (SIGTERM or Ctrl-C) now kills its gate or agent process group instead of orphaning it.
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
