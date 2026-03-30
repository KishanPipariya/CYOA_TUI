import time
import logging
import asyncio
import threading
from enum import Enum
from typing import Callable, Any, TypeVar, Generic, Coroutine, Union, Awaitable

T = TypeVar("T")
logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker(Generic[T]):
    """
    A circuit breaker implementation that is both thread-safe and async-safe.
    """
    def __init__(self, name: str, failure_threshold: int = 3, reset_timeout: float = 60.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0
        self._lock = threading.RLock()
        self._async_lock: asyncio.Lock | None = None

    def _get_async_lock(self) -> asyncio.Lock:
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Calls the function if the circuit is not open. Thread-safe.
        Note: Use async_call for coroutine functions.
        """
        with self._lock:
            self._check_state()
            if self.state == CircuitState.OPEN:
                raise CircuitBreakerOpenError(f"Circuit breaker '{self.name}' is OPEN.")

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure(e)
            raise e

    async def async_call(self, func: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T:
        """
        Calls an async function if the circuit is not open. Async-safe.
        """
        async_lock = self._get_async_lock()
        async with async_lock:
            with self._lock: 
                self._check_state()
                if self.state == CircuitState.OPEN:
                    raise CircuitBreakerOpenError(f"Circuit breaker '{self.name}' is OPEN.")

        try:
            result = await func(*args, **kwargs)
            # Use async lock to serialize success/failure updates from different coroutines
            async with async_lock:
                self._on_success()
            return result
        except Exception as e:
            async with async_lock:
                self._on_failure(e)
            raise e

    def _check_state(self) -> None:
        """Internal helper to check and transition state based on timeout. Should be called under lock."""
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.reset_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info(f"Circuit breaker '{self.name}' entering HALF_OPEN state.")

    def _on_success(self) -> None:
        with self._lock:
            if self.state != CircuitState.CLOSED:
                logger.info(f"Circuit breaker '{self.name}' entering CLOSED state.")
            self.state = CircuitState.CLOSED
            self.failure_count = 0

    def _on_failure(self, e: Exception) -> None:
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            logger.warning(f"Circuit breaker '{self.name}' failure {self.failure_count}: {e}")
            if self.failure_count >= self.failure_threshold:
                if self.state != CircuitState.OPEN:
                    logger.error(f"Circuit breaker '{self.name}' entering OPEN state.")
                self.state = CircuitState.OPEN

    @property
    def is_available(self) -> bool:
        """Returns True if the circuit is closed or half-open."""
        with self._lock:
            if self.state == CircuitState.OPEN:
                if time.time() - self.last_failure_time > self.reset_timeout:
                    return True  # Will transition to half-open on next call
                return False
            return True

class CircuitBreakerOpenError(Exception):
    """Exception raised when the circuit breaker is open."""
    pass
