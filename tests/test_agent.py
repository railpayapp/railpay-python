"""Agent side: PaymentAuth / paid_client answer a 402 from a RailPay agent wallet (mirrors tests/fetch.test.ts)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from railpay import (
    NETWORK,
    USDG,
    AsyncRailPay,
    PaymentAuth,
    PaymentEvent,
    RailPay,
    RailPayError,
    b64json,
    paid_client,
    read_payment_response,
)

API = "https://api.test"
RESOURCE = "https://seller.test/report"
PAY_TO = "0x2222222222222222222222222222222222222222"
TX = "0x" + "ab" * 32


def required(network: str = NETWORK, amount: str = "10000", **extra: Any) -> dict[str, Any]:
    accept = {
        "scheme": "exact",
        "network": network,
        "amount": amount,
        "asset": USDG,
        "payTo": PAY_TO,
        "maxTimeoutSeconds": 300,
        "extra": {"name": "Global Dollar", "version": "1", **extra},
    }
    return {"x402Version": 2, "error": "payment_required", "resource": {"url": RESOURCE}, "accepts": [accept]}


class Wire(httpx.BaseTransport, httpx.AsyncBaseTransport):
    """Sends a request body the way a real transport does: it reads ``request.stream`` once.

    ``httpx.MockTransport`` calls ``request.read()``, which keeps a copy on the request and would hide a body that
    cannot be sent a second time.
    """

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    def _forward(self, request: httpx.Request, body: bytes) -> httpx.Response:
        return self.handler(httpx.Request(request.method, request.url, headers=request.headers, content=body))

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        assert isinstance(request.stream, httpx.SyncByteStream)
        return self._forward(request, b"".join(request.stream))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert isinstance(request.stream, httpx.AsyncByteStream)
        return self._forward(request, b"".join([chunk async for chunk in request.stream]))


@dataclass
class Call:
    url: str
    method: str
    headers: httpx.Headers
    body: bytes


@dataclass
class Server:
    """The RailPay API and an x402 seller behind one transport."""

    pr: dict[str, Any]
    calls: list[Call] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(Call(url, request.method, request.headers, request.read()))
        if url == f"{API}/v1/agent/pay":
            return httpx.Response(
                200,
                json={
                    "payment": "signed-payment",
                    "header": "PAYMENT-SIGNATURE",
                    "authorization": {},
                    "valid_before": 0,
                    "amount": "0.01",
                    "spent_today": "0.01",
                },
            )
        if url == RESOURCE:
            if request.headers.get("payment-signature") == "signed-payment":
                receipt = b64json({"success": True, "transaction": TX, "network": NETWORK})
                return httpx.Response(200, json={"ok": True}, headers={"PAYMENT-RESPONSE": receipt})
            return httpx.Response(402, json=self.pr, headers={"PAYMENT-REQUIRED": b64json(self.pr)})
        return httpx.Response(404, text="not found")

    @property
    def transport(self) -> Wire:
        return Wire(self.handler)

    def sync_client(self, key: str | None = "rp_sk_test") -> RailPay:
        return RailPay(key, base_url=API, http_client=httpx.Client(transport=self.transport))

    def urls(self) -> list[str]:
        return [c.url for c in self.calls]

    def paid(self) -> bool:
        return any(c.url.endswith("/v1/agent/pay") for c in self.calls)

    def async_client(self, key: str | None = "rp_sk_test") -> AsyncRailPay:
        return AsyncRailPay(key, base_url=API, http_client=httpx.AsyncClient(transport=self.transport))


def test_pays_a_402_and_retries_once_with_payment_signature() -> None:
    server = Server(required())
    events: list[PaymentEvent] = []
    with paid_client(server.sync_client(), on_payment=events.append, transport=server.transport) as http:
        res = http.get(RESOURCE)
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    assert server.urls() == [RESOURCE, f"{API}/v1/agent/pay", RESOURCE]
    assert len(events) == 1
    assert events[0].amount == 10_000
    assert events[0].url == RESOURCE
    assert events[0].requirement["payTo"] == PAY_TO
    assert events[0].payment["payment"] == "signed-payment"
    receipt = read_payment_response(res)
    assert receipt is not None
    assert receipt["transaction"] == TX
    assert [r.status_code for r in res.history] == [402]


def test_sends_the_agent_key_and_the_decoded_requirement_to_agent_pay() -> None:
    server = Server(required())
    with paid_client(server.sync_client(), transport=server.transport) as http:
        http.get(RESOURCE)
    pay = next(c for c in server.calls if c.url.endswith("/v1/agent/pay"))
    assert pay.headers["authorization"] == "Bearer rp_sk_test"
    assert pay.headers["content-type"] == "application/json"
    sent = json.loads(pay.body)
    assert sent["payment_required"]["accepts"][0]["payTo"] == PAY_TO
    assert sent["resource"] == RESOURCE
    seller_hits = [c for c in server.calls if c.url == RESOURCE]
    assert "authorization" not in seller_hits[0].headers  # the agent key never goes to the seller
    assert "authorization" not in seller_hits[1].headers
    assert seller_hits[1].headers["payment-signature"] == "signed-payment"


def test_adds_the_resource_url_when_the_402_has_none() -> None:
    pr = required()
    del pr["resource"]
    server = Server(pr)
    with paid_client(server.sync_client(), transport=server.transport) as http:
        http.get(RESOURCE)
    pay = next(c for c in server.calls if c.url.endswith("/v1/agent/pay"))
    assert json.loads(pay.body)["payment_required"]["resource"] == {"url": RESOURCE}


def test_refuses_to_pay_above_max_amount() -> None:
    server = Server(required(NETWORK, "10000"))
    http = paid_client(server.sync_client(), max_amount="0.005", transport=server.transport)
    with http, pytest.raises(RailPayError) as e:
        http.get(RESOURCE)
    assert e.value.code == "over_max_amount"
    assert e.value.status == 0
    assert "0.01 USDG" in e.value.message
    assert not server.paid()


def test_max_amount_as_int_is_base_units() -> None:
    server = Server(required(NETWORK, "10000"))
    http = paid_client(server.sync_client(), max_amount=9_999, transport=server.transport)
    with http, pytest.raises(RailPayError, match="above the limit"):
        http.get(RESOURCE)
    with paid_client(server.sync_client(), max_amount=10_000, transport=server.transport) as http:
        assert http.get(RESOURCE).status_code == 200
    with pytest.raises(ValueError, match="negative"):
        PaymentAuth(server.sync_client(), max_amount=-1)


def test_returns_the_original_402_when_no_requirement_is_payable() -> None:
    for pr in (required("eip155:8453"), required(assetTransferMethod="permit2")):
        server = Server(pr)
        with paid_client(server.sync_client(), transport=server.transport) as http:
            res = http.get(RESOURCE)
        assert res.status_code == 402
        assert res.json()["accepts"][0]["payTo"] == PAY_TO
        assert not server.paid()


def test_returns_a_402_without_a_requirement_unchanged() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, text="pay me somehow")

    railpay = RailPay("k", base_url=API, http_client=httpx.Client(transport=Wire(handler)))
    with paid_client(railpay, transport=Wire(handler)) as http:
        res = http.get(RESOURCE)
    assert (res.status_code, res.text) == (402, "pay me somehow")


def test_resends_the_post_body_on_the_paid_retry() -> None:
    server = Server(required())
    with paid_client(server.sync_client(), transport=server.transport) as http:
        res = http.post(RESOURCE, json={"q": "ping"})
    assert res.status_code == 200
    hits = [c for c in server.calls if c.url == RESOURCE]
    assert [h.method for h in hits] == ["POST", "POST"]
    assert [json.loads(h.body) for h in hits] == [{"q": "ping"}, {"q": "ping"}]


def test_resends_a_streamed_body_on_the_paid_retry() -> None:
    server = Server(required())

    def chunks() -> Iterator[bytes]:
        yield b'{"q":'
        yield b'"ping"}'

    with paid_client(server.sync_client(), transport=server.transport) as http:
        res = http.post(RESOURCE, content=chunks())
    assert res.status_code == 200
    assert [c.body for c in server.calls if c.url == RESOURCE] == [b'{"q":"ping"}', b'{"q":"ping"}']


def test_passes_non_402_responses_through_untouched() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text="hello")

    railpay = RailPay("k", base_url=API, http_client=httpx.Client(transport=Wire(handler)))
    with paid_client(railpay, transport=Wire(handler)) as http:
        assert http.get("https://free.test/").text == "hello"
    assert calls == ["https://free.test/"]


def test_payment_auth_on_a_single_request() -> None:
    server = Server(required())
    with httpx.Client(transport=server.transport) as http:
        assert http.get(RESOURCE).status_code == 402
        assert http.get(RESOURCE, auth=PaymentAuth(server.sync_client())).status_code == 200


def test_raises_api_refusals() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/agent/pay":
            return httpx.Response(403, json={"error": "host_not_allowed", "message": "Host not in the allowlist"})
        return Server(required()).handler(request)

    railpay = RailPay("k", base_url=API, http_client=httpx.Client(transport=Wire(handler)))
    http = paid_client(railpay, transport=Wire(handler))
    with http, pytest.raises(RailPayError) as e:
        http.get(RESOURCE)
    assert (e.value.status, e.value.code) == (403, "host_not_allowed")


# ---------- async ----------


@pytest.mark.anyio
async def test_async_client_pays_and_retries_once() -> None:
    server = Server(required())
    events: list[PaymentEvent] = []

    async def on_payment(event: PaymentEvent) -> None:
        events.append(event)

    async with paid_client(server.async_client(), on_payment=on_payment, transport=server.transport) as http:
        res = await http.get(RESOURCE)
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    assert server.urls() == [RESOURCE, f"{API}/v1/agent/pay", RESOURCE]
    assert [e.amount for e in events] == [10_000]
    receipt = read_payment_response(res)
    assert receipt is not None
    assert receipt["transaction"] == TX


@pytest.mark.anyio
async def test_async_accepts_a_plain_on_payment_callback() -> None:
    server = Server(required())
    events: list[PaymentEvent] = []
    async with paid_client(server.async_client(), on_payment=events.append, transport=server.transport) as http:
        assert (await http.get(RESOURCE)).status_code == 200
    assert len(events) == 1


@pytest.mark.anyio
async def test_async_refuses_to_pay_above_max_amount() -> None:
    server = Server(required(NETWORK, "10000"))
    async with paid_client(server.async_client(), max_amount="0.0099", transport=server.transport) as http:
        with pytest.raises(RailPayError) as e:
            await http.get(RESOURCE)
    assert e.value.code == "over_max_amount"
    assert not server.paid()


@pytest.mark.anyio
async def test_async_returns_the_original_402_when_no_requirement_is_payable() -> None:
    server = Server(required("eip155:8453"))
    async with paid_client(server.async_client(), transport=server.transport) as http:
        res = await http.get(RESOURCE)
    assert res.status_code == 402
    assert not server.paid()


@pytest.mark.anyio
async def test_async_resends_a_streamed_body() -> None:
    server = Server(required())

    async def chunks() -> AsyncIterator[bytes]:
        yield b"raw-"
        yield b"bytes"

    async with paid_client(server.async_client(), transport=server.transport) as http:
        res = await http.post(RESOURCE, content=chunks(), headers={"content-type": "text/plain"})
    assert res.status_code == 200
    assert [c.body for c in server.calls if c.url == RESOURCE] == [b"raw-bytes", b"raw-bytes"]


def test_payment_auth_with_an_async_railpay_refuses_a_sync_client() -> None:
    server = Server(required())
    http = httpx.Client(auth=PaymentAuth(server.async_client()), transport=server.transport)
    with http, pytest.raises(TypeError, match="AsyncClient"):
        http.get(RESOURCE)


@pytest.mark.anyio
async def test_payment_auth_with_a_sync_railpay_refuses_an_async_client() -> None:
    server = Server(required())
    async with httpx.AsyncClient(auth=PaymentAuth(server.sync_client()), transport=server.transport) as http:
        with pytest.raises(TypeError, match="AsyncRailPay"):
            await http.get(RESOURCE)
