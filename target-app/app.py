"""
Target Application: TaskManager API
This is the "production" app that IncidentZero monitors.
It has an intentionally injectable bug for demo purposes.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
from typing import Optional
import asyncio
import random
import logging

# ─── Setup ───────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("target-app")

app = FastAPI(title="TaskManager API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Data Models ─────────────────────────────────────────
class TaskCreate(BaseModel):
    title: str
    description: str = ""

class Task(BaseModel):
    id: int
    title: str
    description: str
    status: str
    created_at: str

# ─── Simulated Database ─────────────────────────────────
tasks_db: dict = {}
task_counter: int = 0

# ─── Simulated Connection Pool ───────────────────────────
class ConnectionPool:
    """
    Simulates a database connection pool.
    This is where the injectable bug lives.
    """
    def __init__(self, max_size: int = 20):
        self.max_size = max_size
        self.active = 0

    async def acquire(self):
        if self.active >= self.max_size:
            raise Exception(
                f"ConnectionPool exhausted: {self.active}/{self.max_size} "
                f"connections in use. No connections available."
            )
        self.active += 1
        return {"connection_id": self.active}

    def release(self, conn):
        if self.active > 0:
            self.active -= 1

pool = ConnectionPool(max_size=20)

# ─── Bug Injection State ────────────────────────────────
BUG_INJECTED = False

# ─── Health & Metrics Endpoints ──────────────────────────
@app.get("/health")
async def health():
    """Health check endpoint - WatcherAgent polls this"""
    return {
        "status": "healthy" if pool.active < pool.max_size * 0.8 else "degraded",
        "timestamp": datetime.utcnow().isoformat(),
        "active_connections": pool.active,
        "max_connections": pool.max_size,
        "bug_injected": BUG_INJECTED
    }

@app.get("/metrics")
async def metrics():
    """Metrics endpoint - WatcherAgent uses this for monitoring"""
    return {
        "active_connections": pool.active,
        "max_connections": pool.max_size,
        "connection_utilization": round(pool.active / pool.max_size, 3),
        "total_tasks": len(tasks_db),
        "bug_injected": BUG_INJECTED,
        "pool_status": "critical" if pool.active >= pool.max_size * 0.9 else "normal"
    }

# ─── CRUD Endpoints ─────────────────────────────────────
@app.get("/tasks")
async def list_tasks():
    """List all tasks - uses connection pool"""
    conn = None
    try:
        conn = await pool.acquire()
        await asyncio.sleep(0.01)  # Simulate DB query
        result = list(tasks_db.values())
        return {"tasks": result, "count": len(result)}

    except Exception as e:
        logger.error(f"Error in list_tasks: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if conn is not None:
            if BUG_INJECTED:
                # THE BUG: 70% chance of NOT releasing the connection
                if random.random() < 0.3:
                    pool.release(conn)
                else:
                    logger.warning("Connection leaked! (bug injected)")
                    pass  # CONNECTION LEAKED
            else:
                pool.release(conn)

@app.post("/tasks")
async def create_task(task: TaskCreate):
    """Create a new task - uses connection pool"""
    global task_counter
    conn = None
    try:
        conn = await pool.acquire()
        await asyncio.sleep(0.01)  # Simulate DB write

        task_counter += 1
        new_task = {
            "id": task_counter,
            "title": task.title,
            "description": task.description,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat()
        }
        tasks_db[task_counter] = new_task
        return new_task

    except Exception as e:
        logger.error(f"Error in create_task: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if conn is not None:
            if BUG_INJECTED:
                if random.random() < 0.3:
                    pool.release(conn)
                else:
                    pass  # CONNECTION LEAKED
            else:
                pool.release(conn)

@app.delete("/tasks/{task_id}")
async def delete_task(task_id: int):
    if task_id not in tasks_db:
        raise HTTPException(status_code=404, detail="Task not found")
    del tasks_db[task_id]
    return {"deleted": task_id}

# ─── Chaos Engineering Endpoints ─────────────────────────
@app.post("/chaos/inject")
async def inject_bug():
    """Inject the connection leak bug"""
    global BUG_INJECTED
    BUG_INJECTED = True
    logger.warning("🐛 BUG INJECTED: Connection pool leak activated")
    return {
        "status": "bug_injected",
        "bug_type": "connection_pool_leak",
        "description": "70% of connections will not be released after use"
    }

@app.post("/chaos/fix")
async def fix_bug():
    """Fix the bug and reset connections"""
    global BUG_INJECTED
    BUG_INJECTED = False
    pool.active = 0  # Reset pool
    logger.info("✅ BUG FIXED: Connection pool leak deactivated")
    return {
        "status": "bug_fixed",
        "connections_reset": True,
        "active_connections": pool.active
    }

@app.get("/chaos/status")
async def chaos_status():
    return {
        "bug_injected": BUG_INJECTED,
        "active_connections": pool.active,
        "max_connections": pool.max_size,
        "utilization_pct": round(pool.active / pool.max_size * 100, 1)
    }

# ─── Load Generator ─────────────────────────────────────
@app.post("/chaos/generate-load")
async def generate_load():
    """Generate synthetic load to trigger the bug faster"""
    results = []
    for i in range(10):
        try:
            conn = None
            conn = await pool.acquire()
            await asyncio.sleep(0.01)
            results.append({"request": i, "status": "success"})
        except Exception as e:
            results.append({"request": i, "status": "error", "detail": str(e)})
        finally:
            if conn is not None:
                if BUG_INJECTED:
                    if random.random() < 0.3:
                        pool.release(conn)
                else:
                    pool.release(conn)

    return {
        "load_test_results": results,
        "pool_status": {
            "active": pool.active,
            "max": pool.max_size
        }
    }

# ─── Main ────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)