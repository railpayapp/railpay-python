"""EIP-712 / EIP-3009: the paywall recovers signatures exactly like viem (the TypeScript SDK) produces them."""

from __future__ import annotations

from eth_account.messages import encode_typed_data
from eth_utils.crypto import keccak

from railpay import USDG
from railpay.seller import _Authorization, _recover

from ._support import DOMAIN, TYPES, new_account, sign_authorization


def test_recovers_a_signature_made_by_viem() -> None:
    """Cross-implementation vector: signed with viem 2.58.2 (TypeScript SDK) by a throwaway key that was not kept."""
    a = _Authorization(
        from_="0x46C019488d484ff13279C55FDD2668c7032b9509",
        to="0x2222222222222222222222222222222222222222",
        value=10_000,
        valid_after=1_700_000_000,
        valid_before=1_700_000_300,
        nonce="0x" + "ab" * 32,
    )
    signature = (
        "0x67e403d463711cd4091dbc392e34bfd3edb6908172fb37d5756ef1fe9fc83e14"
        "49590dbb78ad22fe7c50a868040359079438d2b1edc563fd373557c1556381291b"
    )
    assert _recover(a, signature) == a.from_
    assert _recover(_Authorization(**{**a.__dict__, "value": 10_001}), signature) != a.from_
    assert _recover(a, "0x" + "00" * 65) is None


def test_eip712_digest_matches_viem_and_manual_encoding() -> None:
    message = {
        "from": "0x46C019488d484ff13279C55FDD2668c7032b9509",
        "to": "0x2222222222222222222222222222222222222222",
        "value": 10_000,
        "validAfter": 1_700_000_000,
        "validBefore": 1_700_000_300,
        "nonce": bytes.fromhex("ab" * 32),
    }
    signable = encode_typed_data(
        full_message={"types": TYPES, "primaryType": "TransferWithAuthorization", "domain": DOMAIN, "message": message}
    )
    digest = keccak(b"\x19\x01" + signable.header + signable.body)
    assert "0x" + digest.hex() == "0x33e2c01dc5a89f06f882d452efcc895242dbedc97823c4292d2fbc9d7fecee65"  # viem

    def u256(v: int) -> bytes:
        return v.to_bytes(32, "big")

    def addr(a: str) -> bytes:
        return bytes.fromhex(a[2:]).rjust(32, b"\0")

    domain_type = keccak(text="EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)")
    separator = keccak(domain_type + keccak(text="Global Dollar") + keccak(text="1") + u256(4663) + addr(USDG))
    struct_type = keccak(
        text="TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,"
        "uint256 validBefore,bytes32 nonce)"
    )
    struct = keccak(
        struct_type
        + addr(str(message["from"]))
        + addr(str(message["to"]))
        + u256(10_000)
        + u256(1_700_000_000)
        + u256(1_700_000_300)
        + bytes.fromhex("ab" * 32)
    )
    assert keccak(b"\x19\x01" + separator + struct) == digest


def test_recovers_a_signature_made_with_eth_account() -> None:
    signer = new_account()
    a = _Authorization(
        from_=signer.address,
        to="0x2222222222222222222222222222222222222222",
        value=1_000,
        valid_after=0,
        valid_before=2**40,
        nonce="0x" + "01" * 32,
    )
    message = {
        "from": a.from_,
        "to": a.to,
        "value": a.value,
        "validAfter": a.valid_after,
        "validBefore": a.valid_before,
        "nonce": a.nonce,
    }
    assert _recover(a, sign_authorization(signer, message)) == signer.address
