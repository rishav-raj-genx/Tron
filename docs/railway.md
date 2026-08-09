# Deploying to Railway: EchoMind Autonomous News Publisher

This guide covers deploying the EchoMind autonomous news publisher on Railway using the local filesystem SQLite store.

---

## 1. Prerequisites
- Railway account ([railway.app](https://railway.app))
- OpenRouter API key ([openrouter.ai](https://openrouter.ai))
- Official X / Twitter API keys ([developer.x.com](https://developer.x.com))

---

## 2. Deploying on Railway

### Step 1: Deploy from GitHub
1. In Railway, click **New Project** → **Deploy from GitHub repo**.
2. Select your repository.
3. Railway automatically detects the `Dockerfile` and builds the service.

### Step 2: Configure Environment Variables
In the **Variables** tab, configure:

| Variable | Value / Description | Required? |
| :--- | :--- | :--- |
| `OPENROUTER_API_KEY` | `sk-or-v1-...` | **Yes** (live LLM & search) |
| `X_API_KEY` | Your X API Consumer Key | **Yes** (X publishing) |
| `X_API_SECRET` | Your X API Consumer Secret | **Yes** (X publishing) |
| `X_ACCESS_TOKEN` | Your X API Access Token | **Yes** (X publishing) |
| `X_ACCESS_TOKEN_SECRET` | Your X API Access Token Secret | **Yes** (X publishing) |
| `AGENT_DB_PATH` | `./agent_memory.db` | **Yes** (local SQLite database) |
| `PORT` | `8080` (Railway injects `$PORT` automatically) | Auto |

---

## 3. Verifying the Deployment

```bash
# 1. Health Check
curl https://your-railway-app.up.railway.app/healthz

# 2. Initialize Persona
curl -X POST https://your-railway-app.up.railway.app/api/agent/init \
  -H "Content-Type: application/json" \
  -d '{"persona": {"name": "Ada", "domain": "AI Security"}}'
```
