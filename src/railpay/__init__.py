"""RailPay: x402 v2 agent payments and a USDG paywall kit on Robinhood Chain."""

from importlib.metadata import PackageNotFoundError, version

from .agent import PaymentAuth, PaymentEvent, paid_client
from .client import Agent, AsyncRailPay, Policy, RailPay, RailPayError, SendResult, SignedPayment
from .constants import (
    CHAIN_ID,
    DEFAULT_BASE_URL,
    DEFAULT_FACILITATORS,
    DEFAULT_RPC_URL,
    EXPLORER,
    MIN_X402_AMOUNT,
    NETWORK,
    USDG,
    USDG_DECIMALS,
    USDG_EIP712,
    address_url,
    tx_url,
)
from .encoding import b64json, from_units, to_units, unb64json
from .seller import Charge, MemoryStore, PaymentStore, Paywall, PaywallResponse, StoredPayment
from .x402 import (
    AuthorizationFields,
    PaymentPayload,
    PaymentRequired,
    PaymentRequirements,
    ResourceInfo,
    SettleResponse,
    is_railpay_payable,
    read_payment_required,
    read_payment_response,
    requirement_amount,
)

try:
    __version__ = version("railpay")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = [
    "CHAIN_ID",
    "DEFAULT_BASE_URL",
    "DEFAULT_FACILITATORS",
    "DEFAULT_RPC_URL",
    "EXPLORER",
    "MIN_X402_AMOUNT",
    "NETWORK",
    "USDG",
    "USDG_DECIMALS",
    "USDG_EIP712",
    "Agent",
    "AsyncRailPay",
    "AuthorizationFields",
    "Charge",
    "MemoryStore",
    "PaymentAuth",
    "PaymentEvent",
    "PaymentPayload",
    "PaymentRequired",
    "PaymentRequirements",
    "PaymentStore",
    "Paywall",
    "PaywallResponse",
    "Policy",
    "RailPay",
    "RailPayError",
    "ResourceInfo",
    "SendResult",
    "SettleResponse",
    "SignedPayment",
    "StoredPayment",
    "__version__",
    "address_url",
    "b64json",
    "from_units",
    "is_railpay_payable",
    "paid_client",
    "read_payment_required",
    "read_payment_response",
    "requirement_amount",
    "to_units",
    "tx_url",
    "unb64json",
]
