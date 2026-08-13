"""Resolve a submission repository: GitHub over HTTPS, at an exact commit.

Three intake rules, enforced here rather than by convention:

* the URL is ``https://github.com/<owner>/<repo>`` and nothing else — no SSH,
  no ``git://``, no plaintext HTTP, no other host, no embedded credentials
* the revision is a full 40-character commit SHA, pinned at submit time and
  verified against ``HEAD`` after checkout, so a later force-push cannot change
  what was scored
* the checkout stays under ``MAX_REPO_BYTES`` (100 MB, the cap the harness
  already applies to repositories)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

MAX_REPO_BYTES = 100 * 1024 * 1024
DEFAULT_FETCH_TIMEOUT = 300

_SHA_PATTERN = re.compile(r"\A[0-9a-f]{40}\Z")
_REPO_NAME_PATTERN = re.compile(r"\A[A-Za-z0-9._-]+\Z")


class FetchError(Exception):
    """The submission repository could not be accepted or resolved."""


class RepoRef(NamedTuple):
    owner: str
    repo: str
    url: str


class Checkout(NamedTuple):
    path: Path
    commit: str
    bytes_used: int


def parse_github_https(url: str) -> RepoRef:
    """Validate a GitHub HTTPS clone URL and return its owner/repo."""

    candidate = url.strip()
    if not candidate:
        raise FetchError("repository URL is empty")
    parsed = urlparse(candidate)
    if parsed.scheme != "https":
        raise FetchError(
            f"repository URL must use https, got {parsed.scheme or 'no'} scheme: {url}"
        )
    if parsed.username or parsed.password:
        raise FetchError("repository URL must not embed credentials")
    if parsed.hostname != "github.com":
        raise FetchError(f"repository must be hosted on github.com, got {parsed.hostname}")
    if parsed.port is not None:
        raise FetchError("repository URL must not specify a port")
    if parsed.query or parsed.fragment:
        raise FetchError("repository URL must not carry a query string or fragment")

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        raise FetchError(
            f"repository URL must be https://github.com/<owner>/<repo>, got {url}"
        )
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    for part in (owner, repo):
        if not _REPO_NAME_PATTERN.match(part) or part in (".", ".."):
            raise FetchError(f"invalid owner/repository name in URL: {url}")
    return RepoRef(owner, repo, f"https://github.com/{owner}/{repo}.git")


def parse_commit_sha(value: str) -> str:
    """Require a full, lowercase 40-hex commit SHA."""

    candidate = value.strip()
    if not _SHA_PATTERN.match(candidate):
        raise FetchError(
            "commit must be a full 40-character lowercase hex SHA pinned at "
            f"submit time, got {value!r}"
        )
    return candidate


def directory_size(path: Path, exclude: tuple[str, ...] = (".git",)) -> int:
    """Total bytes of a checkout, not following symlinks."""

    total = 0
    root = Path(path)
    for current, directories, files in os.walk(root, followlinks=False):
        directories[:] = [d for d in directories if d not in exclude]
        for name in files:
            entry = Path(current) / name
            try:
                total += entry.lstat().st_size
            except OSError:
                continue
    return total


def _reject_escaping_symlinks(root: Path) -> None:
    """A submission may not link out of its own checkout."""

    resolved_root = root.resolve()
    for current, directories, files in os.walk(root, followlinks=False):
        for name in (*directories, *files):
            entry = Path(current) / name
            if not entry.is_symlink():
                continue
            target = Path(os.path.realpath(entry))
            if target != resolved_root and resolved_root not in target.parents:
                raise FetchError(
                    f"symlink escapes the checkout: "
                    f"{entry.relative_to(root)} -> {os.readlink(entry)}"
                )


def _git(arguments: list[str], timeout: int) -> subprocess.CompletedProcess:
    environment = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_ALLOW_PROTOCOL": "https",
    }
    hardening = [
        "-c",
        "protocol.file.allow=never",
        "-c",
        "protocol.ext.allow=never",
        "-c",
        "credential.helper=",
        "-c",
        "core.symlinks=true",
    ]
    try:
        return subprocess.run(
            ["git", *hardening, *arguments],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
            check=False,
        )
    except FileNotFoundError as exc:
        raise FetchError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise FetchError(f"git timed out after {timeout}s: {' '.join(arguments)}") from exc


def checkout_pinned(
    url: str,
    commit: str,
    destination: Path,
    max_bytes: int = MAX_REPO_BYTES,
    timeout: int = DEFAULT_FETCH_TIMEOUT,
) -> Checkout:
    """Fetch exactly ``commit`` from ``url`` into ``destination``.

    Submodules are never initialised: a submission is the pinned commit of one
    repository, and nothing it points at.
    """

    reference = parse_github_https(url)
    sha = parse_commit_sha(commit)
    target = Path(destination)
    if target.exists() and any(target.iterdir()):
        raise FetchError(f"destination is not empty: {target}")
    target.mkdir(parents=True, exist_ok=True)

    result = _git(["init", "-q", str(target)], timeout)
    if result.returncode != 0:
        raise FetchError(f"git init failed: {result.stderr.strip()}")
    result = _git(["-C", str(target), "remote", "add", "origin", reference.url], timeout)
    if result.returncode != 0:
        raise FetchError(f"git remote add failed: {result.stderr.strip()}")

    result = _git(
        ["-C", str(target), "fetch", "--depth", "1", "--no-tags", "origin", sha],
        timeout,
    )
    if result.returncode != 0:
        raise FetchError(
            f"cannot fetch {sha} from {reference.url} — is the commit pushed and "
            f"the repository public? git said: {result.stderr.strip()}"
        )

    git_bytes = directory_size(target, exclude=())
    if git_bytes > max_bytes:
        shutil.rmtree(target, ignore_errors=True)
        raise FetchError(
            f"repository exceeds the {max_bytes // (1024 * 1024)} MB cap "
            f"({git_bytes / (1024 * 1024):.1f} MB fetched)"
        )

    result = _git(["-C", str(target), "checkout", "-q", "--detach", "FETCH_HEAD"], timeout)
    if result.returncode != 0:
        raise FetchError(f"git checkout failed: {result.stderr.strip()}")

    result = _git(["-C", str(target), "rev-parse", "HEAD"], timeout)
    head = result.stdout.strip()
    if result.returncode != 0 or head != sha:
        raise FetchError(f"checked-out HEAD {head!r} does not match pinned commit {sha}")

    _reject_escaping_symlinks(target)
    used = directory_size(target)
    if used > max_bytes:
        raise FetchError(
            f"checkout exceeds the {max_bytes // (1024 * 1024)} MB cap "
            f"({used / (1024 * 1024):.1f} MB)"
        )
    return Checkout(target, sha, used)
