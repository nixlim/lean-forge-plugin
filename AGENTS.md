# Working on lean-forge

lean-forge is deliberately small. Before adding anything, check whether a rerun, a gate command or
an existing primitive already covers it.

- **Standard library only, Python 3.11 or newer.** No third-party runtime dependencies.
- **Evidence, not state.** Effectful primitives check append-only evidence for the exact git tree they
  act on. There is no state machine, no markers, no replay and no crash recovery: any failure means
  rerun. Mechanism that brings those back needs a real incident to justify it.
- **Keep the core small.** `lean_forge/` stays near 1,100 lines. Growth past about 1,200 is a signal to
  stop and simplify.
- **Agents are bounded.** Reviewers stay read-only (exact-match tool rules on Claude, never prefix rules; the read-only
  sandbox on Codex, a before-and-after checkout comparison everywhere). Writing agents edit files and
  never move HEAD.
- **Ask a human only for direction.** New escalation paths return `needs_direction` with a question
  the human can answer in a sentence. Nothing asks for permission to commit unless approval is
  configured.
- **Tests are end to end.** They drive real git repositories and a bare origin through the scripted
  fake agent in `tests/fake_agent.py`. A new control needs a test that fails when the control is
  removed.
- **Commit and land through lean-forge itself, but a trusted copy of it:** the installed plugin or a
  checkout of origin/main, never this worktree's `bin/lean-forge`. The worktree is the candidate, and
  a candidate that edits the engine, prompts or rules must not be judged by its own edits. For
  example `<trusted checkout>/bin/lean-forge -C . commit -m "..."`, then `... -C . land`.
