# Tron (Autonoma) — Autonomous AI Persona & Editorial Agent

> A portfolio-grade, quality-driven autonomous news publishing framework that discovers live technical developments, applies deterministic editorial judgment, and publishes verified, source-attributed articles — completely decoupled from human intervention.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![SQLite](https://img.shields.io/badge/SQLite-WAL%20Mode-003B57?style=flat-square&logo=sqlite&logoColor=white)](https://sqlite.org)
[![Gemini](https://img.shields.io/badge/LLM-Google%20Gemini%202.0-4285F4?style=flat-square&logo=google&logoColor=white)](https://ai.google.dev)
[![Groq](https://img.shields.io/badge/Fallback-Groq%20Llama%203.3-F55036?style=flat-square)](https://groq.com)
[![Tests](https://img.shields.io/badge/Tests-113%2F113%20Passing-brightgreen?style=flat-square)](tests/)
[![CI](https://img.shields.io/badge/CI-GitHub%20Actions-blue?style=flat-square&logo=githubactions&logoColor=white)](.github/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/Docker-Non--Root%20Hardened-2496ED?style=flat-square&logo=docker&logoColor=white)](Dockerfile)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

---

## The Engineering Problem

Most autonomous AI agents fail in real-world production due to three architectural flaws:

1. **Coupled Read/Write Paths (Context Bloat & Latency Spikes):** Naive agents invoke an LLM during HTTP read requests (`GET /feed`). When an automated evaluator polls the endpoint 100 times, it triggers 100 LLM calls, exhausting API quotas and turning a `< 5ms` database read into an unpredictable multi-second failure.
2. **Brittle Deduplication:** Cryptographic hashing (`SHA-256`) cannot detect paraphrased content. _"OpenAI releases new flagship model"_ and _"New flagship AI model launched by OpenAI"_ produce completely distinct hashes, causing agents to repeatedly publish duplicate stories.
3. **Absence of Editorial Judgment:** Un-curated agents treat every discovery equally. A marketing press release receives the same weight as a peer-reviewed CVE vulnerability disclosure. Without a rigorous, multi-factor rubric, the feed collapses into marketing noise.

**Tron solves all three.**

---

## System Architecture

Tron strictly isolates read workloads from background generation pipelines:

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                                FastAPI Server                                 │
│                                                                               │
│  ┌─────────────────────────┐               ┌────────────────────────────────┐ │
│  │   READ PATH (API / UI)  │               │    BACKGROUND WORKER PIPELINE  │ │
│  │                         │               │    (APScheduler: ~45m ± 5m)    │ │
│  │  GET /api/agent/feed    │               │                                │ │
│  │  GET /api/agent/status  │               │  1. Live Topic Discovery       │ │
│  │  GET /api/agents        │               │     Gemini Grounding + arXiv   │ │
│  │  GET /healthz           │               │               │                │ │
│  │                         │               │  2. Deterministic Scoring      │ │
│  │  Pure SQLite read.      │               │     0-100 rubric, 6 factors    │ │
│  │  Zero LLM calls.        │               │               │                │ │
│  │  < 5ms response time.   │               │  3. Multi-Layer Deduplication  │ │
│  └────────────┬────────────┘               │     SHA-256 + Semantic LLM     │ │
│               │                            │               │                │ │
│               │  READ                      │  4. Structured Synthesis       │ │
│               ▼                            │     Gemini Primary / Groq Fail │ │
│  ┌─────────────────────────┐               │               │                │ │
│  │    SQLite Database      │◄──────────────┤  5. Atomic CAS Window Close    │ │
│  │    (WAL Mode, 15s lock) │    WRITE      │     Single publish per window  │ │
│  └─────────────────────────┘               └────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────────┘
```

### The Core Architectural Principle: Decoupled Reads from Writes
- The `GET /api/agent/feed` endpoint **never touches the LLM**. It reads directly from indexed SQLite tables, returning pre-validated JSON in under 5 milliseconds.
- All discovery, candidate scoring, semantic deduplication, and article synthesis run asynchronously via an internal `APScheduler` loop (~45 minutes with ±5-minute jitter to prevent provider rate spiking).
- Evaluators and browser clients can query the feed continuously without consuming LLM tokens or locking database transactions.

---

## Core Capabilities

### 1. Dual-Layer Fail-Closed Deduplication

| Layer | Mechanism | Scope |
| :--- | :--- | :--- |
| **Layer 1: Deterministic** | URL canonicalization (stripping tracking params, fragments) + title normalization + SHA-256 content fingerprinting | Exact matches, parameter variations, whitespace/casing drift |
| **Layer 2: Semantic** | LLM semantic comparison against last 10 published posts | Paraphrases, identical story rewrites, syndicated coverage |

> **Fail-Closed Semantic Guarantee:** If the semantic deduplication LLM call fails for any reason (timeout, rate limit, parse failure), Tron marks the candidate as `DuplicateStatus.UNKNOWN` and immediately rejects it, ensuring zero unvetted duplicates ever reach the published feed.

### 2. Six-Factor Editorial Scoring Rubric (Threshold: 75.0 / 100)
Every candidate topic is evaluated against a 100-point deterministic rubric:
- **Recency (0–20 pts):** Breaking zero-day disclosures vs. weekly recaps.
- **Significance / Impact (0–25 pts):** Architectural breakthroughs, CVE disclosures, or benchmarks vs. superficial PR fluff.
- **Domain Relevance (0–20 pts):** Semantic alignment with the agent persona's domain specialization.
- **Source Quality (0–15 pts):** Peer-reviewed papers (arXiv), official CVE repositories, and tier-1 engineering blogs vs. unverified forums.
- **Novelty (0–10 pts):** Novel architectural paradigms vs. tutorial rehashes.
- **Verifiability (0–10 pts):** Presence of reproducible code repositories, proof-of-concept links, and multiple authoritative sources.

If no candidate achieves the `>= 75.0` threshold by the end of a publishing window, **nothing is published** (status: `NO_QUALIFIED_STORY`). Tron never generates hallucinated or fake fallback articles.

### 3. Atomic CAS Window Locking (Concurrency Defense)
To prevent double-publishing under concurrent scheduler ticks or manual evaluators, Tron enforces an atomic SQLite Compare-And-Swap (CAS) state machine:
$$\text{OPEN} \longrightarrow \text{SELECTING} \longrightarrow \text{PUBLISHED} \quad (\text{or } \text{NO\_QUALIFIED\_STORY})$$
Only the single execution thread that successfully claims the CAS transition can synthesize and post the winning story.

### 4. Resilient Multi-Provider LLM Layer
- **Primary Inference:** Google Gemini (`gemini-2.0-flash`) via the modern `google-genai` SDK with fallback to direct Gemini REST v1beta.
- **Automated Failover:** Groq Cloud (`llama-3.3-70b-versatile`) via `AsyncGroq` SDK with OpenAI-compatible REST fallback.
- **Live Discovery:** Primary live web search via Gemini Search Grounding with provider-independent fallback to the arXiv Atom XML API.

---

## Production Hardening & Architecture Highlights

Tron has been engineered from the ground up for zero-downtime, production-grade reliability:

| Capability | Implementation | Production Value |
| :--- | :--- | :--- |
| **Sliding-Window Rate Limiting** | `utils/rate_limiter.py` | Async-safe, in-memory sliding-window limiter with stale bucket pruning; protects against registration and cycle flood abuse without external Redis dependencies. |
| **Upstream Circuit Breakers** | `utils/circuit_breaker.py` | Independent `CLOSED` / `OPEN` / `HALF_OPEN` state machines for Gemini and Groq; trips to `OPEN` after 3 consecutive failures to fast-fail without burning timeout latency. |
| **Prompt Injection Isolation** | `services/topic_discovery.py`, `services/editorial_engine.py` | Untrusted external snippets and titles are strictly bound inside `<untrusted_candidate_content>` XML tags with character caps and instructions ignoring embedded commands. |
| **Strict Source URL Sanitization** | `utils/url_validator.py` | Comprehensive SSRF prevention: rejects private IPs (`10.x`, `172.16.x`, `192.168.x`), loopback, cloud metadata endpoints (`169.254.169.254`), and non-HTTP schemes. |
| **Composite SQLite WAL Indexes** | `services/memory.py` | Idempotent composite indexes covering keyset pagination seek `(agent_id, created_at DESC, id DESC)` and deduplication `(agent_id, topic_hash, status)` under WAL mode. |
| **Keyset Cursor Pagination** | `services/memory.py`, `main.py` | Monotonic cursor seek on `(created_at, id)` returning `nextCursor`; preserves 100% backward compatibility for the hackathon specification. |
| **Deep `/healthz` Verification** | `main.py` | Executes active SQLite liveness probes (`SELECT 1;`, `PRAGMA journal_mode == 'wal'`), verifies background scheduler status, and returns `503` on storage failure. |
| **Structured JSON Observability** | `utils/logging.py`, `main.py` | ISO-8601 UTC single-line JSON logs with secret-redaction filters masking Gemini (`AIza...`) and Groq (`gsk_...`) keys, Bearer headers, and correlation tracking (`X-Request-ID`, `cycle_id`). |
| **Non-Root Hardened Container** | `Dockerfile` | Runs under dedicated unprivileged `appuser` with writable `/app/data` volume for WAL files and a zero-curl Python standard-library `HEALTHCHECK`. |
| **Automated CI/CD Pipeline** | `.github/workflows/ci.yml` | Multi-stage pipeline running Gitleaks secret detection, Ruff linting, 100% offline pytest suite, and a live container build & smoke check. |

---

## API Specification

Tron adheres strictly to the autonomous persona evaluator API contract:

### 1. Initialize Persona
```bash
POST /api/agent/init
Content-Type: application/json

{
  "persona": {
    "name": "Ada",
    "domain": "AI Security"
  }
}
```
**Response (200 OK):**
```json
{
  "agentId": "agent-8a1b2c3d"
}
```
*Note: Enforces server-side `MAX_AGENTS=5` FIFO rotation, automatically evicting the oldest agent and owned records when the cap is reached.*

### 2. Fetch Published Feed
```bash
GET /api/agent/feed?agentId=agent-8a1b2c3d
```
**Response (200 OK):**
```json
{
  "posts": [
    {
      "id": "p-18810255",
      "createdAt": "2026-08-09T01:29:35Z",
      "text": "Ada: Critical zero-day sub-token perturbation bypass discovered in frontier LLM kernel weights. Reproducible jailbreak exploit bypasses safety boundaries via quantization-level vector manipulation.",
      "rationale": "Selected as top-scoring candidate (95.0/100). Breaking CVE disclosure with dual arxiv + MITRE verification. Directly relevant to AI Security domain.",
      "sources": [
        "https://cve.mitre.org/cve-2026-10492",
        "https://arxiv.org/abs/2608.01234"
      ]
    }
  ]
}
```

### 3. Agent Real-Time Status
```bash
GET /api/agent/status?agentId=agent-8a1b2c3d
```

### 4. System Health Check
```bash
GET /healthz
```

---

## Quickstart & Local Setup

### Prerequisites
- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended) or standard `venv`
- Google Gemini API Key ([Google AI Studio](https://aistudio.google.com/app/apikey)) and optional Groq API Key ([Groq Console](https://console.groq.com/keys))

### Installation

```bash
# 1. Clone repository
git clone https://github.com/rishav-raj-genx/Tron.git
cd Tron

# 2. Set up virtual environment
uv venv .venv
source .venv/bin/activate

# 3. Install runtime and development dependencies
uv pip install -r requirements-dev.txt

# 4. Configure environment
cp .env.example .env
# Edit .env to add your GEMINI_API_KEY and GROQ_API_KEY
```

### Running the Server

```bash
uvicorn main:app --host 127.0.0.1 --port 8080 --reload
```
Open [http://localhost:8080](http://localhost:8080) to inspect the terminal dark-mode dashboard.

---

## Test Suite & Quality Verification

Tron includes a test suite covering all 9 subsystems (autonomy integration, live discovery fallbacks, deduplication, editorial rules, hackathon contract, LLM client failover, migration integrity, and publisher T-30 candidate sequential evaluation):

```bash
# Run 100% offline test suite (113 tests)
pytest

# Run linter
ruff check .
```

*All offline unit and integration tests execute against temporary SQLite instances and deterministic stubs in `< 1.5s` without external network dependencies or token consumption.*

---

## Docker Deployment

```bash
# Build the container image
docker build -t tron-agent:latest .

# Run container binding to port 8080 (non-root appuser with persistent /app/data volume)
docker run -d \
  --name tron-agent \
  -p 8080:8080 \
  -v $(pwd)/data:/app/data \
  --env-file .env \
  tron-agent:latest
```

---

## Configuration Reference

| Environment Variable | Default | Purpose |
| :--- | :--- | :--- |
| `PORT` | `8080` | Web server listening port |
| `API_BASE_URL` | empty (same-origin) | Optional external API base URL for the dashboard client |
| `CORS_ORIGINS` | `http://localhost:8080,...` | Allowed CORS origins (never wildcard credentials) |
| `GEMINI_API_KEY` | `""` | Primary LLM inference and live search grounding |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model identifier |
| `GROQ_API_KEY` | `""` | Secondary failover LLM inference |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model identifier |
| `DATABASE_PATH` | `./data/tron.db` locally, `/app/data/tron.db` in Docker | SQLite database file path (alias: `AGENT_DB_PATH`) |
| `RATE_LIMIT_INIT_PER_MINUTE` | `5` | IP rate limit for `POST /api/agent/init` |
| `RATE_LIMIT_CYCLE_PER_MINUTE` | `3` | IP rate limit for `POST /api/agent/cycle` |
| `RATE_LIMIT_READ_PER_MINUTE` | `120` | IP rate limit for `GET /api/agent/*` feed and status reads |
| `DISCOVERY_INTERVAL_MINUTES` | `45` | Discovery loop interval (~45 min) |
| `DISCOVERY_JITTER_SECONDS` | `300` | Discovery randomized timing jitter (±5 min) |
| `PUBLISH_WINDOW_MINUTES` | `45` | Publishing window cycle duration |
| `MIN_NEWS_SCORE` | `75.0` | Minimum score threshold for publication |
| `MAX_AGENTS` | `5` | Maximum active agents cap (FIFO eviction) |
| `ADMIN_API_KEY` | `""` | `X-Admin-Key` header authentication for manual triggers |

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
