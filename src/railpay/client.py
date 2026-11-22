"""Clients for the RailPay API: :class:`RailPay` (sync) and :class:`AsyncRailPay` (async), both on httpx."""

from __future__ import annotations

import contextlib
import json
from decimal import Decimal
from importlib.metadata import PackageNotFoundError, version
from types import TracebackType
from typing import Any, Literal, TypedDict, cast

import httpx

from .constants import DEFAULT_BASE_URL
from .x402 import AuthorizationFields, PaymentRequired

Method = Literal["GET", "POST", "PATCH", "DELETE"]

try:
    _USER_AGENT = f"railpay-python/{version('railpay')}"
except PackageNotFoundError:  # running from a source tree that is not installed
    _USER_AGENT = "railpay-python"


class RailPayError(Exception):
    """An error answer from the RailPay API (``{ error, message }``), or a local refusal (``status`` 0)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def __repr__(self) -> str:
        return f"RailPayError(status={self.status}, code={self.code!r}, message={self.message!r})"


class Policy(TypedDict):
    per_request_max: int
    """Base units."""
    daily_cap: int
    """Base units."""
    allowed_hosts: list[str]
    allowed_pay_to: list[str]
    allow_a2a: bool
    paused: bool


class Agent(TypedDict):
    id: str
    name: str
    address: str
    created_at: int
    balance: int
    """USDG held by the agent wallet, in base units (read from the chain)."""
    eth: str
    """ETH held by the agent wallet, as a decimal string."""
    spent_today: int
    """Spent or held today, in base units."""
    policy: Policy


class SignedPayment(TypedDict):
    """A signed x402 payment, ready for the PAYMENT-SIGNATURE header of the retry."""

    payment: str
    header: Literal["PAYMENT-SIGNATURE"]
    authorization: AuthorizationFields
    valid_before: int
    amount: str
    spent_today: str


class _SendResultBase(TypedDict):
    tx_hash: str
    tx_url: str
    amount: str


class SendResult(_SendResultBase, total=False):
    pending: bool


class _Base:
    def __init__(self, api_key: str | None, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key

    def __repr__(self) -> str:  # never show the key
        return f"{type(self).__name__}(base_url={self.base_url!r}, api_key={'set' if self._api_key else None})"

    def _build(self, method: Method, path: str, body: Any) -> tuple[str, dict[str, str], bytes | None]:
        headers = {"accept": "application/json", "user-agent": _USER_AGENT}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        content = None
        if body is not None:
            headers["content-type"] = "application/json"
            content = json.dumps(body, separators=(",", ":")).encode("utf-8")
        return f"{self.base_url}{path}", headers, content

    @staticmethod
    def _parse(res: httpx.Response) -> Any:
        text = res.text
        data: Any = None
        with contextlib.suppress(ValueError):  # not JSON
            data = json.loads(text) if text else None
        if not res.is_success:
            e = data if isinstance(data, dict) else {}
            code = e.get("error")
            message = e.get("message")
            raise RailPayError(
                res.status_code,
                code if isinstance(code, str) else f"http_{res.status_code}",
                message if isinstance(message, str) else (text[:200] or res.reason_phrase),
            )
        return data

    def _need_key(self) -> None:
        if not self._api_key:
            raise RailPayError(0, "missing_api_key", "Pass an agent key (rp_sk_…) as api_key")

    @staticmethod
    def _pay_body(payment_required: PaymentRequired | dict[str, Any], resource_url: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {"payment_required": payment_required}
        if resource_url is not None:
            body["resource"] = resource_url
        return body

    @staticmethod
    def _send_body(to: str, amount: str | Decimal, memo: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {"to": to, "amount": amount if isinstance(amount, str) else format(amount, "f")}
        if memo is not None:
            body["memo"] = memo
        return body


class RailPay(_Base):
    """Client for the RailPay API.

    ``api_key`` (``rp_sk_…``): an agent key for the agent methods, an account key for :meth:`stats`.
    :meth:`config` is public. Pass ``http_client`` to reuse your own ``httpx.Client`` (it is not closed for you).
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        http_client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(api_key, base_url)
        self._owns_client = http_client is None
        self._http = http_client if http_client is not None else httpx.Client(timeout=timeout)

    def request(self, method: Method, path: str, body: Any = None) -> Any:
        """Low-level call. Raises :class:`RailPayError` on a non-2xx answer."""
        url, headers, content = self._build(method, path, body)
        return self._parse(self._http.request(method, url, headers=headers, content=content))

    def me(self) -> Agent:
        """The calling agent: balance, policy and today's spend. Agent key."""
        self._need_key()
        return cast(Agent, self.request("GET", "/v1/agent/me")["agent"])

    def pay(self, payment_required: PaymentRequired | dict[str, Any], resource_url: str | None = None) -> SignedPayment:
        """Sign an EIP-3009 USDG authorization for a 402 requirement, inside the agent's policy. Agent key.

        Put ``payment`` in the PAYMENT-SIGNATURE header of the retry. The seller (or its facilitator) settles it.
        """
        self._need_key()
        body = self._pay_body(payment_required, resource_url)
        return cast(SignedPayment, self.request("POST", "/v1/agent/pay", body))

    def send(self, to: str, amount: str | Decimal, memo: str | None = None) -> SendResult:
        """Agent-to-agent USDG payment to ``agt_…`` (needs ``allow_a2a``; the paying agent wallet pays the gas in ETH).

        ``amount`` is in USDG (``"0.5"``). Agent key.
        """
        self._need_key()
        return cast(SendResult, self.request("POST", "/v1/agent/send", self._send_body(to, amount, memo)))

    def config(self) -> dict[str, Any]:
        """Network settings: chain, USDG, facilitators, x402 settings. Public."""
        return cast(dict[str, Any], self.request("GET", "/v1/config"))

    def stats(self) -> dict[str, Any]:
        """Network stats: every recorded payment, outside vs team. Needs an account key (signed-in accounts only)."""
        return cast(dict[str, Any], self.request("GET", "/v1/stats"))

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> RailPay:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()


class AsyncRailPay(_Base):
    """Async client for the RailPay API. Same methods as :class:`RailPay`, awaited."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(api_key, base_url)
        self._owns_client = http_client is None
        self._http = http_client if http_client is not None else httpx.AsyncClient(timeout=timeout)

    async def request(self, method: Method, path: str, body: Any = None) -> Any:
        """Low-level call. Raises :class:`RailPayError` on a non-2xx answer."""
        url, headers, content = self._build(method, path, body)
        return self._parse(await self._http.request(method, url, headers=headers, content=content))

    async def me(self) -> Agent:
        self._need_key()
        return cast(Agent, (await self.request("GET", "/v1/agent/me"))["agent"])

    async def pay(
        self, payment_required: PaymentRequired | dict[str, Any], resource_url: str | None = None
    ) -> SignedPayment:
        self._need_key()
        body = self._pay_body(payment_required, resource_url)
        return cast(SignedPayment, await self.request("POST", "/v1/agent/pay", body))

    async def send(self, to: str, amount: str | Decimal, memo: str | None = None) -> SendResult:
        self._need_key()
        return cast(SendResult, await self.request("POST", "/v1/agent/send", self._send_body(to, amount, memo)))

    async def config(self) -> dict[str, Any]:
        return cast(dict[str, Any], await self.request("GET", "/v1/config"))

    async def stats(self) -> dict[str, Any]:
        return cast(dict[str, Any], await self.request("GET", "/v1/stats"))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def __aenter__(self) -> AsyncRailPay:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.aclose()
