from pathlib import Path

import pytest

from stockrelate.market_data import prepare_teaching_data

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TEACHING_SYMBOLS = ("A", "B", "C", "D", "E")


@pytest.fixture
def teaching_data():
    return prepare_teaching_data(TEACHING_SYMBOLS)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()
