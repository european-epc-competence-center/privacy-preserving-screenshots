"""Set the package version from a vX.Y.Z tag and commit it back.

The release workflows run this on the tagged commit, whose tree still has the
previous version. `uv version` writes the tag into `pyproject.toml` and
`uv.lock` — those are the only two places the version is stored.
`eecc_redact.__version__` is read from that metadata.

With `--push`, the change is committed onto the default branch and the tag is
moved to that commit, so the tagged source matches the installer. The branch
push is a fast-forward. Only the tag ref is force-updated, and only when the
commits it gains change nothing but the two version files. A push made with
the workflow token does not start another workflow run, so this does not
rebuild the release.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import tomllib
from pathlib import Path

_TAG = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
_LOCK_VERSION = re.compile(r'\[\[package\]\]\r?\nname = "eecc-redact"\r?\nversion = "([^"]+)"')
_VERSION_FILES = frozenset({"pyproject.toml", "uv.lock"})
_ROOT = Path(__file__).resolve().parents[1]


class ReleaseError(Exception):
    """The tag cannot be published as this version."""


def version_from_tag(tag: str) -> str:
    if not _TAG.fullmatch(tag):
        raise ReleaseError(f"Tag {tag} is not a vX.Y.Z release")
    return tag[1:]


def project_version(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def lock_version(root: Path) -> str:
    match = _LOCK_VERSION.search((root / "uv.lock").read_text())
    if not match:
        raise ReleaseError("uv.lock has no eecc-redact version")
    return match.group(1)


def write_version(root: Path, version: str) -> None:
    """Write `version` into the project and the lock. Dependencies stay pinned."""
    if project_version(root) == version and lock_version(root) == version:
        print(f"Project version is already {version}")
        return
    result = subprocess.run(["uv", "version", version, "--no-sync"], cwd=root)
    if result.returncode != 0:
        raise ReleaseError(f"uv version {version} failed")


def publish(root: Path, tag: str, version: str, branch: str) -> None:
    """Commit `version` on top of `branch` and point `tag` at that commit."""
    _check_branch(branch)
    _fetch_branch(root, branch)
    remote_version = _version_at(root, f"origin/{branch}")
    head = _rev(root, "HEAD")
    tip = _rev(root, f"origin/{branch}")

    if remote_version == version:
        # The other release workflow, or a re-run, already published it.
        _adopt(root, tag, version, branch, head, tip)
        return

    if head != tip:
        raise ReleaseError(
            f"Tag {tag} is {head[:12]}, but {branch} is {tip[:12]}. "
            "The version commit can only be added on top of the branch tip."
        )

    write_version(root, version)
    _require(root, version)
    _git(root, "add", "--", "pyproject.toml", "uv.lock")
    staged = _git(root, "diff", "--cached", "--quiet", check=False)
    if staged.returncode == 0:
        raise ReleaseError(f"{version} was not written to pyproject.toml and uv.lock")
    if staged.returncode != 1:
        raise ReleaseError("git diff --cached failed")
    _git(
        root,
        "-c",
        "user.name=github-actions[bot]",
        "-c",
        "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        f"Set the package version to {version}.",
    )
    pushed = _git(root, "push", "origin", f"HEAD:refs/heads/{branch}", check=False)
    if pushed.returncode != 0:
        # Fast-forward failed: the other workflow may have published the same version.
        print(pushed.stderr, end="")
        _fetch_branch(root, branch)
        tip = _rev(root, f"origin/{branch}")
        if _version_at(root, f"origin/{branch}") != version:
            raise ReleaseError(f"Could not push the {version} commit to {branch}.")
        _adopt(root, tag, version, branch, head, tip)
        return
    _point_tag(root, tag, _rev(root, "HEAD"))


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        version = version_from_tag(args.tag)
        if args.push:
            publish(_ROOT, args.tag, version, args.branch)
        else:
            write_version(_ROOT, version)
            _require(_ROOT, version)
    except ReleaseError as exc:
        raise SystemExit(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=os.environ.get("RELEASE_TAG", ""))
    parser.add_argument("--branch", default=os.environ.get("RELEASE_BRANCH", ""))
    parser.add_argument(
        "--push",
        action="store_true",
        help="commit the version to the branch and move the tag onto it",
    )
    return parser


def _check_branch(branch: str) -> None:
    if not branch or branch.startswith("-") or ".." in branch:
        raise ReleaseError(f"Refusing to push to branch {branch!r}")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
        raise ReleaseError(f"Refusing to push to branch {branch!r}")


def _require(root: Path, version: str) -> None:
    project = project_version(root)
    locked = lock_version(root)
    if project != version or locked != version:
        raise ReleaseError(
            f"Expected {version}, but pyproject.toml is {project} and uv.lock is {locked}"
        )


def _adopt(root: Path, tag: str, version: str, branch: str, head: str, tip: str) -> None:
    """Point `tag` at `tip` when that commit only gained the version files."""
    if not _only_version_files(root, head, tip):
        raise ReleaseError(
            f"{branch} is already {version}, but it differs from this checkout by more than "
            "pyproject.toml and uv.lock. Refusing to move the tag."
        )
    write_version(root, version)
    _require(root, version)
    print(f"{branch} already has {version}; using {tip[:12]}")
    _point_tag(root, tag, tip)


def _fetch_branch(root: Path, branch: str) -> None:
    # Update the remote-tracking ref even when the checkout's refspec is the tag only.
    _git(root, "fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")


def _version_at(root: Path, rev: str) -> str:
    show = _git(root, "show", f"{rev}:pyproject.toml")
    return tomllib.loads(show.stdout)["project"]["version"]


def _only_version_files(root: Path, older: str, newer: str) -> bool:
    if older == newer:
        return True
    diff = _git(root, "diff", "--name-only", older, newer)
    changed = {line.strip() for line in diff.stdout.splitlines() if line.strip()}
    return changed <= _VERSION_FILES


def _point_tag(root: Path, tag: str, commit: str) -> None:
    if _remote_tag(root, tag) == commit:
        print(f"{tag} already points at {commit[:12]}")
        return
    _git(root, "tag", "--force", tag, commit)
    # The tag already exists on the commit that was pushed; moving it is the
    # only way for that name to name the commit that contains its version.
    _git(root, "push", "--force", "origin", f"refs/tags/{tag}")
    print(f"Moved {tag} to {commit[:12]}")


def _remote_tag(root: Path, tag: str) -> str | None:
    listed = _git(root, "ls-remote", "origin", f"refs/tags/{tag}")
    found: dict[str, str] = {}
    for line in listed.stdout.splitlines():
        sha, ref = line.split()
        found[ref] = sha
    # An annotated tag lists the tag object and, with a suffix, the commit.
    return found.get(f"refs/tags/{tag}^{{}}") or found.get(f"refs/tags/{tag}")


def _rev(root: Path, rev: str) -> str:
    return _git(root, "rev-parse", rev).stdout.strip()


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        env=os.environ | {"GIT_TERMINAL_PROMPT": "0"},
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ReleaseError(f"git {' '.join(args)} failed\n{detail}")
    return result


if __name__ == "__main__":
    main()
