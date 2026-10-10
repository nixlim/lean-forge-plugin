# Records and the dashboard

All state lives in `.git/lean-forge/`, the git common directory. Every worktree of the clone shares
it, and it is never committed or pushed.

## Evidence: `evidence.jsonl`

The authority. It is append-only: each line is one fact with a UTC timestamp, written under a file
lock, and nothing is ever edited or deleted. `commit` and `land` decide by looking facts up here, and
reruns skip gates and reviews that already passed for the same tree.

Every fact has `kind` and `at` (the UTC timestamp), plus the fields for its kind:

| Kind | Fields |
|---|---|
| `gate` | tree, name, cmd, ok, run |
| `scan` | tree, base, ok, run |
| `review` | tree, base, role, verdict, findings, summary, questions, run |
| `approval` | tree, base, by |
| `commit` | tree, sha, patch_id |
| `land` | sha, tree, onto |

## Run journals: `runs/<id>/`

One directory per verb invocation:

- `log.jsonl`: timed steps. Start (branch, HEAD, cwd), where the configuration came from, each round,
  each gate with its duration, the scan, each agent call (route, cost, tokens, seconds), each
  verdict, and finish.
- `outcome.json`: the final outcome, with status, cost, tokens and seconds.
- `message.txt`: the commit message, for commit runs.

## The dashboard

`lean-forge runs`, in the repository or any of its worktrees, prints the last 20 runs:

```
20261011-004313-b4c5  land      landed            $   0.00     5.4s  8a2f9aecc9da
20261011-004233-36af  eval      evaluated         $   0.08    32.3s
20261011-003434-eba2  commit    committed         $   3.13   445.2s  8a2f9aecc9da
```

The columns are run id, workflow, status, cost, time, and the sha or the reason it stopped. A run
still in progress shows `running` until it writes its outcome. `lean-forge runs <id>` prints one
run's outcome and every step. Review findings are in `evidence.jsonl`.

## What is committed

Only the eval `.result` files, and git history itself.

## What is not kept

- **Agent transcripts.** Only the structured result survives. Inside `commit`, a fix agent's summary
  is kept only if it asked a question; the `implement` and `fix` verbs keep it in their outcome.
- **A decision journal, telemetry, drift history, retention or pruning.** The files grow forever, and
  each evidence lookup reads the whole file. That is fine at this scale.
- **Interrupted runs.** A run that is killed writes no outcome, so the dashboard shows it as `running`
  forever.

## Provenance belongs to one clone

`land` recognises chain commits by looking up their patch id in the local evidence. A unit committed
in one clone and landed from another, such as a second machine or a fresh clone, looks like it was
made outside the chain, and `land` returns `needs_direction`. Commit and land from the same clone;
any worktree of it is fine.
