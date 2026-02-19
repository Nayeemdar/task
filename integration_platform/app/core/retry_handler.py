"""
Retry handler with exponential backoff and a simple circuit breaker.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, Optional, Type

from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class CircuitState(str, Enum):
    CLOSED = "closed"      # Normal operation — requests pass through
    OPEN = "open"          # Too many failures — requests blocked
    HALF_OPEN = "half_open"  # Probe request allowed to test recovery


@dataclass
class CircuitBreaker:
    service_name: str
    failure_threshold: int = settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD
    recovery_timeout: int = settings.CIRCUIT_BREAKER_RECOVERY_TIMEOUT

    _state: CircuitState = CircuitState.CLOSED
    _failure_count: int = 0
    _last_failure_time: Optional[float] = None

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.monotonic() - (self._last_failure_time or 0) > self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
        return self._state

    def record_success(self) -> None:
        self._failure_count = 0
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker OPEN for '%s' after %d failures",
                self.service_name, self._failure_count,
            )

    def allow_request(self) -> bool:
        return self.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)


class RetryHandler:
    """
    Executes an async callable with configurable retry and circuit-breaker logic.
    """

    def __init__(
        self,
        max_attempts: int = settings.RETRY_MAX_ATTEMPTS,
        base_delay: float = settings.RETRY_BASE_DELAY_SECONDS,
    ):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self._breakers: Dict[str, CircuitBreaker] = {}

    def get_breaker(self, service_name: str) -> CircuitBreaker:
        if service_name not in self._breakers:
            self._breakers[service_name] = CircuitBreaker(service_name=service_name)
        return self._breakers[service_name]

    async def execute(
        self,
        fn: Callable[..., Coroutine[Any, Any, Any]],
        *args,
        service_name: str = "unknown",
        retryable_exceptions: tuple[Type[Exception], ...] = (Exception,),
        **kwargs,
    ) -> Any:
        breaker = self.get_breaker(service_name)

        if not breaker.allow_request():
            raise RuntimeError(
                f"Circuit breaker is OPEN for '{service_name}'. Requests blocked."
            )

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                result = await fn(*args, **kwargs)
                breaker.record_success()
                return result
            except retryable_exceptions as exc:
                last_exc = exc
                breaker.record_failure()
                if attempt < self.max_attempts:
                    delay = self.base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "Attempt %d/%d failed for '%s': %s. Retrying in %.1fs…",
                        attempt, self.max_attempts, service_name, exc, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "All %d attempts exhausted for '%s': %s",
                        self.max_attempts, service_name, exc,
                    )

        raise last_exc  # type: ignore[misc]

    def get_all_states(self) -> Dict[str, str]:
        return {name: breaker.state.value for name, breaker in self._breakers.items()}
