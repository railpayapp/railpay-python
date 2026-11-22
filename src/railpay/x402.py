"""x402 v2 wire types and helpers to read 402 answers and payment receipts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Protocol, TypedDict, TypeGuard, cast

from .constants import NETWORK, USDG
from .encoding import unb64json


class _PaymentRequirementsBase(TypedDict):
    scheme: str
    network: str
    asset: str
    payTo: str


class PaymentRequirements(_PaymentRequirementsBase, total=False):
    """One way to pay, from an x402 ``accepts`` list."""

    amount: str
    """Price in base units (x402 v2)."""
    maxTimeoutSeconds: int
    extra: dict[str, Any]
    """``name`` / ``version`` of the EIP-712 domain, optional ``assetTransferMethod``."""
    maxAmountRequired: str
    """x402 v1 name for the price."""
    resource: str


class _ResourceInfoBase(TypedDict):
    url: str


class ResourceInfo(_ResourceInfoBase, total=False):
    description: str
    mimeType: str


class _PaymentRequiredBase(TypedDict):
    x402Version: int
    accepts: list[PaymentRequirements]


class PaymentRequired(_PaymentRequiredBase, total=False):
    """The 402 answer: PAYMENT-REQUIRED header (base64 JSON) and body."""

    error: str
    message: str
    resource: ResourceInfo


AuthorizationFields = TypedDict(
    "AuthorizationFields",
    {"from": str, "to": str, "value": str, "validAfter": str, "validBefore": str, "nonce": str},
)
"""EIP-3009 TransferWithAuthorization fields, as decimal / hex strings."""


class PaymentSignaturePayload(TypedDict):
    signature: str
    authorization: AuthorizationFields


class _PaymentPayloadBase(TypedDict):
    x402Version: int
    accepted: PaymentRequirements
    payload: PaymentSignaturePayload


class PaymentPayload(_PaymentPayloadBase, total=False):
    """What goes (base64) in the PAYMENT-SIGNATURE header of the retry."""

    resource: ResourceInfo


class _SettleResponseBase(TypedDict):
    success: bool
    transaction: str
    network: str


class SettleResponse(_SettleResponseBase, total=False):
    """PAYMENT-RESPONSE header, and a facilitator's ``/settle`` answer."""

    errorReason: str
    errorMessage: str
    payer: str | None


class _HasHeaders(Protocol):
    @property
    def headers(self) -> Mapping[str, str]: ...


def _truthy(v: object) -> bool:
    """JavaScript truthiness (``{}`` and ``[]`` are truthy), to match the TypeScript checks."""
    if v is None or v is False:
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v == v and v != 0  # NaN and 0 are falsy
    if isinstance(v, str):
        return v != ""
    return True


def is_railpay_payable(req: object) -> TypeGuard[PaymentRequirements]:
    """True for requirements a RailPay agent can pay: exact scheme, Robinhood Chain, USDG, EIP-3009."""
    if not isinstance(req, dict):
        return False
    asset = req.get("asset")
    extra = req.get("extra")
    method = extra.get("assetTransferMethod") if isinstance(extra, dict) else None
    return (
        req.get("scheme") == "exact"
        and req.get("network") == NETWORK
        and isinstance(asset, str)
        and asset.lower() == USDG.lower()
        and (not _truthy(method) or method == "eip3009")
    )


def requirement_amount(req: PaymentRequirements | Mapping[str, Any]) -> int:
    """Price of a requirement in base units (v2 ``amount``, or v1 ``maxAmountRequired``)."""
    raw = req.get("amount")
    if raw is None:
        raw = req.get("maxAmountRequired")
    if raw is None:
        raw = ""
    text = str(raw) if isinstance(raw, int) and not isinstance(raw, bool) else raw
    if not isinstance(text, str) or not re.fullmatch(r"[0-9]{1,30}", text):
        raise ValueError(f"Invalid requirement amount: {raw!r}")
    return int(text)


def read_payment_required(response: Any) -> PaymentRequired | None:
    """Read the payment requirement from a 402 response: the PAYMENT-REQUIRED header, else the JSON body.

    Takes an ``httpx.Response`` whose body has been read (any non-streaming request).
    """
    header = response.headers.get("payment-required")
    if header:
        try:
            pr = unb64json(header)
            if isinstance(pr, dict) and isinstance(pr.get("accepts"), list):
                return cast(PaymentRequired, pr)
        except ValueError:
            pass  # fall through to the body
    try:
        body = response.json()
    except Exception:
        return None
    return cast(PaymentRequired, body) if isinstance(body, dict) and isinstance(body.get("accepts"), list) else None


def read_payment_response(response: _HasHeaders) -> SettleResponse | None:
    """Decode the PAYMENT-RESPONSE (or X-PAYMENT-RESPONSE) header of a paid response."""
    header = response.headers.get("payment-response")
    if header is None:
        header = response.headers.get("x-payment-response")
    if not header:
        return None
    try:
        v = unb64json(header)
    except ValueError:
        return None
    return cast(SettleResponse, v) if isinstance(v, dict) else None
