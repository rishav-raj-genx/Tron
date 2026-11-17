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
- [x] **Step 1.2: SQLite WAL Query Optimization, Composite Indexing, Backward-Compatible Feed Pagination & Deep `/healthz`**
  - [x] Audit all SQL statements ensuring zero string interpolation with bound `?` parameters.
  - [x] Configure connection pragmas on checkout (`PRAGMA journal_mode=WAL;`, `PRAGMA busy_timeout=15000;`, `PRAGMA synchronous=NORMAL;`, `PRAGMA foreign_keys=ON;`).
  - [x] Add composite indexes covering keyset cursor pagination (`(agent_id, created_at DESC, id DESC)`, `(created_at DESC, id DESC)`) and deduplication (`(agent_id, topic_title)`, `(agent_id, topic_hash, status)`).
  - [x] Implement atomic write transactions with `with conn:` and foreign-key safe cascade order.
  - [x] Upgrade `GET /api/agent/feed` with bounded keyset cursor pagination (`limit: 1-50`, `cursor: max 128`), preserving 100% backward-compatible `"posts"` schema while exposing `"nextCursor"`.
  - [x] Upgrade `GET /healthz` to perform deep SQLite liveness verification (`SELECT 1`, `PRAGMA journal_mode == "wal"`, active agents) returning HTTP 200 or HTTP 503 on database operational error.
  - [x] Add comprehensive test suite `tests/test_db_optimization.py` (7 new tests).
  - [x] Ensure 100% test pass (86/86 passing) and zero ruff lint errors.
- [x] **Step 1.3: Upstream Resilience, Dynamic Exponential Backoff Jitter & Circuit Breaker Failover**
  - [x] Enforce explicit connect + read timeouts on every outbound network call (Gemini SDK/REST, Groq SDK/REST, arXiv Atom API).
  - [x] Implement thread/async-safe Circuit Breaker (`utils/circuit_breaker.py`) with `CLOSED`, `OPEN`, and `HALF_OPEN` states, independently wrapping Gemini and Groq.
  - [x] Fast failover from primary (Gemini) to fallback (Groq) without wasting timeout windows when circuit is `OPEN`.
  - [x] Dynamic exponential backoff retries (max 2 attempts) with jitter for transient errors (429, 5xx, timeouts); fatal errors (400, 401, 403) never retried.
  - [x] Prompt injection isolation boundaries (`<untrusted_candidate_content>...</untrusted_candidate_content>`) and maximum character caps across `services/topic_discovery.py` and `services/editorial_engine.py`.
  - [x] Strict source URL validation (`utils/url_validator.py`): public `http/https` only, stripping `javascript:`, `data:`, `file:`, localhost, link-local (`169.254.0.0/16`), and private IP ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`).
  - [x] Add comprehensive test suite `tests/test_resilience_and_guardrails.py` (16 new tests).
  - [x] Ensure 100% test pass (102/102 passing) and zero ruff lint errors.
- [x] **Step 1.4: Structured JSON Observability, Correlation IDs & Portfolio-Grade Frontend Hardening**
  - [x] Create `utils/logging.py` providing structured JSON log formatting (ISO-8601 UTC timestamps, level, logger, message, `request_id`, `cycle_id`, and extra attributes).
  - [x] Implement regex-based secret redaction masking Gemini API keys (`AIza...`), Groq keys (`gsk_...`), Bearer authorization headers, and query parameter keys.
  - [x] Add `RequestCorrelationMiddleware` in `main.py` accepting/validating incoming `X-Request-ID` or generating UUID4, binding via `contextvars`, logging HTTP telemetry (`http_method`, `http_path`, `http_status`, `duration_ms`), and echoing `X-Request-ID` in response headers.
  - [x] Instrument `services/autonomous_publisher.py` with unique `cycle_id` per run, emitting structured cycle telemetry (`candidates_discovered`, `candidates_scored`, `top_score`, `rejected_below_threshold`, `rejected_duplicate`, `published_post_id`, `llm_provider_used`, `cycle_duration_ms`).
  - [x] Audit frontend DOM rendering in `static/app.js`: eliminated all unescaped dynamic `innerHTML` in favor of `document.createElement` + `.textContent`, and enforced strict URL validation on all rendered `<a href>` links (`https://` or `http://` with `target="_blank"` and `rel="noopener noreferrer"`).
  - [x] Enhance frontend UX & resilience: added informative empty-state card detailing Tron's 75.0/100 multi-factor quality bar, friendly 429 rate limit cooldown notice with button countdown, and responsive styling for mobile (375px) and desktop (1280px).
  - [x] Add comprehensive test suite `tests/test_observability.py` (11 new tests).
  - [x] Ensure 100% test pass (113/113 passing) and zero ruff lint errors.
- [x] **Step 1.5: Automated GitHub Actions CI Pipeline, Non-Root Docker Hardening, Docs Alignment & Phase 1 Release Tag**
  - [x] Create multi-stage `.github/workflows/ci.yml` running Gitleaks secret detection, Python 3.11 dependency caching, Ruff linter, offline Pytest suite, and live Docker container build & smoke healthcheck.
  - [x] Harden `Dockerfile`: added dedicated unprivileged non-root user (`appuser`), writable `/app/data` directory for persistent SQLite WAL mode files, and native standard-library Python `HEALTHCHECK` probing `http://127.0.0.1:${PORT:-8080}/healthz` with zero curl bloat.
  - [x] Align `.env.example` documenting `DATABASE_PATH`, `CORS_ORIGINS`, rate-limit thresholds, and structured JSON logging.
  - [x] Update `README.md` with **Production Hardening & Architecture Highlights** showcase table, updated configuration reference, and passing test/CI badges.
  - [x] Verify zero untracked `.db*` or `.env` files.
  - [x] Complete final verification audit (113/113 pytest passing, 0 ruff errors).
  - [x] Tag production release milestone `v1.0-phase1-prod`.

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

---

## Step 1.2 Implementation & Audit Report

### 1. SQLite Pragmas, Schema Indexing & Write Atomicity
- **Connection Pragmas (`services/memory.py`):**
  - `PRAGMA journal_mode=WAL;` (concurrent readers with single writer).
  - `PRAGMA busy_timeout=15000;` (15s internal lock retry before raising busy exception).
  - `PRAGMA synchronous=NORMAL;` (reduces fsync overhead while maintaining crash durability).
  - `PRAGMA foreign_keys=ON;` (enforces relational integrity across all tables).
- **Composite Indexes (`services/memory.py`):**
  - `idx_posts_agent_created_id` on `feed_posts(agent_id, created_at DESC, id DESC)` for $O(1)$ agent feed keyset seek.
  - `idx_posts_created_id` on `feed_posts(created_at DESC, id DESC)` for global feed seek.
  - `idx_decisions_agent_title` on `editorial_decisions(agent_id, topic_title)` for fast topic decision deduplication.
  - `idx_candidates_agent_hash_status` on `news_candidates(agent_id, topic_hash, status)` for published candidate coverage checks.
- **Write Atomicity & Minimal Lock Duration:**
  - All write transactions execute atomically under `with conn:` context managers.
  - Strict foreign-key deletion order: `x_publication_records` -> `news_candidates` -> `publishing_windows` -> `feed_posts` -> `editorial_decisions` -> `agents`.
  - Zero lock hold time across asynchronous network or LLM operations.

### 2. Keyset Cursor Pagination & Deep `/healthz`
- **Keyset Cursor Pagination (`services/memory.py` & `main.py`):**
  - Implemented opaque URL-safe cursor serialization (`encode_cursor` / `decode_cursor`) on `(created_at, id)`.
  - `GET /api/agent/feed` accepts `limit: int = Query(default=20, ge=1, le=50)` and optional `cursor: str = Query(default=None, max_length=128)`.
  - Maintains 100% backward compatibility: returns `{"posts": [...], "nextCursor": "..."}` with exact existing fields (`id`, `createdAt`, `text`, `rationale`, `sources`).
- **Deep Health Check (`main.py`):**
  - `GET /healthz` executes `memory_store.check_liveness()`, executing `SELECT 1;`, verifying `PRAGMA journal_mode == "wal"`, scheduler status, and active agent count.
  - Returns HTTP `200` with `{"status": "ok", "db": "wal", "scheduler": "running", "active_agents": ...}` when healthy.
  - Returns HTTP `503 Service Unavailable` with diagnostic error details if database access raises an operational exception.

### 3. Test Verification Snapshot
- **Total Tests:** 86 passing (100% pass rate in ~1.36s)
- **New Test Suite:** `tests/test_db_optimization.py` (7 new tests)
  - Connection pragmas verification (4 pragmas).
  - Composite indexes presence in `sqlite_master`.
  - Keyset cursor serialization roundtrip.
  - Limit boundary validation (0, 100, negative rejected with 422).
  - Keyset pagination non-overlapping pages and schema preservation.
  - Deep `/healthz` probe on healthy database (200).
  - Deep `/healthz` probe on database failure (503).
- **Linter Status:** `ruff check .` passes with 0 errors.

---

## Step 1.3 Implementation & Audit Report

### 1. Outbound Timeouts, Retries & Circuit Breaker
- **Explicit Network Timeouts:**
  - Gemini & Groq REST/SDK calls (`services/llm.py`): Explicit `httpx.Timeout(30.0, connect=10.0, read=25.0)` and `asyncio.wait_for(..., timeout=30.0)` prevent hung upstream connections.
  - Gemini Grounding Web Search (`tools/shared/web_search.py`): Explicit `httpx.Timeout(20.0, connect=5.0, read=15.0)`.
  - arXiv Fallback Retrieval (`services/topic_discovery.py`): Explicit `httpx.Timeout(15.0, connect=5.0, read=10.0)`.
- **Circuit Breaker State Machine (`utils/circuit_breaker.py`):**
  - Thread/async-safe Circuit Breaker with `CLOSED`, `OPEN`, and `HALF_OPEN` states.
  - Trips provider to `OPEN` after `failure_threshold=3` consecutive transient failures.
  - `OPEN` state short-circuits calls immediately without invoking network requests, fast-failing to the next provider.
  - Automatic `HALF_OPEN` probe after `recovery_timeout=60.0s`: single test probe recovers circuit to `CLOSED` on success or returns to `OPEN` on failure.
  - Providers wrapped independently via `gemini_breaker` and `groq_breaker` singletons.
- **Transient-Only Retry & Error Classification:**
  - Retries use bounded exponential backoff with jitter (`(2 ** attempt) + uniform(0.1, 0.5)`).
  - Retries and trips only transient errors (`429`, `5xx`, connection resets, timeouts).
  - Non-transient errors (`400`, `401`, `403`, `404`, `422`) fail fast, never retry, and never trip the breaker.

### 2. Prompt Injection Isolation & Source URL Sanitization
- **Prompt Injection Defense (`services/topic_discovery.py`, `services/editorial_engine.py`):**
  - All untrusted candidate search results, article summaries, and scraped content are wrapped in explicit XML-style data boundaries: `<untrusted_candidate_content>...</untrusted_candidate_content>`.
  - System prompts instruct the LLM to treat content within those tags strictly as untrusted raw data and ignore all embedded instructions, commands, or format overrides.
  - Search snippets capped at 3,500 characters, titles at 200 characters, and summaries at 1,500 characters to prevent context-window bloat and token spikes.
- **Strict Source URL Sanitization (`utils/url_validator.py`):**
  - Validates public `http://` and `https://` schemes with well-formed hostnames.
  - Rejects SSRF vectors, non-HTTP schemes (`javascript:`, `data:`, `file:`, `ftp:`), `localhost`, loopback (`127.0.0.0/8`), link-local metadata endpoints (`169.254.0.0/16`), and private IP ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`).
  - Applied across `TopicDiscoveryService`, `EditorialEngine`, `AutonomousPublisherService`, and `tools/shared/web_search.py`.

### 3. Test Verification Snapshot
- **Total Tests:** 102 passing (100% pass rate in ~1.53s)
- **New Test Suite:** `tests/test_resilience_and_guardrails.py` (16 new tests)
  - Circuit breaker initial state `CLOSED`.
  - Tripping to `OPEN` after threshold (3) transient failures.
  - Recovery cooldown to `HALF_OPEN` and recovery to `CLOSED` on probe success.
  - Probe failure in `HALF_OPEN` tripping back to `OPEN`.
  - Transient vs non-transient error classification (400, 401, 403, 404, 422 vs 429, 500, 502, 503, 504).
  - Fast failover from Gemini to Groq when Gemini circuit is `OPEN`.
  - 3 transient Gemini errors tripping circuit and falling over to Groq.
  - 401 Unauthorized not retrying and not tripping breaker.
  - Both circuits `OPEN` raising `ProviderError`.
  - Valid public URLs accepted.
  - Non-HTTP schemes rejected.
  - SSRF, localhost, link-local, and private IPs rejected.
  - URL sanitization, deduplication, and trailing punctuation cleanup.
  - Candidate topic extraction prompt containing `<untrusted_candidate_content>` isolation tags.
  - Candidate search snippet capping at 3,500 characters.
  - Editorial synthesis prompt isolating candidate content and sanitizing source URLs.
- **Linter Status:** `ruff check .` passes with 0 errors.

---

## Step 1.4 Implementation & Audit Report

### 1. Structured JSON Logging, Redaction & Correlation IDs
- **Structured JSON Formatter (`utils/logging.py`):**
  - Configured `StructuredJsonFormatter` emitting valid single-line JSON with ISO-8601 UTC timestamps, log level, logger name, redacted message, active correlation IDs (`request_id`, `cycle_id`), and arbitrary custom extra fields.
  - Context propagation via Python `contextvars` (`request_id_ctx`, `cycle_id_ctx`) ensuring seamless trace correlation across asynchronous tasks.
- **Automated Secret Redaction (`utils/logging.py`):**
  - Regex-based redaction engine masking Gemini API keys (`AIza[0-9A-Za-z-_]{35}` -> `AIza[REDACTED_GEMINI_KEY]`), Groq keys (`gsk_[0-9A-Za-z]{20,}` -> `gsk_[REDACTED_GROQ_KEY]`), Bearer authorization tokens (`Bearer [REDACTED_TOKEN]`), and URL query parameter keys (`key=[REDACTED]`).
- **HTTP Request Correlation Middleware (`main.py`):**
  - Inspects incoming `X-Request-ID` header; validates alphanumeric and hyphen format (`<= 64` chars). Generates UUID4 if missing or invalid.
  - Binds `request_id` to contextvars for the duration of the request.
  - Measures request latency (`duration_ms`) and logs structured HTTP telemetry (`http_method`, `http_path`, `http_status`, `duration_ms`, `request_id`).
  - Echoes `X-Request-ID` in outgoing HTTP response headers.
- **Autonomous Publisher Cycle Telemetry (`services/autonomous_publisher.py`):**
  - Generates unique `cycle_id` (`cyc-...`) per discovery and evaluation run, bound to `cycle_id_ctx`.
  - Emits structured completion telemetry at cycle close: `candidates_discovered`, `candidates_scored`, `top_score`, `rejected_below_threshold`, `rejected_duplicate`, `published_post_id`, `llm_provider_used`, and `cycle_duration_ms`.

### 2. Frontend DOM XSS Audit & Portfolio UX Polish
- **DOM XSS Elimination (`static/app.js`):**
  - Eliminated all dynamic `innerHTML` usage across agent lists, window status cards, and feed items.
  - Replaced with programmatic DOM node creation (`document.createElement`) and strict `.textContent` assignment, rendering injection attacks inert.
  - Validates all source links: strictly enforces `https://` or `http://` protocols, setting `target="_blank"` and `rel="noopener noreferrer"`.
- **UX & Resilience Polish (`static/app.js`, `static/styles.css`):**
  - Added informative empty-state card for agents with 0 published stories, explaining Tron's 75.0/100 multi-factor editorial quality bar.
  - Added graceful 429 Too Many Requests handling with friendly cooldown toast and countdown on the submit button.
  - Mobile responsiveness polished for 375px viewports (fluid headers, card padding, single-column grids) and 1280px desktop displays. Zero browser console errors.

### 3. Test Verification Snapshot
- **Total Tests:** 113 passing (100% pass rate in ~1.44s)
- **New Test Suite:** `tests/test_observability.py` (11 new tests)
  - Secret redaction for Gemini keys, Groq keys, Bearer tokens, and URL parameters.
  - Structured JSON format verification with ISO-8601 UTC timestamp and log level.
  - Request and cycle correlation ID contextvar propagation.
  - Custom extra fields serialization and secret sanitization.
  - Automatic UUID4 generation when `X-Request-ID` is missing.
  - Preservation and echoing of valid custom `X-Request-ID`.
  - Replacement of malicious/invalid `X-Request-ID` with safe UUID4.
  - Structured cycle telemetry emission during autonomous discovery cycles.
- **Linter Status:** `ruff check .` passes with 0 errors.

---

## Step 1.5 Implementation & Audit Report

### 1. Automated GitHub Actions CI/CD Pipeline
- **Secret Scan Job (`.github/workflows/ci.yml`):**
  - Executes `gitleaks/gitleaks-action@v2` with `fetch-depth: 0` to scan the full commit history on every push and pull request.
- **Lint & Offline Test Suite Job (`.github/workflows/ci.yml`):**
  - Configures Python 3.11 with pip caching, installs `requirements-dev.txt`, runs `ruff check .`, and executes `pytest -v` across all 113 offline tests.
- **Container Build & Smoke Check Job (`.github/workflows/ci.yml`):**
  - Builds the production Docker image (`docker build -t tron-ci:test .`), starts a background test container with dummy test credentials and SQLite data volume, polls `http://127.0.0.1:8080/healthz` until readiness, verifies HTTP 200, and tears down the container cleanly.

### 2. Non-Root Container Hardening & Healthcheck
- **Dedicated Unprivileged User (`Dockerfile`):**
  - Creates dedicated system group and user `appuser` (`RUN groupadd -r appuser && useradd -r -g appuser -s /bin/bash -m appuser`).
  - Sets `USER appuser` to avoid running container processes as root.
- **Persistent SQLite WAL Directory (`Dockerfile`):**
  - Creates `/app/data` with ownership assigned to `appuser:appuser`.
  - Configures `DATABASE_PATH=/app/data/tron.db` ensuring SQLite WAL mode (`.db-wal`, `.db-shm`) operates reliably in container runtimes and persistent storage mounts.
- **Native Standard-Library Healthcheck (`Dockerfile`):**
  - Uses a lightweight Python standard-library probe (`urllib.request.urlopen`) with dynamic `$PORT` fallback, eliminating curl package bloat and vulnerability exposure.

### 3. Documentation Alignment & Release Tag
- **Environment Reference (`.env.example`):**
  - Fully aligned with Phase 1 options: `DATABASE_PATH`, `CORS_ORIGINS`, `RATE_LIMIT_*`, and structured JSON logging.
- **Architecture Highlights (`README.md`):**
  - Added comprehensive **Production Hardening & Architecture Highlights** showcase table and updated badges (`113/113 Passing`, GitHub Actions CI, Non-Root Hardened Docker).
- **Milestone Tag:** `v1.0-phase1-prod`.
