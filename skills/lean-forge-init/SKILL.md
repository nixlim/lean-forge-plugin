---
name: lean-forge-init
description: Set up lean-forge in a git repository: write .lean-forge/config.toml with the project's real gate commands, review focus, control paths and model routes, verify the gates on a clean tree, and adopt it through the chain. Use when asked to "set up lean-forge", "init lean-forge", "adopt lean-forge" or "add the gate chain" to a repository.
---

# Initialising lean-forge in a repository

The goal is a configuration that mirrors what the repository already does. Do not invent a parallel
process. Ask the human only for choices the repository cannot answer.

## Coming from forge-plugin

If `forge-project.md` or `.forge-manifest` exists, forge-plugin set this repository up. Do these three
things first:

1. **Open forge work.** If `.codex-orchestrator/runs/` or `.forge/chains/` holds runs or chains, list
   them and ask the human which are finished and which can be abandoned. Don't try to finish them;
   nothing in lean-forge resumes them. A branch with forge commits that never landed fails
   `lean-forge land`'s provenance check. Redo that work through lean-forge, or ask the human to land
   it with `lean-forge land --allow-outside-commits` in their own terminal. The hook denies that
   switch to you.
2. **The forge plugin.** Run `claude plugin list`. If any `forge@...` entry is enabled, ask the human to
   disable it in each scope (`claude plugin disable forge@forge --scope <scope>`) and restart the
   session. Otherwise its hooks and skills keep running beside lean-forge.
3. **Guidance.** Rewrite CLAUDE.md and AGENTS.md. Remove the `@forge-project.md` import, the DVRR spine,
   references to forge skills, and any rule that commits must go through forge chains or need
   operator approval. Keep the project's own content: build commands, conventions, coordination.
   lean-forge appends both files to every reviewer prompt, so leftover forge rules make reviewers
   block or ask for approval.

Then follow the steps below. Step 2 also reads `forge-project.md`, and step 8 also removes forge's
files.

## Steps

1. **Write the template.** Run `"${CLAUDE_PLUGIN_ROOT}/bin/lean-forge" init`. It writes
   `.lean-forge/config.toml` unless one exists, and adds `.lean-forge/local.toml` and `.worktrees/`
   to `.gitignore`.
2. **Fill `[gates]` from reality.** Read the CI definitions, package scripts, Makefile or justfile.
   The gates are the commands CI runs: tests, lint and type checks. Order them cheapest first.
   At least one is required: `commit` refuses while `[gates]` is empty.
   In a forge repository, also port the gates from `forge-project.md`: the Gate 1 test command, the
   stack validations, the changelog policy and the executable invariants. Forge passed the
   candidate's paths as arguments and lean-forge passes none. Rewrite each such cell as a plain
   command. Gates run twice: `commit` runs them with the candidate staged, and `land` re-runs them
   on the committed, rebased tree with nothing staged, so `git diff --cached` is empty there. For a
   gate that needs the changed paths, use `git diff --name-only $(git merge-base HEAD <remote>/<base>)`,
   which lists them in both cases.
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
   In a forge repository, the same commit deletes forge's tracked files: `forge-project.md`,
   `.forge-manifest`, `.forge/` (keep `.forge/history/` if the human wants the record) and the
   `.codex/` files forge installed. Ask the human about any `.codex/` file that may be their own.
   `.codex-orchestrator/` is untracked; tell the human it can be deleted.
