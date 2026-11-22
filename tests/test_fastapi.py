"""The FastAPI require_payment dependency."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI

from railpay import Charge, Paywall, unb64json
from railpay.fastapi import PaymentRequiredError, payment_required_handler, require_payment

from ._support import TX1, World, new_account, payment, seller_paywall, settle_ok

pytestmark = pytest.mark.anyio

BASE = "https://seller.test"


def fastapi_app(paywall: Paywall, *, handler: bool = True) -> FastAPI:
    app = FastAPI()
    if handler:
        app.add_exception_handler(PaymentRequiredError, payment_required_handler)

    @app.get("/report")
    async def report(payment: Charge = Depends(require_payment(paywall))) -> dict[str, Any]:  # noqa: B008
        return {"report": "ok", "tx": payment.transaction}

    @app.get("/free")
    async def free() -> dict[str, str]:
        return {"free": "yes"}

    return app


def asgi_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


async def test_answers_402_with_the_x402_body() -> None:
    world, pay_to = World(), new_account()
    async with asgi_client(fastapi_app(seller_paywall(world, pay_to))) as http:
        res = await http.get("/report")
        free = await http.get("/free")
    assert res.status_code == 402
    assert res.json() == unb64json(res.headers["payment-required"])
    assert res.json()["accepts"][0]["amount"] == "10000"
    assert res.json()["resource"]["url"] == f"{BASE}/report"
    assert free.json() == {"free": "yes"}


async def test_without_the_handler_still_sends_payment_required() -> None:
    world, pay_to = World(), new_account()
    async with asgi_client(fastapi_app(seller_paywall(world, pay_to), handler=False)) as http:
        res = await http.get("/report")
    assert res.status_code == 402
    assert res.json()["detail"] == unb64json(res.headers["payment-required"])


async def test_serves_a_paid_request() -> None:
    world, payer, pay_to = World(), new_account(), new_account()
    settle_ok(world, payer.address, pay_to.address)
    async with asgi_client(fastapi_app(seller_paywall(world, pay_to))) as http:
        res = await http.get("/report", headers={"PAYMENT-SIGNATURE": payment(payer, pay_to.address)})
    assert res.status_code == 200
    assert res.json() == {"report": "ok", "tx": TX1}
    assert unb64json(res.headers["payment-response"])["payer"] == payer.address.lower()
    assert res.headers["x-payment-response"] == res.headers["payment-response"]


async def test_refuses_a_payment_to_someone_else() -> None:
    world, payer, pay_to = World(), new_account(), new_account()
    async with asgi_client(fastapi_app(seller_paywall(world, pay_to))) as http:
        res = await http.get("/report", headers={"PAYMENT-SIGNATURE": payment(payer, new_account().address)})
    assert res.status_code == 402
    assert res.json()["error"] == "invalid_exact_evm_payload_recipient_mismatch"
    assert world.settle_calls == []
