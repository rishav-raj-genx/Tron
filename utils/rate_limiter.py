"""
Async-safe sliding-window rate limiter and ASGI middleware.

Provides in-memory rate limiting with zero external dependencies ($0 cost, no Redis)
keyed by client IP (inspecting X-Forwarded-For first hop when behind a reverse proxy)
with automatic stale-bucket pruning to keep memory strictly bounded.
"""

import math
import threading
import time
from typing import Any

from starlette.responses import JSONResponse


class SlidingWindowRateLimiter:
    """Async-safe in-memory sliding-window rate limiter with automatic stale-bucket pruning."""

    def __init__(
        self,
        requests: int,
        window_seconds: float = 60.0,
        prune_interval_seconds: float = 60.0,
        name: str = "rate_limiter",
    ) -> None:
        self.requests = requests
        self.window_seconds = window_seconds
        self.prune_interval_seconds = prune_interval_seconds
        self.name = name
        self._buckets: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._last_prune: float = time.time()

    def check_and_record(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """Check if an action is permitted under the sliding window.

        Args:
            key: Unique bucket identifier (e.g., client IP address).
            now: Optional current timestamp (defaults to time.time()).

        Returns:
            tuple[bool, int]: (allowed, retry_after_seconds)
        """
        if now is None:
            now = time.time()

        with self._lock:
            # Periodic stale-bucket pruning
            if now - self._last_prune >= self.prune_interval_seconds or len(self._buckets) > 1000:
                self._prune_stale_buckets_locked(now)

            cutoff = now - self.window_seconds
            timestamps = self._buckets.setdefault(key, [])

            # Prune expired timestamps in the active bucket
            while timestamps and timestamps[0] <= cutoff:
                timestamps.pop(0)

            # Check capacity
            if len(timestamps) >= self.requests:
                oldest = timestamps[0]
                retry_after = max(1, int(math.ceil(oldest + self.window_seconds - now)))
                return False, retry_after

            # Allow and record timestamp
            timestamps.append(now)
            return True, 0

    def _prune_stale_buckets_locked(self, now: float) -> int:
        cutoff = now - self.window_seconds
        stale_keys = [
            k for k, ts in self._buckets.items()
            if not ts or ts[-1] <= cutoff
        ]
        for k in stale_keys:
            del self._buckets[k]
        self._last_prune = now
        return len(stale_keys)

    def prune_stale_buckets(self, now: float | None = None) -> int:
        """Explicitly prune empty or expired IP buckets."""
        if now is None:
            now = time.time()
        with self._lock:
            return self._prune_stale_buckets_locked(now)

    def reset(self) -> None:
        """Clear all in-memory buckets (useful for test resets)."""
        with self._lock:
            self._buckets.clear()
            self._last_prune = time.time()


def extract_client_ip(source: Any) -> str:
    """Extract client IP, taking first hop of X-Forwarded-For if behind a proxy,

    falling back to client host, and defaulting to '127.0.0.1'.
    """
    # If source is an ASGI scope dict
    if isinstance(source, dict):
        headers = dict(source.get("headers", []))
        xff = headers.get(b"x-forwarded-for")
        if xff:
            first_hop = xff.decode("latin1").split(",")[0].strip()
            if first_hop:
                return first_hop
        client = source.get("client")
        if client and isinstance(client, (list, tuple)) and client[0]:
            return str(client[0])
        return "127.0.0.1"

    # If source is a Starlette / FastAPI Request
    if hasattr(source, "headers"):
        xff = source.headers.get("x-forwarded-for")
        if xff:
            first_hop = xff.split(",")[0].strip()
            if first_hop:
                return first_hop
    if hasattr(source, "client") and source.client and getattr(source.client, "host", None):
        return source.client.host

    return "127.0.0.1"


class RateLimiterRegistry:
    """Central registry of sliding-window rate limiters across routes."""

    def __init__(self) -> None:
        # 5 requests / minute per IP for agent persona initialization
        self.init_limiter = SlidingWindowRateLimiter(requests=5, window_seconds=60.0, name="agent_init")
        # 3 requests / minute per IP for manual LLM discovery/evaluation cycles
        self.cycle_limiter = SlidingWindowRateLimiter(requests=3, window_seconds=60.0, name="agent_cycle")
        # 120 requests / minute per IP for feed/status read polling
        self.read_limiter = SlidingWindowRateLimiter(requests=120, window_seconds=60.0, name="agent_read")

    def reset_all(self) -> None:
        self.init_limiter.reset()
        self.cycle_limiter.reset()
        self.read_limiter.reset()


rate_limiter_registry = RateLimiterRegistry()


class RateLimitMiddleware:
    """ASGI middleware applying IP-based sliding-window rate limits."""

    def __init__(self, app: Any, registry: RateLimiterRegistry | None = None) -> None:
        self.app = app
        self.registry = registry or rate_limiter_registry

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        client_ip = extract_client_ip(scope)

        limiter: SlidingWindowRateLimiter | None = None
        rule_name = ""

        if method == "POST" and path == "/api/agent/init":
            limiter = self.registry.init_limiter
            rule_name = "Agent initialization (POST /api/agent/init)"
        elif method == "POST" and path in {"/api/agent/cycle", "/api/agent/close-window"}:
            limiter = self.registry.cycle_limiter
            rule_name = "Agent cycle trigger (POST /api/agent/cycle)"
        elif method == "GET" and (
            path.startswith("/api/agent/")
            or path in {"/api/agents", "/api/feed", "/feed"}
        ):
            limiter = self.registry.read_limiter
            rule_name = "Agent read feed/status (GET /api/agent/*)"

        if limiter is not None:
            allowed, retry_after = limiter.check_and_record(client_ip)
            if not allowed:
                response = JSONResponse(
                    status_code=429,
                    content={
                        "detail": f"Too Many Requests: Rate limit exceeded for {rule_name}. Retry after {retry_after} seconds.",
                        "retry_after": retry_after,
                    },
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(limiter.requests),
                        "X-RateLimit-Window": f"{int(limiter.window_seconds)}s",
                    },
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)
