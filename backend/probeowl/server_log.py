# Author: @SaadZarook
"""The owl serve log: the terminal, and a file only the person running it can read."""

import io
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

MAX_BYTES = 5_000_000
BACKUPS = 3
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class _PrivateFile(RotatingFileHandler):
    """A rotating log file created for its owner only, and made so if it already existed."""

    def _open(self) -> io.TextIOWrapper:
        # os.open, not open: the file must never exist with the default, wider mode.
        descriptor = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        # On Windows this cannot narrow access: the file relies on the profile folder's own ACL.
        os.chmod(self.baseFilename, 0o600)
        return os.fdopen(descriptor, "a", encoding="utf-8")


def default_log_file() -> Path:
    """Where owl serve logs unless told otherwise."""
    return Path.home() / ".probeowl" / "owl-serve.log"


def configure_server_log(path: Path) -> None:
    """Send ProbeOwl's log to the terminal and to path, replacing where it went before."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    logger = logging.getLogger("probeowl")
    for old in list(logger.handlers):
        logger.removeHandler(old)
        old.close()
    logger.setLevel(logging.INFO)
    handlers: list[logging.Handler] = [
        _PrivateFile(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ]
    for handler in handlers:
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)
