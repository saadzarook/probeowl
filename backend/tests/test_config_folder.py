# Author: @SaadZarook
"""The rules for writing YAML from the web, one by one (docs/decisions.md)."""

import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from probeowl.config_folder import ABSENT, ConfigFolder
from probeowl.errors import ConflictError, RequestError

TEXT = "catalog: wallet\ntables: []\n"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    folder = tmp_path / "config"
    (folder / "wallet").mkdir(parents=True)
    (folder / "wallet" / "catalog.yaml").write_text(TEXT, encoding="utf-8", newline="\n")
    return folder


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    path = tmp_path / "outside.yaml"
    path.write_text("secret: untouched\n", encoding="utf-8")
    return path


def link(target: Path, at: Path) -> None:
    """A symbolic link, or skip where this system does not allow creating them."""
    try:
        at.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as err:  # Windows without Developer Mode or admin rights
        pytest.skip(f"cannot create symbolic links here: {err}")


# --- Inside the loaded folder only ------------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    [
        "../outside.yaml",
        "wallet/../../outside.yaml",
        "wallet/../../config/wallet/catalog.yaml",  # back in, but through ..
        "..",
        "",
    ],
)
def test_a_path_with_dot_dot_is_refused(root: Path, outside: Path, relative: str) -> None:
    folder = ConfigFolder(root)
    with pytest.raises(RequestError, match="leaves the folder owl serve loaded"):
        folder.preview(relative, "x: 1\n")
    with pytest.raises(RequestError, match="leaves the folder owl serve loaded"):
        folder.write(relative, "x: 1\n", ABSENT)
    assert outside.read_text(encoding="utf-8") == "secret: untouched\n"


@pytest.mark.parametrize("make", [str, lambda p: p.as_posix(), lambda p: "/" + p.name])
def test_an_absolute_path_is_refused(root: Path, outside: Path, make: object) -> None:
    relative = make(outside)  # type: ignore[operator]
    with pytest.raises(RequestError, match="absolute path"):
        ConfigFolder(root).write(relative, "x: 1\n", ABSENT)
    assert outside.read_text(encoding="utf-8") == "secret: untouched\n"


@pytest.mark.parametrize(
    ("relative", "message"),
    [
        ("C:\\Windows\\x.yaml", "absolute path"),
        ("C:/Windows/x.yaml", "absolute path"),
        ("\\\\server\\share\\x.yaml", "absolute path"),
        ("\\x.yaml", "absolute path"),
        ("wallet\\..\\..\\outside.yaml", "backslash"),
    ],
)
def test_windows_paths_are_refused_on_every_system(root: Path, relative: str, message: str) -> None:
    # On Linux these would be ordinary file names; they are refused there too.
    with pytest.raises(RequestError, match=message):
        ConfigFolder(root).resolve(relative)


def test_a_file_that_is_a_link_to_outside_is_refused(root: Path, outside: Path) -> None:
    link(outside, root / "wallet" / "linked.yaml")
    folder = ConfigFolder(root)
    # The base of the file the link points to: only the link itself may stop the write.
    base = folder.preview("wallet/catalog.yaml", TEXT).base
    with pytest.raises(RequestError, match=r"symbolic link: linked.yaml"):
        folder.write("wallet/linked.yaml", "x: 1\n", base)
    assert outside.read_text(encoding="utf-8") == "secret: untouched\n"


def test_a_file_that_is_a_link_inside_the_folder_is_refused_too(root: Path) -> None:
    # Its target is inside today, but the rule is about the path: links are not written through.
    link(root / "wallet" / "catalog.yaml", root / "wallet" / "alias.yaml")
    with pytest.raises(RequestError, match=r"symbolic link: alias.yaml"):
        ConfigFolder(root).preview("wallet/alias.yaml", "x: 1\n")


def test_a_folder_on_the_way_that_is_a_link_is_refused(root: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link(elsewhere, root / "shared")
    with pytest.raises(RequestError, match="symbolic link: shared"):
        ConfigFolder(root).write("shared/catalog.yaml", "x: 1\n", ABSENT)
    assert list(elsewhere.iterdir()) == []


def test_a_path_found_while_loading_is_not_resolved_through_a_link(root: Path) -> None:
    folder = ConfigFolder(root)
    link(root / "wallet" / "catalog.yaml", root / "wallet" / "alias.yaml")
    # relative() keeps the link in the path, so resolve() can refuse it.
    relative = folder.relative(root / "wallet" / "alias.yaml")
    assert relative == "wallet/alias.yaml"
    with pytest.raises(RequestError, match="symbolic link"):
        folder.resolve(relative)


def test_a_path_found_outside_the_folder_is_refused(root: Path, outside: Path) -> None:
    with pytest.raises(RequestError, match="outside the folder owl serve loaded"):
        ConfigFolder(root).relative(outside)


def test_a_folder_is_not_a_file(root: Path) -> None:
    with pytest.raises(RequestError, match="is not a file"):
        ConfigFolder(root).preview("wallet", "x: 1\n")


# --- The change is shown before it is written ----------------------------------------------


def test_a_preview_shows_the_diff_and_writes_nothing(root: Path) -> None:
    change = ConfigFolder(root).preview("wallet/catalog.yaml", "catalog: wallet\ntables: [a]\n")
    assert not change.written
    assert change.path == "wallet/catalog.yaml"
    assert "-tables: []\n+tables: [a]\n" in change.diff
    assert change.diff.startswith("--- a/wallet/catalog.yaml\n+++ b/wallet/catalog.yaml\n")
    assert (root / "wallet" / "catalog.yaml").read_text(encoding="utf-8") == TEXT


def test_a_new_file_previews_as_absent(root: Path) -> None:
    change = ConfigFolder(root).preview("cards/catalog.yaml", "catalog: cards\n")
    assert change.base == ABSENT
    assert change.diff.startswith("--- /dev/null\n+++ b/cards/catalog.yaml\n")
    assert not (root / "cards").exists()


def test_a_write_needs_the_base_its_preview_gave(root: Path) -> None:
    folder = ConfigFolder(root)
    new = "catalog: wallet\ntables: [a]\n"
    preview = folder.preview("wallet/catalog.yaml", new)
    (root / "wallet" / "catalog.yaml").write_text("catalog: wallet\n# edited\n", encoding="utf-8")
    with pytest.raises(ConflictError, match="changed since its preview"):
        folder.write("wallet/catalog.yaml", new, preview.base)
    assert (root / "wallet" / "catalog.yaml").read_text(
        encoding="utf-8"
    ) == "catalog: wallet\n# edited\n"


def test_the_write_is_exactly_what_the_preview_showed(root: Path) -> None:
    folder = ConfigFolder(root)
    new = "catalog: wallet\ntables: [a]\n"
    preview = folder.preview("wallet/catalog.yaml", new)
    change = folder.write("wallet/catalog.yaml", new, preview.base)
    assert change.written and change.diff == preview.diff and change.after == preview.after
    assert (root / "wallet" / "catalog.yaml").read_bytes() == new.encode()


def test_a_new_file_is_written_with_the_absent_base(root: Path) -> None:
    ConfigFolder(root).write("cards/catalog.yaml", "catalog: cards\n", ABSENT)
    assert (root / "cards" / "catalog.yaml").read_text(encoding="utf-8") == "catalog: cards\n"


# --- Atomic -----------------------------------------------------------------------------------


def test_a_failed_write_leaves_the_file_and_no_temporary_file(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = ConfigFolder(root)
    preview = folder.preview("wallet/catalog.yaml", "catalog: wallet\ntables: [a]\n")

    def fail(source: str, target: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        folder.write("wallet/catalog.yaml", "catalog: wallet\ntables: [a]\n", preview.base)
    assert (root / "wallet" / "catalog.yaml").read_text(encoding="utf-8") == TEXT
    assert sorted(p.name for p in (root / "wallet").iterdir()) == ["catalog.yaml"]


def test_the_new_content_is_renamed_into_place(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = ConfigFolder(root)
    renames: list[tuple[str, str]] = []
    real = os.replace

    def spy(source: str, target: str) -> None:
        renames.append((Path(source).parent.name, Path(target).name))
        real(source, target)

    monkeypatch.setattr(os, "replace", spy)
    folder.write(
        "wallet/catalog.yaml", "x: 1\n", folder.preview("wallet/catalog.yaml", "x: 1\n").base
    )
    # From a temporary file in the same folder, so the rename is atomic.
    assert renames == [("wallet", "catalog.yaml")]


# --- Never committed or pushed ---------------------------------------------------------------


def test_a_write_runs_no_program(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"a program was started: {args}")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(os, "system", refuse)
    folder = ConfigFolder(root)
    folder.write(
        "wallet/catalog.yaml", "x: 1\n", folder.preview("wallet/catalog.yaml", "x: 1\n").base
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_in_a_git_checkout_the_change_is_left_for_a_person_to_commit(root: Path) -> None:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
        ).stdout

    git("init", "-q")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "base")
    folder = ConfigFolder(root)
    folder.write(
        "wallet/catalog.yaml", "x: 1\n", folder.preview("wallet/catalog.yaml", "x: 1\n").base
    )
    assert git("rev-list", "--count", "HEAD").strip() == "1"  # no new commit
    assert git("status", "--porcelain").strip() == "M wallet/catalog.yaml"  # modified, not staged


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows kind of link")
def test_a_folder_on_the_way_that_is_a_windows_junction_is_refused(
    root: Path, tmp_path: Path
) -> None:
    import _winapi

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    # Junctions need no special rights, unlike symbolic links: the likelier way out on Windows.
    # Typed only on Windows: Linux type checks do not know it.
    _winapi.CreateJunction(str(elsewhere), str(root / "shared"))  # type: ignore[attr-defined,unused-ignore]
    with pytest.raises(RequestError, match="symbolic link: shared"):
        ConfigFolder(root).write("shared/catalog.yaml", "x: 1\n", ABSENT)
    assert list(elsewhere.iterdir()) == []


# --- What is written is what was shown ---------------------------------------------------------


def test_a_crlf_file_keeps_its_line_endings_and_the_diff_shows_only_the_change(root: Path) -> None:
    path = root / "wallet" / "catalog.yaml"
    path.write_bytes(b"catalog: wallet\r\ntables: []\r\n# kept\r\n")
    folder = ConfigFolder(root)
    new = "catalog: wallet\ntables: [a]\n# kept\n"
    preview = folder.preview("wallet/catalog.yaml", new)
    assert "-tables: []\n+tables: [a]\n" in preview.diff
    assert "catalog: wallet" not in [
        line[1:] for line in preview.diff.splitlines() if line[:1] in ("+", "-")
    ]
    change = folder.write("wallet/catalog.yaml", new, preview.base)
    assert path.read_bytes() == b"catalog: wallet\r\ntables: [a]\r\n# kept\r\n"
    assert change.after == preview.after


def test_changing_only_line_endings_is_seen_as_a_change_by_the_base(root: Path) -> None:
    folder = ConfigFolder(root)
    preview = folder.preview("wallet/catalog.yaml", "x: 1\n")
    (root / "wallet" / "catalog.yaml").write_bytes(TEXT.replace("\n", "\r\n").encode())
    with pytest.raises(ConflictError, match="changed since its preview"):
        folder.write("wallet/catalog.yaml", "x: 1\n", preview.base)


def test_two_writes_from_one_preview_cannot_both_win(root: Path) -> None:
    folder = ConfigFolder(root)
    base = folder.preview("wallet/catalog.yaml", "x: 0\n").base
    results: list[str] = []
    start = threading.Barrier(8)

    def write(n: int) -> None:
        start.wait()
        try:
            folder.write("wallet/catalog.yaml", f"x: {n}\n", base)
            results.append("written")
        except ConflictError:
            results.append("conflict")

    threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["conflict"] * 7 + ["written"]
