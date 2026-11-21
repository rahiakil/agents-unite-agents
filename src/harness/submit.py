"""Push one report branch and open a pull request on the ledger.

Force push is refused in code. History on the ledger is not rewritten.
Auto-merge is requested after the PR exists. A later wake merges only after
the ledger's own checks are green.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class PushRefused(RuntimeError):
    pass


def assert_safe_push(argv: list[str]) -> None:
    banned = {"--force", "-f", "--force-with-lease", "--force-if-includes"}
    if any(arg in banned for arg in argv):
        raise PushRefused("force push is disabled")
    for arg in argv:
        if arg.startswith("+") or arg.startswith("HEAD:+"):
            raise PushRefused("force refspec is disabled")


def push_command(branch: str) -> list[str]:
    command = ["git", "push", "origin", f"HEAD:refs/heads/{branch}"]
    assert_safe_push(command)
    return command


def auto_merge_command(repo: str, number: int) -> list[str]:
    return ["gh", "pr", "merge", str(number), "--repo", repo, "--auto", "--squash"]


def direct_merge_command(repo: str, number: int) -> list[str]:
    return ["gh", "pr", "merge", str(number), "--repo", repo, "--squash"]


def _askpass_script() -> str:
    handle = tempfile.NamedTemporaryFile("w", delete=False, prefix="au-askpass-", suffix=".sh")
    handle.write("#!/bin/sh\n")
    handle.write('case "$1" in\n')
    handle.write('  *Username*|*username*) echo x-access-token ;;\n')
    handle.write("  *) printf '%s\\n' \"$LEDGER_TOKEN\" ;;\n")
    handle.write("esac\n")
    handle.close()
    os.chmod(handle.name, 0o700)
    return handle.name


def git_env(token: str) -> dict[str, str]:
    env = os.environ.copy()
    env["LEDGER_TOKEN"] = token
    env["GIT_ASKPASS"] = _askpass_script()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GH_TOKEN"] = token
    return env


def redact(text: str, token: str) -> str:
    if token and text:
        return text.replace(token, "[redacted]")
    return text or ""


def _run(argv: list[str], *, cwd: Path, env: dict[str, str], token: str) -> subprocess.CompletedProcess[str]:
    assert_safe_push(argv) if argv and argv[0] == "git" and "push" in argv else None
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    result.stdout = redact(result.stdout, token)
    result.stderr = redact(result.stderr, token)
    return result


@dataclass
class SubmitResult:
    pr_url: str
    pr_number: int | None
    merge_outcome: str
    detail: str


def fresh_submit_dir() -> Path:
    """Path for a new ledger clone. The path itself must not exist yet.

    ``tempfile.mkdtemp`` creates a directory. Passing that directory to
    ``git clone`` is a reuse, and ``clone_ledger`` refuses it.
    """
    parent = Path(tempfile.mkdtemp(prefix="au-ledger-"))
    return parent / "repo"


def clone_ledger(dest: Path, repo: str, ref: str, token: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    env = git_env(token)
    if dest.exists():
        raise RuntimeError(f"refusing to reuse submit directory {dest}")
    result = _run(
        ["git", "clone", "--depth", "1", "--branch", ref, f"https://github.com/{repo}.git", str(dest)],
        cwd=dest.parent,
        env=env,
        token=token,
    )
    if result.returncode != 0:
        raise RuntimeError(redact(result.stderr or result.stdout, token)[-500:])


def remote_branch_exists(repo: str, branch: str, token: str) -> bool:
    env = git_env(token)
    result = _run(
        ["git", "ls-remote", f"https://github.com/{repo}.git", f"refs/heads/{branch}"],
        cwd=Path("."),
        env=env,
        token=token,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def commit_and_push(
    work: Path,
    *,
    repo: str,
    branch: str,
    message: str,
    author_name: str,
    author_email: str,
    token: str,
    paths: list[str],
) -> str:
    env = git_env(token)
    env["GIT_AUTHOR_NAME"] = author_name
    env["GIT_AUTHOR_EMAIL"] = author_email
    env["GIT_COMMITTER_NAME"] = author_name
    env["GIT_COMMITTER_EMAIL"] = author_email
    if remote_branch_exists(repo, branch, token):
        return "branch-exists"
    checkout = _run(["git", "checkout", "-B", branch, f"origin/{_base_ref()}"], cwd=work, env=env, token=token)
    if checkout.returncode != 0:
        checkout = _run(["git", "checkout", "-B", branch], cwd=work, env=env, token=token)
        if checkout.returncode != 0:
            return f"checkout failed: {checkout.stderr[-300:]}"
    add = _run(["git", "add", "--", *paths], cwd=work, env=env, token=token)
    if add.returncode != 0:
        return f"git add failed: {add.stderr[-300:]}"
    commit = _run(["git", "commit", "-m", message], cwd=work, env=env, token=token)
    if commit.returncode != 0:
        return f"git commit failed: {commit.stderr[-300:]}"
    push = _run(push_command(branch), cwd=work, env=env, token=token)
    if push.returncode != 0:
        return f"git push failed: {(push.stderr or push.stdout)[-300:]}"
    return ""


def _base_ref() -> str:
    return os.environ.get("LEDGER_BASE", "main")


def _pr_list(repo: str, branch: str, token: str) -> tuple[int | None, str]:
    env = git_env(token)
    result = _run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "number,url",
        ],
        cwd=Path("."),
        env=env,
        token=token,
    )
    if result.returncode != 0:
        return None, ""
    try:
        rows = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None, ""
    if not rows:
        return None, ""
    return int(rows[0]["number"]), str(rows[0]["url"])


def open_pr(repo: str, branch: str, title: str, body: str, token: str) -> tuple[int | None, str, str]:
    env = git_env(token)
    result = _run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            repo,
            "--base",
            _base_ref(),
            "--head",
            branch,
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=Path("."),
        env=env,
        token=token,
    )
    text = (result.stdout or "") + "\n" + (result.stderr or "")
    for line in text.splitlines():
        if line.startswith("https://") and "/pull/" in line:
            number = line.rstrip("/").rsplit("/", 1)[-1]
            if number.isdigit():
                return int(number), line.strip(), ""
    number, url = _pr_list(repo, branch, token)
    if number:
        return number, url, ""
    return None, "", text[-400:]


def request_auto_merge(repo: str, number: int, token: str, mode: str) -> tuple[str, str]:
    if mode in {"off", "0", "false"}:
        return "merge_disabled", "HARNESS_MERGE is off"
    env = git_env(token)
    result = _run(auto_merge_command(repo, number), cwd=Path("."), env=env, token=token)
    detail = ((result.stdout or "") + " " + (result.stderr or "")).strip()[-400:]
    if result.returncode == 0:
        return "auto_merge_requested", detail
    return "auto_merge_rejected", detail


def interpret_checks(rollup: list | None) -> str:
    if not rollup:
        return "pending"
    states = []
    for item in rollup:
        if not isinstance(item, dict):
            states.append("pending")
            continue
        if "conclusion" in item or "status" in item and "state" not in item:
            status = str(item.get("status") or "").upper()
            conclusion = str(item.get("conclusion") or "").upper()
            if status and status != "COMPLETED":
                states.append("pending")
            elif conclusion in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
                states.append("success")
            elif conclusion:
                states.append("failure")
            else:
                states.append("pending")
            continue
        state = str(item.get("state") or item.get("conclusion") or "").upper()
        if state in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
            states.append("success")
        elif state in {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT"}:
            states.append("failure")
        else:
            states.append("pending")
    if any(item == "failure" for item in states):
        return "failure"
    if any(item == "pending" for item in states):
        return "pending"
    if any(item == "success" for item in states):
        return "success"
    return "pending"


def pull_request_snapshot(repo: str, number: int, token: str) -> dict:
    env = git_env(token)
    result = _run(
        [
            "gh",
            "pr",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "state,mergedAt,statusCheckRollup,url,mergeable,autoMergeRequest",
        ],
        cwd=Path("."),
        env=env,
        token=token,
    )
    if result.returncode != 0:
        return {"error": (result.stderr or result.stdout)[-300:]}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": "pr view returned non-JSON"}


def maybe_merge_green(repo: str, number: int, token: str, mode: str) -> tuple[str, str]:
    if mode not in {"auto", "1", "true", ""}:
        return "checks_pending", "direct merge is only used when HARNESS_MERGE=auto"
    snapshot = pull_request_snapshot(repo, number, token)
    if snapshot.get("error"):
        return "checks_pending", str(snapshot["error"])
    if snapshot.get("mergedAt") or str(snapshot.get("state") or "").upper() == "MERGED":
        return "merged", "already merged"
    if str(snapshot.get("state") or "").upper() == "CLOSED":
        return "closed_unmerged", "pull request closed"
    checks = interpret_checks(snapshot.get("statusCheckRollup"))
    if checks == "failure":
        return "checks_failed", "a ledger check failed"
    if checks != "success":
        return "checks_pending", "ledger checks still running"
    if str(snapshot.get("mergeable") or "").upper() not in {"MERGEABLE", "UNKNOWN", ""}:
        return "merge_failed", f"mergeable={snapshot.get('mergeable')}"
    env = git_env(token)
    result = _run(direct_merge_command(repo, number), cwd=Path("."), env=env, token=token)
    detail = ((result.stdout or "") + " " + (result.stderr or "")).strip()[-400:]
    if result.returncode == 0:
        return "merged", detail
    return "merge_failed", detail
