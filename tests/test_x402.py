"""x402 helpers: reading 402 requirements and payment receipts, and which requirements an agent can pay."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from railpay import NETWORK, USDG, b64json, is_railpay_payable, read_payment_required, read_payment_response
from railpay import requirement_amount as amount_of

TX = "0x" + "ab" * 32


def required(**extra: Any) -> dict[str, Any]:
    accept = {
        "scheme": "exact",
        "network": NETWORK,
        "amount": "10000",
        "asset": USDG,
        "payTo": "0x2222222222222222222222222222222222222222",
        "maxTimeoutSeconds": 300,
        "extra": {"name": "Global Dollar", "version": "1", **extra},
    }
    return {"x402Version": 2, "error": "payment_required", "accepts": [accept]}


def test_reads_the_requirement_from_the_payment_required_header() -> None:
    pr = required()
    res = httpx.Response(402, text="not json", headers={"PAYMENT-REQUIRED": b64json(pr)})
    assert read_payment_required(res) == pr


def test_reads_the_requirement_from_the_body_when_the_header_is_missing() -> None:
    pr = read_payment_required(httpx.Response(402, json=required()))
    assert pr is not None
    assert pr["accepts"][0].get("amount") == "10000"


def test_falls_back_to_the_body_when_the_header_is_broken() -> None:
    assert read_payment_required(httpx.Response(402, json=required(), headers={"PAYMENT-REQUIRED": "%%%"}))
    no_accepts = b64json({"x402Version": 2})
    assert read_payment_required(httpx.Response(402, json=required(), headers={"PAYMENT-REQUIRED": no_accepts}))
    assert read_payment_required(httpx.Response(402, text="nope")) is None
    assert read_payment_required(httpx.Response(402, json={"accepts": "no"})) is None


def test_payable_requirements() -> None:
    req = required()["accepts"][0]
    assert is_railpay_payable(req)
    assert is_railpay_payable({**req, "asset": USDG.lower()})
    assert is_railpay_payable(required(assetTransferMethod="eip3009")["accepts"][0])
    assert is_railpay_payable(required(assetTransferMethod="")["accepts"][0])
    assert not is_railpay_payable(required(assetTransferMethod="permit2")["accepts"][0])
    assert not is_railpay_payable({**req, "scheme": "upto"})
    assert not is_railpay_payable({**req, "network": "eip155:8453"})
    assert not is_railpay_payable({**req, "asset": "0x0000000000000000000000000000000000000001"})
    assert not is_railpay_payable({**req, "asset": None})
    assert not is_railpay_payable(None)
    assert not is_railpay_payable("exact")


def test_requirement_amount() -> None:
    req = required()["accepts"][0]
    assert amount_of(req) == 10_000
    assert amount_of({**req, "amount": 2_500}) == 2_500
    v1 = {k: v for k, v in req.items() if k != "amount"}
    assert amount_of({**v1, "maxAmountRequired": "2500"}) == 2_500
    for bad in ("1.5", "-1", "", "1" * 31, "0x10", None, True):
        with pytest.raises(ValueError, match="Invalid requirement amount"):
            amount_of({**req, "amount": bad} if bad is not None else v1)


def test_read_payment_response() -> None:
    receipt = {"success": True, "transaction": TX, "network": NETWORK}
    assert read_payment_response(httpx.Response(200, headers={"PAYMENT-RESPONSE": b64json(receipt)})) == receipt
    assert read_payment_response(httpx.Response(200, headers={"X-PAYMENT-RESPONSE": b64json(receipt)})) == receipt
    assert read_payment_response(httpx.Response(200)) is None
    assert read_payment_response(httpx.Response(200, headers={"PAYMENT-RESPONSE": "!!"})) is None
    assert read_payment_response(httpx.Response(200, headers={"PAYMENT-RESPONSE": b64json([1])})) is None
