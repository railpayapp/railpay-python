"""Shared test helpers: throwaway EIP-3009 signers and a mocked Robinhood Chain RPC + facilitators."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from eth_account import Account
from eth_account.signers.local import LocalAccount

from railpay import CHAIN_ID, NETWORK, USDG, Paywall, b64json

RPC = "https://rpc.test"
FAC_A = "https://facilitator-a.test"
FAC_B = "https://facilitator-b.test"
URL = "https://seller.test/report"
PRICE = 10_000  # 0.01 USDG

BALANCE_OF = "0x70a08231"
AUTH_STATE = "0xe94a0102"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

TX1 = "0x" + "11" * 32
TX2 = "0x" + "22" * 32

TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ],
}
DOMAIN = {"name": "Global Dollar", "version": "1", "chainId": CHAIN_ID, "verifyingContract": USDG}


def new_account() -> LocalAccount:
    """A fresh random key, generated in the test and never stored."""
    account: LocalAccount = Account.create()
    return account


def pad(address: str) -> str:
    return "0x" + address.lower()[2:].rjust(64, "0")


def word(value: int) -> str:
    return "0x" + format(value, "064x")


def sign_authorization(signer: LocalAccount, message: dict[str, Any]) -> str:
    full = {
        "types": TYPES,
        "primaryType": "TransferWithAuthorization",
        "domain": DOMAIN,
        "message": {**message, "nonce": bytes.fromhex(str(message["nonce"])[2:])},
    }
    return "0x" + bytes(signer.sign_typed_data(full_message=full).signature).hex()


def authorization(
    payer: LocalAccount,
    pay_to: str,
    *,
    value: int = PRICE,
    valid_after: int | None = None,
    valid_before: int | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    t = int(time.time())
    return {
        "from": payer.address,
        "to": pay_to,
        "value": value,
        "validAfter": t - 60 if valid_after is None else valid_after,
        "validBefore": t + 240 if valid_before is None else valid_before,
        "nonce": nonce or "0x" + os.urandom(32).hex(),
    }


def payment(
    payer: LocalAccount,
    pay_to: str,
    *,
    signer: LocalAccount | None = None,
    to: str | None = None,
    value: int = PRICE,
    valid_after: int | None = None,
    valid_before: int | None = None,
    nonce: str | None = None,
    accepted: dict[str, Any] | None = None,
    version: Any = 2,
) -> str:
    """A PAYMENT-SIGNATURE header with a real EIP-3009 signature, like the TypeScript test helper."""
    message = authorization(
        payer, to or pay_to, value=value, valid_after=valid_after, valid_before=valid_before, nonce=nonce
    )
    signature = sign_authorization(signer or payer, message)
    auth = {k: str(v) if isinstance(v, int) else v for k, v in message.items()}
    return b64json(
        {
            "x402Version": version,
            "accepted": accepted
            if accepted is not None
            else {"scheme": "exact", "network": NETWORK, "amount": str(PRICE), "asset": USDG, "payTo": pay_to},
            "payload": {"signature": signature, "authorization": auth},
        }
    )


def transfer_receipt(frm: str, to: str, value: int, status: str = "0x1") -> dict[str, Any]:
    return {"status": status, "logs": [{"address": USDG, "topics": [TRANSFER, pad(frm), pad(to)], "data": word(value)}]}


FacAnswer = dict[str, Any] | str | Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class World:
    """Mocked chain state and facilitators behind one httpx.MockTransport."""

    balance: int = 1_000_000
    used: bool = False
    rpc_down: bool = False
    receipts: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)
    facilitator: dict[str, FacAnswer] = field(default_factory=dict)
    settle_calls: list[tuple[str, httpx.Headers, dict[str, Any]]] = field(default_factory=list)
    rpc_calls: list[str] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        body = json.loads(request.content or b"null")
        if url == RPC:
            method, params = body["method"], body["params"]
            self.rpc_calls.append(method)
            if self.rpc_down:
                error = {"code": -32000, "message": "down"}
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": error})
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": self._rpc(method, params)})
        for base in (FAC_A, FAC_B):
            if url == f"{base}/settle":
                self.settle_calls.append((url, request.headers, body))
                answer = self.facilitator.get(base, "unreachable")
                if answer == "unreachable":
                    raise httpx.ConnectError("fetch failed", request=request)
                if callable(answer):
                    answer = answer(body)
                return httpx.Response(200, json=answer)
        raise AssertionError(f"unmocked fetch {url}")

    def _rpc(self, method: str, params: list[Any]) -> Any:
        if method == "eth_blockNumber":
            return "0x64"
        if method == "eth_chainId":
            return hex(CHAIN_ID)
        if method == "eth_getTransactionReceipt":
            return self.receipts.get(params[0])
        if method == "eth_getLogs":
            return self.logs
        if method == "eth_call":
            data = params[0]["data"]
            assert params[0]["to"] == USDG
            if data.startswith(BALANCE_OF):
                return word(self.balance)
            if data.startswith(AUTH_STATE):
                return word(1 if self.used else 0)
        raise AssertionError(f"unmocked rpc {method}")

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def seller_paywall(world: World, pay_to: LocalAccount) -> Paywall:
    """A 0.01 USDG paywall that settles through FAC_A on the mocked chain."""
    return Paywall(
        pay_to.address,
        "0.01",
        description="Report",
        facilitators=[FAC_A],
        rpc_url=RPC,
        http_client=world.client(),
        receipt_wait=0,
    )


def settle_ok(world: World, payer: str, pay_to: str) -> None:
    """FAC_A settles as TX1, and TX1's receipt holds the exact transfer."""
    world.facilitator[FAC_A] = {"success": True, "transaction": TX1, "network": NETWORK}
    world.receipts[TX1] = transfer_receipt(payer, pay_to, PRICE)
