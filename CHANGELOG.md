# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

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

- The lean-forge skill covers running several units at once and removing their worktrees after they
  land, and says to run `lean-forge eval` after updating lean-forge or changing a reviewer route.
- The init skill migrates a forge-plugin repository. It asks about open forge runs and an enabled
  forge plugin, rewrites CLAUDE.md and AGENTS.md, ports gates from `forge-project.md` and deletes
  forge's tracked files in the adoption commit.
- An end-to-end bootstrap test for a first commit and push to an empty bare remote.
- Three golden review tasks covering a weakened commit guarantee, a clean docs edit, and a prompt injection.

### Fixed

- The init skill says that `init` also adds `.worktrees/` to `.gitignore` and never overwrites an
  existing configuration.
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
