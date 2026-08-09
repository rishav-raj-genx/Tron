# Deploying to a VPS: EchoMind Autonomous News Publisher

This guide covers deploying the EchoMind autonomous news publisher on any Linux VPS using Docker or direct Python.

---

## 1. System Requirements
- Linux VPS (1 vCPU, 512 MB - 1 GB RAM)
- Python 3.11+ (or Docker)
- OpenRouter API Key & X API Keys

---

## 2. Docker Deployment

### Step 1: Clone Repository
```bash
git clone <repository_url> /opt/echomind
cd /opt/echomind
```

### Step 2: Configure Environment
Create `/opt/echomind/.env`:
```bash
OPENROUTER_API_KEY=sk-or-v1-...
X_API_KEY=...
X_API_SECRET=...
X_ACCESS_TOKEN=...
X_ACCESS_TOKEN_SECRET=...
AGENT_DB_PATH=./agent_memory.db
PORT=8080
```

### Step 3: Build & Run Container
```bash
docker build -t echomind-news-publisher .

docker run -d \
  --name echomind \
  --restart unless-stopped \
  -p 8080:8080 \
  --env-file .env \
  echomind-news-publisher
```

---

## 3. Verifying Evaluator Endpoints

```bash
# 1. Health Check
curl http://localhost:8080/healthz

# 2. Initialize Persona
curl -X POST http://localhost:8080/api/agent/init \
  -H "Content-Type: application/json" \
  -d '{"persona": {"name": "Ada", "domain": "AI Security"}}'
```
