"""Isolated workflow tests: real temporary Git history, simulated transport."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "review"))
import local_review as review


def verdict(base, head, status="APPROVED", stage="inventory-01", iteration=0):
    findings = []
    if status == "CHANGES_REQUESTED":
        findings = [{
            "severity": "P2",
            "file": "sample.txt",
            "line": 1,
            "issue": "The retry path can return a stale value after a conflicting update.",
            "recommendation": "Reload the value before returning from the retry path.",
        }]
    return {
        "status": status,
        "base_sha": base,
        "head_sha": head,
        "summary": "Review completed." if status != "BLOCKED" else "Required context is unavailable.",
        "findings": findings,
        "limitations": ["Required context is unavailable."] if status == "BLOCKED" else [],
        "metadata": {"stage": stage, "iteration": iteration},
    }


def completed(result, code=None):
    return subprocess.CompletedProcess(
        ["ssh"], review.EXIT_CODES[result["status"]] if code is None else code,
        json.dumps(result), "diagnostic output",
    )


class LocalReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="checkstock-local-review-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repository"
        self.repo.mkdir()
        self.git("init", "-b", "dev")
        self.git("config", "user.name", "Review Test")
        self.git("config", "user.email", "review-test@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.commit("baseline\n")
        self.args = argparse.Namespace(
            repo=self.repo,
            state_dir=self.root / "state",
            stage="inventory-01",
            git_url="git@github-personal:vladicem0/CheckStock.git",
            ssh_host="vpn",
            ssh_user="dev",
            remote_script="/home/dev/bin/review-stage.sh",
            timeout=960,
        )
        self.directory = self.args.state_dir / self.args.stage
        self.base = review.local_head(self.repo)
        self.real_remote_head = review.remote_head
        self.remote_patch = patch.object(review, "remote_head", side_effect=lambda repo, url: review.local_head(repo))
        self.remote = self.remote_patch.start()
        self.addCleanup(self.remote_patch.stop)

    def git(self, *args):
        result = subprocess.run(
            ["git", "-C", str(self.repo), *args], capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=False,
        )
        if result.returncode:
            self.fail(f"Fixture Git failed: {args}: {result.stderr}")
        return result.stdout.strip()

    def commit(self, content):
        (self.repo / "sample.txt").write_text(content, encoding="utf-8")
        self.git("add", "sample.txt")
        self.git("commit", "-m", "Update sample behavior")
        return review.local_head(self.repo)

    def state(self):
        return json.loads((self.directory / "state.json").read_text(encoding="utf-8"))

    def start(self):
        result, code = review.start_stage(self.args, self.directory)
        self.assertEqual(code, 0)
        self.assertEqual(result["base_sha"], self.base)

    def run_result(self, status="APPROVED", code=None):
        head = review.local_head(self.repo)
        state = self.state()
        iteration = state["iteration"]
        if state.get("last_reviewed_head") != head and state.get("last_review_status") == "CHANGES_REQUESTED":
            iteration += 1
        result = verdict(self.base, head, status, iteration=iteration)
        with patch.object(review, "invoke_review", return_value=completed(result, code)) as transport:
            actual, exit_code = review.review_stage(self.args, self.directory)
        return actual, exit_code, transport

    def test_start_freezes_base_and_ignores_untracked_files(self):
        (self.repo / "untracked.txt").write_text("excluded from review", encoding="utf-8")
        self.start()
        self.commit("implementation\n")
        with self.assertRaisesRegex(review.ReviewError, "already exists"):
            review.start_stage(self.args, self.directory)
        self.assertEqual(self.state()["base_sha"], self.base)

    def test_dirty_tracked_start_is_blocked(self):
        (self.repo / "sample.txt").write_text("unfinished", encoding="utf-8")
        with self.assertRaisesRegex(review.ReviewError, "Tracked changes"):
            review.start_stage(self.args, self.directory)
        self.assertFalse((self.directory / "state.json").exists())

    def test_wrong_branch_is_blocked(self):
        self.git("switch", "-c", "other")
        with self.assertRaisesRegex(review.ReviewError, "must be dev"):
            review.start_stage(self.args, self.directory)

    def test_unpublished_head_is_blocked(self):
        self.remote.side_effect = None
        self.remote.return_value = "f" * 40
        with self.assertRaisesRegex(review.ReviewError, "differ"):
            review.start_stage(self.args, self.directory)

    def test_remote_dev_lookup_uses_real_git(self):
        self.assertEqual(self.real_remote_head(self.repo, str(self.repo)), self.base)

    def test_approved_result_is_saved_with_logs(self):
        self.start()
        head = self.commit("implementation\n")
        result, code, transport = self.run_result()
        self.assertEqual((result["status"], code), ("APPROVED", 0))
        transport.assert_called_once_with(self.args, self.base, head, 0)
        state = self.state()
        log = self.directory / "runs" / state["last_run"]
        self.assertEqual(json.loads((log / "result.json").read_text())["head_sha"], head)
        metadata = json.loads((log / "metadata.json").read_text())
        self.assertEqual(metadata["finding_counts"], {"P0": 0, "P1": 0, "P2": 0, "P3": 0})
        self.assertEqual(metadata["attempt"], 1)
        self.assertEqual(metadata["iteration"], 0)
        self.assertEqual((log / "stderr.txt").read_text(), "diagnostic output")

    def test_changes_requested_is_normal_exit_two(self):
        self.start()
        self.commit("implementation\n")
        result, code, _ = self.run_result("CHANGES_REQUESTED")
        self.assertEqual((result["status"], code), ("CHANGES_REQUESTED", 2))
        self.assertEqual(self.state()["last_review_status"], "CHANGES_REQUESTED")

    def test_unchanged_changes_requested_head_does_not_restart_review(self):
        self.start()
        self.commit("implementation\n")
        self.run_result("CHANGES_REQUESTED")
        result, code, transport = self.run_result("APPROVED")
        self.assertEqual((result["status"], code), ("BLOCKED", 3))
        transport.assert_not_called()
        self.assertEqual(self.state()["iteration"], 0)

    def test_three_corrections_preserve_base_and_stop_fourth(self):
        self.start()
        self.commit("implementation\n")
        self.run_result("CHANGES_REQUESTED")
        for iteration in range(1, 4):
            head = self.commit(f"correction {iteration}\n")
            result, code, transport = self.run_result("CHANGES_REQUESTED")
            self.assertEqual(code, 2)
            self.assertEqual(result["base_sha"], self.base)
            transport.assert_called_once_with(self.args, self.base, head, iteration)
        self.commit("correction 4\n")
        result, code, transport = self.run_result()
        self.assertEqual((result["status"], code), ("BLOCKED", 3))
        self.assertIn("exhausted", result["summary"])
        transport.assert_not_called()
        self.assertEqual(self.state()["iteration"], 3)

    def test_blocked_retry_does_not_charge_correction_twice(self):
        self.start()
        self.commit("implementation\n")
        self.run_result("CHANGES_REQUESTED")
        head = self.commit("first correction\n")
        self.run_result("BLOCKED")
        result, code, transport = self.run_result("APPROVED")
        self.assertEqual((result["status"], code), ("APPROVED", 0))
        transport.assert_called_once_with(self.args, self.base, head, 1)
        self.assertEqual(self.state()["iteration"], 1)

    def test_ssh_failure_is_blocked_and_logged(self):
        self.start()
        self.commit("implementation\n")
        failure = subprocess.CompletedProcess(["ssh"], 255, "", "Connection refused")
        with patch.object(review, "invoke_review", return_value=failure):
            result, code = review.review_stage(self.args, self.directory)
        self.assertEqual((result["status"], code), ("BLOCKED", 3))
        log = self.directory / "runs" / self.state()["last_run"]
        self.assertEqual((log / "stderr.txt").read_text(), "Connection refused")

    def test_transport_timeout_is_blocked_and_retryable(self):
        self.start()
        self.commit("implementation\n")
        with patch.object(review, "invoke_review", side_effect=review.ReviewError("Command timed out")):
            result, code = review.review_stage(self.args, self.directory)
        self.assertEqual((result["status"], code), ("BLOCKED", 3))
        self.assertEqual(self.state()["iteration"], 0)
        result, code, _ = self.run_result()
        self.assertEqual(code, 0)
        self.assertEqual(self.state()["attempts"], 2)

    def test_malformed_json_never_becomes_approved(self):
        self.start()
        self.commit("implementation\n")
        with patch.object(review, "invoke_review", return_value=subprocess.CompletedProcess(["ssh"], 0, "APPROVED", "")):
            result, code = review.review_stage(self.args, self.directory)
        self.assertEqual((result["status"], code), ("BLOCKED", 3))

    def test_duplicate_status_key_is_rejected(self):
        result = verdict(self.base, self.base)
        raw = json.dumps(result).replace('"status": "APPROVED"', '"status": "BLOCKED", "status": "APPROVED"')
        with self.assertRaisesRegex(review.ReviewError, "Duplicate JSON key"):
            review.parse_remote_result(subprocess.CompletedProcess(["ssh"], 0, raw, ""), self.base, self.base, self.args.stage, 0)

    def test_wrong_stage_or_iteration_is_rejected(self):
        for stage, iteration in (("another-stage", 0), (self.args.stage, 1), (self.args.stage, False)):
            with self.subTest(stage=stage, iteration=iteration):
                result = verdict(self.base, self.base, stage=stage, iteration=iteration)
                with self.assertRaisesRegex(review.ReviewError, "does not match"):
                    review.parse_remote_result(completed(result), self.base, self.base, self.args.stage, 0)

    def test_success_without_runner_metadata_is_rejected(self):
        result = verdict(self.base, self.base)
        del result["metadata"]
        with self.assertRaisesRegex(review.ReviewError, "requested stage"):
            review.parse_remote_result(completed(result), self.base, self.base, self.args.stage, 0)

    def test_exit_code_cannot_contradict_verdict(self):
        self.start()
        self.commit("implementation\n")
        result, code, _ = self.run_result("APPROVED", code=255)
        self.assertEqual((result["status"], code), ("BLOCKED", 3))
        self.assertIn("contradicts", result["summary"])

    def test_wrong_commit_in_json_is_rejected(self):
        self.start()
        self.commit("implementation\n")
        wrong = verdict(self.base, "f" * 40)
        with patch.object(review, "invoke_review", return_value=completed(wrong)):
            result, code = review.review_stage(self.args, self.directory)
        self.assertEqual((result["status"], code), ("BLOCKED", 3))

    def test_remote_moves_while_review_runs(self):
        self.start()
        head = self.commit("implementation\n")
        self.remote.side_effect = [head, "f" * 40]
        with patch.object(review, "invoke_review", return_value=completed(verdict(self.base, head))):
            result, code = review.review_stage(self.args, self.directory)
        self.assertEqual((result["status"], code), ("BLOCKED", 3))
        self.assertIsNone(self.state()["last_review_status"])

    def test_local_edits_while_review_runs_invalidate_verdict(self):
        self.start()
        head = self.commit("implementation\n")
        def edit_during_review(*_):
            (self.repo / "sample.txt").write_text("uncommitted edit", encoding="utf-8")
            return completed(verdict(self.base, head))
        with patch.object(review, "invoke_review", side_effect=edit_during_review):
            result, code = review.review_stage(self.args, self.directory)
        self.assertEqual((result["status"], code), ("BLOCKED", 3))

    def test_approved_status_is_invalidated_when_head_moves(self):
        self.start()
        self.commit("implementation\n")
        self.run_result()
        self.commit("new work\n")
        result, code = review.stage_status(self.args, self.directory)
        self.assertEqual((result["status"], code), ("BLOCKED", 3))
        # Historical evidence is retained, never rewritten to approve the new SHA.
        self.assertEqual(self.state()["last_result"]["status"], "APPROVED")

    def test_approved_stage_cannot_be_reused_for_new_commit(self):
        self.start()
        self.commit("implementation\n")
        self.run_result()
        self.commit("new work\n")
        result, code, transport = self.run_result()
        self.assertEqual((result["status"], code), ("BLOCKED", 3))
        self.assertIn("start a new stage", result["summary"])
        transport.assert_not_called()

    def test_empty_stage_range_is_blocked_before_transport(self):
        self.start()
        result, code, transport = self.run_result()
        self.assertEqual((result["status"], code), ("BLOCKED", 3))
        self.assertIn("no file changes", result["summary"])
        transport.assert_not_called()

    def test_range_rejects_short_sha_and_nonancestor(self):
        with self.assertRaisesRegex(review.ReviewError, "full lowercase"):
            review.check_range(self.repo, self.base[:7], self.base)
        next_head = self.commit("implementation\n")
        with self.assertRaisesRegex(review.ReviewError, "not an ancestor"):
            review.check_range(self.repo, next_head, self.base)

    def test_stage_lock_rejects_concurrent_call(self):
        with review.stage_lock(self.directory):
            with self.assertRaisesRegex(review.ReviewError, "Another operation"):
                with review.stage_lock(self.directory):
                    self.fail("Lock unexpectedly acquired twice")

    def test_ssh_uses_explicit_arguments_and_timeout(self):
        with patch.object(review, "run_command", return_value=completed(verdict(self.base, self.base))) as command:
            review.invoke_review(self.args, self.base, self.base, 2)
        argv = command.call_args.args[0]
        self.assertEqual(argv[0], "ssh")
        self.assertIn("BatchMode=yes", argv)
        self.assertEqual(argv[-2], "vpn")
        self.assertEqual(argv[-1], f"bash /home/dev/bin/review-stage.sh {self.base} {self.base} inventory-01 2")
        self.assertEqual(command.call_args.kwargs, {"timeout": 960, "check": False})

    def test_invalid_stage_returns_blocked_json_and_exit_three(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = review.main(["start", "--stage", "../../escape", "--repo", str(self.repo)])
        self.assertEqual(code, 3)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "BLOCKED")
        review.validate_result(result, "0" * 40, "0" * 40)

    def test_state_directory_cannot_be_in_repository(self):
        with self.assertRaisesRegex(review.ReviewError, "outside the repository"):
            review.parse_arguments(["start", "--stage", "stage", "--repo", str(self.repo), "--state-dir", str(self.repo / "logs")])

    def test_corrupt_stage_state_is_blocked(self):
        self.start()
        (self.directory / "state.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(review.ReviewError, "Unsupported"):
            review.review_stage(self.args, self.directory)


if __name__ == "__main__":
    unittest.main()
