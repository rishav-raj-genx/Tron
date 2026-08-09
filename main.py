"""
Autonomous AI & Technology Persona News Publisher.

FastAPI application providing:
- Web Dashboard interface at / and /dashboard (connected to central API base URL)
- GET /api/agents: List all active autonomous agents (up to 5) with real-time status and configured window duration
- POST /api/agent/init: Initialize autonomous persona with name & domain (enforcing MAX_AGENTS=5)
- GET /api/agent/feed: Fetch reverse-chronological feed with rationale and sources
- GET /api/agent/status: Real-time window, leader, candidate count, and publication status
- GET /health and GET /healthz: Lightweight health check endpoints
- ~35-Minute background discovery with ±5-min jitter and configuration-driven publishing window scheduling via APScheduler
"""

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
except ImportError:
    class AsyncIOScheduler:
        def start(self): pass
        def shutdown(self): pass
        def add_job(self, *args, **kwargs): pass

try:
    from fastapi import FastAPI, HTTPException, Header, Query, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    class FastAPI:
        def __init__(self, **kwargs): pass
        def get(self, *args, **kwargs): return lambda f: f
        def post(self, *args, **kwargs): return lambda f: f
        def delete(self, *args, **kwargs): return lambda f: f
        def mount(self, *args, **kwargs): pass
        def add_middleware(self, *args, **kwargs): pass
    class BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
        def dict(self, *args, **kwargs):
            return self.__dict__
    def Field(*args, **kwargs): return None
    def Query(*args, **kwargs): return None
    def Header(*args, **kwargs): return None
    class HTTPException(Exception): pass
    class Request: pass
    class CORSMiddleware: pass
    class FileResponse: pass
    class StaticFiles:
        def __init__(self, *args, **kwargs): pass

from config.settings import settings
from services.autonomous_publisher import publisher_service
from services.memory import memory_store

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global background scheduler
scheduler = AsyncIOScheduler()


# Request / Response Schemas for Hackathon API Specification
class PersonaInitPayload(BaseModel):
    name: str | None = Field(None, description="Human-readable agent persona identity (e.g. 'Ada')")
    domain: str | None = Field(None, description="Technical domain specialization (e.g. 'AI Security')")


class AgentInitRequest(BaseModel):
    persona: PersonaInitPayload | None = None
    name: str | None = None
    domain: str | None = None
    agent_name: str | None = None


class AgentInitResponse(BaseModel):
    agentId: str = Field(..., description="Unique machine identifier for the agent (e.g. 'agent-8a1b2c3d')")


class AgentListItem(BaseModel):
    agentId: str
    name: str
    domain: str
    createdAt: str
    isActive: bool = False
    status: dict[str, Any] = {}


class AgentListResponse(BaseModel):
    agents: list[AgentListItem]
    count: int
    maxAgents: int = 5
    publishWindowMinutes: int = 120


class FeedPostItem(BaseModel):
    id: str
    createdAt: str
    text: str
    rationale: str
    sources: list[str]


class FeedResponse(BaseModel):
    posts: list[FeedPostItem]


class WindowStatus(BaseModel):
    windowId: str | None = None
    status: str
    startedAt: str | None = None
    endsAt: str | None = None
    candidateCount: int = 0


class LeaderStatus(BaseModel):
    candidateId: str | None = None
    title: str | None = None
    score: float | None = None
    summary: str | None = None


class AgentStatusResponse(BaseModel):
    agentId: str
    window: WindowStatus
    currentLeader: LeaderStatus | None = None
    lastPublishedAt: str | None = None
    lastPublicationStatus: str | None = None
    nextWindow: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown with zero-failure guarantee."""
    logger.info("[APP] Starting Autonomous AI Persona News Publisher...")

    # Start the background task scheduler
    if not scheduler.running:
        # Schedule periodic background discovery and evaluation loop (~35 min + jitter)
        # Governed by publishing windows in AutonomousPublisherService
        # HIGH-002: max_instances=1 and coalesce=True prevents overlapping job execution
        # Jitter adds ±N seconds of randomized offset to each cycle to prevent
        # API spiking and simulate a natural publishing cadence.
        jitter = settings.discovery_jitter_seconds  # default: 300s (±5 min)
        scheduler.add_job(
            publisher_service.run_all_agents_cycle,
            "interval",
            minutes=settings.discovery_interval_minutes,
            jitter=jitter,
            id="autonomous_publishing_cycle",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120
        )
        scheduler.start()
        logger.info(
            f"[APP] Background autonomous discovery scheduler started "
            f"(interval: {settings.discovery_interval_minutes} min, "
            f"jitter: ±{jitter}s, "
            f"window: {settings.publish_window_minutes} min)."
        )

    yield

    # Shutdown
    logger.info("[APP] Shutting down application...")
    if scheduler.running:
        scheduler.shutdown(wait=False)
    logger.info("[APP] Application shutdown complete.")


app = FastAPI(
    title="Autonomous AI & Technology Persona News Publisher",
    description="Quality-driven autonomous news publisher with ~35-min discovery loops (±5-min jitter) and configuration-driven publishing windows",
    version="3.0.0",
    lifespan=lifespan
)

# Enable CORS for browser frontend clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static web frontend assets
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", include_in_schema=False)
@app.get("/dashboard", include_in_schema=False)
async def serve_dashboard():
    """Serve the web dashboard interface."""
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "EchoMind Autonomous Newsroom Backend", "status": "healthy"}


# ============================================================================
# REQUIRED HACKATHON EVALUATOR & MULTI-AGENT API ENDPOINTS
# ============================================================================

@app.get("/api/agents", response_model=AgentListResponse, status_code=200)
async def list_all_agents(activeAgentId: str | None = Query(None, description="Currently active dashboard agent")):
    """
    List all active autonomous agents (up to MAX_AGENTS=5) with their real-time
    publishing window, candidate count, current leader status, and configured window duration.
    Does NOT expose any secrets or credentials.
    """
    try:
        agents_data = memory_store.get_agents_detailed_status(active_agent_id=activeAgentId)
        return AgentListResponse(
            agents=[AgentListItem(**a) for a in agents_data],
            count=len(agents_data),
            maxAgents=settings.max_agents,
            publishWindowMinutes=settings.publish_window_minutes
        )
    except Exception as e:
        logger.error(f"[API] Error listing agents: {e}")
        raise HTTPException(status_code=500, detail="Internal server error listing agents.")


@app.post("/api/agent/init", response_model=AgentInitResponse, status_code=200)
async def init_agent(payload: AgentInitRequest):
    """
    Initialize an autonomous persona with a human-readable name and domain.
    Enforces server-side MAX_AGENTS=5 FIFO rotation: if 5 agents already exist,
    atomically evicts the oldest agent and all owned records.
    Generates a unique machine identifier (agentId), registers in SQLite memory,
    creates the initial publishing window, and triggers background discovery.

    Strictly returns: {"agentId": "abc-123"}
    """
    try:
        persona_name = ""
        persona_domain = ""
        if payload.persona:
            persona_name = (payload.persona.name or "").strip()
            persona_domain = (payload.persona.domain or "").strip()
        if not persona_name:
            persona_name = (payload.name or payload.agent_name or "").strip()
        if not persona_domain:
            persona_domain = (payload.domain or "").strip()

        if not persona_name:
            persona_name = "Autonomous Persona"
        if not persona_domain:
            persona_domain = "AI & Technology"

        # Generate unique machine agentId (e.g. agent-8a1b2c3d)
        agent_id = f"agent-{uuid.uuid4().hex[:8]}"

        # Register in SQLite memory store with atomic FIFO 5-agent rotation
        memory_store.register_agent(agent_id, persona_name, persona_domain, max_agents=settings.max_agents)

        # Create initial publishing window (duration driven by settings.publish_window_minutes)
        memory_store.create_window(agent_id, duration_minutes=settings.publish_window_minutes)

        # Trigger initial discovery cycle in background
        asyncio.create_task(publisher_service.run_publishing_cycle(agent_id))

        logger.info(f"[API] Initialized agent '{persona_name}' in domain '{persona_domain}' with machine id={agent_id}")
        return AgentInitResponse(agentId=agent_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error in agent initialization: {e}")
        raise HTTPException(status_code=500, detail="Internal server error during agent initialization.")


@app.delete("/api/agent/{agent_id}", status_code=200)
async def delete_single_agent(agent_id: str):
    """Explicitly remove an agent and its owned data."""
    try:
        deleted = memory_store.delete_agent(agent_id.strip())
        if not deleted:
            raise HTTPException(status_code=404, detail="Agent not found.")
        return {"success": True, "deletedAgentId": agent_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error deleting agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error deleting agent.")


@app.get("/api/agent/feed", response_model=FeedResponse, status_code=200)
@app.get("/api/feed", response_model=FeedResponse, status_code=200, include_in_schema=False)
@app.get("/feed", response_model=FeedResponse, status_code=200, include_in_schema=False)
async def get_agent_feed(agentId: str | None = Query(None, description="The unique agent identifier returned during initialization")):
    """
    Get the published feed for a given agent (or all published posts if agentId not provided).

    Strict API Contract:
    - createdAt MUST be an ISO 8601 UTC string (e.g. '2026-08-07T10:30:00Z')
    - sources MUST be an array of strings
    - Sorted newest first (ORDER BY created_at DESC)
    - If DB is empty, MUST return {"posts": []} with status 200 OK
    """
    try:
        clean_id = agentId.strip() if (agentId and isinstance(agentId, str)) else None
        posts = memory_store.get_feed(agent_id=clean_id, limit=200)
        return FeedResponse(posts=posts if posts is not None else [])

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error retrieving feed for agentId={agentId}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error retrieving feed.")


@app.get("/api/agent/status", response_model=AgentStatusResponse, status_code=200)
async def get_agent_status(agentId: str = Query(..., description="The unique agent identifier")):
    """
    Get the real-time status of an agent including current window, candidate count,
    current leader story, and last publication status.
    """
    try:
        if not agentId or not agentId.strip():
            raise HTTPException(status_code=400, detail="agentId is required.")

        clean_id = agentId.strip()
        agent = memory_store.get_agent(clean_id)
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent {clean_id} not found.")

        # Get active or latest window
        window = memory_store.get_active_window(clean_id) or memory_store.get_latest_window(clean_id)
        if not window:
            window = memory_store.create_window(clean_id, duration_minutes=settings.publish_window_minutes)

        window_id = window["window_id"]
        candidates = memory_store.get_candidates_for_window(window_id)
        candidate_count = len(candidates)
        leader = memory_store.get_current_leader(window_id, min_score=settings.min_news_score)

        # Get latest feed post
        feed = memory_store.get_feed(clean_id, limit=1)
        last_post_time = feed[0]["createdAt"] if feed else None

        leader_status = None
        if leader:
            leader_status = LeaderStatus(
                candidateId=leader["candidate_id"],
                title=leader["title"],
                score=leader["score"],
                summary=leader.get("summary")
            )

        return AgentStatusResponse(
            agentId=clean_id,
            window=WindowStatus(
                windowId=window["window_id"],
                status=window["status"],
                startedAt=window["started_at"],
                endsAt=window["ends_at"],
                candidateCount=candidate_count
            ),
            currentLeader=leader_status,
            lastPublishedAt=last_post_time,
            lastPublicationStatus=window.get("status", "OPEN"),
            nextWindow=window["ends_at"]
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error retrieving status for agentId={agentId}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error retrieving status.")


# ============================================================================
# COMPATIBILITY & MONITORING ENDPOINTS
# ============================================================================

@app.get("/health")
@app.get("/healthz")
async def health_check():
    """Lightweight health check endpoint with system status."""
    return {
        "status": "healthy",
        "scheduler_running": scheduler.running,
        "total_posts": memory_store.count_posts(),
        "registered_agents": len(memory_store.list_agents()),
        "discovery_interval_minutes": settings.discovery_interval_minutes,
        "publish_window_minutes": settings.publish_window_minutes,
        "min_news_score": settings.min_news_score,
        "max_agents": settings.max_agents,
        "api_base_url": settings.api_base_url,
        "version": "3.0.0"
    }


@app.get("/metrics")
async def metrics():
    """Metrics and statistics for evaluation monitoring."""
    agents = memory_store.list_agents()
    return {
        "agents_count": len(agents),
        "total_posts": memory_store.count_posts(),
        "max_agents": settings.max_agents,
        "publish_window_minutes": settings.publish_window_minutes,
        "discovery_interval_minutes": settings.discovery_interval_minutes,
        "agents": agents
    }


@app.post("/api/agent/cycle")
async def trigger_agent_cycle(
    agentId: str = Query(..., description="Agent ID to trigger immediately"),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key")
):
    """Manual cycle trigger endpoint for testing or simulated fast-forwarding."""
    admin_secret = os.getenv("ADMIN_API_KEY")
    if admin_secret and x_admin_key != admin_secret:
        raise HTTPException(status_code=403, detail="Unauthorized cycle trigger.")

    result = await publisher_service.run_discovery_and_evaluation_cycle(agentId)
    return result


@app.post("/api/agent/close-window")
async def trigger_window_close(
    agentId: str = Query(..., description="Agent ID"),
    windowId: str = Query(..., description="Window ID to close and evaluate"),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key")
):
    """Manual window close trigger endpoint for testing window evaluation."""
    admin_secret = os.getenv("ADMIN_API_KEY")
    if admin_secret and x_admin_key != admin_secret:
        raise HTTPException(status_code=403, detail="Unauthorized window close trigger.")

    result = await publisher_service.process_window_close(agentId, windowId)
    return result


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
