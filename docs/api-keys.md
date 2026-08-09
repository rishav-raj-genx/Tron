# API Keys and Configuration Guide: EchoMind News Publisher

This guide explains how to configure API keys for running the EchoMind autonomous news publisher.

---

## 1. OpenRouter API Key (Required for Live Operations)

OpenRouter provides access to frontier LLMs and real-time live web search plugins through a single unified API.

### Obtaining Your Key:
1. Go to [openrouter.ai](https://openrouter.ai)
2. Sign up and navigate to **Keys** (`openrouter.ai/keys`)
3. Create a new API key and copy its value.
4. Add it to your `.env` file or environment variables:
   ```bash
   OPENROUTER_API_KEY=sk-or-v1-...
   ```

---

## 2. Official X / Twitter API Credentials (Required for X Publishing)

To publish verified news stories to X/Twitter at the end of each 2-hour window:
1. Go to [developer.x.com](https://developer.x.com)
2. Create a project and developer app with **Read and Write** permissions.
3. Configure your environment variables:
   ```bash
   X_API_KEY=your-consumer-api-key
   X_API_SECRET=your-consumer-api-secret
   X_ACCESS_TOKEN=your-access-token
   X_ACCESS_TOKEN_SECRET=your-access-token-secret
   X_BEARER_TOKEN=your-bearer-token
   ```

---

## 3. Storage Configuration (SQLite WAL Mode)

The system uses embedded SQLite with Write-Ahead Logging (WAL) mode, storing all data in the local application filesystem:

```bash
AGENT_DB_PATH=./agent_memory.db
```
