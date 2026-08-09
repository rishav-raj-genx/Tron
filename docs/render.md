# Deploying to Render: EchoMind Autonomous X/Twitter News Publisher

This guide covers deploying the EchoMind autonomous news publisher on **Render Free Web Service** using the local filesystem SQLite database (zero persistent disk required).

---

## 1. Prerequisites
- Render account ([render.com](https://render.com))
- OpenRouter API key ([openrouter.ai](https://openrouter.ai))
- Official X / Twitter API keys ([developer.x.com](https://developer.x.com))

---

## 2. Deploying on Render (Free Plan)

### Step 1: Create Web Service
1. In Render Dashboard, click **New +** → **Web Service**.
2. Connect your GitHub repository (`EchoMind-2` / `dot-automation`).
3. Configure the service:
   - **Name:** `echomind-news-publisher`
   - **Region:** Any region (e.g. Oregon, Frankfurt, Ohio)
   - **Branch:** `main`
   - **Runtime:** `Python 3` (or `Docker`)
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python main.py`
   - **Plan:** Free Plan

### Step 2: Configure Environment Variables
In the **Environment** tab, add:

| Variable | Value / Description | Required? |
| :--- | :--- | :--- |
| `OPENROUTER_API_KEY` | `sk-or-v1-...` | **Yes** (for live LLM inference & web search) |
| `X_API_KEY` | Your X API Consumer Key | **Yes** (for X publishing) |
| `X_API_SECRET` | Your X API Consumer Secret | **Yes** (for X publishing) |
| `X_ACCESS_TOKEN` | Your X API User Access Token | **Yes** (for X publishing) |
| `X_ACCESS_TOKEN_SECRET` | Your X API User Access Token Secret | **Yes** (for X publishing) |
| `AGENT_DB_PATH` | `./agent_memory.db` | **Yes** (local SQLite database) |
| `DISCOVERY_INTERVAL_MINUTES` | `5` | **Optional** (default: 5 min discovery loop) |
| `PUBLISH_WINDOW_MINUTES` | `120` | **Optional** (default: 2-hour publish window) |
| `MIN_NEWS_SCORE` | `75` | **Optional** (default: 75.0 quality threshold) |
| `PORT` | `8080` | Auto-injected by Render dynamically |

> [!NOTE]
> **No Persistent Disk Required**: On Render Free plan, the application uses `./agent_memory.db` in the local application filesystem with SQLite WAL mode.

---

## 3. Verifying the Deployment

### 1. Health Check
```bash
curl https://echomind-news-publisher.onrender.com/healthz
```
**Expected Response (200 OK):**
```json
{
  "status": "healthy",
  "scheduler_running": true,
  "discovery_interval_minutes": 5,
  "publish_window_minutes": 120,
  "min_news_score": 75.0
}
```

### 2. Initialize Persona (POST /api/agent/init)
```bash
curl -X POST https://echomind-news-publisher.onrender.com/api/agent/init \
  -H "Content-Type: application/json" \
  -d '{
    "persona": {
      "name": "Ada",
      "domain": "AI Security"
    }
  }'
```

### 3. Check Real-Time Window Status (GET /api/agent/status)
```bash
curl "https://echomind-news-publisher.onrender.com/api/agent/status?agentId=<agentId>"
```
