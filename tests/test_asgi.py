"""PaywallMiddleware in front of a Starlette app."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from railpay import Paywall, PaywallMiddleware, unb64json
from railpay.asgi import request_url

from ._support import TX1, World, new_account, payment, seller_paywall, settle_ok

pytestmark = pytest.mark.anyio

BASE = "https://seller.test"


def starlette_app(paywall: Paywall) -> Starlette:
    async def report(request: Request) -> JSONResponse:
        return JSONResponse({"report": "ok"}, headers={"PAYMENT-RESPONSE": "forged"})

    async def free(request: Request) -> PlainTextResponse:
        return PlainTextResponse("free")

    app = Starlette(routes=[Route("/report", report), Route("/free", free)])
    app.add_middleware(PaywallMiddleware, paywall=paywall, paths=["/report"])
    return app


def asgi_client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


async def test_answers_402_and_lets_other_paths_through() -> None:
    world, pay_to = World(), new_account()
    async with asgi_client(starlette_app(seller_paywall(world, pay_to))) as http:
        res = await http.get("/report?day=1")
        free = await http.get("/free")
    assert res.status_code == 402
    body = res.json()
    assert unb64json(res.headers["payment-required"]) == body
    assert body["resource"]["url"] == f"{BASE}/report"
    assert body["accepts"][0]["payTo"] == pay_to.address
    assert body["error"] == "payment_required"
    assert res.headers["content-type"] == "application/json"
    assert res.headers["content-length"] == str(len(res.content))
    assert free.status_code == 200
    assert free.text == "free"
    assert "payment-response" not in free.headers
    assert world.rpc_calls == []


async def test_serves_a_paid_request_with_payment_response() -> None:
    world, payer, pay_to = World(), new_account(), new_account()
    settle_ok(world, payer.address, pay_to.address)
    async with asgi_client(starlette_app(seller_paywall(world, pay_to))) as http:
        header = payment(payer, pay_to.address)
        res = await http.get("/report", headers={"PAYMENT-SIGNATURE": header})
        replay = await http.get("/report", headers={"PAYMENT-SIGNATURE": header})
    assert res.status_code == 200
    assert res.json() == {"report": "ok"}
    assert res.headers.get_list("payment-response") == [res.headers["x-payment-response"]]  # "forged" is replaced
    assert unb64json(res.headers["payment-response"])["transaction"] == TX1
    assert "PAYMENT-RESPONSE" in res.headers["access-control-expose-headers"]
    assert replay.status_code == 402
    assert replay.json()["error"] == "invalid_payload"


async def test_matches_paths_with_a_predicate() -> None:
    world, pay_to = World(), new_account()

    async def item(request: Request) -> PlainTextResponse:
        return PlainTextResponse(request.path_params["id"])

    app = Starlette(routes=[Route("/items/{id}", item)])
    app.add_middleware(
        PaywallMiddleware, paywall=seller_paywall(world, pay_to), paths=lambda p: p.startswith("/items/")
    )
    async with asgi_client(app) as http:
        res = await http.get("/items/7")
    assert res.status_code == 402
    assert res.json()["resource"]["url"] == f"{BASE}/items/7"


async def test_wraps_a_bare_asgi_app_and_charges_every_path_by_default() -> None:
    world, pay_to = World(), new_account()

    async def app(scope: Any, receive: Any, send: Any) -> None:
        raise AssertionError("an unpaid request must not reach the app")

    async with asgi_client(PaywallMiddleware(app, seller_paywall(world, pay_to))) as http:
        assert (await http.post("/anything", content=b"x")).status_code == 402


def test_request_url() -> None:
    scope: dict[str, Any] = {
        "type": "http",
        "scheme": "https",
        "path": "/a b",
        "raw_path": b"/a%20b",
        "query_string": b"x=1",
        "headers": [(b"host", b"api.example.com")],
    }
    assert request_url(scope) == "https://api.example.com/a%20b?x=1"
    no_host = {**scope, "headers": [], "server": ("10.0.0.1", 8080), "raw_path": None, "query_string": b""}
    assert request_url(no_host) == "https://10.0.0.1:8080/a%20b"
    default_port = {**no_host, "server": ("example.com", 443)}
    assert request_url(default_port) == "https://example.com/a%20b"
