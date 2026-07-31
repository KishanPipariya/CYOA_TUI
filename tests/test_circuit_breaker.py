import asyncio
from typing import cast

import pytest

from cyoa.core.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, CircuitState


def test_cb_sync_success():
    cb = CircuitBreaker("test", failure_threshold=2)

    def func(x):
        return x * 2

    assert cb.call(func, 5) == 10
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 0


def test_cb_sync_failure():
    cb = CircuitBreaker("test", failure_threshold=2)

    def func():
        raise ValueError("fail")

    with pytest.raises(ValueError):
        cb.call(func)
    assert cb.failure_count == 1
    assert cb.state == CircuitState.CLOSED

    with pytest.raises(ValueError):
        cb.call(func)
    assert cb.failure_count == 2
    assert cb.state == CircuitState.OPEN

    with pytest.raises(CircuitBreakerOpenError):
        cb.call(func)


@pytest.mark.asyncio
async def test_cb_async_success():
    cb = CircuitBreaker("test", failure_threshold=2)

    async def async_func(x):
        await asyncio.sleep(0.01)
        return x * 2

    result = await cb.async_call(async_func, 5)
    assert result == 10
    assert cb.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_cb_async_failure():
    cb = CircuitBreaker("test", failure_threshold=2)

    async def async_func():
        await asyncio.sleep(0.01)
        raise ValueError("async fail")

    with pytest.raises(ValueError):
        await cb.async_call(async_func)
    assert cb.failure_count == 1

    with pytest.raises(ValueError):
        await cb.async_call(async_func)
    assert cb.failure_count == 2
    assert cb.state == CircuitState.OPEN

    with pytest.raises(CircuitBreakerOpenError):
        await cb.async_call(async_func)


def test_cb_reset_timeout(monkeypatch: pytest.MonkeyPatch):
    cb: CircuitBreaker[object] = CircuitBreaker("test", failure_threshold=1, reset_timeout=0.1)
    clock = [100.0]
    monkeypatch.setattr("cyoa.core.circuit_breaker.time.time", lambda: clock[0])

    def func():
        raise ValueError("fail")

    with pytest.raises(ValueError):
        cb.call(func)
    assert cb.state == CircuitState.OPEN

    clock[0] += 0.11

    # Should transition to HALF_OPEN and then CLOSED on success
    def success_func():
        return "ok"

    assert cb.call(success_func) == "ok"
    assert cast(CircuitState, cb.state) is CircuitState.CLOSED


def test_cb_half_open_failure_reopens_circuit(monkeypatch: pytest.MonkeyPatch):
    cb: CircuitBreaker[object] = CircuitBreaker("test", failure_threshold=1, reset_timeout=0.05)
    clock = [100.0]
    monkeypatch.setattr("cyoa.core.circuit_breaker.time.time", lambda: clock[0])

    def fail():
        raise ValueError("still failing")

    with pytest.raises(ValueError):
        cb.call(fail)
    assert cb.state == CircuitState.OPEN

    clock[0] += 0.06
    with pytest.raises(ValueError):
        cb.call(fail)

    assert cb.state == CircuitState.OPEN
    assert cb.failure_count >= 2


def test_cb_is_available_reflects_timeout_window(monkeypatch: pytest.MonkeyPatch):
    cb: CircuitBreaker[object] = CircuitBreaker("test", failure_threshold=1, reset_timeout=0.05)
    clock = [100.0]
    monkeypatch.setattr("cyoa.core.circuit_breaker.time.time", lambda: clock[0])

    def fail():
        raise ValueError("fail")

    with pytest.raises(ValueError):
        cb.call(fail)

    assert cb.is_available is False
    clock[0] += 0.06
    assert cb.is_available is True
