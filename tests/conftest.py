from datetime import datetime, timezone
from pathlib import Path

import pytest

from finops import config

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg():
    return config.load(ROOT / "config" / "auditor.toml")


@pytest.fixture
def now():
    # Wed 14 Oct 2026 08:00 Bangkok time
    return datetime(2026, 10, 14, 1, 0, tzinfo=timezone.utc)


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)
