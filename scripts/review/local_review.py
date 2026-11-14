"""Local stage-review coordinator; all durable state stays outside the checkout.

Only committed, tracked content is reviewed. Existing untracked files are never
staged, uploaded, removed, or treated as part of an approval.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

from protocol import make_blocked, strict_json_loads, validate_result


SHA = re.compile(r"[0-9a-f]{40}\Z")
STAGE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z", re.ASCII)
EXIT_CODES = {"APPROVED": 0, "CHANGES_REQUESTED": 2, "BLOCKED": 3}
DEFAULT_GIT_URL = "git@github-personal:vladicem0/CheckStock.git"
MAX_CORRECTIONS = 3


class ReviewError(Exception):
    """A condition which must never become an approval."""


class ReviewArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ReviewError(f"Invalid arguments: {message}")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def run_command(command, *, timeout=60, check=True):
    """Use argument arrays, never a local shell; constrain every subprocess."""
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewError(f"Command could not complete: {exc}") from exc
    if check and completed.returncode:
        diagnostic = (completed.stderr or completed.stdout).strip()
        raise ReviewError(f"Command failed ({completed.returncode}): {diagnostic[:4000]}")
    return completed


def git(repo, *arguments, check=True):
    return run_command(["git", "-C", str(repo), *arguments], check=check)


def local_head(repo):
    value = git(repo, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip()
    if not SHA.fullmatch(value):
        raise ReviewError("Local HEAD is not a full SHA-1 commit identifier.")
    return value


def remote_head(repo, git_url):
    command = [
        "git", "-C", str(repo), "-c",
        "core.sshCommand=ssh -o BatchMode=yes -o ConnectTimeout=15",
        "ls-remote", "--exit-code", "--", git_url, "refs/heads/dev",
    ]
    lines = run_command(command).stdout.strip().splitlines()
    if len(lines) != 1:
        raise ReviewError("GitHub dev is missing or ambiguous.")
    parts = lines[0].split()
    if len(parts) != 2 or parts[1] != "refs/heads/dev" or not SHA.fullmatch(parts[0]):
        raise ReviewError("GitHub returned an invalid dev reference.")
    return parts[0]


def checked_head(repo, git_url):
    if git(repo, "branch", "--show-current").stdout.strip() != "dev":
        raise ReviewError("The current branch must be dev.")
    if git(repo, "status", "--porcelain=v1", "--untracked-files=no").stdout.strip():
        raise ReviewError("Tracked changes must be committed before stage review.")
    head = local_head(repo)
    if remote_head(repo, git_url) != head:
        raise ReviewError("Local dev and GitHub dev differ; reconcile and push before review.")
    return head


def check_range(repo, base, head):
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise ReviewError("Both commit identifiers must be full lowercase SHA-1 values.")
    git(repo, "cat-file", "-e", f"{base}^{{commit}}")
    git(repo, "cat-file", "-e", f"{head}^{{commit}}")
    if git(repo, "merge-base", "--is-ancestor", base, head, check=False).returncode:
        raise ReviewError("The stage base is not an ancestor of the requested head.")
    difference = git(repo, "diff", "--quiet", base, head, "--", check=False)
    if difference.returncode == 0:
        raise ReviewError("The stage contains no file changes to review.")
    if difference.returncode != 1:
        raise ReviewError("Unable to determine the stage diff.")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def stage_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    lock = (directory / ".lock").open("a+b")
    locked = False
    try:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise ReviewError("Another operation is using this stage.") from exc
        yield
    finally:
        if locked:
            lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def load_state(path, repo, stage, git_url):
    try:
        state = strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReviewError("Stage state is missing or invalid; run start before implementation.") from exc
    if not isinstance(state, dict) or state.get("version") != 1:
        raise ReviewError("Unsupported stage-state format.")
    if state.get("stage") != stage or state.get("repo") != str(repo) or state.get("git_url") != git_url:
        raise ReviewError("The stage belongs to a different checkout or remote.")
    if not SHA.fullmatch(str(state.get("base_sha", ""))):
        raise ReviewError("Stage state contains an invalid base SHA.")
    for field in ("attempts", "iteration"):
        if type(state.get(field)) is not int or state[field] < 0:
            raise ReviewError(f"Stage state contains an invalid {field}.")
    if state["iteration"] > MAX_CORRECTIONS:
        raise ReviewError("The stage exceeded the correction limit.")
    return state


def next_iteration(state, head, *, reopen_approved=False):
    """An initial review plus three code-correction rounds; retries cost no round."""
    previous = state.get("last_reviewed_head")
    iteration = state["iteration"]
    if reopen_approved:
        if not previous or state.get("last_review_status") != "APPROVED":
            raise ReviewError("--reopen-approved requires a prior APPROVED review.")
        if head == previous:
            raise ReviewError("--reopen-approved requires a changed head after approval.")
        if iteration >= MAX_CORRECTIONS:
            raise ReviewError("Three correction/re-review rounds are exhausted; report both positions to the user.")
        return iteration + 1
    if previous == head and state.get("last_review_status") == "CHANGES_REQUESTED":
        raise ReviewError("The requested commit already needs changes; commit a correction before re-review.")
    if previous and head != previous:
        if state.get("last_review_status") == "APPROVED":
            raise ReviewError("This stage was approved for a different commit; use --reopen-approved only for explicitly authorized corrections, or start a new stage.")
        if state.get("last_review_status") == "CHANGES_REQUESTED":
            if iteration >= MAX_CORRECTIONS:
                raise ReviewError("Three correction/re-review rounds are exhausted; report both positions to the user.")
            iteration += 1
    return iteration


def invoke_review(args, base, head, iteration):
    remote = " ".join(shlex.quote(value) for value in (
        "bash", args.remote_script, base, head, args.stage, str(iteration),
    ))
    command = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
        "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
        "-l", args.ssh_user, args.ssh_host, remote,
    ]
    return run_command(command, timeout=args.timeout, check=False)


def validate_metadata(result, stage, iteration):
    if result["status"] != "BLOCKED":
        metadata = result.get("metadata", {})
        if metadata.get("stage") != stage:
            raise ReviewError("Review result does not match the requested stage.")
        if type(metadata.get("iteration")) is not int or metadata["iteration"] != iteration:
            raise ReviewError("Review result does not match the requested correction iteration.")


def parse_remote_result(completed, base, head, stage, iteration):
    try:
        result = strict_json_loads(completed.stdout)
        validate_result(result, base, head)
    except (ValueError, TypeError, KeyError) as exc:
        raise ReviewError(f"Review returned invalid structured JSON: {exc}") from exc
    expected = EXIT_CODES[result["status"]]
    if completed.returncode != expected:
        raise ReviewError(f"Review exit code {completed.returncode} contradicts status {result['status']}.")
    validate_metadata(result, stage, iteration)
    return result


def persist_run(directory, state, result, *, started, duration, stdout="", stderr="", error=None, reopen_approved=False):
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
    run_dir = directory / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (run_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
    atomic_json(run_dir / "result.json", result)
    findings = result.get("findings", [])
    counts = {severity: sum(item.get("severity") == severity for item in findings) for severity in ("P0", "P1", "P2", "P3")}
    metadata = {
        "run_id": run_id,
        "stage": state["stage"],
        "base_sha": state["base_sha"],
        "head_sha": result.get("head_sha"),
        "started_at": started,
        "finished_at": utc_now(),
        "duration_seconds": round(duration, 3),
        "attempt": state["attempts"],
        "iteration": state["iteration"],
        "status": result["status"],
        "finding_counts": counts,
        "reopen_approved": reopen_approved,
        "error": error,
    }
    atomic_json(run_dir / "metadata.json", metadata)
    state["last_run"] = run_id
    state["last_result"] = result
    state["updated_at"] = utc_now()
    atomic_json(directory / "state.json", state)


def start_stage(args, directory):
    state_path = directory / "state.json"
    if state_path.exists():
        state = load_state(state_path, args.repo, args.stage, args.git_url)
        raise ReviewError(f"Stage already exists with immutable base {state['base_sha']}; choose a new stage name.")
    head = checked_head(args.repo, args.git_url)
    state = {
        "version": 1,
        "stage": args.stage,
        "repo": str(args.repo),
        "git_url": args.git_url,
        "base_sha": head,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "attempts": 0,
        "iteration": 0,
        "last_reviewed_head": None,
        "last_review_status": None,
        "last_result": None,
    }
    atomic_json(state_path, state)
    return {"stage": args.stage, "state": "STARTED", "base_sha": head, "state_file": str(state_path)}, 0


def review_stage(args, directory):
    state = load_state(directory / "state.json", args.repo, args.stage, args.git_url)
    base = state["base_sha"]
    head = base
    stdout = stderr = ""
    started = utc_now()
    clock = time.monotonic()
    state["attempts"] += 1
    error = None
    try:
        head = local_head(args.repo)
        if checked_head(args.repo, args.git_url) != head:
            raise ReviewError("Local HEAD changed while preparing review.")
        check_range(args.repo, base, head)
        iteration = next_iteration(state, head, reopen_approved=getattr(args, "reopen_approved", False))
        if getattr(args, "reopen_approved", False):
            previous = state["last_reviewed_head"]
            if git(args.repo, "merge-base", "--is-ancestor", previous, head, check=False).returncode:
                raise ReviewError("The reopened head must descend from the previously approved commit.")
        state["iteration"] = iteration
        # Reserve this head/round before dispatch, including a process failure.
        # A BLOCKED retry therefore cannot charge the same correction twice.
        if state.get("last_reviewed_head") != head:
            state["last_reviewed_head"] = head
            state["last_review_status"] = None
        atomic_json(directory / "state.json", state)
        completed = invoke_review(args, base, head, iteration)
        stdout, stderr = completed.stdout, completed.stderr
        result = parse_remote_result(completed, base, head, args.stage, iteration)
        if checked_head(args.repo, args.git_url) != head:
            raise ReviewError("Local dev or GitHub dev changed during review; this verdict is stale.")
        if result["status"] != "BLOCKED":
            state["last_review_status"] = result["status"]
    except (ReviewError, OSError, ValueError) as exc:
        error = str(exc)
        result = make_blocked(base, head, error)
    persist_run(directory, state, result, started=started, duration=time.monotonic() - clock,
                stdout=stdout, stderr=stderr, error=error,
                reopen_approved=getattr(args, "reopen_approved", False))
    return result, EXIT_CODES[result["status"]]


def stage_status(args, directory):
    state = load_state(directory / "state.json", args.repo, args.stage, args.git_url)
    result = state.get("last_result")
    if result is None:
        return {"stage": args.stage, "state": "STARTED", "base_sha": state["base_sha"], "attempts": state["attempts"], "iteration": state["iteration"]}, 0
    validate_result(result, state["base_sha"], result.get("head_sha"))
    validate_metadata(result, args.stage, state["iteration"])
    if result["status"] == "APPROVED":
        try:
            if checked_head(args.repo, args.git_url) != result["head_sha"]:
                raise ReviewError("Approval applies to an older commit; the current stage is not approved.")
        except ReviewError as exc:
            result = make_blocked(state["base_sha"], result["head_sha"], str(exc))
    return result, EXIT_CODES[result["status"]]


def default_state_directory():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "CheckStock" / "reviews"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "checkstock-review" / "local"


def parse_arguments(argv=None):
    parser = ReviewArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "review", "status"))
    parser.add_argument("--stage", required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--state-dir", type=Path, default=default_state_directory())
    parser.add_argument("--git-url", default=DEFAULT_GIT_URL)
    parser.add_argument("--ssh-host", default="vpn")
    parser.add_argument("--ssh-user", default="dev")
    parser.add_argument("--remote-script", default="/home/dev/bin/review-stage.sh")
    parser.add_argument("--timeout", type=int, default=960)
    parser.add_argument("--reopen-approved", action="store_true",
                        help="Review only: explicitly reopen an approved stage for authorized corrections.")
    args = parser.parse_args(argv)
    if args.reopen_approved and args.action != "review":
        parser.error("--reopen-approved is allowed only with the review action.")
    if not STAGE.fullmatch(args.stage) or args.stage in (".", ".."):
        parser.error("--stage must be a lowercase ASCII identifier (1-64 characters).")
    for field in ("ssh_host", "ssh_user"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", getattr(args, field), re.ASCII):
            parser.error(f"--{field.replace('_', '-')} must be a simple SSH name.")
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", args.remote_script, re.ASCII) or ".." in Path(args.remote_script).parts:
        parser.error("--remote-script must be an absolute safe POSIX path.")
    if args.timeout < 1:
        parser.error("--timeout must be positive.")
    args.repo = args.repo.resolve()
    args.state_dir = args.state_dir.resolve()
    # State must not become part of the code being reviewed or its Git history.
    if args.state_dir == args.repo or args.repo in args.state_dir.parents:
        parser.error("--state-dir must be outside the repository.")
    return args


def main(argv=None):
    args = None
    try:
        args = parse_arguments(argv)
        directory = args.state_dir / args.stage
        with stage_lock(directory):
            if args.action == "start":
                result, code = start_stage(args, directory)
            elif args.action == "review":
                result, code = review_stage(args, directory)
            else:
                result, code = stage_status(args, directory)
    except (ReviewError, OSError, ValueError, TypeError, KeyError) as exc:
        # Startup/transport failures also produce a single, unmistakable JSON error.
        # No synthetic successful SHA or approval is ever manufactured.
        result = make_blocked("", "", str(exc))
        result["metadata"] = {"stage": args.stage if args else None, "phase": "local-startup"}
        code = 3
    print(json.dumps(result, ensure_ascii=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
