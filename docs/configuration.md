# Configuration

## Where it is read from

`.lean-forge/config.toml` is read from the first of these that has it:

1. The remote base tip as last fetched (`refs/remotes/<remote>/<branch>`).
2. HEAD.
3. The working tree. This happens only for the very first commit, before any configuration is
   committed.

`commit` does not fetch, so it uses whatever the last fetch brought. `land` fetches first and judges
with the fresh base. A candidate therefore never relaxes the gates, routes or guidance that judge it.
`lean-forge config` prints the resolved configuration and the ref it came from.

`.lean-forge/local.toml` is uncommitted (`init` adds it to `.gitignore`). It may override a route's
`model` and `effort` only, so each developer picks their own models. Any other key there is an error.

## Settings

| Section | Keys | Default |
|---|---|---|
| `[gates]` | name = shell command; at least one, and all must exit 0 | none |
| `[control]` | `paths`: path prefixes that get `review-final` | `.lean-forge/`, `.claude/`, `.codex/`, `.github/`, `AGENTS.md`, `CLAUDE.md` |
| `[review]` | `max_iterations`, `autofix`, `focus` | 8, true, "" |
| `[approval]` | `mode`: `none`, `control` or `all` | `none` |
| `[land]` | `remote`, `branch`, `review` (`none` or `final`) | origin, main, none |
| `[routes.<role>]` | `provider` (`claude`, `codex`, `command`), `model`, `effort` | Claude, see below |
| `[guard]` | `enabled`: the hook that denies raw git commit and push | true |
| `[timeouts]` | `gate`, `agent`, `git` (ls-remote, fetch, push) in seconds | 1800, 1800, 120 |

## Gates

Gates are the commands CI runs: tests, lint, type checks. They run in order, in the worktree, through
the shell. Put the cheapest first.

- **At least one is required.** `commit` refuses while `[gates]` is empty, before any reviewer runs.
- **Gates get no arguments.** During `commit` the candidate is staged, so `git diff --cached
  --name-only` lists its paths. During `land` nothing is staged, so a gate that needs the changed
  paths must work them out another way, for example from the merge base.
- **Agents can run gates too.** A reviewer may run a gate command exactly as configured. A gate with
  shell operators (`&&`, `;`) may not match that exact rule. It still runs in the chain, but agents
  cannot run it themselves.

If the committed configuration has no gates, for example because the template was committed
unchanged, `commit` returns `needs_direction`. A working-tree edit cannot fix it, because `commit`
judges with the committed configuration. A human adds the gates with a config-only git commit in
their own terminal, then runs `lean-forge land --allow-outside-commits`. `land` does not refuse an
empty `[gates]`; it re-runs whatever gates the base has, which is what lets this fix through.

## Control paths

Changes under `[control].paths` get the binding `review-final` reviewer, and approval when
`approval.mode = "control"`. List everything the project treats as part of its gates: the lean-forge
configuration, CI definitions, agent guidance, and anything else that would weaken a check if it
changed.

## Review focus

`[review].focus` is appended to every reviewer prompt. Three to six bullets that name the project's
real failure modes work best. lean-forge's own focus is in `.lean-forge/config.toml`.

## Routes

The roles are `review-cheap`, `review-final`, `fix` and `implement` (see [agents](agents.md)). The
template routes all four to Claude: `claude-sonnet-5-5` at medium effort for `review-cheap`, and
`claude-opus-5-5` at high effort for the rest. Sonnet on `review-cheap` is the cost default; the
init skill asks and recommends Opus (see below). lean-forge's own repository routes `review-cheap`
to Codex `gpt-5.6-terra`, `review-final` and `fix` to Claude `claude-opus-5-5`, and `implement` to
Codex `gpt-6-sol`. Almost every path there is a control path, so `review-final` judges nearly
everything.

## Who reviews ordinary code

Each commit gets one reviewer, chosen by its changed paths. Anything outside `[control].paths`, which
in most repositories is the bulk of the source code, gets `review-cheap` and nothing else. Choose
that route as carefully as `review-final`. Three settings give ordinary code a stronger review:

1. **Put Opus on the cheap reviewer.** This is the simplest way to get Opus on every commit:

   ```toml
   [routes.review-cheap]
   provider = "claude"
   model = "claude-opus-5-5"
   effort = "high"
   ```

   This upgrades the model, not the prompt. Both reviewer roles get the same review constitution,
   but only the `review-final` prompt has the control-integrity rule, which makes deleting or
   loosening a test, suppressing a check or narrowing a gate a CRITICAL finding. An Opus
   `review-cheap` reviews without that rule. Options 2 and 3 bring it to ordinary code.
   `local.toml` can change a route's model and effort but not its provider, so changing a
   provider needs a committed configuration change. That change touches `.lean-forge/`, so it
   gets `review-final`.
2. **Add source folders to the control list**, for example `paths = [..., "src/", "lib/"]`. Entries
   are prefix matches. Those paths then get `review-final`, and the rest stay on `review-cheap`. With
   `approval.mode = "control"` they would also need human approval. The default mode is `none`, so
   normally nothing else changes.
3. **Set `[land].review = "final"`.** This adds one `review-final`, with its control-integrity rule,
   over the whole branch at land, on top of each commit's review. `land` has no fix loop, so a BLOCK there means fix, commit again and
   land again.

There is no two-stage review, a cheap pass followed by a final one on the same commit, and no
separate "always final" switch.

The `command` provider takes a `command` key: any program that reads a JSON request on stdin and
prints the result. The tests use it.
