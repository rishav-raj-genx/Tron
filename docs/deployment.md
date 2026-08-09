# Deployment Guide: Autonomous AI & Technology Persona

This guide covers deploying the Autonomous AI Persona application to Docker, Railway, Render, or a VPS.

---

## 1. System Requirements
- Python 3.11+ (or Docker)
- Local filesystem SQLite storage (`./agent_memory.db`)
- OpenRouter API key (`OPENROUTER_API_KEY`) for live LLM completions and live web search.
- Official X / Twitter API keys for news publishing.

---

## 2. Docker / Container Deployment

### Build the Image:
```bash
docker build -t echomind-news-publisher .
```

### Run Container:
```bash
docker run -d \
  --name echomind \
  -p 8080:8080 \
  -e OPENROUTER_API_KEY="sk-or-v1-..." \
  -e AGENT_DB_PATH="./agent_memory.db" \
  echomind-news-publisher
```

---

## 3. Evaluator API Verification

Once deployed, the evaluator communicates with the agent using two endpoints:

### 1. Initialize Persona:
```bash
curl -X POST http://localhost:8080/api/agent/init \
  -H "Content-Type: application/json" \
  -d '{
    "persona": {
      "name": "Ada",
      "domain": "AI Security"
    }
  }'
```

### 2. Fetch Feed:
```bash
curl http://localhost:8080/api/agent/feed?agentId=<agentId>
```
