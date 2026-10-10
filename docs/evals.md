# Evals

Evals check the reviewers themselves: does the binding reviewer still block a planted bug, pass a
clean change and block a prompt injection?

## Golden tasks

Each task is `.lean-forge/evals/<id>.md`, with front matter `role` (`review-cheap` or `review-final`)
and `expected` (`PASS` or `BLOCK`), and a fenced `diff` block that applies to HEAD:

````markdown
---
role: review-final
expected: BLOCK
---

```diff
diff --git a/src/app.py b/src/app.py
...
```
````

## Scoring

`lean-forge eval [ids...]` stages each diff in a scratch worktree of HEAD and reviews it afresh,
without recording review evidence. It writes the verdict to `<id>.result` and logs the expected
verdict, the actual one, the summary and the findings in the run journal. Each task costs one review.

`eval` scores the prompts and rules of the lean-forge copy that runs it, together with the project's
configuration and guidance. To measure a change to lean-forge itself, run `eval` from a copy that
contains the change.

## When to re-score

- After updating lean-forge, or after changing a reviewer route. Both change the reviewer.
- In lean-forge's own repository, after a change to `prompts/`, `rules/` or the code that launches
  reviewers lands: fast-forward the trusted copy and run `lean-forge eval` from it (see AGENTS.md).

A result that differs from `expected` is a reviewer regression; fix it before the next unit. A task
whose diff no longer applies to HEAD needs its diff regenerated. Commit a changed `.result` through
the chain.

## As a gate

`lean-forge eval --check` runs no reviewer. It fails unless every task has a recorded result equal to
its expectation. As a gate, it stops a recorded regression from landing as if it were fine. Put
`.lean-forge/` under `[control].paths` (the default), so a hand-edited `.result` goes to
`review-final` like any other change to a control. lean-forge's own repository gates on it.
