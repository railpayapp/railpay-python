import base64
import json
from decimal import Decimal

import pytest

from railpay import b64json, from_units, to_units, unb64json


def test_round_trips_utf8_json_through_base64() -> None:
    v = {"x402Version": 2, "note": "Settlement — ✓ 日本", "n": [1, 2]}
    assert unb64json(b64json(v)) == v


def test_matches_javascript_base64_for_ascii() -> None:
    v = {"a": "b"}
    assert b64json(v) == base64.b64encode(json.dumps(v, separators=(",", ":")).encode()).decode()
    assert b64json(v) == "eyJhIjoiYiJ9"  # btoa(JSON.stringify({a: "b"}))


def test_decodes_like_atob() -> None:
    assert unb64json("eyJhIjoiYiJ9") == {"a": "b"}
    assert unb64json("  eyJhIjox\nfQ==  ") == {"a": 1}
    assert unb64json("eyJhIjoxfQ") == {"a": 1}  # padding is optional
    for bad in ["not-base64!", "eyJhIjoxfQ===", "a", "ey=J"]:
        with pytest.raises(ValueError, match="base64"):
            unb64json(bad)
    with pytest.raises(ValueError, match="JSON"):
        unb64json(base64.b64encode(b"NaN").decode())


def test_converts_usdg_decimals_to_base_units() -> None:
    assert to_units("1.5") == 1_500_000
    assert to_units("0.001") == 1_000
    assert to_units("2") == 2_000_000
    assert to_units(0.25) == 250_000
    assert to_units(Decimal("0.25")) == 250_000
    assert to_units(Decimal("1E+1")) == 10_000_000
    assert to_units("0.000001") == 1
    assert to_units(" 3 ") == 3_000_000


def test_rejects_invalid_amounts() -> None:
    for bad in ["1.0000001", "-1", "1e3", "", ".5", "1.", "\u0661"]:  # \u0661: Arabic-Indic one, not a digit here
        with pytest.raises(ValueError, match="decimal"):
            to_units(bad)
    with pytest.raises(ValueError, match="decimal"):
        to_units(Decimal("NaN"))
    with pytest.raises(TypeError):
        to_units(True)


def test_formats_base_units() -> None:
    assert from_units(1_500_000) == "1.5"
    assert from_units(1_000) == "0.001"
    assert from_units("2000000") == "2"
    assert from_units(0) == "0"
    assert from_units(-1_250_000) == "-1.25"
    assert from_units("0x3e8") == "0.001"
    with pytest.raises(ValueError, match="integer"):
        from_units("1.5")
