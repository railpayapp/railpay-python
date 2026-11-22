"""RailPay: x402 v2 agent payments on Robinhood Chain."""

from importlib.metadata import PackageNotFoundError, version

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
    "__version__",
    "address_url",
    "b64json",
    "from_units",
    "to_units",
    "tx_url",
    "unb64json",
]
