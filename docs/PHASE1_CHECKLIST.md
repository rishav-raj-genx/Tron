# Phase 1: Production Hardening, Security, Observability & Resilience

## Checklist

- [x] **Step 1.1: Phase 1 Branch Setup, Boundary Input Validation, Sliding-Window Rate Limiting & HTTP Security Headers**
  - [x] Verify baseline tag `v0.1-phase0-baseline`, create and checkout branch `feat/phase-1-production`.
  - [x] Record Phase 0 baseline verification snapshot: 59/59 pytest passing, 0 ruff errors.
  - [x] Implement strict Pydantic input validation on `POST /api/agent/init` (`persona.name` 2-40 chars alphanumeric/space/hyphen, `persona.domain` 3-80 chars stripped of control chars/delimiters).
  - [x] Enforce `MAX_AGENTS` ceiling cleanly with an explicit `409 Conflict` JSON error.
  - [x] Validate `agentId` path and query parameters with strict format/length constraints (`max_length=64`).
  - [x] Add ASGI request body size limiter middleware rejecting bodies `> 32768` bytes with `413 Content Too Large` on mutating endpoints.
  - [x] Implement async-safe, in-memory sliding-window rate limiter (`utils/rate_limiter.py`) with automatic stale-bucket pruning keyed by client IP (with reverse proxy `X-Forwarded-For` header support).
  - [x] Apply rate limits: `POST /api/agent/init` (5 req/min), `POST /api/agent/cycle` (3 req/min), `GET /api/agent/*` (120 req/min), returning `429 Too Many Requests` with `Retry-After`.
  - [x] Add HTTP security headers middleware (`nosniff`, `DENY`, `strict-origin-when-cross-origin`, `camera=(), microphone=(), geolocation=()`, hardened CSP).
  - [x] Configure explicit CORS origins from `CORS_ORIGINS` setting (defaulting to safe local dev origins, never wildcard credentials).
  - [x] Add comprehensive test suite `tests/test_security_and_ratelimit.py` (20 new tests).
  - [x] Ensure 100% test pass (79/79 passing) and zero ruff lint errors.
- [ ] **Step 1.2: Structured JSON Logging, Correlation IDs & Request Tracing Middleware**
  - [ ] Structured JSON log formatter with contextual fields (`correlation_id`, `agent_id`, `duration_ms`).
  - [ ] ASGI correlation ID middleware injecting `X-Request-ID` across all inbound requests.
  - [ ] Propagate correlation ID across background discovery, evaluation iterations, and publishing windows.
- [ ] **Step 1.3: Upstream Resilience, Dynamic Exponential Backoff Jitter & Circuit Breaker Failover**
  - [ ] Dynamic exponential backoff with full jitter for Gemini Search Grounding and REST invocations.
  - [ ] Circuit breaker state machine for secondary Groq failovers during sustained upstream network outages.
  - [ ] Transient failure handling with automatic fail-closed deduplication protection.
- [ ] **Step 1.4: Evaluation Observability, Prometheus Metrics & Live Health Telemetry**
  - [ ] Prometheus metrics endpoint (`/metrics`) exposing cycle counts, scoring distributions, LLM latency, and window status.
  - [ ] Deep health check (`/healthz` and `/health`) reporting SQLite WAL integrity, scheduler health, and provider status.
- [ ] **Step 1.5: Production Deployment Verification, Container Health Audit & Phase 1 Completion Tag**
  - [ ] Multi-stage production Dockerfile optimization and security scan.
  - [ ] Free-tier cloud container deployment verification (Render / Railway / Fly.io).
  - [ ] Final end-to-end regression audit across all test suites.
  - [ ] Tag milestone `v1.0-phase1-production`.

---

## Baseline Verification Snapshot (v0.1-phase0-baseline)

- **Git Branch:** `feat/phase-1-production` (branched from `v0.1-phase0-baseline`)
- **Pytest Suite:** 59/59 passing in 0.99s
- **Ruff Linter:** 0 errors (all checks passed)
- **Active Test Suites:**
  - `tests/stress_test_qa.py`: 1 passed
  - `tests/test_autonomy_integration.py`: 3 passed
  - `tests/test_discovery_fallback.py`: 8 passed
  - `tests/test_duplicate_prevention.py`: 10 passed
  - `tests/test_editorial_and_twitter.py`: 4 passed
  - `tests/test_hackathon_spec.py`: 7 passed
  - `tests/test_llm_service.py`: 19 passed
  - `tests/test_migration_integrity.py`: 3 passed
  - `tests/test_t30_publisher.py`: 4 passed

---

## Step 1.1 Implementation & Audit Report

### 1. Boundary Input Validation & Parameter Hardening
- **Pydantic Request Models (`main.py`):**
  - Hardened `PersonaInitPayload` and `AgentInitRequest` with field validators.
  - Enforced `min_length=2, max_length=40` on `persona.name` with alphanumeric/spaces/hyphens whitelist (`^[a-zA-Z0-9 -]+$`).
  - Enforced `min_length=3, max_length=80` on `persona.domain` with control-character/newline rejection (`[\x00-\x1f\x7f-\x9f]`) preventing prompt-injection delimiter escapes.
  - Bound parameter checks on `activeAgentId`, `agent_id`, `agentId`, and `windowId` with `max_length=64` and pattern `^[a-zA-Z0-9_\-]+$` across all routes (`/api/agents`, `/api/agent/init`, `/api/agent/{agent_id}`, `/api/agent/feed`, `/api/agent/status`, `/api/agent/cycle`, `/api/agent/close-window`).
- **Capacity Ceiling Enforcement (`main.py`):**
  - Enforces `MAX_AGENTS` ceiling cleanly: returns HTTP `409 Conflict` with explicit JSON detail when capacity is full.
- **Payload Size Limiter Middleware (`main.py`):**
  - Added lightweight ASGI `RequestSizeLimitMiddleware` rejecting request bodies `> 32768` bytes (`413 Content Too Large` / `Payload Too Large`) on mutating HTTP methods (`POST`, `PUT`, `PATCH`, `DELETE`).

### 2. Sliding-Window Rate Limiting & Security Headers
- **In-Memory Rate Limiter Utility (`utils/rate_limiter.py`):**
  - Thread-safe and async-safe `SlidingWindowRateLimiter` implementing sliding-window time buckets with automatic stale-bucket pruning.
  - Client IP extraction helper `extract_client_ip` parsing the first hop of `X-Forwarded-For` (for proxy/load-balancer deployments) with fallback to `request.client.host`.
  - Rate limits configured:
    - `POST /api/agent/init`: 5 requests / minute per client IP.
    - `POST /api/agent/cycle` & `/api/agent/close-window`: 3 requests / minute per client IP.
    - `GET /api/agent/*` (`/api/agents`, `/api/agent/feed`, `/api/agent/status`, `/api/feed`, `/feed`): 120 requests / minute per client IP.
    - Rate limit responses return HTTP `429 Too Many Requests` with `Retry-After`, `X-RateLimit-Limit`, and `X-RateLimit-Window` headers.
- **HTTP Security Headers Middleware (`main.py`):**
  - Injects `X-Content-Type-Options: nosniff`
  - Injects `X-Frame-Options: DENY`
  - Injects `Referrer-Policy: strict-origin-when-cross-origin`
  - Injects `Permissions-Policy: camera=(), microphone=(), geolocation=()`
  - Injects `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https:; connect-src 'self' https:; frame-ancestors 'none';`
- **CORS Hardening (`config/settings.py` & `main.py`):**
  - Reconciled `CORS_ORIGINS` setting with default local development origins (`http://localhost:8080`, `http://127.0.0.1:8080`, `http://localhost:3000`, `http://127.0.0.1:3000`).
  - Enforced disallowance of wildcard origins (`*`) with credentials.

### 3. Test Verification Snapshot
- **Total Tests:** 79 passing (100% pass rate in ~1.3s)
- **New Test Suite:** `tests/test_security_and_ratelimit.py` (20 new tests)
  - `TestInputValidationAndPayloadGuards` (11 tests)
  - `TestMaxAgentsEnforcement` (1 test)
  - `TestSlidingWindowRateLimiting` (5 tests)
  - `TestSecurityHeadersAndCORS` (3 tests)
- **Linter Status:** `ruff check .` passes with 0 errors.
