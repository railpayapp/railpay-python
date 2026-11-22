"""Robinhood Chain, USDG and x402 constants. Same values as the TypeScript SDK (`src/constants.ts`)."""

from __future__ import annotations

from typing import Final, NamedTuple


class Eip712Name(NamedTuple):
    """Name and version of an EIP-712 domain."""

    name: str
    version: str


CHAIN_ID: Final = 4663
"""Robinhood Chain mainnet."""

NETWORK: Final = "eip155:4663"
"""CAIP-2 network id used in x402 v2 requirements."""

USDG: Final = "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"
"""USDG (Global Dollar) on Robinhood Chain."""

USDG_DECIMALS: Final = 6

USDG_EIP712: Final = Eip712Name(name="Global Dollar", version="1")
"""EIP-712 domain name and version of the USDG contract (EIP-3009 signatures)."""

DEFAULT_BASE_URL: Final = "https://dapp.railpay.app"
DEFAULT_RPC_URL: Final = "https://robinhood-rpc.publicnode.com"

DEFAULT_FACILITATORS: Final[tuple[str, ...]] = ("https://x402.t0ll.xyz", "https://x402.primer.systems")
"""
Outside x402 facilitators that settle USDG on Robinhood Chain and pay the gas, tried in order.
They are third-party services; RailPay does not run them.
"""

MIN_X402_AMOUNT: Final = 1000
"""Smallest x402 price in USDG base units (0.001 USDG), the floor the default facilitators settle."""

EXPLORER: Final = "https://robinhoodchain.blockscout.com"


def tx_url(tx_hash: str) -> str:
    """Explorer link for a transaction."""
    return f"{EXPLORER}/tx/{tx_hash}"


def address_url(address: str) -> str:
    """Explorer link for an address."""
    return f"{EXPLORER}/address/{address}"
