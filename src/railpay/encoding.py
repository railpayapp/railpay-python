"""Base64 JSON (the x402 header encoding) and USDG unit conversion."""

from __future__ import annotations

import base64
import binascii
import json
import re
from decimal import Decimal
from typing import Any

from .constants import USDG_DECIMALS

_DECIMAL = re.compile(r"([0-9]+)(?:\.([0-9]+))?")
_B64 = re.compile(r"[A-Za-z0-9+/]*")
_WHITESPACE = re.compile(r"[\t\n\f\r ]+")


def _json_dumps(value: Any) -> str:
    # Same output as JSON.stringify: no spaces, non-ASCII kept as UTF-8.
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"Not valid JSON: {name}")


def b64json(value: Any) -> str:
    """Base64 of the UTF-8 JSON of a value (the encoding x402 uses in headers)."""
    return base64.b64encode(_json_dumps(value).encode("utf-8")).decode("ascii")


def unb64json(value: str) -> Any:
    """Decode :func:`b64json`. Raises ``ValueError`` on invalid base64 or JSON."""
    # Forgiving base64, like atob(): whitespace ignored, padding optional.
    s = _WHITESPACE.sub("", value)
    if len(s) % 4 == 0 and s.endswith("="):
        s = s[:-2] if s.endswith("==") else s[:-1]
    if not _B64.fullmatch(s) or len(s) % 4 == 1:
        raise ValueError("Not valid base64")
    try:
        raw = base64.b64decode(s + "=" * (-len(s) % 4), validate=True)
    except binascii.Error as e:
        raise ValueError("Not valid base64") from e
    return json.loads(raw.decode("utf-8", errors="replace"), parse_constant=_reject_constant)


def to_units(amount: str | int | float | Decimal, decimals: int = USDG_DECIMALS) -> int:
    """``"1.5"`` → ``1500000`` (USDG base units). Accepts a non-negative decimal with at most ``decimals`` places.

    Pass a ``str`` or a ``Decimal``. An ``int`` is read as a whole number of USDG here (``to_units(2)`` → ``2000000``).
    """
    if isinstance(amount, bool):
        raise TypeError(f"Not a decimal amount: {amount!r}")
    if isinstance(amount, str):
        s = amount.strip()
    elif isinstance(amount, Decimal):
        s = format(amount, "f") if amount.is_finite() else str(amount)
    elif isinstance(amount, (int, float)):
        s = repr(amount)
    else:
        raise TypeError(f"Not a decimal amount: {amount!r}")
    m = _DECIMAL.fullmatch(s)
    if not m:
        raise ValueError(f"Not a decimal amount: {amount!r}")
    whole, frac = m.group(1), m.group(2) or ""
    if len(frac) > decimals:
        raise ValueError(f"At most {decimals} decimals: {s}")
    base: int = 10**decimals
    return int(whole) * base + int(frac.ljust(decimals, "0") or "0")


def from_units(units: int | str, decimals: int = USDG_DECIMALS) -> str:
    """``1500000`` → ``"1.5"``. Use ``Decimal(from_units(x))`` for arithmetic."""
    if isinstance(units, bool) or not isinstance(units, (int, str)):
        raise TypeError(f"Not an integer amount: {units!r}")
    if isinstance(units, str):
        s = units.strip()
        if re.fullmatch(r"0[xX][0-9a-fA-F]+", s):
            v = int(s, 16)
        elif re.fullmatch(r"-?[0-9]+", s):
            v = int(s, 10)
        else:
            raise ValueError(f"Not an integer amount: {units!r}")
    else:
        v = units
    neg = v < 0
    whole, rest = divmod(abs(v), 10**decimals)
    frac = str(rest).rjust(decimals, "0").rstrip("0")
    return f"{'-' if neg else ''}{whole}{'.' + frac if frac else ''}"
