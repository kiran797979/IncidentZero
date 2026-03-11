"""
IncidentZero — Main API Server
Serves the REST API and WebSocket for real-time frontend updates.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import httpx
import json
import logging
import os
import uvicorn

from agents.orchestrator import OrchestratorAgent
from mcp.channel import mcp_bus

# ─── Logging Setup ───────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("incidentzero")

# ─── Global State ────────────────────────────────────────
TARGET_APP_URL = os.getenv("TARGET_APP_URL", "http://localhost:8000")
orchestrator = OrchestratorAgent()
ws_connections: list[WebSocket] = []


# ─── WebSocket Broadcast ─────────────────────────────────
async def broadcast_to_websockets(message: dict):
    """Push every MCP message to all connected frontend clients."""
    disconnected = []
    for ws in ws_connections:
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        if ws in ws_connections:
            ws_connections.remove(ws)


# Register with MCP bus
mcp_bus.on_websocket_message(broadcast_to_websockets)


# ─── Lifespan (replaces deprecated on_event) ─────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 IncidentZero API starting...")
    logger.info("📡 Target app URL: %s", TARGET_APP_URL)
    task = asyncio.create_task(orchestrator.start())
    yield
    orchestrator.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("🛑 IncidentZero API stopped")


# ─── FastAPI App ─────────────────────────────────────────
app = FastAPI(
    title="IncidentZero API",
    description="Autonomous AI SRE Team — Multi-Agent Incident Resolution",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "*",
        "http://localhost:3000",
        "http://localhost:3001",
        "https://incidentzero-frontend.azurewebsites.net",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── REST Endpoints ──────────────────────────────────────
@app.get("/")
async def root():
    return {
        "name": "IncidentZero",
        "tagline": "Autonomous AI SRE Team",
        "version": "1.0.0",
        "status": "running",
        "agents": [
            "WatcherAgent",
            "TriageAgent",
            "DiagnosisAgent",
            "ResolutionAgent",
            "DeployAgent",
            "PostmortemAgent",
        ],
    }


@app.get("/api/health")
async def api_health():
    return {
        "status": "healthy",
        "active_incidents": len(orchestrator.active_incidents),
        "ws_connections": len(ws_connections),
        "target_app_url": TARGET_APP_URL,
    }


@app.get("/api/status")
async def api_status():
    """Full system status — used by deployment checkpoint tests."""
    target_healthy = False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{TARGET_APP_URL}/health")
            target_healthy = resp.status_code == 200
    except Exception:
        target_healthy = False

    return {
        "status": "running",
        "target_app": {
            "url": TARGET_APP_URL,
            "healthy": target_healthy,
        },
        "agents": {
            "orchestrator": "running",
            "watcher": "monitoring",
            "triage": "standby",
            "diagnosis": "standby",
            "resolution": "standby",
            "deploy": "standby",
            "postmortem": "standby",
        },
        "active_incidents": len(orchestrator.active_incidents),
        "ws_connections": len(ws_connections),
    }


@app.post("/api/inject")
async def inject_failure():
    """Trigger chaos injection on the target app (called by frontend button)."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{TARGET_APP_URL}/chaos/inject")
            if resp.status_code == 200:
                logger.info("💥 Chaos injected via API")
                return resp.json()
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Target app returned {resp.status_code}",
            )
    except httpx.RequestError as exc:
        logger.error("Failed to inject chaos: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Cannot reach target app at {TARGET_APP_URL}: {str(exc)}",
        )


@app.post("/api/fix")
async def apply_fix():
    """Manually trigger fix on the target app."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{TARGET_APP_URL}/chaos/fix")
            if resp.status_code == 200:
                logger.info("✅ Fix applied via API")
                return resp.json()
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Target app returned {resp.status_code}",
            )
    except httpx.RequestError as exc:
        logger.error("Failed to apply fix: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Cannot reach target app at {TARGET_APP_URL}: {str(exc)}",
        )


@app.get("/api/target/health")
async def target_health():
    """Proxy the target app health check."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{TARGET_APP_URL}/health")
            return resp.json()
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot reach target app: {str(exc)}",
        )


@app.get("/api/target/metrics")
async def target_metrics():
    """Proxy the target app metrics."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{TARGET_APP_URL}/metrics")
            return resp.json()
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot reach target app: {str(exc)}",
        )


@app.get("/api/incidents")
async def get_incidents():
    """Get all active incidents."""
    try:
        active = orchestrator.get_incidents()
    except AttributeError:
        active = list(orchestrator.active_incidents.values())
    try:
        resolved = orchestrator.get_all_resolved()
    except AttributeError:
        resolved = []
    return {
        "active": active,
        "resolved_count": len(resolved),
    }


@app.get("/api/incidents/{incident_id}")
async def get_incident(incident_id: str):
    """Get details of a specific incident."""
    try:
        incident = orchestrator.get_incident(incident_id)
    except AttributeError:
        incident = orchestrator.active_incidents.get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@app.get("/api/incidents/{incident_id}/messages")
async def get_incident_messages(incident_id: str):
    """Get all MCP messages for an incident."""
    try:
        messages = mcp_bus.get_incident_messages(incident_id)
    except AttributeError:
        messages = []
    return {
        "incident_id": incident_id,
        "count": len(messages),
        "messages": [m.to_dict() for m in messages],
    }


@app.get("/api/incidents/{incident_id}/debate")
async def get_debate(incident_id: str):
    """Get debate messages for an incident."""
    try:
        messages = mcp_bus.get_debate_messages(incident_id)
    except AttributeError:
        messages = []
    return {
        "incident_id": incident_id,
        "debate_rounds": len(messages),
        "messages": [m.to_dict() for m in messages],
    }


@app.get("/api/messages")
async def get_all_messages():
    """Get all MCP messages (for debugging)."""
    try:
        all_msgs = mcp_bus.get_all_messages()
    except AttributeError:
        all_msgs = []
    return {
        "count": len(all_msgs),
        "messages": [m.to_dict() for m in all_msgs[-50:]],
    }


# ─── WebSocket Endpoint ──────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ws_connections.append(websocket)
    logger.info("🔌 WebSocket connected (total: %d)", len(ws_connections))

    try:
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(), timeout=30.0
                )
                if data == "ping":
                    await websocket.send_text("pong")
                else:
                    logger.info("WS received: %s", data)
            except asyncio.TimeoutError:
                # Send keepalive ping to prevent Azure from closing the connection
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("WebSocket error: %s", exc)
    finally:
        if websocket in ws_connections:
            ws_connections.remove(websocket)
        logger.info("🔌 WebSocket disconnected (total: %d)", len(ws_connections))


# ─── Main ────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8091"))
    uvicorn.run(app, host="0.0.0.0", port=port)