# Author: @SaadZarook
# /// script
# requires-python = ">=3.12"
# dependencies = ["fastapi>=0.115,<1", "uvicorn>=0.30,<1"]
# ///
"""Fake payment provider API. Read-only; data matches dev/seed/PLANTED.md."""
from fastapi import FastAPI, HTTPException

app = FastAPI(title="fake-payments")

# payment id -> (top-up id, status, amount, currency), same as dbo.topups in the wallet seed
_PAYMENTS: dict[str, dict[str, str]] = {
    "pay_7hq2m9": {"reference": "tp_91f2c7", "status": "captured", "amount": "500.00", "currency": "USD"},
    "pay_4kd8x1": {"reference": "tp_91f2b9", "status": "captured", "amount": "200.00", "currency": "USD"},
    "pay_8ab1c4": {"reference": "tp_92a1b4", "status": "pending", "amount": "50.00", "currency": "USD"},
    "pay_8ab1f0": {"reference": "tp_92a1f0", "status": "pending", "amount": "75.00", "currency": "USD"},
    "pay_8ab2c3": {"reference": "tp_92a2c3", "status": "pending", "amount": "120.00", "currency": "USD"},
    "pay_3nw6t2": {"reference": "tp_93b001", "status": "captured", "amount": "90.00", "currency": "USD"},
}


def _payment(payment_id: str) -> dict[str, str]:
    return {"id": payment_id, **_PAYMENTS[payment_id]}


@app.get("/payments")
def find_payment_by_reference(reference: str) -> dict[str, str]:
    """Return the payment for a top-up ID."""
    for payment_id, payment in _PAYMENTS.items():
        if payment["reference"] == reference:
            return _payment(payment_id)
    raise HTTPException(status_code=404, detail=f"No payment for reference {reference}")


@app.get("/payments/{payment_id}")
def get_payment(payment_id: str) -> dict[str, str]:
    """Return one payment by its provider ID."""
    if payment_id not in _PAYMENTS:
        raise HTTPException(status_code=404, detail=f"Payment {payment_id} not found")
    return _payment(payment_id)


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run(app, host=os.environ.get("FAKE_HOST", "127.0.0.1"), port=int(os.environ.get("FAKE_PORT", "8081")))
