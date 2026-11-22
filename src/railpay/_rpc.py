"""Robinhood Chain reads for the paywall: plain JSON-RPC over httpx, no web3.py."""

from __future__ import annotations

import json
import re
from typing import Any, Final, cast

import httpx

from .constants import USDG

# keccak256 selectors and topics (checked against eth_utils.keccak in tests/test_rpc.py)
BALANCE_OF: Final = "0x70a08231"  # balanceOf(address)
AUTHORIZATION_STATE: Final = "0xe94a0102"  # authorizationState(address,bytes32)
TRANSFER_TOPIC: Final = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
AUTHORIZATION_USED_TOPIC: Final = "0x98de503528ee59b575ef0c0a2576a82497bfc029a5685b209e9ec333479b10a5"

_HASH = re.compile(r"0x[0-9a-fA-F]{64}")


class RpcError(Exception):
    pass


def pad32(hex_value: str) -> str:
    """Left-pad a 0x hex value (an address) to 32 bytes, lower case: the form of indexed log topics."""
    return "0x" + hex_value.lower().removeprefix("0x").rjust(64, "0")


def _word(result: object, method: str) -> int:
    if not isinstance(result, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64,}", result):
        raise RpcError(f"{method}: unexpected result {result!r}")
    return int(result[2:66], 16)


class Rpc:
    def __init__(self, http: httpx.AsyncClient, url: str) -> None:
        self._http = http
        self.url = url

    async def call(self, method: str, params: list[Any]) -> Any:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, separators=(",", ":"))
        res = await self._http.post(self.url, content=body, headers={"content-type": "application/json"})
        j = res.json()
        if not isinstance(j, dict):
            raise RpcError(f"{method}: not a JSON-RPC answer")
        if j.get("error"):
            err = j["error"]
            msg = err.get("message") if isinstance(err, dict) else None
            raise RpcError(f"{method}: {msg or 'rpc error'}")
        return j.get("result")

    async def _eth_call(self, data: str) -> Any:
        return await self.call("eth_call", [{"to": USDG, "data": data}, "latest"])

    async def balance_of(self, owner: str) -> int:
        return _word(await self._eth_call(BALANCE_OF + pad32(owner)[2:]), "balanceOf")

    async def authorization_used(self, authorizer: str, nonce: str) -> bool:
        data = AUTHORIZATION_STATE + pad32(authorizer)[2:] + nonce[2:].lower()
        v = _word(await self._eth_call(data), "authorizationState")
        if v not in (0, 1):
            raise RpcError(f"authorizationState: not a bool: {v}")
        return v == 1

    async def block_number(self) -> int:
        return int(cast(str, await self.call("eth_blockNumber", [])), 16)

    async def chain_id(self) -> int:
        return int(cast(str, await self.call("eth_chainId", [])), 16)

    async def receipt(self, tx_hash: str) -> dict[str, Any] | None:
        r = await self.call("eth_getTransactionReceipt", [tx_hash])
        return r if isinstance(r, dict) else None

    async def authorization_tx(self, authorizer: str, nonce: str, from_block: int) -> str | None:
        """Hash of the transaction that used an EIP-3009 authorization (its AuthorizationUsed log)."""
        logs = await self.call(
            "eth_getLogs",
            [
                {
                    "address": USDG,
                    "topics": [AUTHORIZATION_USED_TOPIC, pad32(authorizer), nonce],
                    "fromBlock": hex(max(0, from_block - 5)),
                    "toBlock": "latest",
                }
            ],
        )
        if isinstance(logs, list) and logs and isinstance(logs[0], dict):
            h = logs[0].get("transactionHash")
            if isinstance(h, str) and _HASH.fullmatch(h):
                return h.lower()
        return None
