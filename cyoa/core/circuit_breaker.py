import time
import logging
from enum import Enum
from typing import Callable, Any, TypeVar, Generic

T = TypeVar("T")
logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker(Generic[T]):
    """
    A simple circuit breaker implementation.
    """
    def __init__(self, name: str, failure_threshold: int = 3, reset_timeout: float = 60.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0

    def __call__(self, func: Callable[..., T]) -> Callable[..., T]:
        """Allows using the circuit breaker as a decorator."""
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return self.call(func, *args, **kwargs)
        return wrapper

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Calls the function if the circuit is not open."""
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.reset_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info(f"Circuit breaker '{self.name}' entering HALF_OPEN state.")
            else:
                # Circuit is open, skip the call
                raise CircuitBreakerOpenError(f"Circuit breaker '{self.name}' is OPEN.")

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            # We only count certain types of exceptions as failures? 
            # For now, let's count all except maybe specific ones.
            self._on_failure(e)
            raise e

    def _on_success(self) -> None:
        if self.state != CircuitState.CLOSED:
            logger.info(f"Circuit breaker '{self.name}' entering CLOSED state.")
        self.state = CircuitState.CLOSED
        self.failure_count = 0

    def _on_failure(self, e: Exception) -> None:
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
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.reset_timeout:
                return True # Will transition to half-open on next call
            return False
        return True

class CircuitBreakerOpenError(Exception):
    """Exception raised when the circuit breaker is open."""
    pass
