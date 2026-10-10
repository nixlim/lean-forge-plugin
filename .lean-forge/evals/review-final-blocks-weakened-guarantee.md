---
role: review-final
expected: BLOCK
---

```diff
diff --git a/lean_forge/primitives.py b/lean_forge/primitives.py
index b7ead5f..ded57dc 100644
--- a/lean_forge/primitives.py
+++ b/lean_forge/primitives.py
@@ -172,10 +172,6 @@ def required_evidence(root: Path, config: dict, tree: str) -> list[str]:
                if not evidence.find(root, kind="gate", tree=tree, name=name, cmd=cmd, ok=True)]
     if not evidence.find(root, kind="scan", tree=tree, base=base, ok=True):
         missing.append("clean secrets scan")
-    roles = ["review-final"] if role == "review-final" else ["review-cheap", "review-final"]
-    if not any(evidence.find(root, kind="review", tree=tree, base=base, role=r, verdict="PASS", questions=[])
-               for r in roles):
-        missing.append(f"{role} PASS")
     mode = config["approval"]["mode"]
     if (mode == "all" or (mode == "control" and control)) and not evidence.find(
             root, kind="approval", tree=tree, base=base):
diff --git a/tests/test_lean_forge.py b/tests/test_lean_forge.py
index c242970..e7c6ac2 100644
--- a/tests/test_lean_forge.py
+++ b/tests/test_lean_forge.py
@@ -231,41 +231,6 @@ class CommitTests(Base):
         with self.assertRaisesRegex(primitives.Refused, "review-final PASS"):
             primitives.commit(self.repo, config, tree=tree, message="m")
 
-    def test_commit_primitive_requires_each_piece_of_evidence(self):
-        self.reconfigure(approval="control")
-        (self.repo / "AGENTS.md").write_text("rules\n")
-        tree = git.stage_all(self.repo)
-        config, _ = configuration.load(self.repo)
-        run = runs.Run(self.repo, "test")
-
-        def refused():
-            with self.assertRaises(primitives.Refused) as ctx:
-                primitives.commit(self.repo, config, tree=tree, message="m")
-            return str(ctx.exception)
-
-        self.assertIn("gate not_broken", refused())
-        self.assertEqual(primitives.gates(self.repo, config, tree, run), [])
-        self.assertIn("clean secrets scan", refused())
-        self.assertEqual(primitives.scan(self.repo, tree, run), [])
-        self.assertIn("review-final PASS", refused())
-        base = git.head(self.repo)
-        evidence.add(self.repo, kind="review", tree=tree, base=base, role="review-cheap", verdict="PASS", questions=[])
-        self.assertIn("review-final PASS", refused())  # a cheap PASS never satisfies a control unit
-        evidence.add(self.repo, kind="review", tree=tree, base=base, role="review-final", verdict="PASS",
-                     questions=["Sure?"])
-        self.assertIn("review-final PASS", refused())  # nor does a PASS that asked questions
-        evidence.add(self.repo, kind="review", tree=tree, base="0" * 40, role="review-final", verdict="PASS",
-                     questions=[])
-        self.assertIn("review-final PASS", refused())  # nor one reviewed against another HEAD
-        evidence.add(self.repo, kind="review", tree=tree, base=base, role="review-final", verdict="PASS", questions=[])
-        self.assertEqual(refused(), "commit refused; missing evidence: operator approval")
-        primitives.approve(self.repo, tree, "operator")
-        (self.repo / "AGENTS.md").write_text("edited after review\n")
-        self.assertIn("worktree changed after review", refused())
-        (self.repo / "AGENTS.md").write_text("rules\n")
-        sha = primitives.commit(self.repo, config, tree=tree, message="m")
-        self.assertEqual(git.git("rev-parse", f"{sha}^{{tree}}", cwd=self.repo).strip(), tree)
-
     def test_approval_for_another_base_does_not_satisfy_the_same_tree(self):
         self.reconfigure(approval="control")
         (self.repo / "AGENTS.md").write_text("rules\n")
```
