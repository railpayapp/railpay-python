"""Seller side: the USDG paywall (mirrors tests/seller.test.ts) with real EIP-3009 signatures."""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any

import anyio
import pytest
from eth_account.signers.local import LocalAccount

from railpay import NETWORK, USDG, Charge, MemoryStore, Paywall, PaywallResponse, b64json, unb64json

from ._support import (
    FAC_A,
    FAC_B,
    PRICE,
    RPC,
    TX1,
    TX2,
    URL,
    World,
    new_account,
    payment,
    transfer_receipt,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def payer() -> LocalAccount:
    return new_account()


@pytest.fixture
def pay_to() -> LocalAccount:
    return new_account()


class Served:
    def __init__(self, body: dict[str, Any] | None = None) -> None:
        self.count = 0
        self.body = body or {"ok": True}

    def __call__(self) -> PaywallResponse:
        self.count += 1
        return PaywallResponse.json(self.body)


def paywall(world: World, pay_to: LocalAccount, **options: Any) -> Paywall:
    return Paywall(
        pay_to.address,
        "0.01",
        description="Report",
        facilitators=[FAC_A, FAC_B],
        rpc_url=RPC,
        http_client=world.client(),
        receipt_wait=0,
        **options,
    )


def sig_headers(header: str | None) -> dict[str, str]:
    return {"PAYMENT-SIGNATURE": header} if header else {}


def error_of(res: PaywallResponse | Charge) -> str:
    return str(json.loads(res.body)["error"])


async def test_refuses_prices_below_0_001_usdg_and_bad_pay_to(pay_to: LocalAccount) -> None:
    with pytest.raises(ValueError, match="lowest price"):
        Paywall(pay_to.address, "0.0005")
    with pytest.raises(ValueError, match="lowest price"):
        Paywall(pay_to.address, 999)
    with pytest.raises(ValueError, match="0x address"):
        Paywall("nope", "0.01")
    with pytest.raises(ValueError, match="0x address"):
        Paywall("0x5fc5360D0400a0Fd4f2af552ADD042D716F1d16A", "0.01")  # USDG with a broken checksum
    with pytest.raises(ValueError, match="0x address"):
        Paywall(USDG[2:], "0.01")
    assert Paywall(pay_to.address.lower(), Decimal("0.01")).pay_to == pay_to.address
    assert Paywall(pay_to.address, 10_000).price == PRICE
    assert Paywall(pay_to.address, "0.001").price == 1_000


async def test_answers_402_with_the_requirement_in_payment_required(world: World, pay_to: LocalAccount) -> None:
    serve = Served()
    res = await paywall(world, pay_to).handle(URL, {}, serve)
    assert res.status == 402
    pr = unb64json(res.headers["PAYMENT-REQUIRED"])
    assert pr == json.loads(res.body)
    assert pr["x402Version"] == 2
    assert pr["error"] == "payment_required"
    assert pr["resource"] == {"url": URL, "description": "Report", "mimeType": "application/json"}
    assert pr["accepts"] == [
        {
            "scheme": "exact",
            "network": NETWORK,
            "amount": "10000",
            "asset": USDG,
            "payTo": pay_to.address,
            "maxTimeoutSeconds": 300,
            "extra": {"name": "Global Dollar", "version": "1"},
        }
    ]
    assert "PAYMENT-REQUIRED" in res.headers["access-control-expose-headers"]
    assert res.headers["content-type"] == "application/json"
    assert serve.count == 0
    assert world.rpc_calls == []


async def test_requirements_strip_the_query_and_default_port(world: World, pay_to: LocalAccount) -> None:
    pw = paywall(world, pay_to)
    assert pw.requirements("https://Seller.test:443/report?x=1")["resource"]["url"] == URL
    assert pw.requirements("http://seller.test:8080")["resource"]["url"] == "http://seller.test:8080/"
    with pytest.raises(ValueError, match="absolute"):
        pw.requirements("/report")


async def test_rejects_garbage_and_authorizations_that_do_not_match(
    world: World, payer: LocalAccount, pay_to: LocalAccount
) -> None:
    pw = paywall(world, pay_to)
    serve = Served()
    other = new_account()
    now = int(time.time())
    to = pay_to.address

    async def err(header: str) -> str:
        return error_of(await pw.handle(URL, sig_headers(header), serve))

    assert await err("not-base64!") == "invalid_payload"
    assert await err(b64json({"x402Version": 2, "payload": {"signature": "0x00"}})) == "invalid_payload"
    assert await err(payment(payer, to, to=other.address)) == "invalid_exact_evm_payload_recipient_mismatch"
    assert await err(payment(payer, to, value=9_999)) == "invalid_exact_evm_payload_authorization_value_mismatch"
    assert await err(payment(payer, to, value=10_001)) == "invalid_exact_evm_payload_authorization_value_mismatch"
    assert await err(payment(payer, to, valid_before=now + 2)) == "invalid_exact_evm_payload_authorization_valid_before"
    assert await err(payment(payer, to, valid_after=now + 60)) == "invalid_exact_evm_payload_authorization_valid_after"
    assert await err(payment(payer, to, signer=other)) == "invalid_exact_evm_payload_signature"
    assert await err(payment(payer, to, version=3)) == "invalid_x402_version"
    assert await err(payment(payer, to, version=True)) == "invalid_x402_version"
    base = {"scheme": "exact", "network": NETWORK, "amount": str(PRICE), "asset": USDG, "payTo": to}
    assert await err(payment(payer, to, accepted={**base, "scheme": "upto"})) == "invalid_scheme"
    assert await err(payment(payer, to, accepted={**base, "network": "eip155:8453"})) == "invalid_network"
    wrong_asset = {**base, "asset": "0x0000000000000000000000000000000000000001"}
    assert await err(payment(payer, to, accepted=wrong_asset)) == "invalid_payment_requirements"
    assert await err(payment(payer, to, accepted={**base, "asset": None})) == "invalid_payment_requirements"
    assert serve.count == 0
    assert world.settle_calls == []
    assert world.rpc_calls == []


async def test_rejects_a_payer_without_enough_usdg_and_a_nonce_used_on_chain(
    world: World, payer: LocalAccount, pay_to: LocalAccount
) -> None:
    pw = paywall(world, pay_to)
    serve = Served()
    world.balance = 9_999
    assert error_of(await pw.handle(URL, sig_headers(payment(payer, pay_to.address)), serve)) == "insufficient_funds"
    world.balance = 1_000_000
    world.used = True
    assert error_of(await pw.handle(URL, sig_headers(payment(payer, pay_to.address)), serve)) == "invalid_payload"
    assert world.settle_calls == []
    assert serve.count == 0


async def test_answers_unexpected_verify_error_when_the_rpc_is_down(
    world: World, payer: LocalAccount, pay_to: LocalAccount
) -> None:
    world.rpc_down = True
    res = await paywall(world, pay_to).handle(URL, sig_headers(payment(payer, pay_to.address)), Served())
    assert error_of(res) == "unexpected_verify_error"
    assert world.settle_calls == []


async def test_settles_through_the_first_facilitator_checks_the_chain_serves_once(
    world: World, payer: LocalAccount, pay_to: LocalAccount
) -> None:
    pw = paywall(world, pay_to)
    serve = Served({"content": "secret"})
    world.facilitator[FAC_A] = {"success": True, "transaction": TX1, "network": NETWORK}
    world.receipts[TX1] = transfer_receipt(payer.address, pay_to.address, PRICE)
    header = payment(payer, pay_to.address)

    res = await pw.handle(URL, sig_headers(header), serve)
    assert res.status == 200
    assert json.loads(res.body) == {"content": "secret"}
    assert unb64json(res.headers["PAYMENT-RESPONSE"]) == {
        "success": True,
        "transaction": TX1,
        "network": NETWORK,
        "payer": payer.address.lower(),
    }
    assert res.headers["X-PAYMENT-RESPONSE"] == res.headers["PAYMENT-RESPONSE"]
    assert "PAYMENT-RESPONSE" in res.headers["access-control-expose-headers"]
    assert len(world.settle_calls) == 1
    _, _, body = world.settle_calls[0]
    assert body["x402Version"] == 2
    assert body["paymentRequirements"]["amount"] == "10000"
    assert body["paymentPayload"]["payload"]["authorization"]["to"] == pay_to.address

    replay = await pw.handle(URL, sig_headers(header), serve)
    assert replay.status == 402
    assert error_of(replay) == "invalid_payload"
    assert serve.count == 1


async def test_sends_facilitators_the_same_request_as_the_typescript_seller(
    world: World, payer: LocalAccount, pay_to: LocalAccount
) -> None:
    pw = paywall(world, pay_to)
    world.facilitator[FAC_A] = {"success": True, "transaction": TX1, "network": NETWORK}
    world.receipts[TX1] = transfer_receipt(payer.address, pay_to.address, PRICE)
    header = payment(payer, pay_to.address)
    sent = unb64json(header)["payload"]
    await pw.handle(URL, sig_headers(header), Served())

    url, headers, body = world.settle_calls[0]
    assert url == f"{FAC_A}/settle"
    assert headers["content-type"] == "application/json"
    assert headers["accept"] == "application/json"
    assert list(body) == ["x402Version", "paymentPayload", "paymentRequirements"]
    requirement = pw.requirements(URL)["accepts"][0]
    assert body["paymentRequirements"] == requirement
    assert body["paymentPayload"] == {
        "x402Version": 2,
        "resource": {"url": URL, "description": "Report", "mimeType": "application/json"},
        "accepted": requirement,
        "payload": {
            "signature": sent["signature"],
            "authorization": {
                "from": payer.address,
                "to": pay_to.address,
                "value": "10000",
                "validAfter": sent["authorization"]["validAfter"],
                "validBefore": sent["authorization"]["validBefore"],
                "nonce": sent["authorization"]["nonce"].lower(),
            },
        },
    }


async def test_accepts_the_x_payment_header(world: World, payer: LocalAccount, pay_to: LocalAccount) -> None:
    world.facilitator[FAC_A] = {"success": True, "transaction": TX1, "network": NETWORK}
    world.receipts[TX1] = transfer_receipt(payer.address, pay_to.address, PRICE)
    res = await paywall(world, pay_to).handle(URL, {"X-Payment": payment(payer, pay_to.address)}, Served())
    assert res.status == 200


async def test_fails_over_to_the_next_facilitator(world: World, payer: LocalAccount, pay_to: LocalAccount) -> None:
    world.facilitator[FAC_A] = "unreachable"
    world.facilitator[FAC_B] = {"success": True, "transaction": TX2, "network": NETWORK}
    world.receipts[TX2] = transfer_receipt(payer.address, pay_to.address, PRICE)
    res = await paywall(world, pay_to).handle(URL, sig_headers(payment(payer, pay_to.address)), Served())
    assert res.status == 200
    assert [c[0] for c in world.settle_calls] == [f"{FAC_A}/settle", f"{FAC_B}/settle"]


async def test_takes_an_authorization_that_already_landed_as_settled(
    world: World, payer: LocalAccount, pay_to: LocalAccount
) -> None:
    def timed_out_but_landed(body: dict[str, Any]) -> dict[str, Any]:
        world.used = True
        world.logs = [{"transactionHash": TX2.upper().replace("0X", "0x")}]
        return {"success": False, "errorReason": "timeout"}

    world.facilitator[FAC_A] = timed_out_but_landed
    world.facilitator[FAC_B] = {"success": True, "transaction": TX1, "network": NETWORK}
    world.receipts[TX2] = transfer_receipt(payer.address, pay_to.address, PRICE)
    res = await paywall(world, pay_to).handle(URL, sig_headers(payment(payer, pay_to.address)), Served())
    assert res.status == 200
    assert unb64json(res.headers["PAYMENT-RESPONSE"])["transaction"] == TX2
    assert len(world.settle_calls) == 1  # the second facilitator is not asked


async def test_answers_unexpected_settle_error_when_no_facilitator_settles_and_releases_the_claim(
    world: World, payer: LocalAccount, pay_to: LocalAccount
) -> None:
    pw = paywall(world, pay_to)
    serve = Served()
    world.facilitator[FAC_A] = {"success": False, "errorReason": "gas_day_budget_exhausted", "transaction": ""}
    world.facilitator[FAC_B] = "unreachable"
    header = payment(payer, pay_to.address)
    res = await pw.handle(URL, sig_headers(header), serve)
    assert res.status == 402
    assert error_of(res) == "unexpected_settle_error"

    world.facilitator[FAC_A] = {"success": True, "transaction": TX1, "network": NETWORK}
    world.receipts[TX1] = transfer_receipt(payer.address, pay_to.address, PRICE)
    retry = await pw.handle(URL, sig_headers(header), serve)
    assert retry.status == 200
    assert serve.count == 1


async def test_does_not_trust_a_facilitator_success_without_the_on_chain_transfer(
    world: World, payer: LocalAccount, pay_to: LocalAccount
) -> None:
    pw = paywall(world, pay_to)
    serve = Served()
    world.facilitator[FAC_A] = {"success": True, "transaction": TX1, "network": NETWORK}
    header = payment(payer, pay_to.address)

    first = await pw.handle(URL, sig_headers(header), serve)
    assert first.status == 402
    assert error_of(first) == "settlement_pending"
    receipt = unb64json(first.headers["PAYMENT-RESPONSE"])
    assert receipt == {
        "success": False,
        "errorReason": "settlement_pending",
        "transaction": TX1,
        "network": NETWORK,
        "payer": payer.address.lower(),
    }
    assert serve.count == 0

    world.receipts[TX1] = transfer_receipt(payer.address, pay_to.address, PRICE)
    second = await pw.handle(URL, sig_headers(header), serve)
    assert second.status == 200
    third = await pw.handle(URL, sig_headers(header), serve)
    assert error_of(third) == "invalid_payload"
    assert serve.count == 1
    assert len(world.settle_calls) == 1


async def test_a_pending_settlement_is_only_served_on_the_same_resource(
    world: World, payer: LocalAccount, pay_to: LocalAccount
) -> None:
    pw = paywall(world, pay_to)
    world.facilitator[FAC_A] = {"success": True, "transaction": TX1, "network": NETWORK}
    header = payment(payer, pay_to.address)
    assert error_of(await pw.handle(URL, sig_headers(header), Served())) == "settlement_pending"
    world.receipts[TX1] = transfer_receipt(payer.address, pay_to.address, PRICE)
    other = await pw.handle("https://seller.test/other", sig_headers(header), Served())
    assert error_of(other) == "invalid_payload"
    assert (await pw.handle(URL, sig_headers(header), Served())).status == 200


async def test_keeps_a_facilitator_settlement_pending_as_pending(
    world: World, payer: LocalAccount, pay_to: LocalAccount
) -> None:
    world.facilitator[FAC_A] = {"success": False, "errorReason": "settlement_pending", "transaction": TX1}
    charge = await paywall(world, pay_to).charge(URL, sig_headers(payment(payer, pay_to.address)))
    assert not charge.paid
    assert charge.error == "settlement_pending"
    assert charge.transaction == TX1
    assert len(world.settle_calls) == 1


@pytest.mark.parametrize(
    "receipt",
    [
        pytest.param(lambda p, t: transfer_receipt(p, t, PRICE - 1), id="different amount"),
        pytest.param(lambda p, t: transfer_receipt(p, new_account().address, PRICE), id="different recipient"),
        pytest.param(lambda p, t: transfer_receipt(new_account().address, t, PRICE), id="different payer"),
        pytest.param(lambda p, t: transfer_receipt(p, t, PRICE, status="0x0"), id="reverted"),
        pytest.param(lambda p, t: {"status": "0x1", "logs": []}, id="no transfer"),
    ],
)
async def test_does_not_serve_when_the_receipt_holds_a_different_transfer(
    world: World, payer: LocalAccount, pay_to: LocalAccount, receipt: Any
) -> None:
    serve = Served()
    world.facilitator[FAC_A] = {"success": True, "transaction": TX1, "network": NETWORK}
    world.receipts[TX1] = receipt(payer.address, pay_to.address)
    res = await paywall(world, pay_to).handle(URL, sig_headers(payment(payer, pay_to.address)), serve)
    assert error_of(res) == "settlement_pending"
    assert serve.count == 0


async def test_settles_a_payment_once_when_it_arrives_twice_at_the_same_time(
    world: World, payer: LocalAccount, pay_to: LocalAccount
) -> None:
    pw = paywall(world, pay_to)
    world.facilitator[FAC_A] = {"success": True, "transaction": TX1, "network": NETWORK}
    world.receipts[TX1] = transfer_receipt(payer.address, pay_to.address, PRICE)
    header = payment(payer, pay_to.address)
    results: list[Charge] = []

    async def attempt() -> None:
        results.append(await pw.charge(URL, sig_headers(header)))

    async with anyio.create_task_group() as tg:
        tg.start_soon(attempt)
        tg.start_soon(attempt)
    assert sorted(c.paid for c in results) == [False, True]
    assert next(c for c in results if not c.paid).error == "invalid_payload"
    assert len(world.settle_calls) == 1


async def test_charge_reports_the_outcome(world: World, payer: LocalAccount, pay_to: LocalAccount) -> None:
    world.facilitator[FAC_A] = {"success": True, "transaction": TX1, "network": NETWORK}
    world.receipts[TX1] = transfer_receipt(payer.address, pay_to.address, PRICE)
    async with paywall(world, pay_to) as pw:
        unpaid = await pw.charge(URL, {})
        assert (unpaid.paid, unpaid.status, unpaid.error) == (False, 402, "payment_required")
        charge = await pw.charge(URL, sig_headers(payment(payer, pay_to.address)))
    assert charge.paid
    assert charge.status == 200
    assert charge.body == b""
    assert charge.transaction == TX1
    assert charge.payer == payer.address.lower()
    assert set(charge.headers) == {"PAYMENT-RESPONSE", "X-PAYMENT-RESPONSE", "access-control-expose-headers"}


async def test_handle_replaces_payment_headers_from_serve(
    world: World, payer: LocalAccount, pay_to: LocalAccount
) -> None:
    world.facilitator[FAC_A] = {"success": True, "transaction": TX1, "network": NETWORK}
    world.receipts[TX1] = transfer_receipt(payer.address, pay_to.address, PRICE)

    async def serve() -> PaywallResponse:
        return PaywallResponse(201, {"payment-response": "forged", "x-extra": "1"}, b"made")

    res = await paywall(world, pay_to).handle(URL, sig_headers(payment(payer, pay_to.address)), serve)
    assert (res.status, res.body, res.headers["x-extra"]) == (201, b"made", "1")
    assert "payment-response" not in res.headers
    assert unb64json(res.headers["PAYMENT-RESPONSE"])["transaction"] == TX1


async def test_memory_store_claim_is_insert_if_absent() -> None:
    store = MemoryStore()
    assert await store.claim("k", {"status": "settling", "resource": URL})
    assert not await store.claim("k", {"status": "settled", "resource": URL})
    assert await store.get("k") == {"status": "settling", "resource": URL}
    await store.set("k", {"status": "pending", "resource": URL, "tx": TX1})
    stored = await store.get("k")
    assert stored is not None
    assert stored.get("tx") == TX1
    await store.delete("k")
    assert await store.get("k") is None
    assert await store.claim("k", {"status": "settling", "resource": URL})
