# AI Prompt History - Tron (VicoDathon 2026)

This document tracks the prompts used to architect and refine the Tron framework.
Tooling: Anti-Gravity / Claude 4.5 Opus / Gemini 2.0 Flash / Groq.

---

### 1. Base API & Storage Architecture
**Developer Prompt:**
> i have a two day hackathon and i need to build an autonomous ai agent that post tech news, first set up a fast api backend with sqlite, i need exactly two endpoints /api/agent/init to start the agent and /api/agent/feed to get the posts, make sure the feed is reverse ordered and only returns json. do not add any social media api integrations.

---

### 2. Autonomy & High Concurrency
**Developer Prompt:**
> now we need the agent to run in the background completely autonomously. add scheduler to run every 30 mins to generate posts. also when i spam the get api the db gets locked, fix it by adding wal mode and a 15s timeout to the sqlite connection so it doesnt crash during eval.

---

### 3. Discovery Pipeline & Source Provenance Verification
**Developer Prompt:**
> we need a clean discovery engine. use gemini with google search grounding to find latest ai security breakthroughs and vulnerabilities 2026. but listen carefully, do not let the llm hallucinate fake urls. extract candidate titles, summaries, and source urls, then validate that the url actually belongs to the verified sources retrieved before passing it to scoring. if a url is fake or unverified, throw it out immediately.

---

### 4. Direct arXiv API & Groq Fallback Engine
**Developer Prompt:**
> gemini search threw a 429 rate limit error on render. build a fail-proof fallback. if gemini search fails, do NOT make groq search the web because groq is not our search engine. instead, make the app hit the direct arXiv Atom API, parse the raw xml, and extract 5 research paper candidates. if gemini synthesis fails after that, use groq purely for editorial writing and scoring. prove that the pipeline works even when gemini is down.

---

### 5. Scoring Threshold & Submission Readiness
**Developer Prompt:**
> make sure the editorial engine scores candidates strictly. set a MIN_NEWS_SCORE threshold of 75. candidate under 75 gets rejected and logged with rationale. candidate over 75 becomes the leader, gets synthesized into a clean post under 280 chars, and saved to sqlite feed with verified arXiv sources. update the readme and prompt history to reflect this exact pipeline for the judges. guide mw to win not to lose happily.