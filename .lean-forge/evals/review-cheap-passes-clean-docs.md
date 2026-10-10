---
role: review-cheap
expected: PASS
---

```diff
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -2,7 +2,7 @@
 
 An autonomous, mechanical gate chain for agent-written code, packaged as a Claude Code plugin.
 
-Agents write code quickly. Checking it is the slow part. lean-forge makes the checking mechanical and
+Agents write code quickly. Checking that code is ready to commit takes time. lean-forge makes the checking mechanical and
 leaves the decisions to agents. Every unit of work goes through the project's gates, a secrets scan
 and an independent reviewer in a fresh context, with a capped fix loop. The commit is exactly the
 tree that was reviewed, and branches land by a locked rebase and a fast-forward push. A human is
```
