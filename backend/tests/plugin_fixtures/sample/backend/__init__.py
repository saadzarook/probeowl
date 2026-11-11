# Author: @SaadZarook
"""The sample plugin's routes: reads what the person supplied, and audits that it did."""

from fastapi import APIRouter
from pydantic import BaseModel

from probeowl.plugins.api import PluginContext


class Pasted(BaseModel):
    """Text a person pasted."""

    name: str
    text: str


def register(context: PluginContext) -> APIRouter:
    """The plugin's routes."""
    router = APIRouter()

    @router.post("/measure")
    def measure(body: Pasted) -> dict[str, object]:
        pasted = context.local_input(body.name, body.text, max_bytes=16)
        context.audit("measured", {"file": pasted.name, "sha256": pasted.sha256})
        return {"size": pasted.size}

    return router
