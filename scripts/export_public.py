#!/usr/bin/env python3
# Author: @SaadZarook
"""Export the tree at one commit into a new repository with a single commit, for the public repo.

Usage: python scripts/export_public.py --to <empty folder> --name <name> --email <noreply address>
                                       [--ref main] [--message "Initial commit"]
                                       [--exclude <path>]... [--run]

This private repository stays as it is, the archive: nothing here is rewritten, and the new one
gets no remote and is never pushed. Without --run it only says what it would do. It exports the
tracked files at --ref (git archive, so nothing untracked or ignored comes along), and refuses if
any of them holds an email address from this repository's history that is not a GitHub noreply
address, or if --email is not one. The one commit is authored and committed by --name and --email.
Each --exclude leaves out a file, or a folder and everything under it, such as --exclude kickoff;
one that matches nothing is refused, so a typo cannot quietly publish a file meant to stay private.
"""

import argparse
import io
import os
import re
import subprocess
import sys
import tarfile
from collections.abc import Iterable
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# GitHub's own noreply forms: 12345678+user@users.noreply.github.com, user@users.noreply.github.com.
NOREPLY = re.compile(r"^(\d+\+)?[A-Za-z0-9-]+@users\.noreply\.github\.com$")


class ExportError(Exception):
    """Why the export cannot go ahead, in words a person can act on."""


def git(*args: str, cwd: Path = REPO, env: dict[str, str] | None = None) -> str:
    """Run git and return what it printed; ExportError with its message if it fails."""
    done = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise ExportError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def history_emails(repo: Path = REPO) -> set[str]:
    """Every author and committer email in the repository's history, noreply ones left out."""
    emails = set(git("log", "--all", "--format=%ae%n%ce", cwd=repo).split())
    return {e for e in emails if not NOREPLY.match(e) and e != "noreply@github.com"}


def tree_files(ref: str, repo: Path = REPO) -> dict[str, bytes]:
    """The tracked files at ref, by path, as git archive exports them."""
    done = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "archive", "--format=tar", ref],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if done.returncode != 0:
        raise ExportError(
            f"git archive {ref} failed: {done.stderr.decode(errors='replace').strip()}"
        )
    files = {}
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
        for member in archive.getmembers():
            if member.isfile():
                extracted = archive.extractfile(member)
                assert extracted is not None  # a regular file always has content
                files[member.name] = extracted.read()
    return files


def without(files: dict[str, bytes], excluded: Iterable[str]) -> dict[str, bytes]:
    """The files minus each excluded path and all under it; ExportError if one matches none."""
    kept = dict(files)
    for path in excluded:
        prefix = path.strip("/")
        gone = [f for f in kept if f == prefix or f.startswith(prefix + "/")]
        if not prefix or not gone:
            raise ExportError(f"--exclude {path} matches no tracked file")
        for f in gone:
            del kept[f]
    return kept


def private_emails_in(files: dict[str, bytes], emails: Iterable[str]) -> list[str]:
    """Each file that holds one of the emails, as 'path: email'."""
    found: list[str] = []
    for path, content in sorted(files.items()):
        text = content.decode("utf-8", errors="ignore").lower()
        found.extend(f"{path}: {e}" for e in sorted(emails) if e.lower() in text)
    return found


def check(
    destination: Path, email: str, files: dict[str, bytes], emails: set[str], repo: Path = REPO
) -> None:
    """ExportError for anything that would make the export unsafe or touch this repository."""
    if not NOREPLY.match(email):
        raise ExportError(
            f"{email} is not a GitHub noreply address, "
            "such as 12345678+user@users.noreply.github.com"
        )
    target = destination.resolve()
    if target == repo.resolve() or repo.resolve() in target.parents:
        raise ExportError(f"{destination} is inside this repository; choose a folder outside it")
    if target.exists() and any(target.iterdir()):
        raise ExportError(f"{destination} is not empty")
    leaks = private_emails_in(files, emails)
    if leaks:
        raise ExportError("a file holds an email from the history: " + "; ".join(leaks))


def export(
    destination: Path,
    ref: str,
    name: str,
    email: str,
    message: str,
    run: bool,
    repo: Path = REPO,
    excluded: Iterable[str] = (),
) -> list[str]:
    """What the export does, step by step; it does them only when run is true."""
    every = tree_files(ref, repo)
    files = without(every, excluded)
    check(destination, email, files, history_emails(repo), repo)
    commit = git("rev-parse", "--short", ref, cwd=repo).strip()
    steps = [
        f"export {len(files)} tracked files at {ref} ({commit}) into {destination}"
        + (f", leaving out {len(every) - len(files)}" if len(files) < len(every) else ""),
        f"git init -b main in {destination}",
        f'one commit, "{message}", authored and committed by {name} <{email}>',
        "no remote added, nothing pushed; this repository is not changed",
    ]
    if not run:
        return ["dry run, nothing done:", *steps]
    destination.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    who = {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }
    env = {**os.environ, **who}
    git("init", "-q", "-b", "main", cwd=destination, env=env)
    git("-c", "core.autocrlf=false", "add", "-A", cwd=destination, env=env)
    git(
        "-c", f"user.name={name}", "-c", f"user.email={email}", "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", message, cwd=destination, env=env,
    )  # fmt: skip
    return [*steps, f"done: {git('rev-parse', '--short', 'HEAD', cwd=destination).strip()}"]


def main(argv: list[str] | None = None) -> int:
    """Parse the arguments, then export or say why not."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--to", required=True, type=Path, help="a new, empty folder")
    parser.add_argument("--name", required=True, help="the public commit's author")
    parser.add_argument("--email", required=True, help="a GitHub noreply address")
    parser.add_argument("--ref", default="main", help="the commit to export (default: main)")
    parser.add_argument("--message", default="Initial commit")
    parser.add_argument(
        "--exclude", action="append", default=[], help="a file or folder to leave out; repeatable"
    )
    parser.add_argument("--run", action="store_true", help="do it; without it, only say what")
    args = parser.parse_args(argv)
    try:
        steps = export(
            args.to, args.ref, args.name, args.email, args.message, args.run, excluded=args.exclude
        )
        for line in steps:
            print(line)
    except ExportError as err:
        print(f"refused: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
