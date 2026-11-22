"""Package metadata and the public API surface."""

from importlib.metadata import version
from pathlib import Path

import railpay


def test_version_comes_from_the_package_metadata() -> None:
    assert railpay.__version__ == version("railpay")


def test_every_exported_name_exists() -> None:
    assert [name for name in railpay.__all__ if not hasattr(railpay, name)] == []
    assert len(set(railpay.__all__)) == len(railpay.__all__)


def test_ships_type_information() -> None:
    assert (Path(railpay.__file__).parent / "py.typed").is_file()
