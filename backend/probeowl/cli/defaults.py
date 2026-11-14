# Author: @SaadZarook
"""Where owl finds its store and config folder when not told, shared by its commands."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

import typer
import yaml
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from probeowl.errors import ConfigError
from probeowl.store import migrate
from probeowl.store.engine import create_store_engine

STORE_URL_VARIABLE = "PROBEOWL_STORE_URL"
CONFIG_VARIABLE = "PROBEOWL_CONFIG"
DEFAULT_CONFIG_FOLDER = "probeowl"


def default_store_path() -> Path:
    """The SQLite file a store is kept in when no URL is given: ~/.probeowl/store.db."""
    return Path.home() / ".probeowl" / "store.db"


# A store URL given on the command line ends up in shell history and, for owl mcp, in an
# assistant's plain-text config: its password belongs in the environment instead.
INLINE_PASSWORD_REFUSED = (
    "the store URL contains a password; keep it in PROBEOWL_STORE_URL, not in the assistant's "
    "config"
)
INLINE_PASSWORD_WARNING = (
    "the store URL contains a password; keep it in PROBEOWL_STORE_URL, not on the command line"
)
_PASSWORD_PARAMETERS = {"password", "pwd", "passwd"}


def has_password(url: str) -> bool:
    """Whether a store URL carries a password, as user:password@ or as a query parameter."""
    try:
        parsed = make_url(url)
    except ArgumentError:
        return "@" in url  # unreadable, with credentials of some kind: treated as holding one
    if parsed.password:
        return True
    return any(key.lower() in _PASSWORD_PARAMETERS for key in parsed.query)


def warn_inline_password(url: str | None) -> None:
    """Warn, never echoing the URL, when --url holds a password. It still runs (v0.3: #72)."""
    if url is not None and has_password(url):
        typer.echo(f"warning: {INLINE_PASSWORD_WARNING}", err=True)


def default_store_url() -> str:
    """The store URL from PROBEOWL_STORE_URL, or a SQLite file in ~/.probeowl."""
    return os.environ.get(STORE_URL_VARIABLE) or f"sqlite:///{default_store_path()}"


@dataclass(frozen=True)
class ConfigChoice:
    """The config folder, and whether owl chose it because none was given."""

    folder: Path
    by_default: bool
    not_here: str | None = None  # why not ./probeowl in the folder owl was started in
    not_loaded: str | None = None  # why the folder owl was started in was not loaded itself


# Folders a config folder never goes in, whoever runs owl there, as an administrator may.
_POSIX_SYSTEM = ("/usr", "/etc", "/bin", "/sbin", "/lib", "/lib64", "/boot", "/dev", "/proc",
                 "/sys", "/System", "/Library", "/Applications")  # fmt: skip
# Far more than any application.yaml ProbeOwl writes: a larger file is someone else's.
_LARGEST_APPLICATION_FILE = 256 * 1024
_WINDOWS_SYSTEM = {
    "SystemRoot": r"C:\Windows",
    "ProgramFiles": r"C:\Program Files",
    "ProgramFiles(x86)": r"C:\Program Files (x86)",
    "ProgramData": r"C:\ProgramData",
}


def config_path(config: Path | None) -> Path:
    """The config folder: see choose_config_folder."""
    return choose_config_folder(config).folder


def choose_config_folder(
    config: Path | None,
    here: Path | None = None,
    home: Path | None = None,
    windows: bool = os.name == "nt",
    environ: Mapping[str, str] = os.environ,
) -> ConfigChoice:
    """The folder given, else PROBEOWL_CONFIG, else this folder if it holds applications, else
    ./probeowl, unless this folder is a system or program folder or can't be written to: then
    ~/probeowl.

    So a first run from a home folder writes into ./probeowl, not beside everything else there,
    and one started in C:\\Windows\\System32 writes into the home folder. A folder that was not
    named is loaded only for ProbeOwl's own applications, never for a project's own
    application.yaml; one that was named is loaded, and its errors are reported. One level down
    only: never a walk of a whole home folder.
    """
    if config is not None:
        return ConfigChoice(config, by_default=False)
    named = environ.get(CONFIG_VARIABLE)
    if named:
        return ConfigChoice(Path(named), by_default=False)
    # Path.cwd() is absolute already; a folder given is judged as it is, as on another system.
    here = here if here is not None else Path.cwd()
    found = sorted(here.glob("*/application.yaml"))
    if any(_is_probeowl_application(path) for path in found):
        return ConfigChoice(here, by_default=True)
    not_loaded = (
        "its application.yaml files are not ProbeOwl's (none has application: and "
        f"environments:), such as {found[0].relative_to(here).as_posix()}"
        if found
        else None
    )
    reason = unsuitable_for_config(here, windows, environ)
    if reason is None:
        return ConfigChoice(here / DEFAULT_CONFIG_FOLDER, True, not_loaded=not_loaded)
    return ConfigChoice((home or Path.home()) / DEFAULT_CONFIG_FOLDER, True, reason, not_loaded)


def _is_probeowl_application(path: Path) -> bool:
    """Whether a file is a ProbeOwl application.yaml: an application name and its environments."""
    try:
        if path.stat().st_size > _LARGEST_APPLICATION_FILE:
            return False
        document = next(yaml.safe_load_all(path.read_text(encoding="utf-8")), None)
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return False  # not readable as YAML: not ProbeOwl's to load uninvited
    return (
        isinstance(document, dict)
        and isinstance(document.get("application"), str)
        and isinstance(document.get("environments"), dict)
    )


def unsuitable_for_config(
    folder: Path, windows: bool, environ: Mapping[str, str] = os.environ
) -> str | None:
    """Why folder is no place for a new config folder, or None if it is fine."""
    if windows:
        path = PureWindowsPath(str(folder))
        if path == PureWindowsPath(path.anchor):
            return f"{path} is the root of a drive"
        for variable, default in _WINDOWS_SYSTEM.items():
            system = PureWindowsPath(environ.get(variable) or default)
            # Windows paths compare without case: C:\WINDOWS is C:\Windows.
            if str(path).casefold() == str(system).casefold() or any(
                str(parent).casefold() == str(system).casefold() for parent in path.parents
            ):
                return f"{path} is a system or program folder"
    else:
        path_posix = PurePosixPath(folder.as_posix())
        if str(path_posix) == "/":
            return "/ is the root of the file system"
        for system_folder in _POSIX_SYSTEM:
            root = PurePosixPath(system_folder)
            if path_posix == root or root in path_posix.parents:
                return f"{path_posix} is a system folder"
    if folder.exists() and not os.access(folder, os.W_OK):
        return f"{folder} can't be written to"
    return None


def ensure_default_store(url: str | None) -> bool:
    """Create the default SQLite store if no URL was given and it does not exist yet.

    Whether it was created now. A store named by --url or PROBEOWL_STORE_URL is never
    created here: one without ProbeOwl's tables is refused where it is used, with
    `owl store init`, as a server database may need a DBA.
    """
    if url or os.environ.get(STORE_URL_VARIABLE):
        return False
    if default_store_path().exists():
        return False
    create_store(f"sqlite:///{default_store_path()}")
    return True


def create_store(url: str) -> None:
    """Create ProbeOwl's tables in the store, or upgrade them to this release."""
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database:
        _prepare_sqlite_file(Path(parsed.database))
    engine = create_store_engine(url)
    try:
        migrate.upgrade(engine)
    finally:
        engine.dispose()
    # A SQLite store as its file, which a URL would show percent-encoded on Windows.
    shown = parsed.database if parsed.get_backend_name() == "sqlite" else None
    typer.echo(f"Store ready: {shown or parsed.render_as_string(hide_password=True)}")


def _prepare_sqlite_file(path: Path) -> None:
    # SQLite on a network share loses writes when several people use it; a team store
    # belongs in a server database.
    if str(path).startswith(("\\\\", "//")):
        raise ConfigError(
            f"{path} is on a network share; use a server database for a team store "
            f"(set {STORE_URL_VARIABLE})"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
