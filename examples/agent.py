"""An agent that pays for x402 resources with its RailPay wallet.

Run: RAILPAY_AGENT_KEY=rp_sk_… python examples/agent.py
Create an agent, fund it with USDG and make a key in the RailPay dapp (https://dapp.railpay.app).
"""

from __future__ import annotations

import os
import sys

from railpay import PaymentEvent, RailPay, from_units, paid_client, read_payment_response, tx_url


def main() -> None:
    api_key = os.environ.get("RAILPAY_AGENT_KEY")
    if not api_key:
        sys.exit("Set RAILPAY_AGENT_KEY (create an agent and a key in the RailPay dapp)")

    with RailPay(api_key) as railpay:
        me = railpay.me()
        balance, spent = from_units(me["balance"]), from_units(me["spent_today"])
        print(f"{me['name']} {me['address']}: {balance} USDG, spent today {spent} USDG")

        def on_payment(event: PaymentEvent) -> None:
            print(f"paying {from_units(event.amount)} USDG for {event.url}")

        # Pays any 402 that asks for USDG on Robinhood Chain, never more than 0.01 USDG per request.
        with paid_client(railpay, max_amount="0.01", on_payment=on_payment, timeout=60) as http:
            res = http.get("https://dapp.railpay.app/x402/time")
        print(res.status_code, res.text)

        receipt = read_payment_response(res)
        if receipt and receipt["success"]:
            print(f"settled: {tx_url(receipt['transaction'])}")


if __name__ == "__main__":
    main()
