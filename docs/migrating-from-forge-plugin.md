# Migrating from forge-plugin

The `lean-forge-init` skill detects a forge-plugin repository (`forge-project.md` or
`.forge-manifest`) and walks through these steps. This page explains them.

The two plugins do not collide mechanically. lean-forge reads its configuration only from
`.lean-forge/`, plus AGENTS.md and CLAUDE.md as reviewer guidance, and keeps its state in
`.git/lean-forge/`. It never reads `.forge/`, `forge-project.md` or the manifest. The trouble is
what forge-plugin leaves in the repository.

## What goes wrong if you just switch

1. **The guidance still says forge.** CLAUDE.md imports `@forge-project.md`, and AGENTS.md carries the
   DVRR spine and the rule that only forge chains may commit. The session keeps reaching for forge's
   commit and merge skills, which are gone. lean-forge also appends AGENTS.md and CLAUDE.md to every
   reviewer prompt, so reviewers are told that control changes need operator approval and the forge
   chain. They block or ask questions.
2. **Gates do not carry over on their own.** Forge's gates live in `forge-project.md`: the Gate 1 test
   command, stack validations, the changelog policy and executable invariants. Forge passed the
   candidate's paths as arguments and lean-forge passes none, so a cell such as forge's changelog
   check fails with "no target paths supplied" until it is rewritten.
3. **Forge's installed files stay.** `forge-project.md`, `.forge-manifest`, `.forge/` and the `.codex/`
   files forge installed remain tracked. `.codex/` matters most: lean-forge's Codex agents run in the
   repository, Codex may load that project configuration, and forge's Codex hooks reference
   `${CLAUDE_PLUGIN_ROOT}`, which is unset there. Deleting them avoids the question.
4. **Open forge work is orphaned.** Unfinished runs in `.codex-orchestrator/runs/` and chains in
   `.forge/chains/` stay, and nothing resumes them. A branch with forge commits that never landed
   fails `lean-forge land`'s provenance check.
5. **Some forge features have no equivalent:** per-path triggers, risk tiers, STRICT evals, the
   changelog policy, drift sensing, candidate-bound approvals and host slots. Anything you still want
   comes back as a gate command.

Disabling forge-plugin removes its Claude hooks (the commit guard, stop hooks and session hooks),
because they come from the plugin, not the repository.

## The order that works

1. **Finish or abandon open forge work.** Redo unlanded forge commits through lean-forge, or have a
   human land them with `lean-forge land --allow-outside-commits`.
2. **Disable forge-plugin** in every scope where it is enabled (`claude plugin list`, then
   `claude plugin disable forge@forge --scope <scope>`), install lean-forge, and restart the session.
3. **Rewrite CLAUDE.md and AGENTS.md.** Remove the forge import, the DVRR spine, forge skill
   references and any rule that commits go through forge chains or need operator approval. Keep the
   project's own content.
4. **Run the init skill.** It ports gates from `forge-project.md` as well as CI.
5. **Delete forge's tracked files in the adoption commit,** then commit and land. Keep
   `.forge/history/` if you want the record. `.codex-orchestrator/` is untracked and can simply be
   deleted.

The adoption commit reads its configuration and guidance from the working tree, because nothing is
committed yet, so the first reviewer already sees the rewritten guidance.
