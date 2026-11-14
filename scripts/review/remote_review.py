"""Trusted, installed review entry point. Never execute code from the reviewed tree."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

from protocol import make_blocked, parse_claude_output, strict_json_loads, validate_result

SHA = re.compile(r"[0-9a-f]{40}\Z")
STAGE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
EXIT_CODES = {"APPROVED": 0, "CHANGES_REQUESTED": 2, "BLOCKED": 3}


class ReviewError(Exception):
    pass


class ReviewArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ReviewError(f"Invalid arguments: {message}")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def git(repo, *arguments, binary=False, timeout=90):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=15")
    command = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
               "-c", "protocol.ext.allow=never"]
    if repo is not None:
        command += ["-C", str(repo)]
    try:
        completed = subprocess.run(command + list(arguments), capture_output=True,
                                   timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewError(f"Git command failed: {exc}") from exc
    if completed.returncode:
        error = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ReviewError(f"Git {' '.join(arguments[:2])} failed: {error}")
    return completed.stdout if binary else completed.stdout.decode("utf-8", errors="strict").strip()


def remote_head(repo):
    lines = git(repo, "ls-remote", "--exit-code", "origin", "refs/heads/dev").splitlines()
    if len(lines) != 1 or len(lines[0].split()) != 2:
        raise ReviewError("Cannot determine the unique remote dev commit")
    sha, ref = lines[0].split()
    if ref != "refs/heads/dev" or not SHA.fullmatch(sha):
        raise ReviewError("Invalid remote dev reference")
    return sha


def synchronize(config, base, head):
    repo = Path(config["repository"]).expanduser().resolve()
    marker = repo / ".git" / "checkstock-review-clone"
    if not repo.exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        git(None, "clone", "--no-local", "--branch", "dev", "--", config["repository_url"], str(repo))
        marker.write_text("Dedicated review clone.\n", encoding="utf-8")
    if not marker.is_file():
        raise ReviewError("Refusing to use a repository without the dedicated review clone marker")
    if git(repo, "remote", "get-url", "origin") != config["repository_url"]:
        raise ReviewError("Review clone origin does not match the trusted configuration")
    if git(repo, "branch", "--show-current") != "dev":
        raise ReviewError("The review clone must be on dev")
    if git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise ReviewError("The review clone has local changes; preserve them and resolve manually")
    before = git(repo, "rev-parse", "HEAD")
    # No leading '+' refspec: rewritten or divergent history must not be hidden.
    git(repo, "fetch", "--no-tags", "origin", "refs/heads/dev:refs/remotes/origin/dev")
    fetched = git(repo, "rev-parse", "refs/remotes/origin/dev")
    if fetched != head or remote_head(repo) != head:
        raise ReviewError("The requested head is not the current GitHub dev tip")
    git(repo, "cat-file", "-e", base + "^{commit}")
    git(repo, "cat-file", "-e", head + "^{commit}")
    git(repo, "merge-base", "--is-ancestor", base, head)
    if base == head:
        raise ReviewError("The stage range is empty")
    if not git(repo, "diff", "--name-only", base, head, "--"):
        raise ReviewError("The stage contains no file changes")
    # Refuse a locally ahead clone as well as a divergent clone.
    git(repo, "merge-base", "--is-ancestor", before, fetched)
    if before != fetched:
        git(repo, "merge", "--ff-only", "--no-edit", "refs/remotes/origin/dev")
    if git(repo, "rev-parse", "HEAD") != head:
        raise ReviewError("Local dev did not synchronize to the requested head")
    return repo, {"before_sha": before, "after_sha": head, "updated": before != head}


def export_snapshot(repo, sha, destination):
    """Extract only ordinary tracked files; deny symlinks, submodules and secrets."""
    entries = git(repo, "ls-tree", "-r", "-z", "-l", "--full-tree", sha, binary=True).split(b"\0")
    files = []
    total_size = 0
    for entry in filter(None, entries):
        header, raw_name = entry.split(b"\t", 1)
        mode, kind, oid, size = header.split()
        name = raw_name.decode("utf-8", errors="strict")
        path = PurePosixPath(name)
        if mode not in (b"100644", b"100755") or kind != b"blob":
            raise ReviewError(f"Review snapshots do not support symlinks or submodules: {name}")
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ReviewError("Unsafe path in repository tree")
        if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
            raise ReviewError(f"Potential secret file must not be included in review: {name}")
        total_size += int(size)
        if total_size > 100 * 1024 * 1024:
            raise ReviewError("Review snapshot exceeds the 100 MiB limit")
        files.append((path, oid.decode("ascii"), int(size)))
    # Read exact committed blobs: archive export-ignore/export-subst attributes
    # must not omit or rewrite review evidence. Never use working-tree files.
    destination.mkdir()
    for path, oid, size in files:
        target = destination.joinpath(*path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = git(repo, "cat-file", "blob", oid, binary=True)
        if len(content) != size:
            raise ReviewError("Committed blob size changed unexpectedly")
        target.write_bytes(content)


def policy_body(path):
    text = path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        parts = text.split("---\n", 2)
        if len(parts) != 3:
            raise ReviewError("Malformed reviewer skill frontmatter")
        text = parts[2]
    if not text.strip():
        raise ReviewError("The reviewer policy is empty")
    return text


def run_claude(config, snapshot, run_dir, base, head, stage):
    executable = str(Path(config["claude_executable"]).expanduser())
    help_result = subprocess.run([executable, "--help"], capture_output=True, text=True, timeout=30)
    required = ["--safe-mode", "--restricted", "--json-schema", "--tools", "--strict-mcp-config",
                "--permission-prompts", "--system-prompt", "--no-session-persistence"]
    if help_result.returncode or any(flag not in help_result.stdout for flag in required):
        raise ReviewError("Claude CLI lacks required isolation or structured-output options")
    policy = Path(config["policy_file"]).expanduser()
    schema = Path(__file__).with_name("review.schema.json").read_text(encoding="utf-8")
    prompt = (f"Review stage {stage}. Base commit: {base}. Head commit: {head}. "
              "Read request.json and changes.diff, then inspect the relevant files in head/ and base/. "
              "Return the required structured review result in English. Report repository-relative paths.")
    settings = {"disableAllHooks": True, "permissions": {"defaultMode": "dontAsk"}}
    command = [executable, "--print", "--output-format", "json", "--json-schema", schema,
               "--safe-mode", "--restricted", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--tools", "Read,Grep,Glob", "--allowedTools", "Read,Grep,Glob",
               "--permission-mode", "dontAsk", "--permission-prompts", "none",
               "--settings", json.dumps(settings), "--no-session-persistence", "--no-chrome",
               "--disable-slash-commands", "--system-prompt", policy_body(policy), prompt]
    # No SSH/Git/environment secrets are included in the model prompt or snapshot.
    env = {key: value for key, value in os.environ.items()
           if key in ("HOME", "PATH", "LANG", "LC_ALL", "TERM", "USER", "LOGNAME", "XDG_CONFIG_HOME")}
    started = time.monotonic()
    with (run_dir / "claude.stdout.json").open("wb") as output, (run_dir / "claude.stderr.log").open("wb") as errors:
        process = subprocess.Popen(command, cwd=snapshot, env=env, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=errors, start_new_session=True)
        try:
            returncode = process.wait(timeout=config["timeout_seconds"])
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            raise ReviewError("Claude review timed out") from exc
    raw = (run_dir / "claude.stdout.json").read_text(encoding="utf-8")
    if returncode:
        message = f"Claude exited with code {returncode}; see claude.stderr.log"
        try:
            failure = strict_json_loads(raw)
            if (isinstance(failure, dict) and failure.get("is_error") is True
                    and isinstance(failure.get("result"), str) and failure["result"].strip()):
                message += ": " + failure["result"].strip()[:1500]
        except ValueError:
            pass
        raise ReviewError(message)
    result = parse_claude_output(raw, base, head)
    return result, {"claude_seconds": round(time.monotonic() - started, 3),
                    "policy_sha256": hashlib.sha256(policy.read_bytes()).hexdigest()}


@contextlib.contextmanager
def review_lock(state_root):
    import fcntl
    state_root.mkdir(parents=True, exist_ok=True)
    with (state_root / "review.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ReviewError("Another review is already using the dedicated clone") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def execute(config, base, head, stage, iteration):
    for name, sha in (("base", base), ("head", head)):
        if not SHA.fullmatch(sha):
            raise ReviewError(f"Invalid full {name} commit SHA")
    if not STAGE.fullmatch(stage) or stage in (".", ".."):
        raise ReviewError("Invalid stage identifier")
    if not 0 <= iteration <= 3:
        raise ReviewError("Review iteration must be between 0 and 3")
    state_root = Path(config["state_root"]).expanduser().resolve()
    with review_lock(state_root):
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:10]
        run_dir = state_root / "runs" / run_id
        run_dir.mkdir(parents=True, mode=0o700)
        metadata = {"run_id": run_id, "stage": stage, "iteration": iteration,
                    "started_at": utc_now(), "base_sha": base, "head_sha": head}
        try:
            repo, synchronization = synchronize(config, base, head)
            metadata["synchronization"] = synchronization
            snapshot = run_dir / "snapshot"
            snapshot.mkdir()
            export_snapshot(repo, head, snapshot / "head")
            export_snapshot(repo, base, snapshot / "base")
            diff = git(repo, "diff", "--no-ext-diff", "--no-textconv", "--unified=30", base, head, "--", binary=True)
            (snapshot / "changes.diff").write_bytes(diff)
            write_json(snapshot / "request.json", {"stage": stage, "base_sha": base, "head_sha": head,
                       "changed_files": git(repo, "diff", "--name-only", base, head, "--").splitlines(),
                       "scope": "Independent static code review of the complete stage range."})
            result, evidence = run_claude(config, snapshot, run_dir, base, head, stage)
            metadata.update(evidence)
            if (remote_head(repo) != head or git(repo, "rev-parse", "HEAD") != head
                    or git(repo, "branch", "--show-current") != "dev"):
                raise ReviewError("dev moved during review; the verdict is stale")
            if git(repo, "status", "--porcelain", "--untracked-files=all"):
                raise ReviewError("The dedicated clone changed during review")
            validate_result(result, base, head)
        except (ReviewError, ValueError, OSError, subprocess.SubprocessError) as exc:
            result = make_blocked(base, head, str(exc))
            metadata["error"] = str(exc)
        metadata["finished_at"] = utc_now()
        metadata["finding_counts"] = {priority: sum(f["severity"] == priority for f in result["findings"])
                                      for priority in ("P0", "P1", "P2", "P3")}
        result["metadata"] = metadata
        write_json(run_dir / "result.json", result)
        return result


def main(argv=None):
    os.umask(0o077)
    parser = ReviewArgumentParser(description=__doc__)
    parser.add_argument("base_sha")
    parser.add_argument("head_sha")
    parser.add_argument("stage", nargs="?", default="manual")
    parser.add_argument("iteration", nargs="?", type=int, default=0)
    parser.add_argument("--config", type=Path, default=Path.home() / ".config/checkstock-review/config.json")
    base = head = ""
    try:
        args = parser.parse_args(argv)
        base, head = args.base_sha, args.head_sha
        config = json.loads(args.config.read_text(encoding="utf-8"))
        required = ("repository", "repository_url", "state_root", "policy_file", "claude_executable", "timeout_seconds")
        if not isinstance(config, dict) or any(key not in config for key in required):
            raise ReviewError("Incomplete trusted review configuration")
        if any(not isinstance(config[key], str) or not config[key] for key in required[:-1]):
            raise ReviewError("Review paths and repository URL must be nonempty strings")
        if type(config["timeout_seconds"]) is not int or not 1 <= config["timeout_seconds"] <= 1800:
            raise ReviewError("timeout_seconds must be between 1 and 1800")
        result = execute(config, args.base_sha, args.head_sha, args.stage, args.iteration)
    except (ReviewError, ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        result = make_blocked(base, head, str(exc))
    print(json.dumps(result, ensure_ascii=False))
    return EXIT_CODES[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
