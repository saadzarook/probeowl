# Author: @SaadZarook
from fastapi.testclient import TestClient

from door_controller import app as door_app
from payments import app as payments_app

payments = TestClient(payments_app)
doors = TestClient(door_app)


def test_planted_payment_by_id() -> None:
    body = payments.get("/payments/pay_7hq2m9").json()
    assert (body["status"], body["amount"], body["currency"]) == ("captured", "500.00", "USD")


def test_payment_by_reference_matches_id_lookup() -> None:
    assert payments.get("/payments", params={"reference": "tp_91f2c7"}).json() == payments.get(
        "/payments/pay_7hq2m9"
    ).json()


def test_unknown_payment_is_404() -> None:
    assert payments.get("/payments/nope").status_code == 404
    assert payments.get("/payments", params={"reference": "nope"}).status_code == 404


def test_planted_card_mismatch() -> None:
    assert doors.get("/cards/C177031").json()["status"] == "active"


def test_unknown_card_is_404() -> None:
    assert doors.get("/cards/nope").status_code == 404


def test_no_write_endpoints() -> None:
    assert payments.post("/payments/pay_7hq2m9").status_code == 405
    assert doors.delete("/cards/C177031").status_code == 405
