![railpay-python: RailPay](.github/banner.png)

# railpay-python

Python SDK for [RailPay](https://railpay.app): stablecoin payments on Robinhood Chain.

- **Pay** for x402 resources from a RailPay agent wallet: `paid_client()` gives you an httpx client that answers a `402 Payment Required` and retries.
- **Call** the RailPay API with `RailPay`: the agent's balance and policy, signed payments, agent-to-agent transfers and network settings.

Settlement is real: USDG (Global Dollar, 6 decimals) on Robinhood Chain (chain id 4663), using x402 v2, scheme `exact`, with EIP-3009 `TransferWithAuthorization`. This is the Python counterpart of the TypeScript SDK, [railpay-sdk](https://github.com/railpayapp/railpay-sdk), and speaks the same wire format.

## Install

The package is not on PyPI yet. Install it from GitHub:

```sh
pip install git+https://github.com/railpayapp/railpay-python
# uv:
uv add git+https://github.com/railpayapp/railpay-python
```

Python 3.10+. The one runtime dependency is [httpx](https://www.python-httpx.org). Fully typed (`py.typed`).

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

## API

### `RailPay(api_key=None, *, base_url="https://dapp.railpay.app", http_client=None, timeout=30.0)`

It closes its own HTTP client (`close()`, or use it as a context manager), never one you passed in.

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

Returns an `httpx.Client` that pays 402s. Other keyword arguments go to httpx (`timeout`, `headers`, `transport`…). `PaymentAuth(railpay, *, max_amount=None, on_payment=None)` is the `httpx.Auth` behind it, for `httpx.Client`. `max_amount` is a USDG decimal (`"0.05"` or `Decimal`) or base units as an `int`.

### Helpers

- `to_units("1.5")` → `1500000`, `from_units(1500000)` → `"1.5"`
- `read_payment_required(response)`, `read_payment_response(response)`, `is_railpay_payable(requirement)`, `requirement_amount(requirement)`
- `b64json(value)`, `unb64json(string)`
- Constants: `CHAIN_ID`, `NETWORK`, `USDG`, `USDG_DECIMALS`, `USDG_EIP712`, `DEFAULT_FACILITATORS`, `MIN_X402_AMOUNT`, `EXPLORER`, `tx_url(hash)`, `address_url(address)`

## Security

- An agent key can spend from the agent wallet within its policy. Keep it on a server, never in client-side code. Set a daily cap and allowlists in the dapp.
- `PaymentAuth` signs only USDG on Robinhood Chain. Use `max_amount` as a second limit on top of the policy.
- Report vulnerabilities to founder@railpay.app (see [SECURITY.md](SECURITY.md)).

## Development

```sh
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Tests mock HTTP. Nothing touches the network.

## Links

- Website: https://railpay.app
- Docs: https://docs.railpay.app
- Dapp: https://dapp.railpay.app
- TypeScript SDK: https://github.com/railpayapp/railpay-sdk
- MCP server: https://github.com/railpayapp/railpay-mcp
- X: https://x.com/railpayapp

## License

[MIT](LICENSE)
