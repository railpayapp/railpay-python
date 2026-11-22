"""Both sides together: paid_client pays a Paywall-protected ASGI app with a real EIP-3009 signature."""

from __future__ import annotations

import json
import os
import time

import httpx
import pytest
from eth_account.signers.local import LocalAccount
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from railpay import (
    USDG,
    AsyncRailPay,
    PaymentEvent,
    PaywallMiddleware,
    RailPayError,
    b64json,
    paid_client,
    read_payment_response,
)

from ._support import TX1, World, new_account, seller_paywall, settle_ok, sign_authorization

pytestmark = pytest.mark.anyio

BASE = "https://seller.test"


def railpay_api(agent_wallet: LocalAccount) -> httpx.MockTransport:
    """What RailPay does for POST /v1/agent/pay (SPEC.md): sign the payable requirement with the agent wallet."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/agent/pay"
        assert request.headers["authorization"] == "Bearer rp_sk_agent"
        body = json.loads(request.content)
        pr = body["payment_required"]
        req = pr["accepts"][0]
        now = int(time.time())
        message = {
            "from": agent_wallet.address,
            "to": req["payTo"],
            "value": int(req["amount"]),
            "validAfter": now - 60,
            "validBefore": now + max(30, min(600, req["maxTimeoutSeconds"])),
            "nonce": "0x" + os.urandom(32).hex(),
        }
        signature = sign_authorization(agent_wallet, message)
        auth = {k: str(v) if isinstance(v, int) else v for k, v in message.items()}
        payload = {
            "x402Version": 2,
            "resource": pr["resource"],
            "accepted": req,
            "payload": {"signature": signature, "authorization": auth},
        }
        return httpx.Response(
            200,
            json={
                "payment": b64json(payload),
                "header": "PAYMENT-SIGNATURE",
                "authorization": auth,
                "valid_before": message["validBefore"],
                "amount": "0.01",
                "spent_today": "0.01",
            },
        )

    return httpx.MockTransport(handler)


async def test_agent_pays_a_railpay_paywall_end_to_end() -> None:
    world, agent_wallet, pay_to = World(), new_account(), new_account()
    settle_ok(world, agent_wallet.address, pay_to.address)
    world.balance = 50_000

    async def report(request: Request) -> JSONResponse:
        return JSONResponse({"report": "ok"})

    app = Starlette(routes=[Route("/report", report)])
    app.add_middleware(PaywallMiddleware, paywall=seller_paywall(world, pay_to), paths=["/report"])

    events: list[PaymentEvent] = []
    railpay = AsyncRailPay(
        "rp_sk_agent", base_url="https://api.test", http_client=httpx.AsyncClient(transport=railpay_api(agent_wallet))
    )
    transport = httpx.ASGITransport(app=app)
    async with paid_client(railpay, max_amount="0.01", on_payment=events.append, transport=transport) as http:
        res = await http.get(f"{BASE}/report")
    assert res.status_code == 200
    assert res.json() == {"report": "ok"}
    receipt = read_payment_response(res)
    assert receipt is not None
    assert receipt["success"] is True
    assert receipt["transaction"] == TX1
    assert receipt.get("payer") == agent_wallet.address.lower()
    assert [e.amount for e in events] == [10_000]
    settled = world.settle_calls[0][2]["paymentPayload"]
    assert settled["accepted"]["asset"] == USDG
    assert settled["payload"]["authorization"]["from"] == agent_wallet.address


async def test_agent_stops_at_max_amount_before_the_paywall_sees_a_payment() -> None:
    world, agent_wallet, pay_to = World(), new_account(), new_account()

    async def report(request: Request) -> JSONResponse:
        return JSONResponse({"report": "ok"})

    app = Starlette(routes=[Route("/report", report)])
    app.add_middleware(PaywallMiddleware, paywall=seller_paywall(world, pay_to))
    railpay = AsyncRailPay(
        "rp_sk_agent", base_url="https://api.test", http_client=httpx.AsyncClient(transport=railpay_api(agent_wallet))
    )
    async with paid_client(railpay, max_amount="0.005", transport=httpx.ASGITransport(app=app)) as http:
        with pytest.raises(RailPayError, match="above the limit"):
            await http.get(f"{BASE}/report")
    assert world.settle_calls == []
    assert world.rpc_calls == []
