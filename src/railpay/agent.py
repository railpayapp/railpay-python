"""Agent side: pay x402 v2 resources from a RailPay agent wallet with an httpx client."""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from .client import RailPay, RailPayError, SignedPayment
from .encoding import from_units, to_units
from .x402 import PaymentRequired, PaymentRequirements, is_railpay_payable, read_payment_required, requirement_amount


@dataclass(frozen=True)
class PaymentEvent:
    """Passed to ``on_payment`` after a payment is signed, before the retry."""

    url: str
    requirement: PaymentRequirements
    amount: int
    """Base units."""
    payment: SignedPayment


OnPayment = Callable[[PaymentEvent], None]


@dataclass(frozen=True)
class _Plan:
    url: str
    payment_required: PaymentRequired
    requirement: PaymentRequirements
    amount: int


def _max_units(max_amount: str | Decimal | int | None) -> int | None:
    if max_amount is None:
        return None
    if isinstance(max_amount, int) and not isinstance(max_amount, bool):
        if max_amount < 0:
            raise ValueError("max_amount must not be negative")
        return max_amount
    return to_units(max_amount)


class PaymentAuth(httpx.Auth):
    """An httpx auth hook that answers a 402 from a RailPay agent wallet and retries once (like ``withPayment``).

    On a 402 it reads the requirement (PAYMENT-REQUIRED header, else the JSON body). It pays only
    ``exact`` / ``eip155:4663`` / USDG options; otherwise the original 402 comes back. A price above ``max_amount``
    raises :class:`RailPayError` (``over_max_amount``) before anything is signed. RailPay signs the EIP-3009
    authorization (``POST /v1/agent/pay``) after checking the agent's policy, and the request is sent again,
    body included, with PAYMENT-SIGNATURE.

    ``max_amount``: a USDG decimal (``"0.05"`` or ``Decimal``), or base units as an ``int`` (like the TS ``bigint``).
    Use it with ``httpx.Client``.
    """

    def __init__(
        self,
        railpay: RailPay,
        *,
        max_amount: str | Decimal | int | None = None,
        on_payment: OnPayment | None = None,
    ) -> None:
        self.railpay = railpay
        self.max_amount = _max_units(max_amount)
        self.on_payment = on_payment

    def _plan(self, request: httpx.Request, response: httpx.Response) -> _Plan | None:
        pr = read_payment_required(response)
        req = next((r for r in pr["accepts"] if is_railpay_payable(r)), None) if pr else None
        if pr is None or req is None:
            return None
        amount = requirement_amount(req)
        if self.max_amount is not None and amount > self.max_amount:
            raise RailPayError(
                0,
                "over_max_amount",
                f"The resource asks {from_units(amount)} USDG, above the limit of {from_units(self.max_amount)} USDG",
            )
        url = str(request.url)
        resource = pr.get("resource")
        with_resource: PaymentRequired = {**pr, "resource": resource if resource is not None else {"url": url}}
        return _Plan(url=url, payment_required=with_resource, requirement=req, amount=amount)

    def sync_auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.read()  # a request body can be streamed once: keep a copy for the paid retry
        response = yield request
        if response.status_code != 402:
            return
        response.read()
        plan = self._plan(request, response)
        if plan is None:
            return
        payment = self.railpay.pay(plan.payment_required, plan.url)
        if self.on_payment is not None:
            self.on_payment(PaymentEvent(plan.url, plan.requirement, plan.amount, payment))
        request.headers["PAYMENT-SIGNATURE"] = payment["payment"]
        yield request

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        # httpx.Client uses sync_auth_flow above; only httpx.AsyncClient gets here.
        raise TypeError("PaymentAuth works with httpx.Client, not httpx.AsyncClient")


def paid_client(
    railpay: RailPay,
    *,
    max_amount: str | Decimal | int | None = None,
    on_payment: OnPayment | None = None,
    **client_options: Any,
) -> httpx.Client:
    """An ``httpx.Client`` that pays 402s with :class:`PaymentAuth`.

    Other keyword arguments go to ``httpx.Client`` (``timeout``, ``headers``, ``transport``…).
    """
    return httpx.Client(auth=PaymentAuth(railpay, max_amount=max_amount, on_payment=on_payment), **client_options)
