# Author: @SaadZarook
"""Where owl finds its store and config folder when not told, shared by its commands."""

import os
from pathlib import Path

STORE_URL_VARIABLE = "PROBEOWL_STORE_URL"
CONFIG_VARIABLE = "PROBEOWL_CONFIG"


def default_store_url() -> str:
    """The store URL from PROBEOWL_STORE_URL, or a SQLite file in ~/.probeowl."""
    return (
        os.environ.get(STORE_URL_VARIABLE) or f"sqlite:///{Path.home() / '.probeowl' / 'store.db'}"
    )


def config_path(config: Path | None) -> Path:
    """The config folder: the one given, else PROBEOWL_CONFIG, else this folder."""
    return config or Path(os.environ.get(CONFIG_VARIABLE) or ".")
