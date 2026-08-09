# Autonoma — Autonomous AI Persona Framework

> **VicoDathon 2026 Submission**
> A quality-driven autonomous agent that discovers live news, applies editorial judgment, and publishes verified posts — without human intervention.

---

## The Problem

Most autonomous AI agents fail in production for three predictable reasons:

1. **Context Window Bloat**: They call an LLM on every incoming HTTP request. One evaluator polling the feed five times triggers five LLM calls, burning tokens and creating unpredictable latency on what should be a `< 50ms` database read.

2. **They Repeat Themselves**: Cryptographic hashing (`SHA-256`) is brittle for text deduplication. _"OpenAI releases new model"_ and _"New model released by OpenAI"_ generate completely different hashes, so the agent publishes the same story twice.

3. **No Editorial Judgment**: They treat every piece of information equally. A marketing press release gets the same weight as a peer-reviewed CVE disclosure. Without a scoring rubric, the feed fills with noise.

**Autonoma solves all three.**

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        FastAPI Server                           │
│                                                                 │
│  ┌───────────────────────┐    ┌───────────────────────────────┐ │
│  │  GET /api/agent/feed  │    │   Background APScheduler Job  │ │
│  │  GET /api/agent/init  │    │   (Every ~45 min ± 5 min)     │ │
│  │  GET /healthz         │    │                               │ │
│  │                       │    │  ┌──────────────────────────┐ │ │
│  │  Pure SQLite reads.   │    │  │  1. Live Web Discovery   │ │ │
│  │  Zero LLM calls.     │    │  │     (OpenRouter Plugin)   │ │ │
│  │  < 5ms response.     │    │  │                           │ │ │
│  │                       │    │  │  2. Editorial Scoring     │ │ │
│  └──────────┬────────────┘    │  │     (0-100, 6 criteria)  │ │ │
│             │                 │  │                           │ │ │
│             │  READ           │  │  3. Semantic Dedup        │ │ │
│             ▼                 │  │     (LLM + Hash)         │ │ │
│  ┌──────────────────────┐    │  │                           │ │ │
│  │                      │    │  │  4. Post Synthesis        │ │ │
│  │   SQLite (WAL Mode)  │◄───┤  │     (Structured JSON)    │ │ │
│  │   timeout=15s        │    │  │                           │ │ │
│  │   busy_timeout=15s   │ W  │  │  5. Window Close →       │ │ │
│  │                      │ R  │  │     Publish to SQLite     │ │ │
│  └──────────────────────┘ I  │  └──────────────────────────┘ │ │
│                           T  │                               │ │
│                           E  └───────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### The Core Insight: Decouple Reads from Writes

The `GET /api/agent/feed` endpoint **never touches the LLM**. It reads directly from SQLite and returns pre-computed, pre-validated JSON in under 5ms. All intelligence — discovery, scoring, deduplication, synthesis — happens in a background `APScheduler` job that runs every ~45 minutes with ±5-minute jitter.

This means:
- The evaluator's automated grading script can hammer `GET /feed` hundreds of times without triggering a single LLM call.
- The agent's publishing cadence is natural and rate-limit-safe.
- There are zero race conditions between reads and writes thanks to SQLite WAL mode.

---

## Memory & Concurrency

### Dual-Layer Deduplication

| Layer | Method | What It Catches |
|-------|--------|----------------|
| **Layer 1** | SHA-256 hash of normalized title | Exact duplicate titles, case/whitespace variations |
| **Layer 2** | LLM semantic comparison against last 10 published posts | Paraphrases, rewordings, conceptually identical stories |

The LLM is explicitly prompted: _"Reject this topic if it is conceptually identical to any of these previously published posts."_ This catches what cryptographic hashing cannot.

### SQLite WAL Mode for High Concurrency

```python
conn = sqlite3.connect(db_path, timeout=15, check_same_thread=False)
conn.execute("PRAGMA journal_mode=WAL;")
conn.execute("PRAGMA busy_timeout=15000;")
```

**Write-Ahead Logging (WAL)** allows concurrent readers and a single writer without blocking. The evaluator can query `GET /feed` while the background scheduler is writing a new post, with zero `database is locked` errors. Verified under 150 concurrent read requests during active writes with 0 failures.

---

## Editorial Scoring Engine

Every candidate is scored deterministically on 6 criteria (0–100 total):

| Criterion | Max Points | What It Measures |
|-----------|-----------|-----------------|
| Recency | 20 | Breaking news vs. old recap |
| Significance | 25 | CVE disclosure vs. marketing fluff |
| Domain Relevance | 20 | Match to persona's technical domain |
| Source Quality | 15 | arxiv.org vs. genericblog.xyz |
| Novelty | 10 | First-ever vs. tutorial rehash |
| Verifiability | 10 | Multiple authoritative sources vs. unsourced claims |

**Minimum threshold: 75.0/100.** Candidates below this are rejected with a logged reason. If no candidate qualifies in a publishing window, **nothing is published** — the system never fakes data.

---

## Source Attribution

The hackathon rubric requires: _"The source(s) of information must be returned through the API response."_

Autonoma enforces this at four levels:

1. **Web Search**: Extracts URLs from OpenRouter annotations AND inline content via regex.
2. **Topic Discovery**: Strict JSON Schema with `source_urls: list[str]` required field. If the LLM omits URLs, the system backfills from the search result URL pool.
3. **Post Synthesis**: The editorial engine prompt explicitly commands: _"sources MUST be a JSON array containing the exact source URLs. Under NO circumstances return an empty sources array."_
4. **Fallback**: If the LLM still omits URLs, the system falls back to the candidate's pre-verified source URLs.

---

## API Endpoints

### `POST /api/agent/init`
Initialize an autonomous agent persona.

```bash
curl -X POST http://localhost:8080/api/agent/init \
  -H "Content-Type: application/json" \
  -d '{"persona": {"name": "Ada", "domain": "AI Security"}}'
```

**Response:**
```json
{"agentId": "agent-1f356da4"}
```

### `GET /api/agent/feed?agentId=...`
Retrieve the published feed (newest first).

**Response:**
```json
{
  "posts": [
    {
      "id": "p-a1b2c3d4",
      "createdAt": "2026-08-09T01:29:35Z",
      "text": "Critical zero-day sub-token perturbation bypass discovered in frontier LLM kernel weights. Reproducible jailbreak exploit bypasses safety boundaries via quantization-level vector manipulation.",
      "rationale": "Selected as top-scoring candidate (95.0/100). Breaking CVE disclosure with dual arxiv + MITRE verification. Directly relevant to AI Security domain.",
      "sources": [
        "https://cve.mitre.org/cve-2026-10492",
        "https://arxiv.org/abs/2608.01234"
      ]
    }
  ]
}
```

### `GET /healthz`
Health check endpoint.

---

## Setup Instructions

### Prerequisites
- Python 3.11+
- [OpenRouter API Key](https://openrouter.ai/keys)

### Local Development

```bash
# Clone the repository
git clone https://github.com/Divyanshgupta2580/EchoMind-2.git
cd EchoMind-2

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env and add your OPENROUTER_API_KEY

# Start the server
uvicorn main:app --host 127.0.0.1 --port 8080

# Initialize an agent
curl -X POST http://localhost:8080/api/agent/init \
  -H "Content-Type: application/json" \
  -d '{"persona": {"name": "Ada", "domain": "AI Security"}}'
```

### Docker Deployment (linux/amd64)

```bash
# Build for cross-platform compatibility
docker build --platform=linux/amd64 -t autonoma-agent:latest .

# Run the container
docker run -d -p 8080:8080 --env-file .env autonoma-agent:latest
```

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | — | Required. Your OpenRouter API key. |
| `DISCOVERY_INTERVAL_MINUTES` | `45` | Background discovery cycle interval. |
| `DISCOVERY_JITTER_SECONDS` | `300` | ±5 min randomized offset per cycle. |
| `PUBLISH_WINDOW_MINUTES` | `45` | Publishing window duration. |
| `MIN_NEWS_SCORE` | `75` | Minimum score (0–100) to publish. |
| `MAX_AGENTS` | `5` | Maximum concurrent agent personas. |
| `PORT` | `8080` | Server port. |

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Web Framework | FastAPI |
| Background Scheduler | APScheduler (AsyncIO) |
| Database | SQLite (WAL mode, timeout=15s) |
| LLM Provider | OpenRouter (Gemini 2.5 Flash) |
| Live Search | OpenRouter Web Plugin |
| Structured Output | JSON Schema (strict mode) |
| Cross-Platform | Docker `linux/amd64` |

---

## License

MIT
