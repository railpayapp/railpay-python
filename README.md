![railpay-python: RailPay](.github/banner.png)

# railpay-python

Python SDK for [RailPay](https://railpay.app): stablecoin payments on Robinhood Chain.

- **Pay** for x402 resources from a RailPay agent wallet: `paid_client()` gives you an httpx client that answers a `402 Payment Required` and retries.
- **Charge** for your own API in USDG: `Paywall` returns the 402, verifies the payment, settles it and serves the response. It plugs into FastAPI, Starlette or any ASGI app.

Settlement is real: USDG (Global Dollar, 6 decimals) on Robinhood Chain (chain id 4663), using x402 v2, scheme `exact`, with EIP-3009 `TransferWithAuthorization`. This is the Python counterpart of the TypeScript SDK, [railpay-sdk](https://github.com/railpayapp/railpay-sdk), and speaks the same wire format.

## Install

The package is not on PyPI yet. Install it from GitHub:

```sh
pip install git+https://github.com/railpayapp/railpay-python
# with the FastAPI integration:
pip install "railpay[fastapi] @ git+https://github.com/railpayapp/railpay-python"
# uv:
uv add git+https://github.com/railpayapp/railpay-python
```

Python 3.10+. Runtime dependencies: [httpx](https://www.python-httpx.org), [eth-account](https://github.com/ethereum/eth-account) (signature recovery for the paywall) and anyio. Fully typed (`py.typed`).

## Pay for an x402 resource (agent)

Create an agent in the [RailPay dapp](https://dapp.railpay.app), set its policy (per-request max, daily cap, allowed hosts, allowed recipients), fund its wallet with USDG and create an agent key (`rp_sk_…`).

```python
import os

from railpay import RailPay, paid_client, read_payment_response

railpay = RailPay(os.environ["RAILPAY_AGENT_KEY"])

with paid_client(railpay, max_amount="0.05") as http:
    res = http.get("https://dapp.railpay.app/x402/time")

print(res.json())
receipt = read_payment_response(res)
print(receipt and receipt["transaction"])  # settlement tx hash
```

Async, with `AsyncRailPay`:

```python
from railpay import AsyncRailPay, paid_client

async with AsyncRailPay(os.environ["RAILPAY_AGENT_KEY"]) as railpay:
    async with paid_client(railpay, max_amount="0.05") as http:
        res = await http.get("https://dapp.railpay.app/x402/time")
```

Already have an httpx client? `PaymentAuth` is an `httpx.Auth`, so you can pass it per request or per client:

```python
from railpay import PaymentAuth

auth = PaymentAuth(railpay, max_amount="0.05")
res = http.get("https://dapp.railpay.app/x402/time", auth=auth)
```

What happens:

1. The first request gets `402` with a `PAYMENT-REQUIRED` header.
2. If one of the accepted options is `exact` / `eip155:4663` / USDG, the SDK asks RailPay (`POST /v1/agent/pay`) to sign an EIP-3009 authorization from the agent wallet. RailPay checks the agent's policy first.
3. The request is retried once with `PAYMENT-SIGNATURE`. The seller settles the authorization on-chain.

If no option is payable, the original 402 comes back unchanged. A price above `max_amount` raises `RailPayError` with code `over_max_amount` before anything is signed. Request bodies, streamed ones included, are kept and sent again on the retry. The agent key goes only to RailPay, never to the seller. `on_payment` is called with a `PaymentEvent` (url, requirement, amount in base units, signed payment) before the retry.

## Charge for your API (seller)

```python
from railpay import Paywall

paywall = Paywall(
    "0xYourWallet",
    "0.01",  # USDG per request, at least 0.001
    description="Daily market report",
)
```

FastAPI (`pip install "railpay[fastapi] @ git+…"`):

```python
from fastapi import Depends, FastAPI

from railpay import Charge
from railpay.fastapi import PaymentRequiredError, payment_required_handler, require_payment

app = FastAPI()
app.add_exception_handler(PaymentRequiredError, payment_required_handler)


@app.get("/report")
async def report(payment: Charge = Depends(require_payment(paywall))):
    return {"report": "…", "tx": payment.transaction}
```

Starlette or any other ASGI app:

```python
from railpay import PaywallMiddleware

app.add_middleware(PaywallMiddleware, paywall=paywall, paths=["/report"])
# or wrap any ASGI app: app = PaywallMiddleware(app, paywall, paths=["/report"])
```

Any other framework: pass the full request URL and headers.

```python
from railpay import PaywallResponse

res = await paywall.handle(url, headers, lambda: PaywallResponse.json({"report": "…"}))
# res.status, res.headers, res.body: turn them into your framework's response

# or decide yourself:
charge = await paywall.charge(url, headers)
if not charge.paid:
    return make_response(charge.status, charge.headers, charge.body)  # the 402
```

`charge()` / `handle()`:

1. No payment: answers `402` with the requirement (`PAYMENT-REQUIRED` header and the same JSON in the body).
2. With `PAYMENT-SIGNATURE` (or `X-PAYMENT`): checks recipient, amount, validity window and signature, then reads the payer's USDG balance and the authorization nonce from the chain.
3. Sends the authorization to an x402 facilitator to settle.
4. Reads the transaction receipt and looks for the exact USDG transfer, payer → `pay_to`, for the price. A facilitator's "success" alone is not trusted.
5. Reports the request as paid (`handle()` calls `serve()` once) and adds `PAYMENT-RESPONSE` with the transaction hash.

If the transfer is broadcast but not confirmed yet, the answer is `402 settlement_pending`. Retrying with the same payment serves the resource once the transfer is on-chain. A payment unlocks one response. Replays get `invalid_payload`.

The paywall is async (httpx.AsyncClient). Close it with `await paywall.aclose()` or `async with paywall:` when your app shuts down.

### Gas and facilitators

Settlement goes through outside x402 facilitators that pay the gas and need no key: [Toll](https://x402.t0ll.xyz) first, then [Primer](https://x402.primer.systems). They are third-party services, not run by RailPay, and they have their own limits (Toll: 0.001 to 100 USDG per payment). You can pass your own list with `facilitators=`. A RailPay relayer is coming soon.

### Running on more than one instance

The default `MemoryStore` remembers payments inside one process. Once a payment is settled its nonce is spent on-chain, so a replay sent to another instance is refused anyway. The gap is two instances settling the same authorization at the same moment. With several worker processes or replicas (uvicorn `--workers`, gunicorn, serverless), pass a shared `store` whose `claim()` is an atomic insert-if-absent (Redis `SET NX`, a database row with a unique key).

```python
from railpay import PaymentStore, StoredPayment


class RedisStore:  # implements PaymentStore
    async def claim(self, key: str, value: StoredPayment) -> bool: ...  # False if the key exists
    async def get(self, key: str) -> StoredPayment | None: ...
    async def set(self, key: str, value: StoredPayment) -> None: ...
    async def delete(self, key: str) -> None: ...


paywall = Paywall("0xYourWallet", "0.01", store=RedisStore())
```

## API

### `RailPay(api_key=None, *, base_url="https://dapp.railpay.app", http_client=None, timeout=30.0)`

`AsyncRailPay` takes the same arguments (`http_client` is then an `httpx.AsyncClient`) and has the same methods, awaited. Both close their own HTTP client (`close()` / `aclose()`, or use them as context managers), never one you passed in.

| Method | Auth | Returns |
|---|---|---|
| `me()` | agent key | the agent: address, USDG `balance` (base units), `eth`, `spent_today`, `policy` |
| `pay(payment_required, resource_url=None)` | agent key | `{payment, header, authorization, valid_before, amount, spent_today}` |
| `send(to, amount, memo=None)` | agent key | agent-to-agent payment `{tx_hash, tx_url, amount}`. `amount` is USDG (`"0.5"` or `Decimal`). Needs `allow_a2a`, and the paying agent wallet pays the gas in ETH |
| `config()` | public | network, USDG, facilitators, relayer status |
| `stats()` | account key | network stats for signed-in accounts: every recorded payment, outside vs team |
| `request(method, path, body=None)` | | any other endpoint |

API errors raise `RailPayError` with `status`, `code` and `message`. Policy refusals are `403` with code `paused`, `over_per_request`, `over_daily_cap`, `host_not_allowed`, `pay_to_not_allowed`, `a2a_not_allowed`, `insufficient_funds` or `insufficient_gas`, and each one is logged on the agent in the dapp. Local refusals have `status` 0 (`missing_api_key`, `over_max_amount`).

### `paid_client(railpay, *, max_amount=None, on_payment=None, **httpx_options)`

Returns an `httpx.Client` for a `RailPay`, an `httpx.AsyncClient` for an `AsyncRailPay`. Other keyword arguments go to httpx (`timeout`, `headers`, `transport`…). `PaymentAuth(railpay, *, max_amount=None, on_payment=None)` is the `httpx.Auth` behind it. `max_amount` is a USDG decimal (`"0.05"` or `Decimal`) or base units as an `int`.

### `Paywall(pay_to, price, *, description=None, mime_type=None, facilitators=None, rpc_url=None, store=None, http_client=None, receipt_wait=12.0)`

`price` is a USDG decimal (`"0.01"` or `Decimal`) or base units as an `int`, at least 0.001 USDG. `rpc_url` defaults to `https://robinhood-rpc.publicnode.com`. `receipt_wait` is how many seconds to wait for the settlement receipt before answering `settlement_pending`.

- `requirements(url)`: the x402 `PaymentRequired` for a URL.
- `await charge(url, headers)` → `Charge(paid, status, headers, body, error, message, transaction, payer)`. When `paid`, `headers` holds `PAYMENT-RESPONSE` for your response.
- `await handle(url, headers, serve)` → `PaywallResponse(status, headers, body)`. `serve` may be sync or async.
- `await aclose()`.

### Integrations

- `PaywallMiddleware(app, paywall, paths=None)`: ASGI middleware. `paths` is a list of exact paths, a predicate on the path, or `None` for every HTTP request.
- `railpay.fastapi.require_payment(paywall)`: a dependency that returns the `Charge` and adds `PAYMENT-RESPONSE` to the response. Register `payment_required_handler` for `PaymentRequiredError` so the 402 body is the x402 JSON itself (without it FastAPI wraps it in `{"detail": …}`; the header is right either way).

### Helpers

- `to_units("1.5")` → `1500000`, `from_units(1500000)` → `"1.5"`
- `read_payment_required(response)`, `read_payment_response(response)`, `is_railpay_payable(requirement)`, `requirement_amount(requirement)`
- `b64json(value)`, `unb64json(string)`
- Constants: `CHAIN_ID`, `NETWORK`, `USDG`, `USDG_DECIMALS`, `USDG_EIP712`, `DEFAULT_FACILITATORS`, `MIN_X402_AMOUNT`, `EXPLORER`, `tx_url(hash)`, `address_url(address)`

## Examples

- [`examples/agent.py`](examples/agent.py): pay `https://dapp.railpay.app/x402/time` (0.001 USDG) from an agent wallet.
- [`examples/fastapi_seller.py`](examples/fastapi_seller.py): charge 0.01 USDG per request on a FastAPI route.

## Security

- An agent key can spend from the agent wallet within its policy. Keep it on a server, never in client-side code. Set a daily cap and allowlists in the dapp.
- `PaymentAuth` signs only USDG on Robinhood Chain. Use `max_amount` as a second limit on top of the policy.
- The paywall serves a request only after it has read the exact USDG transfer from the chain.
- Report vulnerabilities to founder@railpay.app (see [SECURITY.md](SECURITY.md)).

## Development

```sh
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
uv run python scripts/smoke.py   # read-only check against https://dapp.railpay.app and the chain, spends nothing
```

Tests mock HTTP and RPC, and sign real EIP-3009 authorizations with throwaway keys made in the test.

## Links

- Website: https://railpay.app
- Docs: https://docs.railpay.app
- Dapp: https://dapp.railpay.app
- TypeScript SDK: https://github.com/railpayapp/railpay-sdk
- MCP server: https://github.com/railpayapp/railpay-mcp
- X: https://x.com/railpayapp

## License

[MIT](LICENSE)
