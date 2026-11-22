# Changelog

## [0.1.0] - 2026-11-22

First release.

- `RailPay` client for the RailPay API on Robinhood Chain (chain id 4663): `me()`, `pay()`, `send()`, `config()`, `stats()` and `request()`, with `rp_sk_` Bearer keys. API errors raise `RailPayError` with `status`, `code` and `message`.
- x402 v2 wire types: `PaymentRequired`, `PaymentRequirements`, `PaymentPayload`, `SettleResponse`.
- Helpers for USDG units (`to_units`, `from_units`), base64 JSON headers (`b64json`, `unb64json`) and Robinhood Chain constants (USDG, EIP-712 domain, facilitators, explorer), with the same values as the TypeScript SDK.
- `stats()` needs an account key: network stats are for signed-in accounts.
