# AI Prompt History - VicoDathon 2026 Submission

This document outlines the AI prompts used to architect the Autonoma platform, mapped to our git history. Tools used: Anti-Gravity / Claude 4.5 Opus.

### 1. Initial Infrastructure & API
**Prompt:**
> i have a two day hackathon and i need to build an autonomous ai agent that post tech news, first set up a fast api backend with sqlite, i need exactly two endpoints /api/agent/init to start the agent and /api/agent/feed to get the posts, make sure the feed is reverse chronological and only returns json. do not add any social media api integrations.

### 2. Database Concurrency & Scheduler
**Prompt:**
> now we need the agent to run in the background completely autonomously. add a feature to run every 30 mins to generate posts. also when i spam the get api the db gets locked, fix it by adding wal mode and a 15s timeout to the sqlite connection so it doesnt crash during eval

### 3. The Pivot: Web Scraper & Deduplication
**Prompt:**
> open router is throwing 402 payment errors so i have changed the plan. remove it completely to make it full failure proof. replace it with a local llm setup that will web scrap the latest ai security news directly, then apply an editorial filter to reject bad topics. also add a deduplication check so the agent doesn't post the exact same topic twice in a row, be strict on this.

### 4. Code Pruning 
**Prompt:**
> the core is working but we have too much dead code from older experiments. command the agent to strip out any x or twitter integration, remove the static fallback arrays for news, just keep what matters and looks ideal for the req project. guide mw to win not to lose happily. 

### 5. Final Polish & Dockerization
**Prompt:**
> everything looks good now i want to prepare for submission. write a world class readme explaining our background loop architecture and the sqlite wal mode. also update the dockerfile to use --platform=linux/amd64 because i am on a mac m2 and i want to make sure it compiles perfectly on the judges servers without any histrory of segfaults.