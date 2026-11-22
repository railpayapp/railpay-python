"""The RailPay API clients: request shapes, auth and error mapping."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest

from railpay import NETWORK, USDG, RailPay, RailPayError

API = "https://api.test"
TX = "0x" + "ab" * 32


def required() -> dict[str, Any]:
    accept = {
        "scheme": "exact",
        "network": NETWORK,
        "amount": "10000",
        "asset": USDG,
        "payTo": "0x2222222222222222222222222222222222222222",
        "maxTimeoutSeconds": 300,
        "extra": {"name": "Global Dollar", "version": "1"},
    }
    return {"x402Version": 2, "resource": {"url": "https://seller.test/report"}, "accepts": [accept]}


def client(handler: Any, key: str | None = "rp_sk_test", base_url: str = API) -> RailPay:
    return RailPay(key, base_url=base_url, http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_maps_api_errors_to_railpay_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "over_daily_cap", "message": "Daily cap reached"})

    with pytest.raises(RailPayError) as e:
        client(handler).pay(required())
    assert (e.value.status, e.value.code, e.value.message) == (403, "over_daily_cap", "Daily cap reached")
    assert str(e.value) == "Daily cap reached"


def test_maps_non_json_errors_to_http_codes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    with pytest.raises(RailPayError) as e:
        client(handler, key=None).config()
    assert (e.value.status, e.value.code, e.value.message) == (502, "http_502", "bad gateway")


def test_falls_back_to_the_reason_phrase_for_an_empty_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(RailPayError) as e:
        client(handler).config()
    assert (e.value.status, e.value.code, e.value.message) == (503, "http_503", "Service Unavailable")


def test_needs_an_agent_key_for_agent_methods() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    railpay = client(handler, key=None)
    for call in (railpay.me, lambda: railpay.pay(required()), lambda: railpay.send("agt_x", "1")):
        with pytest.raises(RailPayError) as e:
            call()
        assert (e.value.status, e.value.code) == (0, "missing_api_key")


def test_request_shapes() -> None:
    seen: list[tuple[str, str, httpx.Headers, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.headers, request.read()))
        if request.url.path == "/v1/agent/me":
            return httpx.Response(200, json={"agent": {"id": "agt_1", "balance": 5}})
        if request.url.path == "/v1/agent/send":
            return httpx.Response(200, json={"tx_hash": TX, "tx_url": "u", "amount": "0.5"})
        if request.url.path == "/v1/agent/pay":
            return httpx.Response(200, json={"payment": "p", "header": "PAYMENT-SIGNATURE"})
        return httpx.Response(200, json={"network": NETWORK})

    assert client(handler, key=None, base_url=API + "/").config() == {"network": NETWORK}
    method, url, headers, body = seen[-1]
    assert (method, url, body) == ("GET", f"{API}/v1/config", b"")
    assert "authorization" not in headers
    assert headers["accept"] == "application/json"
    assert headers["user-agent"].startswith("railpay-python")

    railpay = client(handler)
    assert railpay.me()["id"] == "agt_1"
    assert seen[-1][2]["authorization"] == "Bearer rp_sk_test"
    assert railpay.send("agt_2", "0.5")["tx_hash"] == TX
    assert json.loads(seen[-1][3]) == {"to": "agt_2", "amount": "0.5"}
    assert seen[-1][2]["content-type"] == "application/json"
    railpay.send("agt_2", Decimal("0.50"), memo="thanks")
    assert json.loads(seen[-1][3]) == {"to": "agt_2", "amount": "0.50", "memo": "thanks"}
    assert railpay.pay(required())["payment"] == "p"
    assert json.loads(seen[-1][3]) == {"payment_required": required()}
    railpay.pay(required(), "https://seller.test/report")
    assert json.loads(seen[-1][3])["resource"] == "https://seller.test/report"
    assert railpay.request("DELETE", "/v1/anything") == {"network": NETWORK}
    assert seen[-1][:2] == ("DELETE", f"{API}/v1/anything")


def test_never_shows_the_key() -> None:
    railpay = RailPay("rp_sk_secret")
    assert "rp_sk_secret" not in repr(railpay)
    railpay.close()


def test_closes_only_its_own_http_client() -> None:
    own = RailPay()
    with own:
        pass
    assert own._http.is_closed
    shared = httpx.Client()
    with RailPay(http_client=shared):
        pass
    assert not shared.is_closed
    shared.close()
