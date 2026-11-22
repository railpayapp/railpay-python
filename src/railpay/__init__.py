"""RailPay: x402 v2 agent payments on Robinhood Chain."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("railpay")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = [
    "__version__",
]
