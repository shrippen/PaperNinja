from __future__ import annotations

import pytest

from app.api_cache import cache
from app.login_limit import limiter
from app.settings import get_settings


@pytest.fixture(autouse=True)
def _reset_process_globals() -> None:
    cache.clear()
    limiter.reset()
    limiter.fail_delay_seconds = 0.0
    get_settings.cache_clear()
    yield
    cache.clear()
    limiter.reset()
    limiter.fail_delay_seconds = 0.0
    get_settings.cache_clear()
