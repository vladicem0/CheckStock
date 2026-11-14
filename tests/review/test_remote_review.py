"""Exercise the remote runner against disposable local Git repositories.

No network, application database, or installed Claude instance is required.
Only the POSIX lock is replaced on Windows; synchronization and snapshots use Git.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REVIEW_MODULES = Path(__file__).resolve().parents[2] / "scripts" / "review"
sys.path.insert(0, str(REVIEW_MODULES))
import remote_review as runner


@unittest.skipUnless(shutil.which("git"), "Git is required for the local repository fixtures")
class RemoteReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="checkstock-review-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.origin = self.root / "origin.git"
        self.source = self.root / "source"
        self.clone = self.root / "review-clone"
        self.git(None, "init", "--bare", str(self.origin))
        self.git(None, "init", "--initial-branch=dev", str(self.source))
        self.git(self.source, "remote", "add", "origin", str(self.origin))
        self.base = self.commit("requirements.md", "The stage preserves inventory.\n", "Record requirements")
        self.head = self.commit("inventory.py", "VALUE = 1\n", "Implement inventory stage")
        self.publish()
        self.policy = self.root / "policy.md"
        self.policy.write_text("Review the supplied project evidence.\n", encoding="utf-8")
        self.config = {
            "repository": str(self.clone),
            "repository_url": str(self.origin),
            "state_root": str(self.root / "state"),
            "policy_file": str(self.policy),
            "claude_executable": str(self.root / "fake-claude"),
            "timeout_seconds": 5,
        }
        if os.name == "nt":
            lock = mock.patch.object(runner, "review_lock", side_effect=lambda _: contextlib.nullcontext())
            lock.start()
            self.addCleanup(lock.stop)

    def git(self, repo, *arguments, input=None):
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
        command = [
            "git", "-c", "user.name=Review Test", "-c", "user.email=review-test@example.invalid",
            "-c", "core.autocrlf=false", "-c", "core.hooksPath=" + os.devnull,
        ]
        if repo is not None:
            command += ["-C", str(repo)]
        result = subprocess.run(command + list(arguments), input=input, capture_output=True, env=env, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        return result.stdout.decode("utf-8").strip()

    def commit(self, name, contents, message, *, repo=None):
        repo = self.source if repo is None else repo
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8", newline="\n")
        self.git(repo, "add", "--", name)
        self.git(repo, "commit", "-m", message)
        return self.git(repo, "rev-parse", "HEAD")

    def publish(self):
        self.git(self.source, "push", "origin", "dev:dev")

    def approved(self, config, snapshot, run_dir, base, head, stage):
        self.assertEqual(stage, "inventory")
        request = json.loads((snapshot / "request.json").read_text(encoding="utf-8"))
        self.assertEqual(request["base_sha"], base)
        self.assertEqual(request["head_sha"], head)
        self.assertTrue((snapshot / "changes.diff").is_file())
        self.assertTrue((snapshot / "head" / "requirements.md").is_file())
        return {
            "status": "APPROVED", "base_sha": base, "head_sha": head,
            "summary": "No blocking defects found in the supplied stage.",
            "findings": [], "limitations": [],
        }, {"claude_seconds": 0.01, "policy_sha256": "test-policy"}

    def execute(self, *, base=None, head=None, reviewer=None):
        with mock.patch.object(runner, "run_claude", side_effect=reviewer or self.approved) as review:
            result = runner.execute(self.config, base or self.base, head or self.head, "inventory", 0)
        return result, review

    def assert_blocked_before_review(self, result, review, explanation=None):
        self.assertEqual(result["status"], "BLOCKED", result)
        review.assert_not_called()
        if explanation:
            self.assertIn(explanation, result["summary"])

    def test_creates_marked_clone_and_fast_forwards_to_published_dev(self):
        first, review = self.execute()
        self.assertEqual(first["status"], "APPROVED", first)
        review.assert_called_once()
        self.assertTrue((self.clone / ".git" / "checkstock-review-clone").is_file())
        previous = self.head
        self.head = self.commit("inventory.py", "VALUE = 2\n", "Refine inventory stage")
        self.publish()
        result, _ = self.execute()
        self.assertEqual(result["status"], "APPROVED", result)
        self.assertEqual(self.git(self.clone, "rev-parse", "HEAD"), self.head)
        self.assertEqual(self.git(self.clone, "branch", "--show-current"), "dev")
        self.assertEqual(result["metadata"]["synchronization"], {
            "before_sha": previous, "after_sha": self.head, "updated": True,
        })
        saved = self.root / "state" / "runs" / result["metadata"]["run_id"] / "result.json"
        self.assertEqual(json.loads(saved.read_text(encoding="utf-8")), result)

    def test_stale_requested_head_is_rejected(self):
        stale = self.head
        self.head = self.commit("inventory.py", "VALUE = 2\n", "Publish newer stage")
        self.publish()
        result, review = self.execute(head=stale)
        self.assert_blocked_before_review(result, review, "not the current")

    def test_existing_unmarked_repository_is_preserved(self):
        self.git(None, "clone", "--branch", "dev", str(self.origin), str(self.clone))
        result, review = self.execute()
        self.assert_blocked_before_review(result, review, "marker")
        self.assertEqual(self.git(self.clone, "rev-parse", "HEAD"), self.head)

    def test_dirty_clone_is_rejected_without_deleting_local_work(self):
        runner.synchronize(self.config, self.base, self.head)
        local = self.clone / "local-notes.txt"
        local.write_text("Preserve this work.\n", encoding="utf-8")
        result, review = self.execute()
        self.assert_blocked_before_review(result, review, "local changes")
        self.assertEqual(local.read_text(encoding="utf-8"), "Preserve this work.\n")

    def test_tracked_dirty_clone_is_rejected_without_reset(self):
        runner.synchronize(self.config, self.base, self.head)
        path = self.clone / "inventory.py"
        path.write_text("LOCAL_CHANGE = True\n", encoding="utf-8")
        result, review = self.execute()
        self.assert_blocked_before_review(result, review, "local changes")
        self.assertEqual(path.read_text(encoding="utf-8"), "LOCAL_CHANGE = True\n")

    def test_locally_ahead_clone_is_preserved_and_rejected(self):
        runner.synchronize(self.config, self.base, self.head)
        ahead = self.commit("local.txt", "Local work.\n", "Local unpublished commit", repo=self.clone)
        result, review = self.execute()
        self.assert_blocked_before_review(result, review)
        self.assertEqual(self.git(self.clone, "rev-parse", "HEAD"), ahead)

    def test_diverged_clone_is_preserved_and_rejected(self):
        runner.synchronize(self.config, self.base, self.head)
        local = self.commit("local.txt", "Local work.\n", "Local divergent commit", repo=self.clone)
        self.head = self.commit("remote.txt", "Published work.\n", "Published divergent commit")
        self.publish()
        result, review = self.execute()
        self.assert_blocked_before_review(result, review)
        self.assertEqual(self.git(self.clone, "rev-parse", "HEAD"), local)

    def test_wrong_branch_is_rejected_even_at_expected_head(self):
        runner.synchronize(self.config, self.base, self.head)
        self.git(self.clone, "checkout", "-b", "main")
        result, review = self.execute()
        self.assert_blocked_before_review(result, review, "must be on dev")

    def test_empty_range_is_rejected(self):
        result, review = self.execute(base=self.head)
        self.assert_blocked_before_review(result, review, "empty")

    def test_nonancestor_base_is_rejected(self):
        self.git(self.source, "checkout", "-b", "unrelated", self.base)
        unrelated = self.commit("other.txt", "Other work.\n", "Different branch")
        self.git(self.source, "push", "origin", "unrelated:unrelated")
        self.git(self.source, "checkout", "dev")
        result, review = self.execute(base=unrelated)
        self.assert_blocked_before_review(result, review)

    def test_symlink_and_submodule_tree_entries_are_rejected_before_export(self):
        for mode, kind in (("120000", "symlink"), ("160000", "submodule")):
            with self.subTest(kind=kind):
                destination = self.root / ("snapshot-" + kind)
                object_id = self.base if mode == "160000" else self.git(
                    self.source, "hash-object", "-w", "--stdin", input=b"../outside-secret\n")
                self.git(self.source, "update-index", "--add", "--cacheinfo",
                         mode + "," + object_id + ",unsafe-entry")
                tree = self.git(self.source, "write-tree")
                with self.assertRaisesRegex(runner.ReviewError, "symlinks or submodules"):
                    runner.export_snapshot(self.source, tree, destination)
                self.assertFalse(destination.exists())
                self.git(self.source, "update-index", "--force-remove", "unsafe-entry")

    def test_committed_secret_file_is_rejected_before_model_call(self):
        self.head = self.commit(".env", "FAKE_TEST_ONLY=value\n", "Include forbidden fixture")
        self.publish()
        result, review = self.execute()
        self.assert_blocked_before_review(result, review, "Potential secret file")

    def test_snapshot_ignores_archive_attributes_and_preserves_exact_blob_bytes(self):
        source_bytes = b'REVISION = "$Format:%H$"\n# Keep the literal placeholder.\n'
        self.commit(".gitattributes", "CLAUDE.md export-ignore\ninventory.py export-subst\n",
                    "Add archive attributes")
        self.commit("CLAUDE.md", "Required project context.\n", "Add project context")
        self.head = self.commit("inventory.py", source_bytes.decode("utf-8"), "Add literal revision text")
        snapshot = self.root / "exact-snapshot"
        runner.export_snapshot(self.source, self.head, snapshot)
        self.assertEqual((snapshot / "CLAUDE.md").read_text(encoding="utf-8"), "Required project context.\n")
        self.assertEqual((snapshot / "inventory.py").read_bytes(), source_bytes)

    def test_remote_moving_during_review_invalidates_approval(self):
        def move_remote(*arguments):
            verdict = self.approved(*arguments)
            self.head = self.commit("later.txt", "Later publication.\n", "Move dev during review")
            self.publish()
            return verdict
        result, review = self.execute(reviewer=move_remote)
        self.assertEqual(result["status"], "BLOCKED", result)
        self.assertIn("moved during review", result["summary"])
        review.assert_called_once()

    def test_branch_switch_during_review_invalidates_approval(self):
        def switch_branch(*arguments):
            verdict = self.approved(*arguments)
            self.git(self.clone, "checkout", "-b", "main")
            return verdict
        result, review = self.execute(reviewer=switch_branch)
        self.assertEqual(result["status"], "BLOCKED", result)
        review.assert_called_once()

    def test_dirty_clone_after_review_invalidates_approval(self):
        def dirty_clone(*arguments):
            verdict = self.approved(*arguments)
            (self.clone / "unexpected.txt").write_text("Changed during review.\n", encoding="utf-8")
            return verdict
        result, review = self.execute(reviewer=dirty_clone)
        self.assertEqual(result["status"], "BLOCKED", result)
        self.assertIn("changed during review", result["summary"])
        review.assert_called_once()

    def test_review_exports_source_without_running_reviewed_python(self):
        sentinel = self.root / "reviewed-code-executed"
        trap = "from pathlib import Path\nPath(" + repr(str(sentinel)) + ").write_text('executed')\n"
        self.head = self.commit("sitecustomize.py", trap, "Add source execution tripwire")
        self.head = self.commit("tests/test_tripwire.py", trap, "Add test execution tripwire")
        self.publish()
        original_run = subprocess.run
        with mock.patch.object(runner.subprocess, "run", wraps=original_run) as processes:
            result, _ = self.execute()
        self.assertEqual(result["status"], "APPROVED", result)
        self.assertFalse(sentinel.exists())
        self.assertTrue(processes.call_args_list)
        for call in processes.call_args_list:
            self.assertEqual(Path(call.args[0][0]).name.lower(), "git")
        snapshot = self.root / "state" / "runs" / result["metadata"]["run_id"] / "snapshot" / "head"
        self.assertEqual((snapshot / "sitecustomize.py").read_text(encoding="utf-8"), trap)

    def test_main_returns_structured_blocked_for_invalid_sha_stage_or_iteration(self):
        config_path = self.root / "config.json"
        config_path.write_text(json.dumps(self.config), encoding="utf-8")
        cases = [
            ["not-a-sha", self.head, "inventory", "0"],
            [self.base, "main", "inventory", "0"],
            [self.base, self.head, "../stage", "0"],
            [self.base, self.head, "inventory", "4"],
            [self.base, self.head, "inventory", "not-an-integer"],
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                output = io.StringIO()
                with mock.patch.object(runner.os, "umask"), contextlib.redirect_stdout(output):
                    code = runner.main(arguments + ["--config", str(config_path)])
                result = json.loads(output.getvalue())
                self.assertEqual(code, 3)
                self.assertEqual(result["status"], "BLOCKED")

    def test_main_returns_structured_blocked_for_malformed_config_json(self):
        config_path = self.root / "invalid-config.json"
        config_path.write_text("{invalid-json", encoding="utf-8")
        output = io.StringIO()
        with mock.patch.object(runner.os, "umask"), contextlib.redirect_stdout(output):
            code = runner.main([self.base, self.head, "inventory", "0", "--config", str(config_path)])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output.getvalue())["status"], "BLOCKED")

    def test_main_returns_structured_blocked_for_nonobject_config(self):
        config_path = self.root / "nonobject-config.json"
        config_path.write_text("42", encoding="utf-8")
        output = io.StringIO()
        with mock.patch.object(runner.os, "umask"), contextlib.redirect_stdout(output):
            code = runner.main([self.base, self.head, "inventory", "0", "--config", str(config_path)])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output.getvalue())["status"], "BLOCKED")

    def test_model_invocation_allows_only_read_tools_and_disables_hooks(self):
        snapshot = self.root / "model-snapshot"
        snapshot.mkdir()
        run_dir = self.root / "model-run"
        run_dir.mkdir()
        envelope = {
            "is_error": False, "subtype": "success",
            "structured_output": {
                "status": "APPROVED", "base_sha": self.base, "head_sha": self.head,
                "summary": "No blocking defects found.", "findings": [], "limitations": [],
            },
        }
        help_flags = (
            "--safe-mode --restricted --json-schema --tools --strict-mcp-config "
            "--permission-prompts --system-prompt --no-session-persistence"
        )

        def start_process(command, **kwargs):
            kwargs["stdout"].write(json.dumps(envelope).encode("utf-8"))
            kwargs["stdout"].flush()
            process = mock.Mock()
            process.wait.return_value = 0
            return process

        with mock.patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, stdout=help_flags, stderr="")), mock.patch.object(
                    runner.subprocess, "Popen", side_effect=start_process) as launch:
            result, _ = runner.run_claude(self.config, snapshot, run_dir, self.base, self.head, "inventory")
        self.assertEqual(result["status"], "APPROVED")
        command = launch.call_args.args[0]
        self.assertEqual(command[command.index("--tools") + 1], "Read,Grep,Glob")
        self.assertEqual(command[command.index("--allowedTools") + 1], "Read,Grep,Glob")
        self.assertEqual(command[command.index("--permission-prompts") + 1], "none")
        self.assertEqual(command[command.index("--mcp-config") + 1], '{"mcpServers":{}}')
        self.assertTrue(json.loads(command[command.index("--settings") + 1])["disableAllHooks"])
        self.assertIn("--safe-mode", command)
        self.assertIn("--restricted", command)
        self.assertIn("--no-session-persistence", command)
        self.assertEqual(launch.call_args.kwargs["cwd"], snapshot)
        self.assertEqual(launch.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def failed_cli_error(self, stdout):
        snapshot = self.root / "failed-model-snapshot"
        snapshot.mkdir()
        run_dir = self.root / "failed-model-run"
        run_dir.mkdir()
        help_flags = (
            "--safe-mode --restricted --json-schema --tools --strict-mcp-config "
            "--permission-prompts --system-prompt --no-session-persistence"
        )

        def start_process(command, **kwargs):
            kwargs["stdout"].write(stdout.encode("utf-8"))
            kwargs["stdout"].flush()
            process = mock.Mock()
            process.wait.return_value = 1
            return process

        with mock.patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, stdout=help_flags, stderr="")), mock.patch.object(
                    runner.subprocess, "Popen", side_effect=start_process), mock.patch.object(
                        runner, "parse_claude_output") as parse_verdict:
            with self.assertRaises(runner.ReviewError) as failure:
                runner.run_claude(self.config, snapshot, run_dir, self.base, self.head, "inventory")
        parse_verdict.assert_not_called()
        return str(failure.exception)

    def test_failed_model_reports_actionable_api_error_without_treating_it_as_verdict(self):
        message = "Updated terms require acceptance."
        failure = self.failed_cli_error(json.dumps({
            "is_error": True, "api_error_status": 400, "result": message,
        }))
        self.assertIn(message, failure)
        self.assertIn("1", failure)

    def test_failed_model_with_nonjson_output_preserves_generic_exit_diagnostic(self):
        failure = self.failed_cli_error("<html>Upstream proxy failed</html>")
        self.assertIn("Claude exited with code 1", failure)
        self.assertIn("claude.stderr.log", failure)
        self.assertNotIn("<html>", failure)


if __name__ == "__main__":
    unittest.main()
