# Changelog

## [0.5.0] - 2026-11-22

- `PaywallMiddleware`: ASGI middleware that puts a `Paywall` in front of chosen paths of any ASGI app (Starlette, FastAPI…). Unpaid requests get the 402. Paid ones reach the app, with `PAYMENT-RESPONSE` added to its answer.
- `railpay.fastapi.require_payment(paywall)`: a FastAPI dependency that returns the `Charge`, with `PaymentRequiredError` and `payment_required_handler` for an exact x402 402 body. Install it with the `[fastapi]` extra.
- Examples: `examples/agent.py` (pays `https://dapp.railpay.app/x402/time`) and `examples/fastapi_seller.py`.
- `scripts/smoke.py`: a read-only check against the live RailPay API and Robinhood Chain. It spends nothing.
- README: seller integrations, examples and the full API.

## [0.4.0] - 2026-11-22

- `Paywall`: charge USDG for your own API. It returns the 402, checks the EIP-3009 authorization (recipient, amount, validity window, EIP-712 signature, then the payer's balance and the nonce on-chain), settles it through keyless x402 facilitators (Toll, then Primer) that pay the gas, and reports the request as paid only after the exact USDG transfer is in the receipt. A transfer that is not confirmed yet answers `settlement_pending`.
- `charge(url, headers)` and `handle(url, headers, serve)` work with any framework.
- `PaymentStore` protocol and `MemoryStore`: one payment unlocks one response. Pass a shared store with an atomic `claim()` when you run more than one instance.
- Signatures are recovered the same way the TypeScript SDK (viem) makes them, checked against a cross-implementation test vector.
- New runtime dependencies: eth-account, eth-utils and anyio.

## [0.3.0] - 2026-11-22

- `AsyncRailPay`: the async client, with the same methods as `RailPay`, on `httpx.AsyncClient`.
- `PaymentAuth` and `paid_client()` work with `httpx.AsyncClient` when given an `AsyncRailPay`. `on_payment` may be a coroutine function.

## [0.2.0] - 2026-11-22

- `PaymentAuth`: an `httpx.Auth` that answers an x402 v2 `402 Payment Required` from a RailPay agent wallet and retries once with `PAYMENT-SIGNATURE` (the Python form of `withPayment`). It pays only `exact` / `eip155:4663` / USDG options, and `max_amount` refuses a higher price before anything is signed. Request bodies, streamed ones included, are sent again on the retry.
- `paid_client(railpay)`: an `httpx.Client` that pays with `PaymentAuth`.
- `read_payment_required()`, `read_payment_response()`, `is_railpay_payable()` and `requirement_amount()`.

## [0.1.0] - 2026-11-22

First release.

- `RailPay` client for the RailPay API on Robinhood Chain (chain id 4663): `me()`, `pay()`, `send()`, `config()`, `stats()` and `request()`, with `rp_sk_` Bearer keys. API errors raise `RailPayError` with `status`, `code` and `message`.
- x402 v2 wire types: `PaymentRequired`, `PaymentRequirements`, `PaymentPayload`, `SettleResponse`.
- Helpers for USDG units (`to_units`, `from_units`), base64 JSON headers (`b64json`, `unb64json`) and Robinhood Chain constants (USDG, EIP-712 domain, facilitators, explorer), with the same values as the TypeScript SDK.
- `stats()` needs an account key: network stats are for signed-in accounts.
