# Phase 0: Baseline Verification, Inventory & Triage

## Checklist

- [x] **Step 0.1: Safety Checkpoint, Full Codebase Inventory & Legacy Triage**
  - [x] Verify git status and establish baseline tag `v0.0-pre-phase0` on working branch `chore/phase-0-baseline`.
  - [x] Complete inventory of all repository directories (`main.py`, `config/`, `services/`, `tools/`, `utils/`, `static/`, `tests/`, `scripts/`, `docs/`).
  - [x] Map all mounted FastAPI endpoints in `main.py` (methods, paths, query/body schemas).
  - [x] Document LLM provider/SDK wiring (`google-genai`, `groq`, OpenRouter discrepancy in docs).
  - [x] Triage legacy assets: Remove unreferenced `tools/legacy/` and `AI_INSTRUCTIONS.md` from root.
- [x] **Step 0.2: Secret Hygiene, .gitignore Hardening & Environment Configuration Reconciliation**
  - [x] Scan working tree and commit history for exposed API keys, tokens, or committed `.env` files.
  - [x] Harden `.gitignore` against environment files (`.env`, `.env.*`), SQLite databases (`*.db*`, `*.sqlite*`), caches, and OS artifacts.
  - [x] Reconcile configuration models using `pydantic-settings` with `AliasChoices`, defaults, and `extra="ignore"`.
  - [x] Add actionable startup validation via `settings.validate_api_keys()` for graceful missing-key reporting.
  - [x] Create comprehensive `.env.example` with defaults, comments, and documented environment variables.
- [ ] **Step 0.3: Architectural & LLM Provider Realignment**
  - [ ] Clean up obsolete OpenRouter and X/Twitter references across `docs/` and `README.md`.
  - [ ] Align documentation with active Gemini Primary + Groq Fallback architecture.
  - [ ] Verify configuration models in `config/models.py` and `config/settings.py`.
- [ ] **Step 0.4: Test Suite Stabilization & Regression Hardening**
  - [ ] Mock un-mocked live LLM call in `tests/test_hackathon_spec.py` (`test_source_extraction_and_structured_json_synthesis`).
  - [ ] Ensure 100% test pass rate across all 9 test suites without network dependencies.
  - [ ] Add pytest configuration / `pyproject.toml` to eliminate manual `PYTHONPATH` exports.
- [ ] **Step 0.5: Phase 0 Verification & Milestone Baseline Tagging**
  - [ ] End-to-end lint, test, and type-check validation.
  - [ ] Tag verified milestone `v0.0-phase0-complete`.

---

## Step 0.1 Audit Report

### 1. Safety Checkpoint
- **Base Tag:** `v0.0-pre-phase0` created at the clean repository state prior to Phase 0 changes.
- **Working Branch:** `chore/phase-0-baseline` active.

---

### 2. Full Codebase Inventory

#### Root Files
- `main.py`: Application entry point; mounts FastAPI routes, static files, CORS, and APScheduler lifespan task.
- `requirements.txt`: Defines core dependencies (`fastapi`, `uvicorn`, `apscheduler`, `httpx`, `pydantic`, `pydantic-settings`, `python-dotenv`, `google-genai`, `groq`).
- `Dockerfile`: Container build definition targeting `python:3.11-slim` on `linux/amd64`.
- `.gitignore`: Ignores virtual environments, caches, compiled binaries, SQLite databases, `.env`, and `.DS_Store`.
- `README.md`: Project documentation (currently contains legacy OpenRouter / X references to be updated in Step 0.3).
- `LICENSE`: MIT License.

#### `config/`
- `config/__init__.py`: Package marker.
- `config/models.py`: Central model declarations (`GEMINI_MODEL`, `GROQ_MODEL`, `IMAGE_MODEL`).
- `config/persona_engine.py`: Dynamic persona generator with 5 built-in domain templates (AI Security, Machine Learning, Robotics, AI Product, AI Ethics).
- `config/settings.py`: Pydantic Settings implementation with stdlib fallback loading configuration and intervals.

#### `services/`
- `services/__init__.py`: Package marker.
- `services/autonomous_publisher.py`: Autonomous publishing loop orchestrator (`AutonomousPublisherService`), managing per-agent asyncio locks, publishing windows, candidate scoring, and atomic SQLite CAS closing.
- `services/editorial_engine.py`: Candidate evaluation engine (`EditorialEngine`) computing 0-100 scores across 6 criteria, managing multi-tier deduplication, and executing structured LLM synthesis.
- `services/llm.py`: Unified LLM client (`LLMClient`) implementing primary Gemini inference (`google-genai` SDK + REST) with automated failover to Groq (`groq` SDK + REST), JSON schema normalization, and credential sanitization.
- `services/memory.py`: Persistent storage manager (`AgentMemoryStore`) using SQLite in WAL mode with a 15-second busy timeout for concurrent read/write isolation.
- `services/topic_discovery.py`: Autonomous topic discovery (`TopicDiscoveryService`) using Gemini Google Search Grounding primary retrieval with automated fallback to arXiv Atom API.

#### `tools/`
- `tools/__init__.py`: Exports tools registry.
- `tools/registry.py`: Dynamic tool discovery scanning `tools/shared/`.
- `tools/shared/web_search.py`: Live web search tool via Gemini search grounding.
- `tools/legacy/`: *(Removed in Step 0.1 triage)* Contained unreferenced legacy `image_generation.py`.

#### `utils/`
- `utils/__init__.py`: Exports utility helpers.
- `utils/api.py`: Provider endpoints and URL credential sanitization regexes.
- `utils/duplicate.py`: Multi-factor deterministic duplicate detection (URL canonicalization, title normalization, SHA-256 fingerprinting).

#### `static/`
- `static/index.html`: Dashboard single-page application markup.
- `static/app.js`: Frontend client interacting with `/api/agents`, `/api/agent/init`, `/api/agent/status`, and `/api/agent/feed`.
- `static/styles.css`: Dark-mode terminal styling for the newsroom UI.

#### `scripts/`
- `scripts/live_provider_smoke_test.py`: CLI diagnostic test checking live connectivity to Gemini and Groq APIs.

#### `tests/` (9 test files)
1. `tests/stress_test_qa.py`: High-concurrency load test and WAL verification for `/api/agent/feed`.
2. `tests/test_autonomy_integration.py`: Autonomy and feed contract validation using deterministic LLM stubs.
3. `tests/test_discovery_fallback.py`: Live discovery fallback tests (arXiv Atom XML parsing and failure handling).
4. `tests/test_duplicate_prevention.py`: Unit tests for URL normalization, title canonicalization, and fail-closed deduplication.
5. `tests/test_editorial_and_twitter.py`: Verification of article length integrity and Twitter/tweepy independence.
6. `tests/test_hackathon_spec.py`: Full system specification suite covering scoring, window CAS locks, and API endpoints.
7. `tests/test_llm_service.py`: LLM client unit tests (Gemini REST, Groq REST, error extraction, rate limiting).
8. `tests/test_migration_integrity.py`: Source attribution integrity verification during structured synthesis.
9. `tests/test_t30_publisher.py`: Publisher T-30 candidate sequential fallback and single-publish enforcement.

---

### 3. API Endpoint Inventory (`main.py`)

| Method | Path | Input Parameters / Schema | Response Schema / Status | Description |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/` | None | `FileResponse` / HTML | Serves frontend dashboard |
| `GET` | `/dashboard` | None | `FileResponse` / HTML | Alias for frontend dashboard |
| `GET` | `/api/agents` | Query: `activeAgentId: str \| None` | `AgentListResponse` (200) | Lists active agents (max 5) with window status |
| `POST` | `/api/agent/init` | Body: `AgentInitRequest` (`persona.name`, `persona.domain`, etc.) | `AgentInitResponse` (`agentId: str`) (200) | Initializes agent, enforces 5-agent FIFO rotation |
| `DELETE` | `/api/agent/{agent_id}` | Path: `agent_id: str` | `{"success": bool, "deletedAgentId": str}` (200) | Removes agent and cascades owned data |
| `GET` | `/api/agent/feed` | Query: `agentId: str \| None` | `FeedResponse` (`posts: list[FeedPostItem]`) (200) | Returns reverse-chronological published articles |
| `GET` | `/api/feed` | Query: `agentId: str \| None` | `FeedResponse` (200) | Alias for `/api/agent/feed` (hidden from schema) |
| `GET` | `/feed` | Query: `agentId: str \| None` | `FeedResponse` (200) | Alias for `/api/agent/feed` (hidden from schema) |
| `GET` | `/api/agent/status` | Query: `agentId: str` (required) | `AgentStatusResponse` (200) | Real-time agent status, window, leader, candidate count |
| `GET` | `/health` | None | Dict (200) | System health, scheduler status, agent metrics |
| `GET` | `/healthz` | None | Dict (200) | Lightweight health check alias |
| `GET` | `/metrics` | None | Dict (200) | Aggregate metrics for evaluation monitoring |
| `POST` | `/api/agent/cycle` | Query: `agentId: str`; Header: `X-Admin-Key` | Dict (200) | Manual discovery/evaluation cycle trigger |
| `POST` | `/api/agent/close-window`| Query: `agentId: str`, `windowId: str`; Header: `X-Admin-Key` | Dict (200) | Manual window close and publication trigger |
| `MOUNT`| `/static` | Path: `/static/*` | Static files | Serves static assets (`app.js`, `styles.css`) |

---

### 4. LLM Providers & SDK Wiring Analysis

| Component | Intended Provider / SDK | Actual Runtime Implementation | Notes |
| :--- | :--- | :--- | :--- |
| `services/llm.py` (Primary) | Google Gemini (`google-genai` SDK) | `genai.Client` via `client.aio.models.generate_content`, falling back to direct REST POST `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` with 429 exponential backoff. | Active & verified. Normalizes model names and JSON schema. |
| `services/llm.py` (Fallback) | Groq (`groq` SDK) | `AsyncGroq` via `client.chat.completions.create`, falling back to direct REST POST `https://api.groq.com/openai/v1/chat/completions`. | Active & verified. Triggered automatically on Gemini transient or quota failure. |
| `services/topic_discovery.py` | Live Web Grounding + Research Fallback | 1. Primary: Gemini Search Grounding via `tools/shared/web_search.py`<br>2. Fallback: arXiv Atom API via `httpx.AsyncClient` | Fully provider-independent fallback; no static mocked pools. |
| OpenRouter (`httpx`) | Mentioned in legacy docs (`docs/api-keys.md`, `README.md`) | **NOT present in runtime code.** | Discrepancy documented. All legacy documentation to be aligned in Step 0.3. |

---

### 5. Legacy & Cross-Reference Triage

1. **`tools/legacy/`**:
   - Inspected: `tools/legacy/image_generation.py` (old Gemini Imagen tool).
   - Symbol & grep audit: No imports or references in `main.py`, `services/`, `tools/registry.py`, `utils/`, or `tests/`.
   - Action: Removed from working tree. History preserved under tag `v0.0-pre-phase0`.
2. **`AI_INSTRUCTIONS.md`**:
   - Inspected: 1304 lines of internal hackathon agent prompt instructions.
   - Code audit: Unreferenced across the codebase.
   - Action: Removed from root to maintain a clean portfolio repository.
3. **`utils/` Dependencies**:
   - `utils/api.py` and `utils/duplicate.py` are actively imported across `services/llm.py`, `services/editorial_engine.py`, `services/topic_discovery.py`, and test suites. Fully preserved and validated.
4. **Test Suite Baseline**:
   - 57 passing tests, 1 failing test (`tests/test_hackathon_spec.py::TestEchoMindNewsPublisher::test_source_extraction_and_structured_json_synthesis`) due to an un-mocked LLM call attempting live network egress. Flagged for resolution in Step 0.4.

---

## Step 0.2 Audit Report

### 1. Secret & Repository Hygiene Audit
- **Commit History & Tree Scan:**
  - Scanned full git history and working tree for regex patterns of API keys (`AIza[0-9A-Za-z_-]{35}`, `gsk_[0-9A-Za-z]{20,}`, `sk-[0-9A-Za-z]{20,}`).
  - Zero real secrets found in commit history or tracked files. All historical occurrences of `sk-` were placeholder documentation snippets in `docs/` (`OPENROUTER_API_KEY=sk-or-v1-...`).
  - Verified no `.env` or `.env.*` files were ever committed to git history.
  - Verified zero SQLite database files (`*.db*`, `*.sqlite*`) exist in git index.

### 2. `.gitignore` Hardening
- Strengthened `.gitignore` to explicitly ignore:
  - Environments: `.venv/`, `venv/`, `env/`, `ENV/`
  - Byte-compiled & cache artifacts: `__pycache__/`, `*.py[cod]`, `*$py.class`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`
  - Secrets: `.env`, `.env.*`, with explicit inclusion for `!.env.example`
  - Databases: `*.db`, `*.db-wal`, `*.db-shm`, `*.sqlite`, `*.sqlite3`
  - OS files: `.DS_Store`, `**/.DS_Store`, `Thumbs.db`
- Tested and verified via `git check-ignore -v`.

### 3. Reconciled Environment Variables Matrix

| Environment Variable | Alias / Fallback | Type | Default Value | Usage Scope | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `PORT` | `port` | `int` | `8080` | `main.py` entrypoint | Active |
| `API_BASE_URL` | `ECHOMIND_API_BASE_URL` | `str` | `"https://echomind-ltwo.onrender.com"` | Frontend dashboard & API link | Active |
| `MAX_AGENTS` | `max_agents` | `int` | `5` | `main.py`, `services/memory.py` | Active |
| `GEMINI_API_KEY` | `gemini_api_key` | `str` | `""` | `services/llm.py`, `tools/shared/web_search.py` | Active (Primary) |
| `GEMINI_MODEL` | `gemini_model` | `str` | `"gemini-2.0-flash"` | `services/llm.py`, `config/models.py` | Active |
| `GROQ_API_KEY` | `groq_api_key` | `str` | `""` | `services/llm.py` | Active (Fallback) |
| `GROQ_MODEL` | `groq_model` | `str` | `"llama-3.3-70b-versatile"` | `services/llm.py`, `config/models.py` | Active |
| `LLM_PRIMARY_PROVIDER` | `llm_primary_provider` | `str` | `"gemini"` | Provider routing | Active |
| `LLM_FALLBACK_PROVIDER` | `llm_fallback_provider` | `str` | `"groq"` | Failover routing | Active |
| `DATABASE_URL` | `database_url` | `str` | `"sqlite:///agent_memory.db"` | Database connection string | Active |
| `AGENT_DB_PATH` | `agent_db_path` | `str` | `"agent_memory.db"` | SQLite file path | Active |
| `DISCOVERY_INTERVAL_MINUTES` | `AGENT_INTERVAL_MINUTES` | `int` | `45` | Background discovery scheduler | Active |
| `DISCOVERY_JITTER_SECONDS` | `discovery_jitter_seconds` | `int` | `300` | Discovery jitter window (±5 min) | Active |
| `PUBLISH_WINDOW_MINUTES` | `publish_window_minutes` | `int` | `45` | Publishing window cycle | Active |
| `MIN_NEWS_SCORE` | `min_news_score` | `float` | `75.0` | Editorial threshold | Active |
| `ADMIN_API_KEY` | `admin_api_key` | `str` | `""` | Manual trigger header auth | Active |
| `IMAGE_MODEL` | — | `str` | `"imagen-3.0-generate-002"` | `config/models.py` | Legacy reference |
| `OPENROUTER_API_KEY` | — | — | — | Legacy docs only | Unwired in code |

### 4. Configuration Model & Startup Feedback
- Refactored `config/settings.py` to use Pydantic v2 `BaseSettings`, `Field(validation_alias=AliasChoices(...))`, and `extra="ignore"`.
- Implemented `settings.validate_api_keys()` returning structured diagnostic messages.
- Updated `main.py`'s `lifespan` handler to emit actionable warnings/errors at startup if required LLM credentials are unconfigured without aborting test harnesses.
- Created [.env.example](file:///Users/apple/Desktop/Tron/.env.example) documenting all reconciled variables with production-ready defaults and comments.
