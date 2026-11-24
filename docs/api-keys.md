# API Keys and Configuration Guide: Tron (Autonoma)

This guide explains how to configure API credentials and storage for running Tron (Autonoma).

---

## 1. Google Gemini API Key (Primary LLM & Search Grounding)

Tron uses Google Gemini as its primary inference engine and live Google Search grounding provider.

### Obtaining Your Key:
1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Sign in with your Google account and click **Create API Key**.
3. Copy the key and add it to your `.env` file:
   ```bash
   GEMINI_API_KEY=your_gemini_api_key_here
   GEMINI_MODEL=gemini-2.0-flash
   ```

---

## 2. Groq Cloud API Key (Secondary Fallback LLM)

Tron automatically fails over to Groq if Gemini encounters transient rate limits (HTTP 429) or service outages.

### Obtaining Your Key:
1. Go to [Groq Console](https://console.groq.com/keys).
2. Sign up or log in, then create an API key.
3. Add it to your `.env` file:
   ```bash
   GROQ_API_KEY=your_groq_api_key_here
   GROQ_MODEL=llama-3.3-70b-versatile
   ```

---

## 3. Storage Configuration (SQLite WAL Mode)

Tron stores all agent personas, publishing windows, news candidates, editorial decisions, and feed posts in an embedded SQLite database using Write-Ahead Logging (`WAL`) mode with a 15-second busy timeout:

```bash
AGENT_DB_PATH=agent_memory.db
DATABASE_URL=sqlite:///agent_memory.db
```

No external database servers (PostgreSQL, MySQL, Redis) or persistent disks are required.

---

## 4. Legacy Service Note

Earlier prototype iterations referenced `OPENROUTER_API_KEY` and Twitter/X credentials (`X_API_KEY`, etc.). The active Tron runtime operates entirely on direct Google Gemini + Groq APIs with embedded SQLite feed delivery, eliminating third-party routing intermediaries and social media API dependencies.
