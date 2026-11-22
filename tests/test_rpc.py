"""The Robinhood Chain JSON-RPC reader behind the paywall."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from eth_utils.address import to_checksum_address
from eth_utils.crypto import keccak

from railpay import USDG
from railpay._rpc import (
    AUTHORIZATION_STATE,
    AUTHORIZATION_USED_TOPIC,
    BALANCE_OF,
    TRANSFER_TOPIC,
    Rpc,
    RpcError,
    pad32,
)

pytestmark = pytest.mark.anyio

OWNER = "0x46C019488d484ff13279C55FDD2668c7032b9509"
NONCE = "0x" + "AB" * 32


def test_selectors_and_topics_are_keccak_of_their_signatures() -> None:
    assert "0x" + keccak(text="balanceOf(address)")[:4].hex() == BALANCE_OF
    assert "0x" + keccak(text="authorizationState(address,bytes32)")[:4].hex() == AUTHORIZATION_STATE
    assert "0x" + keccak(text="Transfer(address,address,uint256)").hex() == TRANSFER_TOPIC
    assert "0x" + keccak(text="AuthorizationUsed(address,bytes32)").hex() == AUTHORIZATION_USED_TOPIC


def test_usdg_address_has_a_valid_checksum() -> None:
    assert to_checksum_address(USDG) == USDG


def test_pad32() -> None:
    assert pad32(OWNER) == "0x000000000000000000000000" + OWNER[2:].lower()
    assert len(pad32(OWNER)) == 66


def rpc(answer: Any, calls: list[dict[str, Any]] | None = None) -> Rpc:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["content-type"] == "application/json"
        if calls is not None:
            calls.append(body)
        result = answer(body) if callable(answer) else answer
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], **result})

    return Rpc(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "https://rpc.test")


async def test_reads_the_usdg_balance() -> None:
    calls: list[dict[str, Any]] = []
    assert await rpc({"result": "0x" + format(1_500_000, "064x")}, calls).balance_of(OWNER) == 1_500_000
    (call,) = calls
    assert call["method"] == "eth_call"
    assert call["params"] == [{"to": USDG, "data": BALANCE_OF + pad32(OWNER)[2:]}, "latest"]


async def test_reads_the_authorization_state() -> None:
    calls: list[dict[str, Any]] = []
    assert await rpc({"result": "0x" + "0" * 63 + "1"}, calls).authorization_used(OWNER, NONCE) is True
    assert calls[0]["params"][0]["data"] == AUTHORIZATION_STATE + pad32(OWNER)[2:] + NONCE[2:].lower()
    assert await rpc({"result": "0x" + "0" * 64}).authorization_used(OWNER, NONCE) is False
    with pytest.raises(RpcError, match="not a bool"):
        await rpc({"result": "0x" + "0" * 63 + "2"}).authorization_used(OWNER, NONCE)


async def test_refuses_errors_and_malformed_results() -> None:
    with pytest.raises(RpcError, match="execution reverted"):
        await rpc({"error": {"code": 3, "message": "execution reverted"}}).balance_of(OWNER)
    with pytest.raises(RpcError, match="rpc error"):
        await rpc({"error": "boom"}).balance_of(OWNER)
    for bad in ("0x", "0x1", None, 7):
        with pytest.raises(RpcError, match="unexpected result"):
            await rpc({"result": bad}).balance_of(OWNER)


async def test_chain_id_block_number_and_receipt() -> None:
    results: dict[str, dict[str, Any]] = {
        "eth_chainId": {"result": "0x1237"},
        "eth_blockNumber": {"result": "0x64"},
        "eth_getTransactionReceipt": {"result": None},
    }

    def answer(body: dict[str, Any]) -> dict[str, Any]:
        return results[body["method"]]

    r = rpc(answer)
    assert await r.chain_id() == 4663
    assert await r.block_number() == 100
    assert await r.receipt("0x" + "11" * 32) is None


async def test_finds_the_transaction_that_used_an_authorization() -> None:
    calls: list[dict[str, Any]] = []
    tx = "0x" + "AA" * 32
    assert await rpc({"result": [{"transactionHash": tx}]}, calls).authorization_tx(OWNER, NONCE, 100) == tx.lower()
    (flt,) = calls[0]["params"]
    assert flt == {
        "address": USDG,
        "topics": [AUTHORIZATION_USED_TOPIC, pad32(OWNER), NONCE],
        "fromBlock": hex(95),
        "toBlock": "latest",
    }
    calls.clear()
    assert await rpc({"result": []}, calls).authorization_tx(OWNER, NONCE, 2) is None
    assert calls[0]["params"][0]["fromBlock"] == "0x0"
    assert await rpc({"result": [{"transactionHash": "0x12"}]}).authorization_tx(OWNER, NONCE, 9) is None
