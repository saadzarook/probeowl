# Author: @SaadZarook
"""A test client that calls owl serve as its own tools do: the local Host and the header."""

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from probeowl.api.app import DEFAULT_PORT
from probeowl.api.local_only import HEADER

LOCAL = f"http://127.0.0.1:{DEFAULT_PORT}"


def local_client(app: FastAPI, **kwargs: Any) -> TestClient:
    """A TestClient sending Host 127.0.0.1:7878 and X-ProbeOwl-Request: 1, like the CLI."""
    kwargs.setdefault("raise_server_exceptions", False)
    return TestClient(app, base_url=LOCAL, headers={HEADER: "1"}, **kwargs)
