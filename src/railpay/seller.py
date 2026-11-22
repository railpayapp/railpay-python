"""Seller side: an x402 v2 paywall that charges USDG on Robinhood Chain (scheme ``exact``, EIP-3009)."""

from __future__ import annotations

import inspect
import math
import re
import threading
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import TracebackType
from typing import Any, Literal, Protocol, TypedDict
from urllib.parse import urlsplit

import anyio
import httpx
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils.address import to_checksum_address

from ._rpc import TRANSFER_TOPIC, Rpc, pad32
from .constants import CHAIN_ID, DEFAULT_FACILITATORS, DEFAULT_RPC_URL, MIN_X402_AMOUNT, NETWORK, USDG, USDG_EIP712
from .encoding import _json_dumps, b64json, from_units, to_units, unb64json
from .x402 import (
    AuthorizationFields,
    PaymentPayload,
    PaymentRequired,
    PaymentRequirements,
    ResourceInfo,
    SettleResponse,
    _truthy,
)

__all__ = ["Charge", "MemoryStore", "PaymentStore", "Paywall", "PaywallResponse", "StoredPayment"]

# ---------- payment store ----------


class _StoredPaymentBase(TypedDict):
    status: Literal["settling", "pending", "settled"]
    resource: str


class StoredPayment(_StoredPaymentBase, total=False):
    """Where an authorization stands on this server."""

    tx: str


class PaymentStore(Protocol):
    """Remembers which authorizations were settled and served, so one payment unlocks one response.

    :class:`MemoryStore` lives in one process. With several instances (serverless, more than one replica) pass a
    shared store (Redis, a database table) whose ``claim`` is an atomic insert-if-absent.
    """

    async def claim(self, key: str, value: StoredPayment) -> bool:
        """Insert if the key is absent. Return False if it already exists. Must be atomic."""
        ...

    async def get(self, key: str) -> StoredPayment | None: ...

    async def set(self, key: str, value: StoredPayment) -> None: ...

    async def delete(self, key: str) -> None: ...


class MemoryStore:
    """In-process :class:`PaymentStore`. Atomic within one process (event loop or threads)."""

    def __init__(self) -> None:
        self._m: dict[str, StoredPayment] = {}
        self._lock = threading.Lock()

    async def claim(self, key: str, value: StoredPayment) -> bool:
        with self._lock:
            if key in self._m:
                return False
            self._m[key] = value
            return True

    async def get(self, key: str) -> StoredPayment | None:
        with self._lock:
            return self._m.get(key)

    async def set(self, key: str, value: StoredPayment) -> None:
        with self._lock:
            self._m[key] = value

    async def delete(self, key: str) -> None:
        with self._lock:
            self._m.pop(key, None)


# ---------- results ----------


@dataclass(frozen=True)
class Charge:
    """Outcome of :meth:`Paywall.charge`.

    ``paid``: serve the resource and add ``headers`` (PAYMENT-RESPONSE) to your response.
    Not paid: answer ``status`` (402) with ``headers`` and ``body`` (the x402 PaymentRequired JSON).
    """

    paid: bool
    status: int
    headers: dict[str, str]
    body: bytes = b""
    error: str | None = None
    message: str | None = None
    transaction: str | None = None
    payer: str | None = None


@dataclass
class PaywallResponse:
    """A plain HTTP response for :meth:`Paywall.handle`."""

    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    @classmethod
    def json(cls, data: Any, status: int = 200, headers: Mapping[str, str] | None = None) -> PaywallResponse:
        return cls(status, {"content-type": "application/json", **(headers or {})}, _json_dumps(data).encode("utf-8"))


# ---------- parsing ----------

_TIMEOUT_SECONDS = 300
_EXPOSE = "PAYMENT-REQUIRED, PAYMENT-RESPONSE, X-PAYMENT-RESPONSE"
_PAID_HEADERS = ("payment-response", "x-payment-response", "access-control-expose-headers")
_TX = re.compile(r"0x[0-9a-fA-F]{64}")
_MISSING: Any = object()  # JavaScript `undefined`, as opposed to JSON null


@dataclass(frozen=True)
class _Authorization:
    from_: str
    to: str
    value: int
    valid_after: int
    valid_before: int
    nonce: str

    def strings(self) -> AuthorizationFields:
        return {
            "from": self.from_,
            "to": self.to,
            "value": str(self.value),
            "validAfter": str(self.valid_after),
            "validBefore": str(self.valid_before),
            "nonce": self.nonce,
        }


@dataclass(frozen=True)
class _Parsed:
    version: object
    scheme: object
    network: object
    asset: object
    signature: str
    auth: _Authorization


def _is_address(s: str) -> bool:
    """Like viem ``isAddress`` (strict): 0x + 40 hex, and a valid checksum unless all lower case."""
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", s):
        return False
    return s == s.lower() or s == to_checksum_address(s)


def _get(obj: object, key: str) -> Any:
    return obj[key] if isinstance(obj, dict) and key in obj else _MISSING


def _nullish(v: Any) -> bool:
    return v is _MISSING or v is None


def _big(v: object) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, float):
        if not (math.isfinite(v) and v.is_integer() and abs(v) < 1e21):
            return None
        v = int(v)
    if isinstance(v, int):
        v = str(v)
    return int(v) if isinstance(v, str) and re.fullmatch(r"[0-9]{1,30}", v) else None


def _parse_payment(header: str) -> _Parsed | None:
    """PAYMENT-SIGNATURE (x402 v2; X-PAYMENT accepted too) = base64 JSON with payload.signature + .authorization."""
    try:
        j = unb64json(header)
    except ValueError:
        return None
    payload = _get(j, "payload")
    a = _get(payload, "authorization")
    sig = _get(payload, "signature")
    if not isinstance(a, dict) or not isinstance(sig, str) or not re.fullmatch(r"0x[0-9a-fA-F]{130}", sig):
        return None
    value, valid_after, valid_before = _big(a.get("value")), _big(a.get("validAfter")), _big(a.get("validBefore"))
    frm, to, nonce = a.get("from"), a.get("to"), a.get("nonce")
    if (
        not isinstance(frm, str)
        or not _is_address(frm)
        or not isinstance(to, str)
        or not _is_address(to)
        or value is None
        or valid_after is None
        or valid_before is None
        or not isinstance(nonce, str)
        or not re.fullmatch(r"0x[0-9a-fA-F]{64}", nonce)
    ):
        return None
    accepted = _get(j, "accepted")
    scheme = _get(accepted, "scheme")
    if _nullish(scheme):
        scheme = _get(j, "scheme")
    network = _get(accepted, "network")
    if _nullish(network):
        network = _get(j, "network")
    return _Parsed(
        version=_get(j, "x402Version"),
        scheme=scheme,
        network=network,
        asset=_get(accepted, "asset"),
        signature=sig,
        auth=_Authorization(
            from_=to_checksum_address(frm),
            to=to_checksum_address(to),
            value=value,
            valid_after=valid_after,
            valid_before=valid_before,
            nonce=nonce.lower(),
        ),
    )


def _recover(a: _Authorization, signature: str) -> str | None:
    """EIP-712 signer of a USDG TransferWithAuthorization, or None if the signature does not recover."""
    try:
        message = encode_typed_data(
            full_message={
                "types": {
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
                },
                "primaryType": "TransferWithAuthorization",
                "domain": {
                    "name": USDG_EIP712.name,
                    "version": USDG_EIP712.version,
                    "chainId": CHAIN_ID,
                    "verifyingContract": USDG,
                },
                "message": {
                    "from": a.from_,
                    "to": a.to,
                    "value": a.value,
                    "validAfter": a.valid_after,
                    "validBefore": a.valid_before,
                    "nonce": bytes.fromhex(a.nonce[2:]),
                },
            }
        )
        return str(Account.recover_message(message, signature=signature))
    except Exception:
        return None


def _resource_url(url: str) -> str:
    """Origin + path of a URL (no query), like ``${u.origin}${u.pathname}`` in the TypeScript SDK."""
    u = urlsplit(url)
    if not u.scheme or not u.hostname:
        raise ValueError(f"Not an absolute URL: {url!r}")
    scheme = u.scheme.lower()
    host = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
    port = u.port
    netloc = host if port is None or port == {"http": 80, "https": 443}.get(scheme) else f"{host}:{port}"
    return f"{scheme}://{netloc}{u.path or '/'}"


def _header(headers: Mapping[str, str], name: str) -> str | None:
    v = headers.get(name)
    if v is not None:
        return v
    for k, val in headers.items():
        if k.lower() == name:
            return val
    return None


class _SettlementPending(Exception):
    def __init__(self, tx_hash: str) -> None:
        super().__init__(f"settlement broadcast, not confirmed yet: {tx_hash}")
        self.tx_hash = tx_hash


# ---------- paywall ----------


class Paywall:
    """An x402 v2 paywall that charges USDG on Robinhood Chain (scheme ``exact``, EIP-3009).

    Without a payment, :meth:`charge` answers 402 with the requirement (PAYMENT-REQUIRED header and body). With
    PAYMENT-SIGNATURE it verifies the authorization (recipient, amount, validity window, EIP-712 signature, then the
    payer's USDG balance and the nonce on-chain), settles it through a facilitator (which pays the gas), and reads
    the exact USDG transfer back from the receipt before it reports the request as paid.

    ``price``: a USDG decimal (``"0.01"`` or ``Decimal``), or base units as an ``int``. At least 0.001 USDG.
    ``receipt_wait``: seconds to wait for the settlement receipt before answering ``settlement_pending``.
    ``now``: clock in seconds (tests). ``http_client``: an ``httpx.AsyncClient`` for the RPC and the facilitators.
    """

    def __init__(
        self,
        pay_to: str,
        price: str | Decimal | int,
        *,
        description: str | None = None,
        mime_type: str | None = None,
        facilitators: Iterable[str] | None = None,
        rpc_url: str | None = None,
        store: PaymentStore | None = None,
        http_client: httpx.AsyncClient | None = None,
        receipt_wait: float = 12.0,
        now: Callable[[], float] | None = None,
    ) -> None:
        units = price if isinstance(price, int) and not isinstance(price, bool) else to_units(price)
        if units < MIN_X402_AMOUNT:
            raise ValueError(
                f"The lowest price is {from_units(MIN_X402_AMOUNT)} USDG, the smallest payment x402 facilitators settle"
            )
        if not isinstance(pay_to, str) or not _is_address(pay_to):
            raise ValueError("pay_to must be a 0x address")
        self.price: int = units
        self.pay_to: str = to_checksum_address(pay_to)
        self.description = description
        self.mime_type = mime_type
        self.facilitators: tuple[str, ...] = tuple(
            s
            for s in (f.strip().rstrip("/") for f in (DEFAULT_FACILITATORS if facilitators is None else facilitators))
            if re.fullmatch(r"https?://\S+", s)
        )
        self.store: PaymentStore = store if store is not None else MemoryStore()
        self._owns_client = http_client is None
        self._http = http_client if http_client is not None else httpx.AsyncClient(timeout=30.0)
        self._rpc = Rpc(self._http, rpc_url or DEFAULT_RPC_URL)
        self.receipt_wait = receipt_wait
        self._now = now or time.time

    # -- requirement --

    def _accept(self) -> PaymentRequirements:
        return {
            "scheme": "exact",
            "network": NETWORK,
            "amount": str(self.price),
            "asset": USDG,
            "payTo": self.pay_to,
            "maxTimeoutSeconds": _TIMEOUT_SECONDS,
            "extra": {"name": USDG_EIP712.name, "version": USDG_EIP712.version},
        }

    def _resource(self, url: str) -> ResourceInfo:
        return {
            "url": _resource_url(url),
            "description": self.description or "Paid resource",
            "mimeType": self.mime_type or "application/json",
        }

    def requirements(self, url: str) -> PaymentRequired:
        """The x402 v2 PaymentRequired object for a resource URL."""
        return {"x402Version": 2, "resource": self._resource(url), "accepts": [self._accept()]}

    # -- chain --

    async def _transfer_in(self, tx_hash: str, payer: str, wait: float) -> bool | None:
        """The exact USDG transfer payer → pay_to in this transaction? None = no receipt yet."""
        until = time.monotonic() + wait
        frm, to = pad32(payer), pad32(self.pay_to)
        while True:
            try:
                r = await self._rpc.receipt(tx_hash)
            except Exception:
                r = None
            if r is not None:
                if r.get("status") != "0x1":
                    return False
                logs = r.get("logs")
                return isinstance(logs, list) and any(self._is_payment_log(log, frm, to) for log in logs)
            left = until - time.monotonic()
            if left <= 0:
                return None
            await anyio.sleep(min(1.0, left))

    def _is_payment_log(self, log: object, frm: str, to: str) -> bool:
        if not isinstance(log, dict):
            return False
        address, topics, data = log.get("address"), log.get("topics"), log.get("data")
        if not isinstance(address, str) or not isinstance(topics, list) or len(topics) < 3 or not isinstance(data, str):
            return False
        if not all(isinstance(t, str) for t in topics[:3]):
            return False
        try:
            value = int(data, 16)
        except ValueError:
            return False
        return (
            address.lower() == USDG.lower()
            and topics[0].lower() == TRANSFER_TOPIC
            and topics[1].lower() == frm
            and topics[2].lower() == to
            and value == self.price
        )

    async def _verify_reads(self, a: _Authorization) -> tuple[bool, int]:
        out: dict[str, Any] = {}

        async def used() -> None:
            out["used"] = await self._rpc.authorization_used(a.from_, a.nonce)

        async def balance() -> None:
            out["balance"] = await self._rpc.balance_of(a.from_)

        async with anyio.create_task_group() as tg:
            tg.start_soon(used)
            tg.start_soon(balance)
        return bool(out["used"]), int(out["balance"])

    async def _via_facilitators(self, a: _Authorization, payment_payload: PaymentPayload) -> str | None:
        """Facilitators in order. A ``success`` is only a candidate: the caller reads the transfer back.

        Before trying the next one the chain is checked, so an authorization that already landed is taken as settled.
        """
        try:
            start = await self._rpc.block_number()
        except Exception:
            start = 0
        body = _json_dumps({"x402Version": 2, "paymentPayload": payment_payload, "paymentRequirements": self._accept()})
        for base in self.facilitators:
            r: dict[str, Any] | None = None
            try:
                res = await self._http.post(
                    f"{base}/settle",
                    content=body.encode("utf-8"),
                    headers={"content-type": "application/json", "accept": "application/json"},
                    timeout=40.0,
                )
                try:
                    parsed = res.json()
                    r = parsed if isinstance(parsed, dict) else None
                except ValueError:
                    r = None
            except Exception:
                r = None
            raw_tx = r.get("transaction") if r is not None else None
            tx = raw_tx.lower() if isinstance(raw_tx, str) and _TX.fullmatch(raw_tx) else None
            if r is not None and _truthy(r.get("success")) and tx:
                return tx
            if tx and r is not None and r.get("errorReason") == "settlement_pending":
                raise _SettlementPending(tx)
            try:
                used = await self._rpc.authorization_used(a.from_, a.nonce)
            except Exception:
                used = False
            if used:
                for _ in range(3):
                    try:
                        h = await self._rpc.authorization_tx(a.from_, a.nonce, start)
                    except Exception:
                        h = None
                    if h:
                        return h
                    await anyio.sleep(1.5)
                raise RuntimeError("authorization was used on-chain but its transaction was not found yet")
        return None

    # -- handling --

    def _deny(
        self,
        resource: ResourceInfo,
        error: str,
        message: str,
        extra: Mapping[str, str] | None = None,
        *,
        transaction: str | None = None,
        payer: str | None = None,
    ) -> Charge:
        pr = {"x402Version": 2, "error": error, "message": message, "resource": resource, "accepts": [self._accept()]}
        headers = {
            "content-type": "application/json",
            "PAYMENT-REQUIRED": b64json(pr),
            "access-control-expose-headers": _EXPOSE,
            **(extra or {}),
        }
        return Charge(
            paid=False,
            status=402,
            headers=headers,
            body=_json_dumps(pr).encode("utf-8"),
            error=error,
            message=message,
            transaction=transaction,
            payer=payer,
        )

    def _pending(self, resource: ResourceInfo, tx_hash: str, payer: str) -> Charge:
        receipt: SettleResponse = {
            "success": False,
            "errorReason": "settlement_pending",
            "transaction": tx_hash,
            "network": NETWORK,
            "payer": payer,
        }
        return self._deny(
            resource,
            "settlement_pending",
            "Settlement was broadcast and is still confirming. Retry with the same payment shortly.",
            {"PAYMENT-RESPONSE": b64json(receipt)},
            transaction=tx_hash,
            payer=payer,
        )

    async def charge(self, url: str, headers: Mapping[str, str]) -> Charge:
        """Verify and settle the payment on a request (its full URL and headers). Framework-agnostic.

        Returns a :class:`Charge`: when ``paid``, serve the resource once and add ``charge.headers`` to the response;
        otherwise answer ``charge.status`` (402) with ``charge.headers`` and ``charge.body``.
        """
        resource = self._resource(url)
        deny = self._deny
        header = _header(headers, "payment-signature")
        if header is None:
            header = _header(headers, "x-payment")
        if not header:
            return deny(
                resource,
                "payment_required",
                "Pay with the PAYMENT-SIGNATURE header: x402 v2, scheme exact, "
                "an EIP-3009 authorization for USDG on Robinhood Chain",
            )
        p = _parse_payment(header)
        if p is None:
            return deny(
                resource,
                "invalid_payload",
                "PAYMENT-SIGNATURE must be base64 JSON with payload.signature and payload.authorization",
            )
        if isinstance(p.version, bool) or not isinstance(p.version, (int, float)) or p.version not in (1, 2):
            return deny(resource, "invalid_x402_version", "Use x402Version 2")
        if p.scheme is not _MISSING and p.scheme != "exact":
            return deny(resource, "invalid_scheme", 'Only the "exact" scheme is accepted')
        if p.network is not _MISSING and p.network != NETWORK:
            return deny(resource, "invalid_network", f"Network must be {NETWORK} (Robinhood Chain)")
        if p.asset is not _MISSING and (not isinstance(p.asset, str) or p.asset.lower() != USDG.lower()):
            return deny(resource, "invalid_payment_requirements", f"Asset must be USDG ({USDG})")

        a = p.auth
        payer = a.from_.lower()
        key = f"{payer}:{a.nonce}"
        url_key = resource["url"]
        prior = await self.store.get(key)

        if prior is not None:
            # Only a retry of a settlement that was still confirming can be served, once.
            prior_tx = prior.get("tx")
            if prior["status"] != "pending" or not prior_tx or prior["resource"] != url_key:
                return deny(resource, "invalid_payload", "This authorization was already used")
            if not await self._transfer_in(prior_tx, payer, 3.0):
                return deny(resource, "settlement_pending", "Settlement is still confirming. Retry shortly.")
            if not await self.store.claim(f"{key}#served", {"status": "settled", "resource": url_key, "tx": prior_tx}):
                return deny(resource, "invalid_payload", "This authorization was already used")
            await self.store.set(key, {"status": "settled", "resource": url_key, "tx": prior_tx})
            tx_hash = prior_tx
        else:
            t = math.floor(self._now())
            if a.to.lower() != self.pay_to.lower():
                return deny(
                    resource, "invalid_exact_evm_payload_recipient_mismatch", f"authorization.to must be {self.pay_to}"
                )
            if a.value != self.price:
                return deny(
                    resource,
                    "invalid_exact_evm_payload_authorization_value_mismatch",
                    f"authorization.value must be exactly {self.price}",
                )
            if a.valid_after > t:
                return deny(
                    resource,
                    "invalid_exact_evm_payload_authorization_valid_after",
                    "The authorization is not valid yet",
                )
            if a.valid_before < t + 6:
                return deny(
                    resource, "invalid_exact_evm_payload_authorization_valid_before", "The authorization has expired"
                )
            signer = _recover(a, p.signature)
            if signer is None or signer.lower() != payer:
                return deny(
                    resource,
                    "invalid_exact_evm_payload_signature",
                    "The signature does not recover to authorization.from",
                )
            try:
                used, balance = await self._verify_reads(a)
            except Exception:
                return deny(
                    resource,
                    "unexpected_verify_error",
                    "Could not read Robinhood Chain to verify the payment. Retry shortly.",
                )
            if used:
                return deny(resource, "invalid_payload", "This authorization nonce was already used on-chain")
            if balance < a.value:
                return deny(resource, "insufficient_funds", "The payer holds less USDG than the price")
            if not await self.store.claim(key, {"status": "settling", "resource": url_key}):
                return deny(resource, "invalid_payload", "This authorization is already being settled")

            payload: PaymentPayload = {
                "x402Version": 2,
                "resource": resource,
                "accepted": self._accept(),
                "payload": {"signature": p.signature, "authorization": a.strings()},
            }
            settled: str | None
            try:
                settled = await self._via_facilitators(a, payload)
            except _SettlementPending as e:
                await self.store.set(key, {"status": "pending", "resource": url_key, "tx": e.tx_hash})
                return self._pending(resource, e.tx_hash, payer)
            except Exception:
                settled = None
            if not settled:
                await self.store.delete(key)
                return deny(
                    resource,
                    "unexpected_settle_error",
                    "No x402 facilitator could settle this payment right now. Retry shortly.",
                )
            # Serve only once the exact USDG transfer is on-chain, whoever submitted it.
            if not await self._transfer_in(settled, payer, self.receipt_wait):
                await self.store.set(key, {"status": "pending", "resource": url_key, "tx": settled})
                return self._pending(resource, settled, payer)
            await self.store.claim(f"{key}#served", {"status": "settled", "resource": url_key, "tx": settled})
            await self.store.set(key, {"status": "settled", "resource": url_key, "tx": settled})
            tx_hash = settled

        receipt = b64json({"success": True, "transaction": tx_hash, "network": NETWORK, "payer": payer})
        headers = {"PAYMENT-RESPONSE": receipt, "X-PAYMENT-RESPONSE": receipt, "access-control-expose-headers": _EXPOSE}
        return Charge(
            paid=True,
            status=200,
            headers=headers,
            transaction=tx_hash,
            payer=payer,
        )

    async def handle(
        self,
        url: str,
        headers: Mapping[str, str],
        serve: Callable[[], PaywallResponse | Awaitable[PaywallResponse]],
    ) -> PaywallResponse:
        """:meth:`charge`, then ``serve()`` once when paid, with PAYMENT-RESPONSE added. Framework-agnostic."""
        charge = await self.charge(url, headers)
        if not charge.paid:
            return PaywallResponse(charge.status, dict(charge.headers), charge.body)
        res = serve()
        if inspect.isawaitable(res):
            res = await res
        out = {k: v for k, v in res.headers.items() if k.lower() not in _PAID_HEADERS}
        out.update(charge.headers)
        return PaywallResponse(res.status, out, res.body)

    # -- lifecycle --

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def __aenter__(self) -> Paywall:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.aclose()
