"""ASGI middleware: put a :class:`~railpay.seller.Paywall` in front of paths of any ASGI app (FastAPI, Starlette…)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, MutableMapping
from typing import Any
from urllib.parse import quote

from .seller import Paywall

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_PAID_HEADERS = {b"payment-response", b"x-payment-response", b"access-control-expose-headers"}


def request_url(scope: Scope) -> str:
    """The full URL of an ASGI HTTP request, from the Host header (or the server address)."""
    scheme = str(scope.get("scheme") or "http")
    host = next((v.decode("latin-1") for k, v in scope.get("headers") or [] if k.lower() == b"host"), None)
    if host is None:
        server = scope.get("server")
        if server:
            name, port = server[0], server[1]
            host = name if port is None or port == {"http": 80, "https": 443}.get(scheme) else f"{name}:{port}"
        else:
            host = "localhost"
    raw = scope.get("raw_path")
    if raw:
        path = raw.decode("latin-1").split("?", 1)[0]
    else:
        root = str(scope.get("root_path") or "")
        p = str(scope["path"])
        path = quote(p if p.startswith(root) else root + p)
    qs = scope.get("query_string") or b""
    return f"{scheme}://{host}{path}" + (f"?{qs.decode('latin-1')}" if qs else "")


def _headers(scope: Scope) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in scope.get("headers") or []:
        name, value = k.decode("latin-1").lower(), v.decode("latin-1")
        out[name] = f"{out[name]}, {value}" if name in out else value
    return out


def _encode(headers: dict[str, str]) -> list[tuple[bytes, bytes]]:
    return [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]


class PaywallMiddleware:
    """Charge for matching paths with a :class:`~railpay.seller.Paywall`; everything else passes through.

    ``paths``: exact paths (``["/report"]``), a predicate on the path, or ``None`` for every HTTP request.
    Unpaid requests get the 402 from the paywall. Paid ones reach the app, and PAYMENT-RESPONSE is added to its answer.

    Starlette / FastAPI: ``app.add_middleware(PaywallMiddleware, paywall=paywall, paths=["/report"])``.
    """

    def __init__(
        self,
        app: ASGIApp,
        paywall: Paywall,
        paths: Iterable[str] | Callable[[str], bool] | None = None,
    ) -> None:
        self.app = app
        self.paywall = paywall
        if paths is None:
            self._match: Callable[[str], bool] = lambda _path: True
        elif callable(paths):
            self._match = paths
        else:
            self._match = frozenset(paths).__contains__

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._match(str(scope["path"])):
            await self.app(scope, receive, send)
            return
        charge = await self.paywall.charge(request_url(scope), _headers(scope))
        if not charge.paid:
            headers = [*_encode(charge.headers), (b"content-length", str(len(charge.body)).encode("ascii"))]
            await send({"type": "http.response.start", "status": charge.status, "headers": headers})
            await send({"type": "http.response.body", "body": charge.body})
            return
        extra = _encode(charge.headers)

        async def send_paid(message: Message) -> None:
            if message["type"] == "http.response.start":
                kept = [(k, v) for k, v in message.get("headers") or [] if bytes(k).lower() not in _PAID_HEADERS]
                message = {**message, "headers": [*kept, *extra]}
            await send(message)

        await self.app(scope, receive, send_paid)
