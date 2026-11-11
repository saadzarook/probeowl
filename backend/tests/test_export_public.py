# Author: @SaadZarook
"""scripts/export_public.py, on a throwaway repository: one commit, no private email, no push."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "export_public", Path(__file__).resolve().parents[2] / "scripts" / "export_public.py"
)
assert _SPEC is not None and _SPEC.loader is not None
export_public = importlib.util.module_from_spec(_SPEC)
sys.modules["export_public"] = export_public
_SPEC.loader.exec_module(export_public)

NOREPLY = "12345678+probeowl@users.noreply.github.com"
PRIVATE = "someone.private@mail.example"


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with two commits by a private address, and an ignored file."""
    root = tmp_path / "private"
    root.mkdir()
    git("init", "-q", "-b", "main", cwd=root)
    for key, value in (("user.name", "Private Person"), ("user.email", PRIVATE)):
        git("config", key, value, cwd=root)
    (root / "README.md").write_text("ProbeOwl\n", encoding="utf-8")
    (root / ".gitignore").write_text("secret.env\n", encoding="utf-8")
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", "first", cwd=root)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", "second", cwd=root)
    (root / "secret.env").write_text("TOKEN=not-tracked\n", encoding="utf-8")
    return root


def test_without_run_it_only_says_what_it_would_do(repo: Path, tmp_path: Path) -> None:
    to = tmp_path / "public"
    lines = export_public.export(to, "main", "ProbeOwl", NOREPLY, "Initial commit", False, repo)
    assert lines[0] == "dry run, nothing done:"
    assert "export 3 tracked files at main" in lines[1]
    assert not to.exists()


def test_the_export_is_one_commit_by_the_noreply_address_with_no_remote(
    repo: Path, tmp_path: Path
) -> None:
    to = tmp_path / "public"
    before = git("rev-parse", "HEAD", cwd=repo)
    export_public.export(to, "main", "ProbeOwl", NOREPLY, "Initial commit", True, repo)
    log = git("log", "--format=%an <%ae>|%cn <%ce>|%s", cwd=to).splitlines()
    assert log == [f"ProbeOwl <{NOREPLY}>|ProbeOwl <{NOREPLY}>|Initial commit"]
    assert git("remote", cwd=to) == ""
    # The tracked files at the ref, and nothing untracked or ignored.
    assert sorted(git("ls-files", cwd=to).split()) == [".gitignore", "README.md", "src/app.py"]
    assert not (to / "secret.env").exists()
    # The private repository is left exactly as it was.
    assert git("rev-parse", "HEAD", cwd=repo) == before
    assert git("status", "--porcelain", "--untracked-files=no", cwd=repo) == ""


def test_an_excluded_folder_and_everything_under_it_is_left_out(repo: Path, tmp_path: Path) -> None:
    to = tmp_path / "public"
    lines = export_public.export(
        to, "main", "ProbeOwl", NOREPLY, "Initial commit", True, repo, excluded=["src/"]
    )
    assert "export 2 tracked files at main" in lines[0] and "leaving out 1" in lines[0]
    assert sorted(git("ls-files", cwd=to).split()) == [".gitignore", "README.md"]


def test_an_excluded_file_holding_a_private_email_no_longer_blocks_the_export(
    repo: Path, tmp_path: Path
) -> None:
    (repo / "CONTRIBUTORS").write_text(f"Private Person <{PRIVATE}>\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "contributors", cwd=repo)
    lines = export_public.export(
        tmp_path / "public", "main", "ProbeOwl", NOREPLY, "Initial commit", False, repo,
        excluded=["CONTRIBUTORS"],
    )  # fmt: skip
    assert lines[0] == "dry run, nothing done:"


@pytest.mark.parametrize("path", ["kickof", "src/app", "/"])
def test_an_exclude_that_matches_no_tracked_file_is_refused(
    repo: Path, tmp_path: Path, path: str
) -> None:
    with pytest.raises(export_public.ExportError, match="matches no tracked file"):
        export_public.export(
            tmp_path / "public", "main", "ProbeOwl", NOREPLY, "Initial commit", False, repo,
            excluded=[path],
        )  # fmt: skip


def test_a_file_that_holds_an_email_from_the_history_is_refused(repo: Path, tmp_path: Path) -> None:
    (repo / "CONTRIBUTORS").write_text(f"Private Person <{PRIVATE}>\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "contributors", cwd=repo)
    with pytest.raises(export_public.ExportError, match=f"CONTRIBUTORS: {PRIVATE}"):
        export_public.export(tmp_path / "public", "main", "P", NOREPLY, "m", False, repo)


@pytest.mark.parametrize(
    "email", ["someone@gmail.com", "probeowl@users.noreply.github.com.evil", "noreply@github.com"]
)
def test_only_a_github_noreply_address_may_author_it(
    repo: Path, tmp_path: Path, email: str
) -> None:
    with pytest.raises(export_public.ExportError, match="not a GitHub noreply address"):
        export_public.export(tmp_path / "public", "main", "P", email, "m", False, repo)


def test_a_folder_that_is_not_empty_or_inside_the_repository_is_refused(
    repo: Path, tmp_path: Path
) -> None:
    full = tmp_path / "full"
    full.mkdir()
    (full / "x").write_text("x", encoding="utf-8")
    with pytest.raises(export_public.ExportError, match="is not empty"):
        export_public.export(full, "main", "P", NOREPLY, "m", False, repo)
    with pytest.raises(export_public.ExportError, match="inside this repository"):
        export_public.export(repo / "public", "main", "P", NOREPLY, "m", False, repo)


def test_the_real_repositorys_tree_holds_no_email_from_its_history() -> None:
    # What a run on this repository would check first, without exporting anything.
    files = export_public.tree_files("HEAD")
    assert export_public.private_emails_in(files, export_public.history_emails()) == []
