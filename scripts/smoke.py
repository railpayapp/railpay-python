"""Read-only check against the live RailPay network and Robinhood Chain. Spends nothing, needs no key.

Usage: uv run python scripts/smoke.py [base_url]
"""

from __future__ import annotations

import os
import sys

import anyio
import httpx

from railpay import (
    CHAIN_ID,
    DEFAULT_BASE_URL,
    DEFAULT_RPC_URL,
    USDG,
    RailPay,
    from_units,
    is_railpay_payable,
    read_payment_required,
    requirement_amount,
)
from railpay._rpc import Rpc


def check(ok: bool, message: str) -> None:
    if not ok:
        sys.exit(f"smoke failed: {message}")


def api(base: str) -> None:
    with RailPay(base_url=base) as client:
        config = client.config()
    keys = ("network", "chain_id", "facilitators", "relayer_status")
    print("config:", {k: config.get(k) for k in keys})

    with httpx.Client(timeout=30) as http:
        stats = http.get(f"{base}/v1/stats")
        check(stats.status_code == 401, f"expected /v1/stats to need an account key, got {stats.status_code}")
        print("stats: account key required (401 without one)")

        res = http.get(f"{base}/x402/time")
    check(res.status_code == 402, f"expected 402 from /x402/time, got {res.status_code}")
    pr = read_payment_required(res)
    check(pr is not None, "the 402 has no readable payment requirement")
    assert pr is not None
    req = next((r for r in pr["accepts"] if is_railpay_payable(r)), None)
    check(req is not None, "no RailPay-payable requirement in the 402")
    assert req is not None
    print(f"402 ok: {from_units(requirement_amount(req))} USDG to {req['payTo']} on {req['network']}")


async def chain(rpc_url: str) -> None:
    async with httpx.AsyncClient(timeout=30) as http:
        rpc = Rpc(http, rpc_url)
        chain_id = await rpc.chain_id()
        check(chain_id == CHAIN_ID, f"expected chain id {CHAIN_ID}, got {chain_id}")
        block = await rpc.block_number()
        code = await rpc.call("eth_getCode", [USDG, "latest"])
        check(isinstance(code, str) and len(code) > 2, "no contract code at the USDG address")
        unused = await rpc.authorization_used("0x000000000000000000000000000000000000dEaD", "0x" + os.urandom(32).hex())
        check(unused is False, "a random EIP-3009 nonce reads as used")
    print(f"rpc ok: chain id {chain_id}, block {block}, USDG contract deployed, authorizationState reads")


def main() -> None:
    base = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE_URL).rstrip("/")
    api(base)
    anyio.run(chain, os.environ.get("RAILPAY_RPC_URL", DEFAULT_RPC_URL))
    print("smoke ok")


if __name__ == "__main__":
    main()
