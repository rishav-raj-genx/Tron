"""
Thread-safe and async-safe Circuit Breaker pattern implementation.

Protects upstream LLM providers (Gemini, Groq) from cascading failures.
Tracks provider health across CLOSED, OPEN, and HALF_OPEN states:
- CLOSED: Normal operation; tracks consecutive transient failures.
- OPEN: Trips after N consecutive failures; fast-fails immediately without network latency.
- HALF_OPEN: Probes upstream once after cooldown; recovers to CLOSED on success or trips to OPEN on failure.
"""

import logging
import threading
import time
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """Operational states of a Circuit Breaker."""
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenError(Exception):
    """Exception raised when an invocation is rejected because the circuit is OPEN."""

    def __init__(self, provider: str, retry_after: float) -> None:
        self.provider = provider
        self.retry_after = retry_after
        super().__init__(
            f"Circuit breaker for provider '{provider}' is OPEN. "
            f"Cooldown active for {max(0.0, retry_after):.1f}s."
        )


def is_transient_error(exc: Exception) -> bool:
    """Determine whether an exception represents a transient/recoverable failure.

    Retries and trips on 429 (rate limits), 5xx (server errors), and network/timeout errors.
    Explicitly ignores 400 (Bad Request), 401 (Unauthorized), and 403 (Forbidden).
    """
    if exc is None:
        return False

    # Check for HTTP status code attribute (e.g. from ProviderError, httpx.HTTPStatusError)
    status_code = getattr(exc, "status_code", None)
    if status_code is None and hasattr(exc, "response") and hasattr(exc.response, "status_code"):
        status_code = exc.response.status_code

    if status_code is not None:
        # Non-transient client/auth errors: never retry or trip circuit
        if status_code in (400, 401, 403, 404, 422):
            return False
        # Transient errors: rate limits or server-side issues
        if status_code in (429, 500, 502, 503, 504):
            return True

    # Check exception types
    exc_type_name = type(exc).__name__.lower()
    if any(k in exc_type_name for k in ("timeout", "connection", "network", "connecterror", "readerror")):
        return True

    # Check error message strings
    msg = str(exc).lower()
    if any(k in msg for k in ("429", "too many requests", "resourceexhausted", "quota", "timeout", "timed out", "503", "502", "504", "connection reset")):
        return True

    return False


class CircuitBreaker:
    """Async-safe and thread-safe circuit breaker with automatic half-open recovery."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._half_open_probe_active = False
        self._lock = threading.Lock()

    def get_state(self, now: float | None = None) -> CircuitState:
        if now is None:
            now = time.time()
        with self._lock:
            self._check_cooldown_locked(now)
            return self._state

    @property
    def state(self) -> CircuitState:
        return self.get_state()

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count

    def _check_cooldown_locked(self, now: float) -> None:
        """Transition OPEN to HALF_OPEN if recovery cooldown has elapsed."""
        if self._state == CircuitState.OPEN:
            elapsed = now - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_probe_active = False
                logger.info(
                    f"[CIRCUIT_BREAKER] {self.name}: OPEN -> HALF_OPEN "
                    f"(cooldown {self.recovery_timeout}s elapsed, probing recovery)"
                )

    def can_execute(self, now: float | None = None) -> bool:
        """Check if an execution is allowed through the circuit breaker."""
        if now is None:
            now = time.time()

        with self._lock:
            self._check_cooldown_locked(now)

            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.HALF_OPEN:
                if not self._half_open_probe_active:
                    self._half_open_probe_active = True
                    return True
                return False

            # Circuit is OPEN
            return False

    def remaining_cooldown(self, now: float | None = None) -> float:
        """Return remaining cooldown seconds until half-open probe is allowed."""
        if now is None:
            now = time.time()
        with self._lock:
            if self._state != CircuitState.OPEN:
                return 0.0
            return max(0.0, self.recovery_timeout - (now - self._last_failure_time))

    def record_success(self) -> None:
        """Record a successful execution, resetting failures and recovering to CLOSED."""
        with self._lock:
            prev_state = self._state
            self._failure_count = 0
            self._half_open_probe_active = False
            self._state = CircuitState.CLOSED
            if prev_state != CircuitState.CLOSED:
                logger.info(f"[CIRCUIT_BREAKER] {self.name}: {prev_state.value} -> CLOSED (health restored)")

    def record_failure(self, exc: Exception | None = None, now: float | None = None) -> None:
        """Record a failed execution. Trips circuit to OPEN if threshold is reached."""
        if now is None:
            now = time.time()

        with self._lock:
            self._last_failure_time = now
            self._half_open_probe_active = False

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logger.warning(
                    f"[CIRCUIT_BREAKER] {self.name}: HALF_OPEN -> OPEN "
                    f"(recovery probe failed: {exc})"
                )
                return

            if self._state == CircuitState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    logger.warning(
                        f"[CIRCUIT_BREAKER] {self.name}: CLOSED -> OPEN "
                        f"(tripped after {self._failure_count} consecutive failures: {exc})"
                    )

    def reset(self) -> None:
        """Force reset circuit breaker to clean CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = 0.0
            self._half_open_probe_active = False


# Provider singletons for unified LLM routing
gemini_breaker = CircuitBreaker("gemini", failure_threshold=3, recovery_timeout=60.0)
groq_breaker = CircuitBreaker("groq", failure_threshold=3, recovery_timeout=60.0)
