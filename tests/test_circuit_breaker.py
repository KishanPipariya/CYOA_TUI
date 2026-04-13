import asyncio
import time

import pytest

from cyoa.core.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, CircuitState


def test_cb_sync_success():
    cb = CircuitBreaker("test", failure_threshold=2)
    def func(x): return x * 2
    assert cb.call(func, 5) == 10
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 0

def test_cb_sync_failure():
    cb = CircuitBreaker("test", failure_threshold=2)
    def func(): raise ValueError("fail")

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

def test_cb_reset_timeout():
    cb = CircuitBreaker("test", failure_threshold=1, reset_timeout=0.1)
    def func(): raise ValueError("fail")

    with pytest.raises(ValueError):
        cb.call(func)
    assert cb.state == CircuitState.OPEN

    time.sleep(0.15)

    # Should transition to HALF_OPEN and then CLOSED on success
    def success_func(): return "ok"
    assert cb.call(success_func) == "ok"
    assert cb.state == CircuitState.CLOSED


def test_cb_half_open_failure_reopens_circuit():
    cb = CircuitBreaker("test", failure_threshold=1, reset_timeout=0.05)

    def fail():
        raise ValueError("still failing")

    with pytest.raises(ValueError):
        cb.call(fail)
    assert cb.state == CircuitState.OPEN

    time.sleep(0.06)
    with pytest.raises(ValueError):
        cb.call(fail)

    assert cb.state == CircuitState.OPEN
    assert cb.failure_count >= 2


def test_cb_is_available_reflects_timeout_window():
    cb = CircuitBreaker("test", failure_threshold=1, reset_timeout=0.05)

    def fail():
        raise ValueError("fail")

    with pytest.raises(ValueError):
        cb.call(fail)

    assert cb.is_available is False
    time.sleep(0.06)
    assert cb.is_available is True
