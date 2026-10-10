---
name: lean-forge-init
description: Set up lean-forge in a git repository: write .lean-forge/config.toml with the project's real gate commands, review focus, control paths and model routes, verify the gates on a clean tree, and adopt it through the chain. Use when asked to "set up lean-forge", "init lean-forge", "adopt lean-forge" or "add the gate chain" to a repository.
---

# Initialising lean-forge in a repository

The goal is a configuration that mirrors what the repository already does. Do not invent a parallel
process. Ask the human only for choices the repository cannot answer.

1. **Write the template.** Run `"${CLAUDE_PLUGIN_ROOT}/bin/lean-forge" init`. It writes
   `.lean-forge/config.toml` unless one exists, and adds `.lean-forge/local.toml` and `.worktrees/`
   to `.gitignore`.
2. **Fill `[gates]` from reality.** Read the CI definitions, package scripts, Makefile or justfile.
   The gates are the commands CI runs: tests, lint and type checks. Order them cheapest first.
   At least one is required: `commit` refuses while `[gates]` is empty.
   Run each gate once on the clean tree. A gate that fails on untouched code is miscalibrated, so fix
   the command, not the code.
3. **Choose `[control].paths`.** These are paths whose changes get the binding `review-final`
   reviewer: the lean-forge config, CI definitions, agent guidance (AGENTS.md, CLAUDE.md), and
   anything else the project treats as a gate. Add tests if a change to tests should get the binding
   reviewer.
4. **Write `[review].focus`.** Give three to six bullets naming this project's real failure modes and
   conventions. Mine them from the git history (fix and revert commits) and from the docs.
5. **Set routes.** The defaults use Claude. If `codex` is installed and the human wants it, Codex
   makes a good cheap first-pass reviewer (`[routes.review-cheap]`). Keep `review-final` on a strong
   model. Per-developer overrides go in `.lean-forge/local.toml`.
6. **Leave approval off** (`mode = "none"`) unless the human asks for it. The system is meant to run
   autonomously and ask only for direction.
7. **Optionally seed evals.** Add two or three golden tasks under `.lean-forge/evals/`: a planted bug
   that must BLOCK, a clean change that must PASS, and an injected instruction that must BLOCK. Each
   is a Markdown file with front matter `role:` and `expected:` and a fenced `diff` block that
   applies to HEAD. Score them with `lean-forge eval`.
8. **Adopt through the chain.** Run `lean-forge commit -m "Adopt lean-forge"`, then `lean-forge land`.
   The first commit reads the configuration from the working tree, because nothing is committed yet.
   Every later run reads it from the remote base, so no change can relax the gates that judge it.
