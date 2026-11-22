"""Charge 0.01 USDG per request on a FastAPI route.

Run: pip install "railpay[fastapi] @ git+https://github.com/railpayapp/railpay-python" uvicorn
     PAY_TO=0xYourWallet uvicorn examples.fastapi_seller:app
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI

from railpay import Charge, Paywall, tx_url
from railpay.fastapi import PaymentRequiredError, payment_required_handler, require_payment

paywall = Paywall(
    os.environ.get("PAY_TO", "0x000000000000000000000000000000000000dEaD"),  # your wallet
    "0.01",
    description="Daily market report",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await paywall.aclose()


app = FastAPI(lifespan=lifespan)
app.add_exception_handler(PaymentRequiredError, payment_required_handler)


@app.get("/report")
async def report(payment: Charge = Depends(require_payment(paywall))) -> dict[str, Any]:  # noqa: B008
    return {
        "report": "…",
        "at": datetime.now(timezone.utc).isoformat(),
        "paid_by": payment.payer,
        "tx": tx_url(payment.transaction) if payment.transaction else None,
    }


@app.get("/")
async def index() -> dict[str, str]:
    return {"paid": "/report", "price": "0.01 USDG"}
