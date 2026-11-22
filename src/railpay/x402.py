"""x402 v2 wire types."""

from __future__ import annotations

from typing import Any, TypedDict


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
