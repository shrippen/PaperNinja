from __future__ import annotations

from app.login_limit import LoginLimiter


def test_lock_after_max_failures() -> None:
    clock = {"now": 0.0}
    limiter = LoginLimiter(
        max_failures=3, lock_seconds=30, clock=lambda: clock["now"]
    )
    assert limiter.fail("ip") == 0
    assert limiter.fail("ip") == 0
    assert limiter.fail("ip") == 30
    assert limiter.locked_seconds("ip") == 30
    assert limiter.fail("ip") == 30


def test_lock_expires_and_resets() -> None:
    clock = {"now": 0.0}
    limiter = LoginLimiter(
        max_failures=2, lock_seconds=10, clock=lambda: clock["now"]
    )
    limiter.fail("ip")
    limiter.fail("ip")
    clock["now"] = 10.1
    assert limiter.locked_seconds("ip") == 0
    assert limiter.fail("ip") == 0
    assert limiter.fail("ip") == 10


def test_success_clears_failures() -> None:
    limiter = LoginLimiter(max_failures=3, lock_seconds=60)
    limiter.fail("ip")
    limiter.fail("ip")
    limiter.success("ip")
    assert limiter.fail("ip") == 0
    assert limiter.fail("ip") == 0
    assert limiter.fail("ip") == 60


def test_keys_are_independent() -> None:
    limiter = LoginLimiter(max_failures=2, lock_seconds=9)
    limiter.fail("a")
    limiter.fail("a")
    assert limiter.locked_seconds("a") > 0
    assert limiter.locked_seconds("b") == 0
