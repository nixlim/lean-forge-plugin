"""End-to-end tests: real git repositories, a bare origin, and the scripted fake agent."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

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


def config_text(max_iterations=8, autofix=True, approval="none", land_review="none", git_timeout=120):
    routes = "".join(f'[routes.{r}]\nprovider = "command"\ncommand = "{FAKE}"\n\n'
                     for r in ("review-cheap", "review-final", "fix", "implement"))
    return (f'[gates]\nnot_broken = "test ! -e BROKEN"\n\n[control]\npaths = [".lean-forge/", "AGENTS.md"]\n\n'
            f"[review]\nmax_iterations = {max_iterations}\nautofix = {'true' if autofix else 'false'}\n\n"
            f'[approval]\nmode = "{approval}"\n\n[land]\nremote = "origin"\nbranch = "main"\nreview = "{land_review}"\n\n'
            f"[timeouts]\ngit = {git_timeout}\n\n" + routes)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lean-forge-test-"))
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
            primitives.commit(self.repo, config, tree=tree, role="review-cheap", control=False, message="m")

    def test_commit_primitive_requires_each_piece_of_evidence(self):
        self.reconfigure(approval="control")
        (self.repo / "x.txt").write_text("two\n")
        tree = git.stage_all(self.repo)
        config, _ = configuration.load(self.repo)
        run = runs.Run(self.repo, "test")

        def refused():
            with self.assertRaises(primitives.Refused) as ctx:
                primitives.commit(self.repo, config, tree=tree, role="review-final", control=True, message="m")
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
        (self.repo / "x.txt").write_text("edited after review\n")
        self.assertIn("worktree changed after review", refused())
        (self.repo / "x.txt").write_text("two\n")
        sha = primitives.commit(self.repo, config, tree=tree, role="review-final", control=True, message="m")
        self.assertEqual(git.git("rev-parse", f"{sha}^{{tree}}", cwd=self.repo).strip(), tree)

    def test_a_writer_that_moves_head_is_rejected(self):
        (self.repo / "x.txt").write_text("two\n")
        self.script({"fix": [{**FIXED, "_git": ["commit", "-q", "-a", "-m", "sneaky"]}]})
        config, _ = configuration.load(self.repo)
        with self.assertRaises(primitives.AgentError) as ctx:
            primitives.work(self.repo, config, runs.Run(self.repo, "test"), role="fix", brief="b")
        self.assertIn("moved HEAD", str(ctx.exception))

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
        out = workflows.evals(self.repo)
        self.assertEqual((out["status"], out["ok"]), ("evaluated", True), out)
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
