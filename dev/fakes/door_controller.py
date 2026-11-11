# Author: @SaadZarook
# /// script
# requires-python = ">=3.12"
# dependencies = ["fastapi>=0.115,<1", "uvicorn>=0.30,<1"]
# ///
"""Fake door controller API. Read-only; data matches dev/seed/PLANTED.md."""
from fastapi import FastAPI, HTTPException

app = FastAPI(title="fake-door-controller")

# The controller still reports C177031 as active although the database revoked it (planted mismatch).
_CARDS: dict[str, str] = {
    "C204518": "active",
    "C198220": "active",
    "C177031": "active",
}


@app.get("/cards/{card_id}")
def get_card(card_id: str) -> dict[str, str]:
    """Return the controller's view of one card."""
    if card_id not in _CARDS:
        raise HTTPException(status_code=404, detail=f"Card {card_id} not found")
    return {"card_id": card_id, "status": _CARDS[card_id]}


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run(app, host=os.environ.get("FAKE_HOST", "127.0.0.1"), port=int(os.environ.get("FAKE_PORT", "8082")))
