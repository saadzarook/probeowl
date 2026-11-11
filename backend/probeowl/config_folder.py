# Author: @SaadZarook
"""Write YAML into the folder owl serve loaded, safely.

The rules (docs/decisions.md, "Writing YAML from the web"):
- only inside the loaded folder: a path is relative, has no `..`, and no part of it, the
  file included, is a symbolic link, so nothing can point outside;
- the change is shown before it is written: a preview gives the diff and a hash of the file
  as it was, and a write needs that hash, so what is written is what was shown;
- written atomically: a temporary file in the same folder, then a rename, keeping the
  file's line endings so that only the lines the diff shows change;
- never committed or pushed: this module does not run git or any other program.
Auditing each write is the caller's, which knows the actor and what changed.
"""

import difflib
import hashlib
import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

from probeowl.errors import ConfigError, ConflictError, RequestError

ABSENT = "absent"  # the hash of a file that does not exist yet


@dataclass(frozen=True)
class Change:
    """A change to one file: where, the unified diff, and the file's hash before and after."""

    path: str  # relative to the loaded folder, with forward slashes
    diff: str
    base: str  # sha256 of the file as it was, or "absent"
    after: str  # sha256 of the new content
    written: bool


class ConfigFolder:
    """The folder owl serve loaded its YAML from."""

    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        # The folder as it was named, which may itself sit below a link: paths found while
        # loading are under this one, not always under the resolved root.
        self._named = Path(os.path.abspath(root))
        # Routes run on many threads: comparing the hash and replacing the file are one step,
        # and callers hold this too while they update what they loaded.
        self.lock = threading.RLock()

    def read(self, relative: str) -> str | None:
        """The file's text exactly as it is, or None if it does not exist."""
        return _read(self.resolve(relative))

    def preview(self, relative: str, content: str) -> Change:
        """What writing content to the file would change. Writes nothing."""
        path = self.resolve(relative)
        before = _read(path)
        content = _with_endings_of(before, content)
        return Change(
            path=_shown(relative),
            diff=_diff(relative, before, content),
            base=_hash(before),
            after=_hash(content),
            written=False,
        )

    def write(self, relative: str, content: str, base: str) -> Change:
        """Write content if the file is still as it was when previewed (base)."""
        with self.lock:
            path = self.resolve(relative)
            before = _read(path)
            if _hash(before) != base:
                raise ConflictError(
                    f"{_shown(relative)} changed since its preview; preview the change again"
                )
            content = _with_endings_of(before, content)
            _replace(path, content)
        return Change(
            path=_shown(relative),
            diff=_diff(relative, before, content),
            base=base,
            after=_hash(content),
            written=True,
        )

    def write_all(
        self, files: list[tuple[str, str, str]], verify: Callable[[], None] | None = None
    ) -> list[Change]:
        """Write every (relative, content, base), or none of them.

        Bases are compared before anything is written. If a write fails, or verify raises once
        all are written, the files already written are put back as they were.
        """
        with self.lock:
            paths = [self.resolve(relative) for relative, _, _ in files]
            befores = [_read(path) for path in paths]
            for (relative, _, base), before in zip(files, befores, strict=True):
                if _hash(before) != base:
                    raise ConflictError(
                        f"{_shown(relative)} changed since its preview; preview the change again"
                    )
            written: list[tuple[Path, str, str | None]] = []
            changes: list[Change] = []
            try:
                for (relative, content, _), path, before in zip(files, paths, befores, strict=True):
                    content = _with_endings_of(before, content)
                    _replace(path, content)  # atomic: one that fails has changed nothing
                    written.append((path, relative, before))
                    changes.append(
                        Change(
                            path=_shown(relative),
                            diff=_diff(relative, before, content),
                            base=_hash(before),
                            after=_hash(content),
                            written=True,
                        )
                    )
                if verify is not None:
                    verify()
            except Exception:
                _put_back(written)
                raise
        return changes

    def relative(self, path: Path) -> str:
        """A path under the folder, as the relative form the other methods take.

        Links are not followed here: resolve() then refuses a path that goes through one.
        """
        absolute = Path(os.path.abspath(path))
        for base in (self._named, self.root):
            if absolute.is_relative_to(base):
                return absolute.relative_to(base).as_posix()
        raise RequestError(f"{path} is outside the folder owl serve loaded")

    def resolve(self, relative: str) -> Path:
        """The file a relative path names, inside the folder; RequestError otherwise."""
        # Judged the same on every system: a Windows drive or backslash is refused on Linux too,
        # where it would otherwise be a file name.
        if PureWindowsPath(relative).drive or relative.startswith(("/", "\\")):
            raise RequestError(f"{relative} is an absolute path; only paths inside the folder")
        if "\\" in relative:
            raise RequestError(f"{relative} has a backslash; paths use forward slashes")
        given = PurePosixPath(relative)
        if any(part == ".." for part in given.parts) or not given.parts:
            raise RequestError(f"{relative} leaves the folder owl serve loaded")
        path = self.root
        for part in given.parts:
            path = path / part
            # A link anywhere on the way could lead outside, now or after it is changed.
            # Windows junctions are not symbolic links to Python, but lead elsewhere too.
            if path.is_symlink() or path.is_junction():
                raise RequestError(f"{_shown(relative)} goes through a symbolic link: {part}")
        if not path.resolve().is_relative_to(self.root):
            raise RequestError(f"{_shown(relative)} is outside the folder owl serve loaded")
        if path.exists() and not path.is_file():
            raise RequestError(f"{_shown(relative)} is not a file")
        return path


def _read(path: Path) -> str | None:
    # As bytes: reading as text would hide the file's line endings from the hash.
    return path.read_bytes().decode("utf-8") if path.exists() else None


def _with_endings_of(before: str | None, content: str) -> str:
    # A CRLF checkout stays CRLF: otherwise every line would change, unseen in the diff.
    lines = content.replace("\r\n", "\n")
    return lines.replace("\n", "\r\n") if before and "\r\n" in before else lines


def _hash(content: str | None) -> str:
    if content is None:
        return ABSENT
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _diff(relative: str, before: str | None, after: str) -> str:
    name = _shown(relative)
    # Line endings are kept, not changed, so the diff is about the lines themselves.
    lines = difflib.unified_diff(
        (before or "").replace("\r\n", "\n").splitlines(keepends=True),
        after.replace("\r\n", "\n").splitlines(keepends=True),
        fromfile="/dev/null" if before is None else f"a/{name}",
        tofile=f"b/{name}",
    )
    return "".join(lines)


def _replace(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        # Atomic on one file system: readers see the old file or the new one, never half.
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _put_back(written: list[tuple[Path, str, str | None]]) -> None:
    for path, relative, before in reversed(written):
        try:
            if before is None:
                path.unlink(missing_ok=True)
            else:
                _replace(path, before)
        except OSError as err:
            # Not swallowed: the person has to know the two files no longer match.
            raise ConfigError(
                f"the save failed and {_shown(relative)} could not be put back; "
                "check it before saving again"
            ) from err


def _shown(relative: str) -> str:
    return PurePath(relative).as_posix()
