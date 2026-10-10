# Operating lean-forge

lean-forge runs on its own and asks a human only for direction. These are the controls a human has.

## Answering `needs_direction`

The outcome carries `questions`, each answerable in a sentence. The session asks you, acts on the
answer and reruns. Some answers need a human-only action (below). In that case you run the command
yourself, in your own terminal.

## Human-only actions

The hook denies these to agents when they are invoked as `lean-forge` (it matches that word in the
command; see the cooperative-agents caveat under "Deliberately out of scope"):

| Action | Command |
|---|---|
| Approve a commit waiting on approval | `lean-forge approve <run>` |
| Clear a halt | `lean-forge halt --off` |
| Land commits made outside the chain | `lean-forge land --allow-outside-commits` |

Option abbreviations are refused, so `halt --of` cannot stand in for `halt --off`.

## Halt

`lean-forge halt --on "reason"` stops every commit and land in every worktree of the clone until a
human runs `lean-forge halt --off`. `lean-forge halt` shows the current state.

## Approval

Approval is off by default (`approval.mode = "none"`). With `"control"`, a candidate that touches a
control path returns `awaiting_approval` after its review passes. With `"all"`, every candidate does.
`lean-forge approve <run>` records the approval for that run's tree and base, then commits.

## The hook

In a repository with `.lean-forge/config.toml`, the Claude Code hook denies:

- **Raw history writes:** any `git` word followed by a `commit` or `push` word in the same shell
  segment. It is deliberately crude and errs toward denial. Commands that invoke lean-forge itself
  are exempt, so a commit message that mentions git is fine. `[guard].enabled = false` turns this
  part off.
- **The human-only actions above.** These are always denied, whatever `[guard]` says.

The hook does not catch every way to make a local commit, such as `git commit-tree` or
`cherry-pick`. Such a commit cannot reach the base without a human, because `land` refuses commits
made outside the chain and only a human may override that.

## Deliberately out of scope

- Crash recovery. An interrupted run is rerun.
- Cross-session authorization markers.
- Enforcement against a hostile local process. lean-forge assumes cooperative agents. The hook is a
  guard rail, not a sandbox.
- Distinguishing a commit that went through the chain from one made with the same patch by hand.
  Provenance is checked by patch id.
