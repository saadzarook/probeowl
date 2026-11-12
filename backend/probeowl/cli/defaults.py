# Author: @SaadZarook
"""Where owl finds its store and config folder when not told, shared by its commands."""

import os
from pathlib import Path

import typer
from sqlalchemy.engine import make_url

from probeowl.errors import ConfigError
from probeowl.store import migrate
from probeowl.store.engine import create_store_engine

STORE_URL_VARIABLE = "PROBEOWL_STORE_URL"
CONFIG_VARIABLE = "PROBEOWL_CONFIG"
DEFAULT_CONFIG_FOLDER = "probeowl"


def default_store_path() -> Path:
    """The SQLite file a store is kept in when no URL is given: ~/.probeowl/store.db."""
    return Path.home() / ".probeowl" / "store.db"


def default_store_url() -> str:
    """The store URL from PROBEOWL_STORE_URL, or a SQLite file in ~/.probeowl."""
    return os.environ.get(STORE_URL_VARIABLE) or f"sqlite:///{default_store_path()}"


def config_path(config: Path | None) -> Path:
    """The config folder: the one given, else PROBEOWL_CONFIG, else this folder if it holds
    applications, else ./probeowl.

    So a first run from a home folder writes its applications into ./probeowl, not beside
    everything else there. One level down only: never a walk of a whole home folder.
    """
    if config is not None:
        return config
    named = os.environ.get(CONFIG_VARIABLE)
    if named:
        return Path(named)
    here = Path(".")
    if any(here.glob("*/application.yaml")):
        return here
    return Path(DEFAULT_CONFIG_FOLDER)


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
