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
# At INFO these log each request's whole URL, and a REST lookup's key is in it.
_LOGS_URLS = ("httpx", "httpcore")
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class _PrivateFile(RotatingFileHandler):
    """A rotating log file created for its owner only, and made so if it already existed."""

    def _open(self) -> io.TextIOWrapper:
        # os.open, not open: the file must never exist with the default, wider mode.
        descriptor = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        # On Windows this cannot narrow access: the file relies on the profile folder's own ACL.
        os.chmod(self.baseFilename, 0o600)
        return os.fdopen(descriptor, "a", encoding="utf-8")


class _NoQueryString(logging.Filter):
    """Cuts the query string from uvicorn's access log: a search holds what the person pasted."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                arg.partition("?")[0] if isinstance(arg, str) and arg.startswith("/") else arg
                for arg in record.args
            )
        return True


def keep_urls_out_of_logs() -> None:
    """No log of owl serve's holds a request's URL: a REST lookup's, or one with a query string."""
    for name in _LOGS_URLS:
        logging.getLogger(name).setLevel(logging.WARNING)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(each, _NoQueryString) for each in access.filters):
        access.addFilter(_NoQueryString())


def default_log_file() -> Path:
    """Where owl serve logs unless told otherwise."""
    return Path.home() / ".probeowl" / "owl-serve.log"


def configure_server_log(path: Path) -> None:
    """Send ProbeOwl's log to the terminal and to path, replacing where it went before."""
    keep_urls_out_of_logs()
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
