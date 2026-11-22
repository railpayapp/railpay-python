"""FastAPI integration: a per-route dependency that charges with a :class:`~railpay.seller.Paywall`.

Needs FastAPI (``pip install "railpay[fastapi] @ git+https://github.com/railpayapp/railpay-python"``).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

try:
    from fastapi import HTTPException, Request, Response
except ImportError as e:  # pragma: no cover
    raise ImportError("railpay.fastapi needs FastAPI: install railpay with the [fastapi] extra") from e

from .seller import Charge, Paywall

__all__ = ["PaymentRequiredError", "payment_required_handler", "require_payment"]


class PaymentRequiredError(HTTPException):
    """Raised by :func:`require_payment` for an unpaid request: status 402, PAYMENT-REQUIRED header.

    Register :func:`payment_required_handler` so the body is the x402 PaymentRequired JSON itself. Without it,
    FastAPI wraps the same object in ``{"detail": …}`` (the PAYMENT-REQUIRED header is right either way).
    """

    def __init__(self, charge: Charge) -> None:
        super().__init__(status_code=charge.status, detail=json.loads(charge.body), headers=dict(charge.headers))
        self.charge = charge


async def payment_required_handler(request: Request, exc: Exception) -> Response:
    """``app.add_exception_handler(PaymentRequiredError, payment_required_handler)``."""
    if not isinstance(exc, PaymentRequiredError):  # pragma: no cover
        raise exc
    return Response(exc.charge.body, status_code=exc.charge.status, headers=exc.charge.headers)


def require_payment(paywall: Paywall) -> Callable[[Request, Response], Awaitable[Charge]]:
    """A FastAPI dependency: ``payment: Charge = Depends(require_payment(paywall))``.

    Unpaid: raises :class:`PaymentRequiredError` (402). Paid: returns the :class:`~railpay.seller.Charge` and sets
    PAYMENT-RESPONSE on the response. FastAPI copies those headers only when the endpoint returns data, not a
    ``Response`` object; if you return a ``Response``, add ``payment.headers`` to it yourself.
    """

    async def dependency(request: Request, response: Response) -> Charge:
        charge = await paywall.charge(str(request.url), request.headers)
        if not charge.paid:
            raise PaymentRequiredError(charge)
        for k, v in charge.headers.items():
            response.headers[k] = v
        return charge

    return dependency
