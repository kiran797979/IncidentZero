"""
Target Application — Azure Functions
TaskManager API with injectable connection pool bug.
"""

import azure.functions as func
import json
import random
import logging
from datetime import datetime

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("target-app")

# ─── Simulated State ────────────────────────────────────
tasks_db: dict = {}
task_counter: int = 0
BUG_INJECTED: bool = False


class ConnectionPool:
    def __init__(self, max_size: int = 20):
        self.max_size = max_size
        self.active = 0

    def acquire(self):
        if self.active >= self.max_size:
            raise Exception(
                f"ConnectionPool exhausted: {self.active}/{self.max_size}"
            )
        self.active += 1
        return {"connection_id": self.active}

    def release(self, conn):
        if self.active > 0:
            self.active -= 1


pool = ConnectionPool(max_size=20)


def make_response(data: dict, status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(data),
        status_code=status_code,
        mimetype="application/json",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        },
    )


# ─── Health Check ────────────────────────────────────────
@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    status = "healthy" if pool.active < pool.max_size * 0.8 else "degraded"
    return make_response({
        "status": status,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "active_connections": pool.active,
        "max_connections": pool.max_size,
        "bug_injected": BUG_INJECTED,
    })


# ─── Metrics ─────────────────────────────────────────────
@app.route(route="metrics", methods=["GET"])
def metrics(req: func.HttpRequest) -> func.HttpResponse:
    return make_response({
        "active_connections": pool.active,
        "max_connections": pool.max_size,
        "connection_utilization": round(
            pool.active / pool.max_size, 3
        ) if pool.max_size > 0 else 0,
        "total_tasks": len(tasks_db),
        "bug_injected": BUG_INJECTED,
        "pool_status": (
            "critical" if pool.active >= pool.max_size * 0.9 else "normal"
        ),
    })


# ─── List Tasks ──────────────────────────────────────────
@app.route(route="tasks", methods=["GET"])
def list_tasks(req: func.HttpRequest) -> func.HttpResponse:
    global BUG_INJECTED
    conn = None
    try:
        conn = pool.acquire()
        result = list(tasks_db.values())
        return make_response({"tasks": result, "count": len(result)})
    except Exception as e:
        logger.error(f"Error listing tasks: {e}")
        return make_response({"error": str(e)}, status_code=500)
    finally:
        if conn is not None:
            if BUG_INJECTED:
                if random.random() < 0.3:
                    pool.release(conn)
                else:
                    logger.warning("Connection leaked!")
            else:
                pool.release(conn)


# ─── Create Task ─────────────────────────────────────────
@app.route(route="tasks", methods=["POST"])
def create_task(req: func.HttpRequest) -> func.HttpResponse:
    global task_counter, BUG_INJECTED
    conn = None
    try:
        conn = pool.acquire()
        body = req.get_json()
        task_counter += 1
        new_task = {
            "id": task_counter,
            "title": body.get("title", "Untitled"),
            "description": body.get("description", ""),
            "status": "pending",
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        tasks_db[task_counter] = new_task
        return make_response(new_task, status_code=201)
    except Exception as e:
        logger.error(f"Error creating task: {e}")
        return make_response({"error": str(e)}, status_code=500)
    finally:
        if conn is not None:
            if BUG_INJECTED:
                if random.random() < 0.3:
                    pool.release(conn)
            else:
                pool.release(conn)


# ─── Chaos: Inject Bug ──────────────────────────────────
@app.route(route="chaos/inject", methods=["POST"])
def chaos_inject(req: func.HttpRequest) -> func.HttpResponse:
    global BUG_INJECTED
    BUG_INJECTED = True
    logger.warning("BUG INJECTED: Connection pool leak activated")
    return make_response({
        "status": "bug_injected",
        "bug_type": "connection_pool_leak",
        "description": "70% of connections will not be released",
    })


# ─── Chaos: Fix Bug ─────────────────────────────────────
@app.route(route="chaos/fix", methods=["POST"])
def chaos_fix(req: func.HttpRequest) -> func.HttpResponse:
    global BUG_INJECTED
    BUG_INJECTED = False
    pool.active = 0
    logger.info("BUG FIXED: Connection pool leak deactivated")
    return make_response({
        "status": "bug_fixed",
        "connections_reset": True,
        "active_connections": pool.active,
    })


# ─── Chaos: Status ───────────────────────────────────────
@app.route(route="chaos/status", methods=["GET"])
def chaos_status(req: func.HttpRequest) -> func.HttpResponse:
    return make_response({
        "bug_injected": BUG_INJECTED,
        "active_connections": pool.active,
        "max_connections": pool.max_size,
        "utilization_pct": round(pool.active / pool.max_size * 100, 1),
    })


# ─── Chaos: Generate Load ───────────────────────────────
@app.route(route="chaos/generate-load", methods=["POST"])
def chaos_generate_load(req: func.HttpRequest) -> func.HttpResponse:
    global BUG_INJECTED
    results = []
    for i in range(10):
        conn = None
        try:
            conn = pool.acquire()
            results.append({"request": i, "status": "success"})
        except Exception as e:
            results.append({
                "request": i,
                "status": "error",
                "detail": str(e),
            })
        finally:
            if conn is not None:
                if BUG_INJECTED:
                    if random.random() < 0.3:
                        pool.release(conn)
                else:
                    pool.release(conn)

    return make_response({
        "load_test_results": results,
        "pool_status": {
            "active": pool.active,
            "max": pool.max_size,
        },
    })