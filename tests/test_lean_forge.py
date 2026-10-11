"""End-to-end tests: real git repositories, a bare origin, and the scripted fake agent."""

import contextlib
import fcntl
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lean_forge import cli, evidence, git, hook, primitives, providers, runs, workflows  # noqa: E402
from lean_forge import config as configuration  # noqa: E402

FAKE = f"{sys.executable} {ROOT / 'tests' / 'fake_agent.py'}"
PASS = {"verdict": "PASS", "findings": [], "summary": "ok", "questions": []}
BLOCK = {"verdict": "BLOCK", "findings": ["[COR-05] MAJOR: x.txt: wrong; evidence; fix it"], "summary": "bad", "questions": []}
FIXED = {"summary": "fixed", "questions": []}


def sh(*args, cwd):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


def config_text(max_iterations=8, autofix=True, approval="none", land_review="none", git_timeout=120, command=FAKE):
    routes = "".join(f'[routes.{r}]\nprovider = "command"\ncommand = "{command}"\n\n'
                     for r in ("review-cheap", "review-final", "fix", "implement"))
    return (f'[gates]\nnot_broken = "test ! -e BROKEN"\n\n[control]\npaths = [".lean-forge/", "AGENTS.md"]\n\n'
            f"[review]\nmax_iterations = {max_iterations}\nautofix = {'true' if autofix else 'false'}\n\n"
            f'[approval]\nmode = "{approval}"\n\n[land]\nremote = "origin"\nbranch = "main"\nreview = "{land_review}"\n\n'
            f"[timeouts]\ngit = {git_timeout}\n\n" + routes)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lean-forge-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.origin = self.tmp / "origin.git"
        sh("git", "init", "-q", "--bare", "-b", "main", str(self.origin), cwd=self.tmp)
        self.repo = self.clone("work")
        self.write_config()
        (self.repo / "x.txt").write_text("one\n")
        self.raw_commit(self.repo, "base")
        sh("git", "push", "-q", "origin", "HEAD:main", cwd=self.repo)
        self.fake = self.tmp / "fake.json"
        os.environ["LEAN_FORGE_FAKE"] = str(self.fake)
        self.script({})

    def clone(self, name):
        path = self.tmp / name
        sh("git", "clone", "-q", str(self.origin), str(path), cwd=self.tmp)
        sh("git", "config", "user.name", "Test", cwd=path)
        sh("git", "config", "user.email", "test@example.com", cwd=path)
        sh("git", "checkout", "-q", "-B", "main", cwd=path)
        return path

    def write_config(self, **kw):
        (self.repo / ".lean-forge").mkdir(exist_ok=True)
        (self.repo / ".lean-forge" / "config.toml").write_text(config_text(**kw))

    def raw_commit(self, path, message):
        sh("git", "add", "-A", cwd=path)
        sh("git", "commit", "-q", "-m", message, cwd=path)

    def script(self, data):
        self.fake.write_text(json.dumps(data))

    def calls(self):
        return json.loads(self.fake.read_text()).get("calls", [])

    def reconfigure(self, **kw):
        """Config is read from the remote base tip, so a new config takes effect once it is pushed."""
        self.write_config(**kw)
        self.raw_commit(self.repo, "reconfigure")
        sh("git", "push", "-q", "origin", "HEAD:main", cwd=self.repo)


class CommitTests(Base):
    def test_happy_path_commits_exactly_the_reviewed_tree(self):
        (self.repo / "x.txt").write_text("two\n")
        self.script({"review-cheap": [PASS]})
        out = workflows.commit_unit(self.repo, "change x")
        self.assertEqual(out["status"], "committed", out)
        self.assertEqual(git.git("rev-parse", "HEAD^{tree}", cwd=self.repo).strip(), out["tree"])
        self.assertEqual(self.calls(), ["review-cheap"])
        self.assertEqual(runs.recent(self.repo)[0]["status"], "committed")

    def test_block_then_fix_then_pass(self):
        (self.repo / "x.txt").write_text("bad\n")
        self.script({"review-cheap": [BLOCK, PASS], "fix": [{**FIXED, "_write": {"x.txt": "good\n"}}]})
        out = workflows.commit_unit(self.repo, "change x")
        self.assertEqual((out["status"], out["iterations"]), ("committed", 2), out)
        self.assertEqual(git.show("HEAD", "x.txt", self.repo), "good\n")

    def test_cap_asks_for_direction_and_commits_nothing(self):
        self.reconfigure(max_iterations=2)
        head = git.head(self.repo)
        (self.repo / "x.txt").write_text("bad\n")
        self.script({"review-cheap": [BLOCK, BLOCK], "fix": [FIXED]})
        out = workflows.commit_unit(self.repo, "change x")
        self.assertEqual((out["status"], out["stage"]), ("needs_direction", "cap"), out)
        self.assertEqual(git.head(self.repo), head)
        self.assertEqual(self.calls(), ["review-cheap", "fix", "review-cheap"])

    def test_reviewer_question_asks_for_direction(self):
        (self.repo / "x.txt").write_text("two\n")
        self.script({"review-cheap": [{**PASS, "questions": ["Should x be two or three?"]}]})
        out = workflows.commit_unit(self.repo, "change x")
        self.assertEqual(out["status"], "needs_direction")
        self.assertEqual(out["questions"], ["Should x be two or three?"])

    def test_control_change_gets_review_final_and_optional_approval_reenters(self):
        self.reconfigure(approval="control")
        (self.repo / "AGENTS.md").write_text("rules\n")
        self.script({"review-final": [PASS]})
        out = workflows.commit_unit(self.repo, "add guidance")
        self.assertEqual(out["status"], "awaiting_approval", out)
        done = workflows.approve_run(self.repo, out["run"], "operator")
        self.assertEqual(done["status"], "committed", done)
        self.assertEqual(self.calls(), ["review-final"])

    def test_approval_commits_in_the_run_worktree_from_any_checkout(self):
        self.reconfigure(approval="control")
        side = self.tmp / "side"
        sh("git", "worktree", "add", "-q", "-b", "side", str(side), cwd=self.repo)
        (side / "AGENTS.md").write_text("rules\n")
        self.script({"review-final": [PASS]})
        out = workflows.commit_unit(side, "add guidance")
        self.assertEqual(out["status"], "awaiting_approval", out)
        (self.repo / "y.txt").write_text("the operator's own work\n")
        head = git.head(self.repo)
        (side / "AGENTS.md").write_text("changed after the request\n")
        self.assertIn("changed since", workflows.approve_run(self.repo, out["run"], "operator")["reason"])
        (side / "AGENTS.md").write_text("rules\n")
        done = workflows.approve_run(self.repo, out["run"], "operator")
        self.assertEqual(done["status"], "committed", done)
        self.assertEqual(git.show("side", "AGENTS.md", self.repo), "rules\n")
        self.assertEqual(git.head(self.repo), head)
        self.assertEqual(git.git("status", "--porcelain", cwd=self.repo).strip(), "?? y.txt")
        self.assertEqual(self.calls(), ["review-final"])

    def test_approval_run_rejects_a_moved_base(self):
        self.reconfigure(approval="control")
        (self.repo / "AGENTS.md").write_text("rules\n")
        self.script({"review-final": [PASS]})
        out = workflows.commit_unit(self.repo, "add guidance")
        self.assertEqual(out["status"], "awaiting_approval", out)
        sh("git", "restore", "--staged", "AGENTS.md", cwd=self.repo)
        sh("git", "commit", "-q", "--allow-empty", "-m", "move base", cwd=self.repo)
        done = workflows.approve_run(self.repo, out["run"], "operator")
        self.assertEqual(done["status"], "error", done)
        self.assertIn("changed base", done["reason"])
        self.assertIsNone(evidence.find(self.repo, kind="approval", tree=out["tree"], base=git.head(self.repo)))

    def test_restoring_an_earlier_tree_is_reviewed_again(self):
        self.script({"review-cheap": [PASS, PASS, PASS]})
        for text in ("two\n", "three\n", "two\n"):
            (self.repo / "x.txt").write_text(text)
            self.assertEqual(workflows.commit_unit(self.repo, f"x is {text.strip()}")["status"], "committed")
        self.assertEqual(self.calls(), ["review-cheap"] * 3)

    def test_config_types_are_checked(self):
        for broken in ("review = 5\n" + config_text().replace("[review]", "[unused]"),
                       "routes = 5\n" + config_text().replace("[routes.", "[unused."),
                       config_text().replace('"test ! -e BROKEN"', "5"),
                       config_text().replace('paths = [".lean-forge/", "AGENTS.md"]', 'paths = ".lean-forge/"'),
                       "land = 5\n" + config_text().replace("[land]", "[unused]"),
                       config_text().replace("[routes.fix]\n", "[routes.fix]\nmodel = 5\n")):
            (self.repo / ".lean-forge" / "config.toml").write_text(broken)
            self.raw_commit(self.repo, "broken config")
            sh("git", "push", "-q", "origin", "HEAD:main", cwd=self.repo)
            with self.assertRaises(configuration.ConfigError, msg=broken[:40]):
                configuration.load(self.repo)

    def test_default_needs_no_approval_for_control_changes(self):
        (self.repo / "AGENTS.md").write_text("rules\n")
        self.script({"review-final": [PASS]})
        self.assertEqual(workflows.commit_unit(self.repo, "add guidance")["status"], "committed")

    def test_halt_stops_before_anything_runs(self):
        (git.state_dir(self.repo) / "HALT").write_text("maintenance\n")
        (self.repo / "x.txt").write_text("two\n")
        out = workflows.commit_unit(self.repo, "change x")
        self.assertEqual((out["status"], out["reason"]), ("halted", "maintenance"))
        self.assertEqual(self.calls(), [])

    def test_gate_failure_without_autofix_blocks(self):
        (self.repo / "BROKEN").write_text("")
        out = workflows.commit_unit(self.repo, "break", autofix=False)
        self.assertEqual((out["status"], out["stage"]), ("blocked", "gates"), out)

    def test_gate_failure_with_autofix_goes_to_the_fixer(self):
        (self.repo / "BROKEN").write_text("")
        self.script({"fix": [{**FIXED, "_delete": ["BROKEN"], "_write": {"x.txt": "two\n"}}], "review-cheap": [PASS]})
        self.assertEqual(workflows.commit_unit(self.repo, "change x")["status"], "committed")

    def test_secrets_block_without_review(self):
        (self.repo / "x.txt").write_text('token = "ghp_' + "a" * 36 + '"\n')
        out = workflows.commit_unit(self.repo, "leak")
        self.assertEqual((out["status"], out["stage"]), ("blocked", "scan"), out)
        self.assertEqual(out["hits"], ["x.txt:1"])  # where, never the credential itself
        self.assertEqual(self.calls(), [])

    def test_a_reviewer_that_writes_is_rejected(self):
        (self.repo / "x.txt").write_text("two\n")
        self.script({"review-cheap": [{**PASS, "_write": {"x.txt": "sneaky\n"}}]})
        out = workflows.commit_unit(self.repo, "change x")
        self.assertEqual(out["status"], "error")
        self.assertIn("read-only", out["reason"])

    def test_commit_primitive_refuses_without_evidence(self):
        (self.repo / "x.txt").write_text("two\n")
        tree = git.stage_all(self.repo)
        config, _ = configuration.load(self.repo)
        with self.assertRaises(primitives.Refused):
            primitives.commit(self.repo, config, tree=tree, message="m")

    def test_commit_with_no_committed_gates_needs_direction_and_recovers_via_land(self):
        (self.repo / ".lean-forge" / "config.toml").write_text(config_text().replace('not_broken = "test ! -e BROKEN"\n', ""))
        self.raw_commit(self.repo, "drop the gates")
        sh("git", "push", "-q", "origin", "HEAD:main", cwd=self.repo)
        (self.repo / "x.txt").write_text("two\n")
        self.script({"review-cheap": [PASS]})
        out = workflows.commit_unit(self.repo, "change x")
        self.assertEqual((out["status"], out["stage"]), ("needs_direction", "gates"), out)
        self.assertIn("refs/remotes/origin/main", out["questions"][0])
        self.assertIn("land --allow-outside-commits", out["questions"][0])
        self.assertEqual(self.calls(), [])
        # The recovery the question names: a config-only commit landed outside the chain.
        (self.repo / "x.txt").write_text("one\n")
        self.write_config()
        self.raw_commit(self.repo, "restore the gates")
        self.assertEqual(workflows.land(self.repo, allow_outside=True)["status"], "landed")
        (self.repo / "x.txt").write_text("two\n")
        out = workflows.commit_unit(self.repo, "change x")
        self.assertEqual(out["status"], "committed", out)

    def test_bootstrap_with_no_gates_is_blocked_before_any_agent_runs(self):
        repo = self.tmp / "bootstrap-no-gates"
        sh("git", "init", "-q", "-b", "main", str(repo), cwd=self.tmp)
        (repo / ".lean-forge").mkdir()
        (repo / configuration.CONFIG).write_text(config_text().replace('not_broken = "test ! -e BROKEN"\n', ""))
        out = workflows.commit_unit(repo, "bootstrap")
        self.assertEqual((out["status"], out["stage"]), ("blocked", "gates"), out)
        self.assertIn("working tree (bootstrap)", out["reason"])
        self.assertEqual(self.calls(), [])

    def test_commit_primitive_refuses_when_no_gates_are_configured(self):
        (self.repo / "x.txt").write_text("two\n")
        tree = git.stage_all(self.repo)
        config, _ = configuration.load(self.repo)
        config["gates"] = {}
        run = runs.Run(self.repo, "test")
        self.assertEqual(primitives.scan(self.repo, tree, run), [])
        evidence.add(self.repo, kind="review", tree=tree, base=git.head(self.repo), role="review-cheap",
                     verdict="PASS", questions=[])
        self.assertEqual(primitives.required_evidence(self.repo, config, tree), ["at least one configured gate"])
        with self.assertRaisesRegex(primitives.Refused, "at least one configured gate"):
            primitives.commit(self.repo, config, tree=tree, message="m")

    def test_control_tree_cannot_commit_with_only_cheap_pass(self):
        (self.repo / "AGENTS.md").write_text("rules\n")
        tree = git.stage_all(self.repo)
        config, _ = configuration.load(self.repo)
        run = runs.Run(self.repo, "test")
        self.assertEqual(primitives.gates(self.repo, config, tree, run), [])
        self.assertEqual(primitives.scan(self.repo, tree, run), [])
        evidence.add(self.repo, kind="review", tree=tree, base=git.head(self.repo), role="review-cheap",
                     verdict="PASS", questions=[])
        with self.assertRaisesRegex(primitives.Refused, "review-final PASS"):
            primitives.commit(self.repo, config, tree=tree, message="m")

    def test_commit_primitive_requires_each_piece_of_evidence(self):
        self.reconfigure(approval="control")
        (self.repo / "AGENTS.md").write_text("rules\n")
        tree = git.stage_all(self.repo)
        config, _ = configuration.load(self.repo)
        run = runs.Run(self.repo, "test")

        def refused():
            with self.assertRaises(primitives.Refused) as ctx:
                primitives.commit(self.repo, config, tree=tree, message="m")
            return str(ctx.exception)

        self.assertIn("gate not_broken", refused())
        self.assertEqual(primitives.gates(self.repo, config, tree, run), [])
        self.assertIn("clean secrets scan", refused())
        self.assertEqual(primitives.scan(self.repo, tree, run), [])
        self.assertIn("review-final PASS", refused())
        base = git.head(self.repo)
        evidence.add(self.repo, kind="review", tree=tree, base=base, role="review-cheap", verdict="PASS", questions=[])
        self.assertIn("review-final PASS", refused())  # a cheap PASS never satisfies a control unit
        evidence.add(self.repo, kind="review", tree=tree, base=base, role="review-final", verdict="PASS",
                     questions=["Sure?"])
        self.assertIn("review-final PASS", refused())  # nor does a PASS that asked questions
        evidence.add(self.repo, kind="review", tree=tree, base="0" * 40, role="review-final", verdict="PASS",
                     questions=[])
        self.assertIn("review-final PASS", refused())  # nor one reviewed against another HEAD
        evidence.add(self.repo, kind="review", tree=tree, base=base, role="review-final", verdict="PASS", questions=[])
        self.assertEqual(refused(), "commit refused; missing evidence: operator approval")
        primitives.approve(self.repo, tree, "operator")
        (self.repo / "AGENTS.md").write_text("edited after review\n")
        self.assertIn("worktree changed after review", refused())
        (self.repo / "AGENTS.md").write_text("rules\n")
        sha = primitives.commit(self.repo, config, tree=tree, message="m")
        self.assertEqual(git.git("rev-parse", f"{sha}^{{tree}}", cwd=self.repo).strip(), tree)

    def test_approval_for_another_base_does_not_satisfy_the_same_tree(self):
        self.reconfigure(approval="control")
        (self.repo / "AGENTS.md").write_text("rules\n")
        tree = git.stage_all(self.repo)
        primitives.approve(self.repo, tree, "operator")
        old_base = git.head(self.repo)
        sh("git", "restore", "--staged", "AGENTS.md", cwd=self.repo)
        sh("git", "commit", "-q", "--allow-empty", "-m", "move base", cwd=self.repo)
        self.assertNotEqual(git.head(self.repo), old_base)
        config, _ = configuration.load(self.repo)
        run = runs.Run(self.repo, "test")
        self.assertEqual(primitives.gates(self.repo, config, tree, run), [])
        self.assertEqual(primitives.scan(self.repo, tree, run), [])
        evidence.add(self.repo, kind="review", tree=tree, base=git.head(self.repo), role="review-final",
                     verdict="PASS", questions=[])
        self.assertEqual(primitives.required_evidence(self.repo, config, tree), ["operator approval"])

    def test_a_writer_that_moves_head_is_rejected(self):
        (self.repo / "x.txt").write_text("two\n")
        self.script({"fix": [{**FIXED, "_git": ["commit", "-q", "-a", "-m", "sneaky"]}]})
        config, _ = configuration.load(self.repo)
        with self.assertRaises(primitives.AgentError) as ctx:
            primitives.work(self.repo, config, runs.Run(self.repo, "test"), role="fix", brief="b")
        self.assertIn("moved HEAD", str(ctx.exception))

    def test_a_writer_that_changes_the_ignored_local_override_is_rejected(self):
        local = self.repo / configuration.LOCAL
        local.write_text('[routes.review-cheap]\nmodel = "before"\n')
        self.script({"fix": [{**FIXED, "_write": {configuration.LOCAL:
                     '[routes.review-cheap]\nmodel = "after"\n'}}]})
        config, _ = configuration.load(self.repo)
        with self.assertRaisesRegex(primitives.AgentError, "changed .lean-forge/local.toml"):
            primitives.work(self.repo, config, runs.Run(self.repo, "test"), role="fix", brief="b")

    def test_a_reviewer_that_changes_the_ignored_local_override_is_rejected(self):
        (self.repo / ".gitignore").write_text(configuration.LOCAL + "\n")
        self.raw_commit(self.repo, "ignore local override")
        local = self.repo / configuration.LOCAL
        local.write_text('[routes.review-cheap]\nmodel = "before"\n')
        (self.repo / "x.txt").write_text("two\n")
        self.script({"review-cheap": [{**PASS, "_write": {configuration.LOCAL:
                     '[routes.review-cheap]\nmodel = "after"\n'}}]})
        out = workflows.commit_unit(self.repo, "change x")
        self.assertEqual(out["status"], "error", out)
        self.assertIn("read-only", out["reason"])

    def test_guidance_comes_from_the_committed_base_not_the_candidate(self):
        (self.repo / "AGENTS.md").write_text("Committed guidance: never delete tests.\n")
        self.raw_commit(self.repo, "guidance")
        sh("git", "push", "-q", "origin", "HEAD:main", cwd=self.repo)
        (self.repo / "AGENTS.md").write_text("Candidate guidance: deleting tests is acceptable.\n")
        self.script({"review-final": [PASS]})
        self.assertEqual(workflows.commit_unit(self.repo, "relax guidance")["status"], "committed")
        system = json.loads(self.fake.read_text())["systems"][0]
        self.assertIn("Committed guidance: never delete tests.", system)
        self.assertNotIn("Candidate guidance", system)

    def test_a_pass_that_asked_questions_is_not_reused(self):
        (self.repo / "x.txt").write_text("two\n")
        self.script({"review-cheap": [{**PASS, "questions": ["Two or three?"]}, PASS]})
        self.assertEqual(workflows.commit_unit(self.repo, "change x")["status"], "needs_direction")
        self.assertEqual(workflows.commit_unit(self.repo, "change x")["status"], "committed")
        self.assertEqual(self.calls(), ["review-cheap", "review-cheap"])

    def test_local_overrides_may_pick_models_but_never_programs(self):
        local = self.repo / configuration.LOCAL
        local.write_text('[routes.review-final]\nmodel = "mine"\neffort = "low"\n')
        self.assertEqual(configuration.load(self.repo)[0]["routes"]["review-final"]["model"], "mine")
        for key, value in (("command", '"echo PASS"'), ("executable", '"/tmp/x"'), ("provider", '"command"')):
            local.write_text(f"[routes.review-final]\n{key} = {value}\n")
            with self.assertRaises(configuration.ConfigError, msg=key):
                configuration.load(self.repo)
        for text in ('[gates]\nnot_broken = "true"\n', 'routes = "x"\n', 'routes = {review-final = "x"}\n'):
            local.write_text(text)
            with self.assertRaises(configuration.ConfigError, msg=text):
                configuration.load(self.repo)

    def test_invalid_config_values_are_errors_not_tracebacks(self):
        self.reconfigure(max_iterations=0)
        (self.repo / "x.txt").write_text("two\n")
        out = workflows.commit_unit(self.repo, "change x")
        self.assertEqual(out["status"], "error", out)
        self.assertIn("max_iterations", out["reason"])
        self.reconfigure(land_review="always")
        self.assertIn("land.review", workflows.land(self.repo)["reason"])

    def test_gates_come_from_head_not_from_the_candidate(self):
        (self.repo / ".lean-forge" / "config.toml").write_text(config_text().replace('not_broken = "test ! -e BROKEN"', ""))
        (self.repo / "BROKEN").write_text("")
        out = workflows.commit_unit(self.repo, "weaken gates", autofix=False)
        self.assertEqual((out["status"], out["stage"]), ("blocked", "gates"), out)

    def test_rerun_after_pass_reuses_evidence(self):
        (self.repo / "x.txt").write_text("two\n")
        self.script({"review-cheap": [PASS]})
        tree = git.stage_all(self.repo)
        config, _ = configuration.load(self.repo)
        run = runs.Run(self.repo, "test")
        primitives.review(self.repo, config, run, tree=tree, role="review-cheap")
        self.assertEqual(workflows.commit_unit(self.repo, "change x")["status"], "committed")
        self.assertEqual(self.calls(), ["review-cheap"])

    def test_the_reviewer_sees_the_tree_the_evidence_is_for_not_the_later_index(self):
        (self.repo / "x.txt").write_text("two\n")
        tree = git.stage_all(self.repo)
        (self.repo / "x.txt").write_text("three\n")
        git.stage_all(self.repo)
        self.script({"review-cheap": [PASS]})
        config, _ = configuration.load(self.repo)
        primitives.review(self.repo, config, runs.Run(self.repo, "test"), tree=tree, role="review-cheap")
        prompt = json.loads(self.fake.read_text())["prompts"][0]
        self.assertIn("+two", prompt)
        self.assertNotIn("+three", prompt)

    def test_a_timeout_kills_the_whole_process_tree(self):
        config, _ = configuration.load(self.repo)
        config["gates"] = {"leaky": "(sleep 2; touch LEAKED_GATE) & sleep 5"}
        config["timeouts"]["gate"] = 1
        failures = primitives.gates(self.repo, config, git.stage_all(self.repo), runs.Run(self.repo, "test"))
        self.assertIn("timed out", failures[0]["output"])
        with self.assertRaises(providers.AgentError):
            providers.run_agent({"provider": "command", "command": "sh -c '(sleep 2; touch LEAKED_AGENT) & sleep 5'"},
                                role="r", system="s", prompt="p", schema=providers.WORK_SCHEMA, write=False,
                                bash_prefixes=[], cwd=self.repo, timeout=1)
        time.sleep(3)
        self.assertFalse((self.repo / "LEAKED_GATE").exists())
        self.assertFalse((self.repo / "LEAKED_AGENT").exists())


class LandTests(Base):
    def commit(self, text, verdicts=None):
        (self.repo / "x.txt").write_text(text)
        self.script({"review-cheap": verdicts or [PASS]})
        out = workflows.commit_unit(self.repo, f"set x to {text.strip()}")
        self.assertEqual(out["status"], "committed", out)

    def test_land_rebases_onto_a_moved_base_and_pushes(self):
        other = self.clone("other")
        (other / "y.txt").write_text("other\n")
        self.raw_commit(other, "other work")
        sh("git", "push", "-q", "origin", "HEAD:main", cwd=other)
        self.commit("two\n")
        out = workflows.land(self.repo)
        self.assertEqual((out["status"], out["pulled"]), ("landed", 1), out)
        self.assertEqual(git.git("ls-remote", "origin", "main", cwd=self.repo).split()[0], git.head(self.repo))
        self.assertEqual(git.git("rev-list", "--merges", "--count", "HEAD", cwd=self.repo).strip(), "0")

    def test_commits_outside_the_chain_need_direction(self):
        (self.repo / "x.txt").write_text("raw\n")
        self.raw_commit(self.repo, "raw commit")
        out = workflows.land(self.repo)
        self.assertEqual((out["status"], out["stage"]), ("needs_direction", "provenance"), out)
        self.assertEqual(workflows.land(self.repo, allow_outside=True)["status"], "landed")

    def test_conflict_needs_direction_and_leaves_the_branch_intact(self):
        other = self.clone("other")
        (other / "x.txt").write_text("theirs\n")
        self.raw_commit(other, "theirs")
        sh("git", "push", "-q", "origin", "HEAD:main", cwd=other)
        self.commit("ours\n")
        head = git.head(self.repo)
        out = workflows.land(self.repo)
        self.assertEqual((out["status"], out["stage"]), ("needs_direction", "rebase"), out)
        self.assertEqual(git.head(self.repo), head)

    def test_land_review_reviews_the_whole_branch_afresh(self):
        self.reconfigure(land_review="final")
        for second, status in ((BLOCK, ("blocked", "land-review")), (PASS, ("landed", None))):
            (self.repo / "AGENTS.md").write_text(f"rules {status[0]}\n")
            self.script({"review-final": [PASS, second]})
            self.assertEqual(workflows.commit_unit(self.repo, "guidance")["status"], "committed")
            out = workflows.land(self.repo)
            self.assertEqual((out["status"], out.get("stage")), status, out)
            self.assertEqual(self.calls(), ["review-final", "review-final"])

    def test_a_hung_remote_times_out_instead_of_blocking_land(self):
        self.reconfigure(git_timeout=1)
        self.commit("two\n")
        sh("git", "config", "protocol.ext.allow", "always", cwd=self.repo)
        sh("git", "config", "remote.origin.url", "ext::sleep 5", cwd=self.repo)
        out = workflows.land(self.repo)
        self.assertEqual(out["status"], "error", out)
        self.assertIn("timed out", out["reason"])

    def test_land_reruns_the_gates_on_the_rebased_tree(self):
        other = self.clone("other")
        (other / "BROKEN").write_text("")
        self.raw_commit(other, "break the base")
        sh("git", "push", "-q", "origin", "HEAD:main", cwd=other)
        self.commit("two\n")
        out = workflows.land(self.repo)
        self.assertEqual((out["status"], out["stage"]), ("blocked", "gates"), out)
        self.assertEqual(git.git("ls-remote", "origin", "main", cwd=self.repo).split()[0], git.head(other))

    def test_land_judges_with_the_config_of_the_base_it_fetched(self):
        self.commit("two\n")
        other = self.clone("other")
        (other / ".lean-forge" / "config.toml").write_text(
            config_text().replace("[gates]\n", '[gates]\nx_is_one = "grep -qx one x.txt"\n'))
        self.raw_commit(other, "stricter gates")
        sh("git", "push", "-q", "origin", "HEAD:main", cwd=other)
        out = workflows.land(self.repo)
        self.assertEqual((out["status"], out["stage"]), ("blocked", "gates"), out)
        self.assertEqual([f["gate"] for f in out["failures"]], ["x_is_one"])
        self.assertEqual(git.git("ls-remote", "origin", "main", cwd=self.repo).split()[0], git.head(other))

    def test_land_waits_for_the_land_lock_then_refuses_naming_its_holder(self):
        (self.repo / "x.txt").write_text("two\n")
        self.script({"review-cheap": [PASS]})
        self.assertEqual(workflows.commit_unit(self.repo, "change x")["status"], "committed")
        with open(git.state_dir(self.repo) / "land.lock", "a+") as fh, mock.patch.object(runs.time, "sleep") as slept:
            fcntl.flock(fh, fcntl.LOCK_EX)
            print("20261011-000000-abcd", file=fh, flush=True)
            out = workflows.land(self.repo)
        self.assertEqual(out["status"], "error", out)
        self.assertIn("the land lock is held by run 20261011-000000-abcd", out["reason"])
        self.assertEqual(sum(c.args[0] for c in slept.call_args_list), 300)  # five minutes of waiting, then the refusal
        self.assertEqual(workflows.land(self.repo)["status"], "landed")

    def test_land_refuses_a_dirty_worktree(self):
        (self.repo / "x.txt").write_text("dirty\n")
        self.assertEqual(workflows.land(self.repo)["status"], "blocked")


FAKE_CLI = """
import json, sys
from pathlib import Path
sys.stdin.read()
argv = sys.argv[1:]
LOG.write_text(json.dumps(argv))
out = {"verdict": "PASS", "findings": [], "summary": "ok", "questions": []}
if argv[0] == "exec":
    Path(argv[argv.index("-o") + 1]).write_text(json.dumps(out))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}))
else:
    print(json.dumps({"structured_output": out, "usage": {"input_tokens": 1}}))
"""


class ProviderTests(Base):
    """Pin the flags lean-forge passes to the real CLIs, through stand-in executables that record them."""

    def launch(self, provider, write):
        exe, log = self.tmp / f"fake-{provider}", self.tmp / f"argv-{provider}.json"
        exe.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nLOG = Path({str(log)!r})\n{FAKE_CLI}")
        os.chmod(exe, 0o755)
        out = providers.run_agent({"provider": provider, "model": "m", "executable": str(exe)}, role="r", system="s",
                                  prompt="p", schema=providers.VERDICT_SCHEMA, write=write,
                                  bash_prefixes=["test ! -e BROKEN"], cwd=self.repo, timeout=60)
        self.assertEqual(out["output"]["verdict"], "PASS")
        return json.loads(log.read_text())

    def test_claude_shell_rules_are_exact_commands_and_reviewers_cannot_edit(self):
        for write in (False, True):
            argv = self.launch("claude", write)
            tools = argv[argv.index("--tools") + 1].split(",")
            allowed = argv[argv.index("--allowedTools") + 1:argv.index("--permission-prompts")]
            bash = [rule[5:-1] for rule in allowed if rule.startswith("Bash(")]
            self.assertIn("test ! -e BROKEN", bash)
            self.assertIn("git diff HEAD", bash)
            for rule in bash:  # a prefix rule would admit `--output=<file>`, `-O<pager>`, `--fix` and the like
                self.assertFalse(rule.endswith(":*") or rule.startswith("git grep"), rule)
            for attack in ("git diff --output=x", "git grep -Osh x", "test ! -e BROKEN; touch x"):
                self.assertNotIn(attack, bash)
            self.assertEqual(("Edit" in tools, "Write" in allowed), (write, write))
            self.assertIn("--safe-mode", argv)

    def test_codex_reviewers_get_the_read_only_sandbox(self):
        for write, sandbox in ((False, "read-only"), (True, "workspace-write")):
            argv = self.launch("codex", write)
            self.assertEqual(argv[argv.index("-s") + 1], sandbox)
            self.assertIn("--ephemeral", argv)


class HookAndEvalTests(Base):
    def test_hook_reserves_operator_verbs_however_invoked(self):
        for command in ("lean-forge approve 1", "python3 bin/lean-forge approve 1", "lean-forge -C x halt --off",
                        "cd a && /opt/lf/bin/lean-forge halt --off", "lean-forge land --allow-outside-commits",
                        "python3 bin/lean-forge -C x land --allow-outside-commits"):
            self.assertTrue(hook.operator_action(command), command)
        for command in ("lean-forge halt --on stop", 'lean-forge commit -m "approve the plan"', "lean-forge runs",
                        "lean-forge land"):
            self.assertFalse(hook.operator_action(command), command)
        self.assertFalse(hook.writes_history('python3 bin/lean-forge commit -m "fix git push"'))

    def test_stopping_a_run_kills_the_agent_process_group(self):
        """SIGTERM, or SIGHUP from a closed terminal, unwinds the real CLI so it kills the agent it is waiting on."""
        pidfile = self.tmp / "agent.pid"
        sleeper = self.tmp / "sleeper.py"
        sleeper.write_text(f"import os, pathlib, time\npathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
                           "time.sleep(60)\n")
        self.reconfigure(command=f"{sys.executable} {sleeper}")
        for stop in (signal.SIGTERM, signal.SIGHUP):
            pidfile.unlink(missing_ok=True)
            pid = None
            try:
                with subprocess.Popen([sys.executable, str(ROOT / "bin" / "lean-forge"), "-C", str(self.repo), "fix", "b"],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as proc:
                    deadline = time.time() + 20
                    while not (pidfile.exists() and pidfile.read_text()) and time.time() < deadline:
                        time.sleep(0.05)
                    pid = int(pidfile.read_text())
                    proc.send_signal(stop)
                    proc.communicate(timeout=10)
                    self.assertEqual(proc.returncode, 128 + stop, stop)
                deadline = time.time() + 5
                while time.time() < deadline:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.05)
                else:
                    self.fail(f"the agent subprocess outlived the run stopped with {stop!r}")
            finally:
                if pid is not None:
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(pid, 9)

    def test_one_run_per_worktree_at_a_time(self):
        """A second run in a worktree refuses, naming the run in flight. Other worktrees proceed, and so does a rerun
        once the first run is stopped: the lock is the kernel's, so it never goes stale."""
        pidfile, agent = self.tmp / "agent.pid", self.tmp / "agent.py"
        agent.write_text("import json, os, pathlib, sys, time\n"
                         "if json.load(sys.stdin)['role'] in ('fix', 'implement'):\n"
                         f"    pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
                         "    time.sleep(60)\n"
                         "print(json.dumps({'verdict': 'PASS', 'findings': [], 'summary': 'ok', 'questions': []}))\n")
        self.reconfigure(command=f"{sys.executable} {agent}")
        other = self.tmp / "other"
        sh("git", "worktree", "add", "-q", "-b", "other", str(other), "origin/main", cwd=self.repo)
        proc = subprocess.Popen([sys.executable, str(ROOT / "bin" / "lean-forge"), "-C", str(self.repo), "fix", "b"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pid = None
        try:
            deadline = time.time() + 20
            while not (pidfile.exists() and pidfile.read_text()) and time.time() < deadline:
                time.sleep(0.05)
            pid = int(pidfile.read_text())
            first = runs.recent(self.repo)[0]["run"]
            (self.repo / "x.txt").write_text("two\n")
            for out in (workflows.commit_unit(self.repo, "change x"), workflows.land(self.repo), workflows.evals(self.repo)):
                self.assertEqual(out["status"], "error", out)
                self.assertIn(f"held by run {first}", out["reason"])
            for argv in (["implement", "x"], ["review"]):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(cli.main(["-C", str(self.repo), *argv]), 1)
                self.assertIn(f"held by run {first}", json.loads(buf.getvalue())["reason"])
            (other / "x.txt").write_text("three\n")
            self.assertEqual(workflows.commit_unit(other, "change x elsewhere")["status"], "committed")
            proc.terminate()
            proc.wait(timeout=10)
            self.assertIsNone(runs.load(self.repo, first)[0])  # killed, so no outcome, and yet the worktree is free
            self.assertEqual(workflows.commit_unit(self.repo, "change x")["status"], "committed")
        finally:
            proc.kill()
            proc.wait()
            if pid is not None:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, 9)

    def test_a_run_started_under_nohup_survives_sighup(self):
        """An inherited SIG_IGN for SIGHUP is kept, so `nohup lean-forge ...` outlives a closed terminal."""
        pidfile, sleeper = self.tmp / "agent.pid", self.tmp / "sleeper.py"
        sleeper.write_text(f"import os, pathlib, time\npathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
                           "time.sleep(60)\n")
        self.reconfigure(command=f"{sys.executable} {sleeper}")
        pid = None
        with subprocess.Popen([sys.executable, str(ROOT / "bin" / "lean-forge"), "-C", str(self.repo), "fix", "b"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN)) as proc:
            try:
                deadline = time.time() + 20
                while not (pidfile.exists() and pidfile.read_text()) and time.time() < deadline:
                    time.sleep(0.05)
                pid = int(pidfile.read_text())
                proc.send_signal(signal.SIGHUP)
                time.sleep(1)
                self.assertIsNone(proc.poll(), "a run started under nohup stopped on SIGHUP")
                os.kill(pid, 0)  # the agent still runs
                proc.terminate()
                self.assertEqual(proc.wait(timeout=10), 143)
            finally:
                proc.kill()
                if pid is not None:
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(pid, 9)

    def test_a_lock_that_cannot_be_taken_finishes_with_an_error(self):
        lock = Path(git.git("rev-parse", "--path-format=absolute", "--git-dir", cwd=self.repo).strip()) / "lean-forge.lock"
        lock.mkdir()
        (self.repo / "x.txt").write_text("two\n")
        out = workflows.commit_unit(self.repo, "change x")
        self.assertEqual(out["status"], "error", out)
        self.assertIn("cannot lock", out["reason"])
        self.assertEqual(runs.load(self.repo, out["run"])[0]["status"], "error")  # an outcome, not `running` forever

    def test_hook_classification(self):
        for command in ("git commit -m x", "cd a && git -C b push origin", "/usr/bin/git commit", 'echo "git commit"'):
            self.assertTrue(hook.writes_history(command), command)
        for command in ("git status", "git log --oneline", 'lean-forge commit -m "fix git push"', "ls"):
            self.assertFalse(hook.writes_history(command), command)

    def test_hook_denies_only_in_lean_forge_repositories(self):
        def decide(cwd):
            out = io.StringIO()
            hook.main(io.StringIO(json.dumps({"tool_name": "Bash", "cwd": str(cwd),
                                              "tool_input": {"command": "git commit -m x"}})), out)
            return out.getvalue()
        self.assertIn('"deny"', decide(self.repo))
        plain = self.tmp / "plain"
        sh("git", "init", "-q", str(plain), cwd=self.tmp)
        self.assertEqual(decide(plain), "")

    def test_hook_reserves_approval_and_halt_clear_for_the_operator(self):
        def decision(command):
            out = io.StringIO()
            hook.main(io.StringIO(json.dumps({"tool_name": "Bash", "cwd": str(self.repo),
                                              "tool_input": {"command": command}})), out)
            return json.loads(out.getvalue()) if out.getvalue() else None

        for command in ("lean-forge approve run-id", "lean-forge halt --off",
                        "lean-forge -C . approve run-id", "lean-forge halt --on reason; lean-forge halt --off"):
            self.assertIn("human in their own terminal", decision(command)["hookSpecificOutput"]["permissionDecisionReason"])
        for command in ("lean-forge halt --on reason", "lean-forge commit -m x", "lean-forge land"):
            self.assertIsNone(decision(command), command)

    def test_option_abbreviations_are_refused(self):
        """`halt --of` must not act as `halt --off`: the hook matches operator options exactly."""
        for argv in (["halt", "--of"], ["land", "--allow"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                cli.parser().parse_args(argv)

    def test_init_writes_the_shipped_template_on_a_fresh_repository(self):
        fresh = self.tmp / "fresh"
        sh("git", "init", "-q", str(fresh), cwd=self.tmp)
        out = cli._init(fresh)
        self.assertTrue(out["created"])
        written = (fresh / configuration.CONFIG).read_text()
        self.assertEqual(written, cli.TEMPLATE.read_text())
        self.assertIn("[routes.review-final]", written)

    def test_init_ignores_local_overrides_and_worktrees(self):
        self.assertEqual(cli._init(self.repo)["status"], "ok")
        ignore = (self.repo / ".gitignore").read_text().splitlines()
        self.assertEqual(ignore, [configuration.LOCAL, ".worktrees/"])
        cli._init(self.repo)
        self.assertEqual((self.repo / ".gitignore").read_text().splitlines(), ignore)

    def test_bootstrap_commits_working_config_and_lands_on_empty_remote(self):
        origin = self.tmp / "empty.git"
        repo = self.tmp / "bootstrap"
        sh("git", "init", "-q", "--bare", "-b", "main", str(origin), cwd=self.tmp)
        sh("git", "clone", "-q", str(origin), str(repo), cwd=self.tmp)
        sh("git", "config", "user.name", "Test", cwd=repo)
        sh("git", "config", "user.email", "test@example.com", cwd=repo)
        sh("git", "checkout", "-q", "-B", "main", cwd=repo)
        (repo / ".lean-forge").mkdir()
        (repo / configuration.CONFIG).write_text(config_text())
        self.assertIsNone(git.head(repo))
        self.script({"review-final": [PASS]})
        committed = workflows.commit_unit(repo, "bootstrap")
        self.assertEqual(committed["status"], "committed", committed)
        self.assertEqual(committed["role"], "review-final")
        landed = workflows.land(repo)
        self.assertEqual(landed["status"], "landed", landed)
        self.assertEqual(git.git("ls-remote", str(origin), "main", cwd=repo).split()[0], committed["sha"])

    def test_bootstrap_first_commit_can_be_approved(self):
        origin = self.tmp / "empty-approval.git"
        repo = self.tmp / "bootstrap-approval"
        sh("git", "init", "-q", "--bare", "-b", "main", str(origin), cwd=self.tmp)
        sh("git", "clone", "-q", str(origin), str(repo), cwd=self.tmp)
        sh("git", "config", "user.name", "Test", cwd=repo)
        sh("git", "config", "user.email", "test@example.com", cwd=repo)
        sh("git", "checkout", "-q", "-B", "main", cwd=repo)
        (repo / ".lean-forge").mkdir()
        (repo / configuration.CONFIG).write_text(config_text(approval="control"))
        self.assertIsNone(git.head(repo))
        self.script({"review-final": [PASS]})
        out = workflows.commit_unit(repo, "bootstrap")
        self.assertEqual(out["status"], "awaiting_approval", out)
        done = workflows.approve_run(repo, out["run"], "operator")
        self.assertEqual(done["status"], "committed", done)
        self.assertEqual(git.git("rev-parse", "HEAD^{tree}", cwd=repo).strip(), out["tree"])

    def test_cli_reports_missing_inputs_as_json(self):
        for argv in (["runs", "no-such-run"], ["commit", "-m", "@" + str(self.tmp / "missing.txt")]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = cli.main(["-C", str(self.repo), *argv])
            self.assertEqual((code, json.loads(out.getvalue())["status"]), (1, "error"), argv)

    def test_eval_scores_fixtures_and_check_compares_them(self):
        evals = self.repo / ".lean-forge" / "evals"
        evals.mkdir()
        diff = "--- a/x.txt\n+++ b/x.txt\n@@ -1 +1 @@\n-one\n+two\n"
        (evals / "planted.md").write_text(f"---\nrole: review-cheap\nexpected: BLOCK\n---\n\n```diff\n{diff}```\n")
        self.raw_commit(self.repo, "add fixture")
        self.script({"review-cheap": [BLOCK]})
        scratch = self.tmp / "scratch"
        scratch.mkdir()
        with mock.patch.object(tempfile, "tempdir", str(scratch)):
            out = workflows.evals(self.repo)
        self.assertEqual((out["status"], out["ok"]), ("evaluated", True), out)
        self.assertEqual(list(scratch.iterdir()), [])  # the scratch worktree and its temp dir are gone
        self.assertEqual(workflows.eval_check(self.repo)["status"], "ok")
        (evals / "planted.result").write_text("PASS\n")
        self.assertEqual(workflows.eval_check(self.repo)["status"], "blocked")
        (evals / "planted.result").write_text("BLOCK\n")
        (evals / "malformed.md").write_text(f"---\nrole: review-cheap\n---\n\n```diff\n{diff}```\n")
        check = workflows.eval_check(self.repo)
        self.assertEqual((check["status"], len(check["problems"])), ("blocked", 1), check)
        self.assertIsNone(evidence.find(self.repo, kind="review", verdict="BLOCK"))


if __name__ == "__main__":
    unittest.main()
