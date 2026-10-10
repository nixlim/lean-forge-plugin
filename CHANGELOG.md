# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
