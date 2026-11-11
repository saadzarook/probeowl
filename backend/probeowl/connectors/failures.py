# Author: @SaadZarook
"""What a SQL connector's failure may say: a category with a fixed message.

A driver's own text can name the server, the login and values from the statement, so it never
reaches the API or the store. The local server log gets it with quoted values masked and never
a parameter's value, unless owl serve was started with --debug-driver-errors.
"""

import re
from collections.abc import Iterable
from enum import StrEnum

MASKED = "[masked]"
# To the end of the text when a quote is never closed: masking too much beats too little.
_QUOTED = re.compile(r"""'[^']*(?:'|$)|"[^"]*(?:"|$)|`[^`]*(?:`|$)""")
_raw_driver_errors = False


class Failure(StrEnum):
    """Why a SQL connector failed."""

    AUTHENTICATION = "authentication failed"
    CONNECTION = "connection refused"
    TLS_NOT_TRUSTED = "TLS certificate not trusted"
    TLS_NOT_OFFERED = "TLS not offered"
    TIMED_OUT = "timed out"
    LOCK_NOT_GRANTED = "lock not granted"
    REFUSED = "refused by the database"
    UNKNOWN = "failed"


_MESSAGES = {
    Failure.AUTHENTICATION: "authentication failed; check the binding's login and secret",
    Failure.CONNECTION: (
        "the connection was refused or could not be made; "
        "check the database is up and the binding is right"
    ),
    Failure.TLS_NOT_TRUSTED: (
        "the server's TLS certificate could not be verified; install a certificate signed by "
        "a trusted authority, or see trust_server_certificate"
    ),
    Failure.TLS_NOT_OFFERED: (
        "the connection could not be made over TLS; check the server offers it, or see encrypt"
    ),
    Failure.LOCK_NOT_GRANTED: "a lock was not granted in time: another session holds it",
    Failure.REFUSED: "refused by the database; the owl serve log says why",
    Failure.UNKNOWN: "the connector failed; the owl serve log says why",
}


def failure_message(failure: Failure, statement_timeout_s: int) -> str:
    """The fixed message for a failure, the one thing about it a response may carry."""
    if failure is Failure.TIMED_OUT:
        return f"the statement timed out after {statement_timeout_s} s"
    return _MESSAGES[failure]


def show_raw_driver_errors(on: bool) -> None:
    """Whether the log gets drivers' raw text: owl serve --debug-driver-errors."""
    global _raw_driver_errors
    _raw_driver_errors = on


def driver_log_text(text: str, raw: str, password: str, parameters: Iterable[object] = ()) -> str:
    """What the log may hold of a driver's error. A password never, even with the debug flag."""
    if _raw_driver_errors:
        return raw.replace(password, "[redacted]") if password else raw
    if password:
        text = text.replace(password, "[redacted]")
    text = _QUOTED.sub(MASKED, text)
    for value in parameters:
        if value is not None and str(value):
            text = re.sub(rf"(?<![\w-]){re.escape(str(value))}(?![\w-])", MASKED, text)
    return text
