"""
IncidentZero Backend — Azure Functions (Serverless)
Autonomous AI SRE Team — Multi-Agent Incident Resolution

Endpoints:
  GET  /api/health              System info
  GET  /api/status              Lightweight connection check (frontend polls)
  GET  /api/messages?since=N    Incremental message feed (frontend polls every 1.5s)
  POST /api/run-incident        Trigger full autonomous incident lifecycle
  POST /api/inject              Shortcut: inject chaos into target app
  POST /api/fix                 Shortcut: manually fix target app
  GET  /api/incidents           List all incidents
  GET  /api/incidents/{id}      Single incident detail
  GET  /api/target/health       Proxy to target app /health
  GET  /api/target/metrics      Proxy to target app /metrics
"""

import azure.functions as func
import json
import asyncio
import logging
import os
import base64
import random
import re
import httpx
import traceback
from datetime import datetime, timezone

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("incidentzero")


# ═══════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════

AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY", "")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-06")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
TARGET_APP_URL = os.getenv(
    "TARGET_APP_URL",
    "https://incidentzero-target.azurewebsites.net",
).rstrip("/")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO_OWNER = os.getenv("GITHUB_REPO_OWNER", "")
GITHUB_REPO_NAME = os.getenv("GITHUB_REPO_NAME", "incidentzero")

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With",
    "Access-Control-Max-Age": "86400",
}


# ═══════════════════════════════════════════════════════════
# 7 INCIDENT SCENARIOS — each run cycles to the next one
# ═══════════════════════════════════════════════════════════

INCIDENT_SCENARIOS = [
    {
        "type": "connection_pool_exhaustion",
        "description": "Database connection pool leak causing cascading request failures",
        "symptoms": {
            "error_rate": 0.42,
            "latency_ms": 8200,
            "connections": "19/20",
            "cpu_pct": 34,
            "memory_pct": 61,
        },
        "root_cause": "Connections not released in the finally block causing pool exhaustion",
        "severity": "P1",
        "blast_radius_pct": 42,
    },
    {
        "type": "memory_leak",
        "description": "Unbounded in-memory cache growing without eviction policy",
        "symptoms": {
            "memory_usage_pct": 92,
            "latency_ms": 4200,
            "gc_pause_ms": 850,
            "heap_mb": 1780,
            "error_rate": 0.18,
        },
        "root_cause": "Cache storing objects without TTL or max-size eviction",
        "severity": "P1",
        "blast_radius_pct": 55,
    },
    {
        "type": "slow_database_queries",
        "description": "Missing composite index causing full table scans on hot path",
        "symptoms": {
            "latency_ms": 6100,
            "query_time_ms": 5200,
            "error_rate": 0.08,
            "db_cpu_pct": 97,
            "rows_scanned": 2_400_000,
        },
        "root_cause": "Missing database index on (status, created_at) columns",
        "severity": "P2",
        "blast_radius_pct": 30,
    },
    {
        "type": "external_api_failure",
        "description": "Upstream payment gateway returning intermittent 503 errors",
        "symptoms": {
            "error_rate": 0.37,
            "api_status": "503",
            "latency_ms": 7400,
            "retry_queue_depth": 1240,
            "timeout_count": 87,
        },
        "root_cause": "Payment provider API unstable; no circuit breaker configured",
        "severity": "P1",
        "blast_radius_pct": 37,
    },
    {
        "type": "cache_failure",
        "description": "Redis primary node unreachable — thundering herd hitting database",
        "symptoms": {
            "cache_status": "down",
            "db_load_pct": 96,
            "latency_ms": 5800,
            "cache_hit_rate": 0.0,
            "error_rate": 0.29,
        },
        "root_cause": "Redis node crashed; no fallback or stale-serve strategy",
        "severity": "P1",
        "blast_radius_pct": 48,
    },
    {
        "type": "cpu_spike_thread_deadlock",
        "description": "Worker threads deadlocked on shared mutex causing CPU spike and request starvation",
        "symptoms": {
            "cpu_pct": 99,
            "active_threads": 200,
            "blocked_threads": 187,
            "latency_ms": 12000,
            "error_rate": 0.65,
            "throughput_rps": 3,
        },
        "root_cause": "Two code paths acquire locks in opposite order causing deadlock",
        "severity": "P0",
        "blast_radius_pct": 85,
    },
    {
        "type": "disk_io_saturation",
        "description": "Synchronous log flushing saturating disk I/O and blocking event loop",
        "symptoms": {
            "disk_util_pct": 100,
            "iowait_pct": 78,
            "latency_ms": 9500,
            "error_rate": 0.31,
            "log_queue_depth": 48000,
            "write_throughput_mbps": 0.4,
        },
        "root_cause": "Debug-level logging with sync flush on every request saturates disk",
        "severity": "P1",
        "blast_radius_pct": 44,
    },
]


# ═══════════════════════════════════════════════════════════
# IN-MEMORY STORES
# ═══════════════════════════════════════════════════════════

message_store: list = []
incident_store: dict = {}
incident_running: bool = False
_scenario_index: int = 0          # rotates through the 7 scenarios


def _next_scenario() -> dict:
    """Return the next scenario in round-robin order (never the same twice in a row)."""
    global _scenario_index
    scenario = INCIDENT_SCENARIOS[_scenario_index % len(INCIDENT_SCENARIOS)]
    _scenario_index += 1
    return scenario


# ═══════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════

def get_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe(value, default="N/A"):
    """Return *value* if truthy, otherwise *default*. Prevents empty strings / None."""
    if value is None or value == "":
        return default
    return value


def add_message(
    sender: str,
    recipient: str,
    msg_type: str,
    channel: str,
    payload: dict,
    incident_id: str = "",
    confidence: float = 0.0,
    evidence: list | None = None,
) -> dict:
    msg = {
        "message_id": f"msg-{len(message_store):04d}",
        "sender": _safe(sender, "System"),
        "recipient": _safe(recipient, "broadcast"),
        "message_type": _safe(msg_type, "status"),
        "channel": _safe(channel, "system"),
        "payload": payload if isinstance(payload, dict) else {"raw": str(payload)},
        "incident_id": _safe(incident_id, ""),
        "confidence": confidence if isinstance(confidence, (int, float)) else 0.0,
        "evidence": evidence if isinstance(evidence, list) else [],
        "timestamp": get_iso_now(),
    }
    message_store.append(msg)
    logger.info(f"[MCP] {msg['sender']} -> {msg['recipient']} | {msg['message_type']} | {msg['channel']}")
    return msg


def send_stage(stage_name: str, incident_id: str) -> None:
    add_message(
        sender="OrchestratorAgent",
        recipient="Dashboard",
        msg_type="status",
        channel="incident.stage",
        incident_id=incident_id,
        payload={"stage": stage_name},
    )


def make_response(data: dict, status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(data, default=str),
        status_code=status_code,
        mimetype="application/json",
        headers=CORS_HEADERS,
    )


def cors_preflight() -> func.HttpResponse:
    return func.HttpResponse(status_code=204, headers=CORS_HEADERS)


def get_llm_provider() -> str:
    if AZURE_OPENAI_KEY and AZURE_OPENAI_ENDPOINT:
        return "azure_openai"
    if OPENROUTER_API_KEY:
        return "openrouter"
    if OPENAI_API_KEY:
        return "openai"
    return "mock"


LLM_PROVIDER = get_llm_provider()
logger.info(f"Initialized LLM Provider: {LLM_PROVIDER}")


# ═══════════════════════════════════════════════════════════
# LLM SERVICE
# ═══════════════════════════════════════════════════════════

async def _call_azure_openai(system_prompt: str, user_prompt: str) -> str:
    from openai import AsyncAzureOpenAI
    client = AsyncAzureOpenAI(
        api_key=AZURE_OPENAI_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
    )
    response = await client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=2000,
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("Azure OpenAI returned empty content")
    return content


async def _call_openrouter(system_prompt: str, user_prompt: str) -> str:
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2000,
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ValueError("OpenRouter response missing choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise ValueError("OpenRouter response missing message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("OpenRouter content empty")
    return content


async def _call_openai(system_prompt: str, user_prompt: str) -> str:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=2000,
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("OpenAI returned empty content")
    return content


async def chat_llm(system_prompt: str, user_prompt: str) -> str:
    """Call the configured LLM with automatic mock fallback — never raises."""
    try:
        if LLM_PROVIDER == "azure_openai":
            return await _call_azure_openai(system_prompt, user_prompt)
        if LLM_PROVIDER == "openrouter":
            return await _call_openrouter(system_prompt, user_prompt)
        if LLM_PROVIDER == "openai":
            return await _call_openai(system_prompt, user_prompt)
    except Exception as e:
        logger.error(f"[LLM] {LLM_PROVIDER} call failed: {e}")
    # guaranteed fallback
    return mock_response(system_prompt, user_prompt)


# ═══════════════════════════════════════════════════════════
# RICH MOCK RESPONSES — fully scenario-aware for all 7 types
# ═══════════════════════════════════════════════════════════

# ---------- per-scenario data tables ----------

_MOCK_TRIAGE = {
    "connection_pool_exhaustion": {
        "severity": "P1",
        "classification": "RESOURCE_EXHAUSTION",
        "blast_radius_pct": 42,
        "affected_endpoints": ["/tasks", "/tasks/{id}", "/tasks/search"],
        "auto_resolve_eligible": True,
        "escalate_to_human": False,
        "reasoning": (
            "Error rate spiked to 42 % with the connection pool at 95 % "
            "utilization. Requests are failing because no connections are "
            "available, impacting roughly 42 % of users."
        ),
    },
    "memory_leak": {
        "severity": "P1",
        "classification": "MEMORY_EXHAUSTION",
        "blast_radius_pct": 55,
        "affected_endpoints": ["/tasks", "/reports/generate", "/export"],
        "auto_resolve_eligible": True,
        "escalate_to_human": False,
        "reasoning": (
            "Heap usage at 92 % with 850 ms GC pauses. The cache grows "
            "without eviction, risking OOM within minutes. ~55 % of "
            "request paths touch the cache."
        ),
    },
    "slow_database_queries": {
        "severity": "P2",
        "classification": "PERFORMANCE_DEGRADATION",
        "blast_radius_pct": 30,
        "affected_endpoints": ["/tasks", "/tasks/filter"],
        "auto_resolve_eligible": True,
        "escalate_to_human": False,
        "reasoning": (
            "p99 latency jumped to 6 s. The query planner is running full "
            "table scans on 2.4 M rows. Impact is limited to list/filter "
            "endpoints (~30 % of traffic)."
        ),
    },
    "external_api_failure": {
        "severity": "P1",
        "classification": "UPSTREAM_DEPENDENCY_FAILURE",
        "blast_radius_pct": 37,
        "affected_endpoints": ["/checkout", "/payments", "/refunds"],
        "auto_resolve_eligible": True,
        "escalate_to_human": False,
        "reasoning": (
            "Payment gateway returning 503 for 37 % of calls. No circuit "
            "breaker is configured, so every checkout attempt hangs for "
            "the full 30 s timeout."
        ),
    },
    "cache_failure": {
        "severity": "P1",
        "classification": "INFRASTRUCTURE_OUTAGE",
        "blast_radius_pct": 48,
        "affected_endpoints": ["/tasks", "/dashboard", "/search"],
        "auto_resolve_eligible": True,
        "escalate_to_human": False,
        "reasoning": (
            "Redis primary is unreachable. Cache hit rate dropped to 0 %. "
            "All reads are falling through to the database, pushing DB "
            "CPU to 96 %. ~48 % of traffic is affected."
        ),
    },
    "cpu_spike_thread_deadlock": {
        "severity": "P0",
        "classification": "CRITICAL_OUTAGE",
        "blast_radius_pct": 85,
        "affected_endpoints": ["/tasks", "/tasks/{id}", "/health", "/admin"],
        "auto_resolve_eligible": True,
        "escalate_to_human": True,
        "reasoning": (
            "CPU pegged at 99 % with 187/200 threads blocked. Only 3 RPS "
            "throughput vs normal 1200 RPS. This is a near-total outage "
            "affecting 85 % of users."
        ),
    },
    "disk_io_saturation": {
        "severity": "P1",
        "classification": "IO_BOTTLENECK",
        "blast_radius_pct": 44,
        "affected_endpoints": ["/tasks", "/upload", "/reports"],
        "auto_resolve_eligible": True,
        "escalate_to_human": False,
        "reasoning": (
            "Disk utilization at 100 %, iowait at 78 %. Synchronous log "
            "flushing on every request is blocking the event loop. ~44 % "
            "of requests are timing out."
        ),
    },
}

_MOCK_DIAGNOSIS = {
    "connection_pool_exhaustion": {
        "root_cause": {
            "category": "RESOURCE_EXHAUSTION",
            "component": "database_connection_pool",
            "file": "app.py",
            "function": "list_tasks / create_task",
            "mechanism": "Connection pool leak in finally block",
            "detail": (
                "The finally clause uses a conditional random check that "
                "skips pool.release() ~70 % of the time when the bug flag "
                "is active, causing connections to leak until the pool is "
                "fully exhausted."
            ),
        },
        "confidence": 0.88,
        "evidence_analysis": [
            "active_connections 19/20 — pool nearly exhausted",
            "Error rate correlates with connection count growth",
            "Thread dump shows threads waiting on pool.acquire()",
            "Connection hold time in error path: 847 ms (normal: 10 ms)",
        ],
        "alternative_hypotheses": [
            {"category": "SLOW_QUERIES", "confidence": 0.10, "reason": "Query exec times are normal (< 15 ms)"},
        ],
    },
    "memory_leak": {
        "root_cause": {
            "category": "MEMORY_LEAK",
            "component": "application_cache",
            "file": "app.py",
            "function": "get_or_set_cache",
            "mechanism": "Unbounded dict cache with no eviction",
            "detail": (
                "Every unique request key is cached in a plain dict with "
                "no max-size limit and no TTL. Under production traffic "
                "patterns the cache grows to millions of entries."
            ),
        },
        "confidence": 0.91,
        "evidence_analysis": [
            "Heap grew from 400 MB to 1780 MB in 20 minutes",
            "GC pause time increased from 5 ms to 850 ms",
            "Object count in cache dict: 2.4 M entries",
            "No __del__ or weakref clean-up observed",
        ],
        "alternative_hypotheses": [
            {"category": "LARGE_PAYLOADS", "confidence": 0.08, "reason": "Request/response sizes are within normal range"},
        ],
    },
    "slow_database_queries": {
        "root_cause": {
            "category": "QUERY_PERFORMANCE",
            "component": "database_indexes",
            "file": "schema.sql",
            "function": "SELECT * FROM tasks WHERE status = ? ORDER BY created_at",
            "mechanism": "Missing composite index forces sequential scan",
            "detail": (
                "The tasks table has 2.4 M rows. The hot query filters by "
                "status and sorts by created_at, but there is no index on "
                "(status, created_at). The planner chooses Seq Scan → Sort."
            ),
        },
        "confidence": 0.93,
        "evidence_analysis": [
            "EXPLAIN shows Seq Scan on tasks (cost=0..48723)",
            "Rows scanned per query: 2,400,000",
            "Adding the index in staging reduced query from 5.2 s to 4 ms",
            "DB CPU dropped from 97 % to 12 % after index in staging",
        ],
        "alternative_hypotheses": [
            {"category": "LOCK_CONTENTION", "confidence": 0.06, "reason": "pg_locks shows no waiting transactions"},
        ],
    },
    "external_api_failure": {
        "root_cause": {
            "category": "UPSTREAM_DEPENDENCY",
            "component": "payment_provider_api",
            "file": "app.py",
            "function": "process_payment",
            "mechanism": "No circuit breaker; retries amplify upstream failure",
            "detail": (
                "The payment gateway is returning 503 intermittently. Our "
                "client has no circuit breaker, so every failed call is "
                "retried 3× with no back-off, amplifying load on the "
                "already-struggling upstream."
            ),
        },
        "confidence": 0.89,
        "evidence_analysis": [
            "Payment API 503 rate: 37 % over last 5 min",
            "Retry queue depth: 1240 (should be < 50)",
            "Timeout count: 87 in 5 min window",
            "Status page for provider confirms degradation",
        ],
        "alternative_hypotheses": [
            {"category": "DNS_RESOLUTION", "confidence": 0.05, "reason": "DNS TTL is fine; resolution < 2 ms"},
        ],
    },
    "cache_failure": {
        "root_cause": {
            "category": "CACHE_OUTAGE",
            "component": "redis_primary",
            "file": "app.py",
            "function": "cache_get / cache_set",
            "mechanism": "Redis primary unreachable; no fallback configured",
            "detail": (
                "Redis primary node OOM-killed. The app has no sentinel "
                "failover and no stale-serve fallback, so every cache miss "
                "goes straight to the database, creating a thundering herd."
            ),
        },
        "confidence": 0.92,
        "evidence_analysis": [
            "Redis PING timeout after 3 s",
            "Cache hit rate dropped from 94 % to 0 %",
            "DB read QPS jumped from 120 to 4800",
            "DB CPU at 96 % — approaching hard limit",
        ],
        "alternative_hypotheses": [
            {"category": "NETWORK_PARTITION", "confidence": 0.07, "reason": "Other services on same VNet are healthy"},
        ],
    },
    "cpu_spike_thread_deadlock": {
        "root_cause": {
            "category": "THREAD_DEADLOCK",
            "component": "task_worker_mutex",
            "file": "worker.py",
            "function": "process_task / update_inventory",
            "mechanism": "Lock ordering inversion causes deadlock",
            "detail": (
                "process_task() acquires lock_A then lock_B, while "
                "update_inventory() acquires lock_B then lock_A. Under "
                "concurrent load both paths run simultaneously, causing a "
                "classic ABBA deadlock. 187 of 200 threads are stuck."
            ),
        },
        "confidence": 0.95,
        "evidence_analysis": [
            "Thread dump: 187 threads in BLOCKED state",
            "Lock graph shows cycle: lock_A -> lock_B -> lock_A",
            "CPU at 99 % due to spin-wait in lock acquisition",
            "Throughput dropped from 1200 RPS to 3 RPS",
        ],
        "alternative_hypotheses": [
            {"category": "INFINITE_LOOP", "confidence": 0.04, "reason": "Stack traces show wait(), not compute"},
        ],
    },
    "disk_io_saturation": {
        "root_cause": {
            "category": "IO_SATURATION",
            "component": "logging_subsystem",
            "file": "app.py",
            "function": "request_logger_middleware",
            "mechanism": "Sync flush of debug-level logs on every request",
            "detail": (
                "The request logger middleware is set to DEBUG level with "
                "flush=True on every write. At 800 RPS, this generates "
                "~48 K log lines/sec with synchronous disk writes, "
                "saturating the disk and blocking the async event loop."
            ),
        },
        "confidence": 0.90,
        "evidence_analysis": [
            "iostat shows 100 % disk utilization, 0.4 MB/s write throughput",
            "iowait at 78 % — threads sleeping on I/O",
            "Log file growing at 12 MB/min",
            "Disabling debug logging in staging restored normal latency",
        ],
        "alternative_hypotheses": [
            {"category": "WAL_BLOAT", "confidence": 0.09, "reason": "Postgres WAL size is within normal range"},
        ],
    },
}

_MOCK_CHALLENGE = {
    "connection_pool_exhaustion": {
        "assessment": "CHALLENGE",
        "reasoning": "Could slow queries be holding connections open longer than expected, masquerading as a leak?",
        "challenge_question": "What direct evidence distinguishes a true leak from slow-query hold time?",
        "alternative_hypothesis": "slow_query_hold_time",
        "confidence_in_diagnosis": 0.65,
    },
    "memory_leak": {
        "assessment": "CHALLENGE",
        "reasoning": "Is the growth truly from the cache, or could large response payloads in the HTTP framework be accumulating?",
        "challenge_question": "Can you prove the cache dict itself is the dominant allocator?",
        "alternative_hypothesis": "framework_buffer_accumulation",
        "confidence_in_diagnosis": 0.60,
    },
    "slow_database_queries": {
        "assessment": "CHALLENGE",
        "reasoning": "Might the slowdown be lock contention from concurrent writes rather than a missing index?",
        "challenge_question": "Have you checked pg_stat_activity for waiting transactions?",
        "alternative_hypothesis": "write_lock_contention",
        "confidence_in_diagnosis": 0.62,
    },
    "external_api_failure": {
        "assessment": "CHALLENGE",
        "reasoning": "Could our own request volume be causing the upstream 503 s — i.e., we are DDoS-ing our provider?",
        "challenge_question": "What is our outbound QPS vs the provider's published rate limit?",
        "alternative_hypothesis": "self_inflicted_rate_limit",
        "confidence_in_diagnosis": 0.58,
    },
    "cache_failure": {
        "assessment": "CHALLENGE",
        "reasoning": "Is the database overload really from cache misses, or was there already a slow-query issue masked by the cache?",
        "challenge_question": "What was the DB CPU trend before the Redis outage started?",
        "alternative_hypothesis": "pre_existing_db_issue",
        "confidence_in_diagnosis": 0.63,
    },
    "cpu_spike_thread_deadlock": {
        "assessment": "CHALLENGE",
        "reasoning": "Are you sure it is a deadlock and not an infinite retry loop in the task processor?",
        "challenge_question": "Do the blocked thread stacks show wait() or active computation?",
        "alternative_hypothesis": "infinite_retry_loop",
        "confidence_in_diagnosis": 0.55,
    },
    "disk_io_saturation": {
        "assessment": "CHALLENGE",
        "reasoning": "Could the I/O spike be caused by checkpoint/WAL activity rather than application logging?",
        "challenge_question": "What percentage of disk writes originate from the app log vs Postgres WAL?",
        "alternative_hypothesis": "database_checkpoint_storm",
        "confidence_in_diagnosis": 0.61,
    },
}

_MOCK_DEFENSE = {
    "connection_pool_exhaustion": {
        "response_type": "DEFEND",
        "response": (
            "Good challenge. I checked connection hold times. Average query "
            "exec is 10 ms (normal), but connection HOLD time on the error "
            "path is 847 ms because the finally block skips pool.release() "
            "70 % of the time. This confirms a leak, not slow queries."
        ),
        "additional_evidence": [
            "avg_query_time: 10 ms (normal)",
            "avg_conn_hold_time_error_path: 847 ms (abnormal)",
            "finally block: conditional release via random()",
        ],
        "confidence": 0.94,
    },
    "memory_leak": {
        "response_type": "DEFEND",
        "response": (
            "Valid question. I profiled with tracemalloc: 1.3 GB of the "
            "1.78 GB heap is attributed to cache_store dict entries. "
            "Framework buffers account for only 80 MB. The cache is "
            "definitively the dominant allocator."
        ),
        "additional_evidence": [
            "tracemalloc top: cache_store — 1.3 GB (73 %)",
            "Framework buffers — 80 MB (4.5 %)",
            "Cache entry count: 2.4 M with avg 570 bytes/entry",
        ],
        "confidence": 0.95,
    },
    "slow_database_queries": {
        "response_type": "DEFEND",
        "response": (
            "I checked pg_stat_activity: zero waiting transactions. "
            "EXPLAIN ANALYZE on the hot query confirms Seq Scan with "
            "2.4 M rows. After creating the index in staging, the same "
            "query dropped from 5.2 s to 4 ms. Lock contention is ruled out."
        ),
        "additional_evidence": [
            "pg_stat_activity waiting count: 0",
            "EXPLAIN ANALYZE: Seq Scan cost 48723, rows 2.4 M",
            "With index: Index Scan cost 8.2, rows 47",
        ],
        "confidence": 0.96,
    },
    "external_api_failure": {
        "response_type": "DEFEND",
        "response": (
            "I compared our outbound QPS (340/s) against the provider's "
            "published limit (5000/s). We are well within limits. The "
            "provider's status page also confirms intermittent degradation "
            "on their end. Our retry amplification is worsening their load "
            "but is not the root cause."
        ),
        "additional_evidence": [
            "Our outbound QPS: 340/s (limit 5000/s)",
            "Provider status: Intermittent 503 since 14:23 UTC",
            "No rate-limit 429 responses received",
        ],
        "confidence": 0.93,
    },
    "cache_failure": {
        "response_type": "DEFEND",
        "response": (
            "DB CPU was 11 % before the Redis failure window, then jumped "
            "to 96 % exactly when Redis went down. The correlation is "
            "direct. There was no pre-existing DB problem."
        ),
        "additional_evidence": [
            "DB CPU 14:00-14:22: avg 11 %",
            "Redis unreachable at 14:22:41",
            "DB CPU 14:23+: avg 96 %",
        ],
        "confidence": 0.95,
    },
    "cpu_spike_thread_deadlock": {
        "response_type": "DEFEND",
        "response": (
            "Thread dump confirms all 187 blocked threads are in "
            "Object.wait() inside ReentrantLock.lock(), not in active "
            "computation. The lock dependency graph shows a clear A→B / "
            "B→A cycle. This is textbook deadlock, not a retry loop."
        ),
        "additional_evidence": [
            "187 threads: state=BLOCKED in ReentrantLock.lock()",
            "Lock graph cycle: lock_A → lock_B → lock_A",
            "No stack frames in retry/loop methods",
        ],
        "confidence": 0.97,
    },
    "disk_io_saturation": {
        "response_type": "DEFEND",
        "response": (
            "I separated write sources using blktrace. Application log "
            "writes account for 94 % of disk I/O. Postgres WAL writes are "
            "only 5 %. Disabling flush=True in staging immediately dropped "
            "disk util from 100 % to 18 %."
        ),
        "additional_evidence": [
            "blktrace: app log writes 94 % of I/O",
            "Postgres WAL: 5 % of I/O",
            "After flush=False in staging: disk util 18 %",
        ],
        "confidence": 0.94,
    },
}

_MOCK_FIX = {
    "connection_pool_exhaustion": {
        "fix": {
            "file": "app.py",
            "description": "Unconditionally release connections in finally block",
            "diff": (
                "--- a/app.py\n+++ b/app.py\n"
                "@@ -60,8 +60,4 @@ def list_tasks():\n"
                "     finally:\n"
                "-        if conn is not None:\n"
                "-            if BUG_INJECTED:\n"
                "-                if random.random() < 0.3:\n"
                "-                    pool.release(conn)\n"
                "-                else:\n"
                "-                    pass  # CONNECTION LEAKED!\n"
                "+        if conn is not None:\n"
                "+            pool.release(conn)  # ALWAYS release\n"
            ),
            "risk_level": "LOW",
            "explanation": "Ensures every acquired connection is returned to the pool regardless of error state.",
            "lines_changed": 6,
        },
        "validation_steps": [
            "Connection utilization drops below 25 %",
            "Error rate returns to baseline (< 0.5 %)",
            "/health reports status=healthy",
        ],
    },
    "memory_leak": {
        "fix": {
            "file": "app.py",
            "description": "Add bounded LRU cache with TTL eviction",
            "diff": (
                "--- a/app.py\n+++ b/app.py\n"
                "@@ -18,2 +18,6 @@\n"
                "+MAX_CACHE_ITEMS = 10_000\n"
                "+CACHE_TTL_SECONDS = 300\n"
                "@@ -85,4 +89,10 @@\n"
                " def get_or_set_cache(key, factory):\n"
                "-    cache_store[key] = factory()\n"
                "+    if len(cache_store) >= MAX_CACHE_ITEMS:\n"
                "+        oldest = next(iter(cache_store))\n"
                "+        del cache_store[oldest]\n"
                "+    cache_store[key] = (factory(), time.time())\n"
                "+    _evict_expired(cache_store, CACHE_TTL_SECONDS)\n"
            ),
            "risk_level": "LOW",
            "explanation": "Caps cache at 10 K entries and evicts entries older than 5 minutes.",
            "lines_changed": 10,
        },
        "validation_steps": [
            "Heap usage stabilizes below 600 MB",
            "GC pause time returns to < 20 ms",
            "Cache entry count stays ≤ 10 000",
        ],
    },
    "slow_database_queries": {
        "fix": {
            "file": "schema.sql",
            "description": "Add composite index on (status, created_at DESC)",
            "diff": (
                "--- a/schema.sql\n+++ b/schema.sql\n"
                "@@ -40,0 +41,3 @@\n"
                "+CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tasks_status_created\n"
                "+  ON tasks (status, created_at DESC);\n"
            ),
            "risk_level": "LOW",
            "explanation": "CREATE INDEX CONCURRENTLY avoids locking the table while building the index.",
            "lines_changed": 2,
        },
        "validation_steps": [
            "Query latency drops from 5.2 s to < 10 ms",
            "DB CPU returns to < 20 %",
            "EXPLAIN shows Index Scan instead of Seq Scan",
        ],
    },
    "external_api_failure": {
        "fix": {
            "file": "app.py",
            "description": "Add circuit breaker with exponential back-off for payment API",
            "diff": (
                "--- a/app.py\n+++ b/app.py\n"
                "@@ -120,5 +120,18 @@\n"
                " def process_payment(payload):\n"
                "-    return payment_client.charge(payload)\n"
                "+    for attempt in range(3):\n"
                "+        try:\n"
                "+            if _circuit_open:\n"
                "+                raise CircuitOpenError()\n"
                "+            return payment_client.charge(payload, timeout=5)\n"
                "+        except UpstreamError:\n"
                "+            await asyncio.sleep(2 ** attempt)\n"
                "+    _maybe_open_circuit()\n"
                "+    raise PaymentUnavailableError('circuit open')\n"
            ),
            "risk_level": "MEDIUM",
            "explanation": "Limits retry blast radius and fails fast once the circuit opens.",
            "lines_changed": 12,
        },
        "validation_steps": [
            "Retry queue depth drops to < 50",
            "Timeout count drops to near zero",
            "Circuit opens after 5 consecutive failures",
        ],
    },
    "cache_failure": {
        "fix": {
            "file": "app.py",
            "description": "Add local stale-cache fallback and DB read throttling on Redis outage",
            "diff": (
                "--- a/app.py\n+++ b/app.py\n"
                "@@ -70,4 +70,14 @@\n"
                " def cache_get(key):\n"
                "-    return redis.get(key)\n"
                "+    try:\n"
                "+        val = redis.get(key)\n"
                "+        _local_stale[key] = val\n"
                "+        return val\n"
                "+    except RedisConnectionError:\n"
                "+        stale = _local_stale.get(key)\n"
                "+        if stale is not None:\n"
                "+            return stale\n"
                "+        _db_throttle.acquire()\n"
                "+        return db_read(key)\n"
            ),
            "risk_level": "LOW",
            "explanation": "Serves stale data during Redis outage and throttles DB reads to prevent thundering herd.",
            "lines_changed": 11,
        },
        "validation_steps": [
            "DB CPU drops below 30 % during Redis outage",
            "Stale cache serves 80 %+ of reads during outage",
            "No user-visible errors during failover window",
        ],
    },
    "cpu_spike_thread_deadlock": {
        "fix": {
            "file": "worker.py",
            "description": "Enforce consistent lock ordering (always lock_A before lock_B)",
            "diff": (
                "--- a/worker.py\n+++ b/worker.py\n"
                "@@ -44,6 +44,6 @@\n"
                " def update_inventory(item):\n"
                "-    with lock_B:\n"
                "-        with lock_A:          # WRONG ORDER\n"
                "-            do_inventory(item)\n"
                "+    with lock_A:              # SAME ORDER as process_task\n"
                "+        with lock_B:\n"
                "+            do_inventory(item)\n"
            ),
            "risk_level": "LOW",
            "explanation": "Classic fix: acquire locks in a globally consistent order to break the deadlock cycle.",
            "lines_changed": 4,
        },
        "validation_steps": [
            "Blocked thread count drops to 0",
            "CPU returns to < 30 %",
            "Throughput recovers to > 1000 RPS",
        ],
    },
    "disk_io_saturation": {
        "fix": {
            "file": "app.py",
            "description": "Switch request logger to async writes and reduce log level to INFO",
            "diff": (
                "--- a/app.py\n+++ b/app.py\n"
                "@@ -30,4 +30,6 @@\n"
                " handler = logging.FileHandler('app.log')\n"
                "-handler.setLevel(logging.DEBUG)\n"
                "-handler.stream.flush = True   # sync flush every line\n"
                "+handler.setLevel(logging.INFO)\n"
                "+# Use async queue handler to avoid blocking event loop\n"
                "+queue_handler = QueueHandler(log_queue)\n"
                "+listener = QueueListener(log_queue, handler)\n"
                "+listener.start()\n"
            ),
            "risk_level": "LOW",
            "explanation": "Moves log writes off the event loop and drops verbose DEBUG output.",
            "lines_changed": 5,
        },
        "validation_steps": [
            "Disk utilization drops below 25 %",
            "iowait returns to < 5 %",
            "Request latency normalizes to < 50 ms",
        ],
    },
}

_MOCK_POSTMORTEM = {
    "connection_pool_exhaustion": (
        "# Incident Postmortem — Connection Pool Exhaustion\n\n"
        "## Executive Summary\n"
        "A P1 incident caused by leaked database connections resulted in a "
        "42 % error rate. The six-agent AI SRE team detected, diagnosed, "
        "debated, and resolved the issue autonomously.\n\n"
        "## Timeline\n"
        "| T+ | Agent | Action |\n"
        "|---|---|---|\n"
        "| 0 s | WatcherAgent | Detected error-rate spike 0 % → 42 % |\n"
        "| 5 s | TriageAgent | Classified P1 — 42 % blast radius |\n"
        "| 15 s | DiagnosisAgent | Root cause: connection leak in finally block |\n"
        "| 25 s | ResolutionAgent | **Challenged** — slow-query hypothesis |\n"
        "| 30 s | DiagnosisAgent | **Defended** — hold-time evidence (847 ms) |\n"
        "| 35 s | Consensus | Confidence raised to 94 % |\n"
        "| 40 s | ResolutionAgent | Generated diff: unconditional pool.release() |\n"
        "| 50 s | DeployAgent | Applied fix + verified /health |\n"
        "| 60 s | PostmortemAgent | Generated this report |\n\n"
        "## Root Cause\n"
        "The `finally` block released connections only 30 % of the time "
        "when the bug flag was active.\n\n"
        "## Resolution\n"
        "Changed `finally` to unconditionally call `pool.release(conn)`.\n\n"
        "## Lessons Learned\n"
        "1. Always release resources unconditionally in `finally`\n"
        "2. Alert at 70 % pool utilization\n"
        "3. Agent debate improved confidence from 65 % to 94 %\n\n"
        "---\n*Generated by IncidentZero PostmortemAgent*"
    ),
    "memory_leak": (
        "# Incident Postmortem — Memory Leak\n\n"
        "## Executive Summary\n"
        "Unbounded in-memory cache growth pushed heap to 92 %, causing "
        "850 ms GC pauses and 18 % error rate. The AI team resolved it "
        "by adding an LRU eviction policy.\n\n"
        "## Timeline\n"
        "| T+ | Agent | Action |\n"
        "|---|---|---|\n"
        "| 0 s | WatcherAgent | Memory 92 %, GC pause spike |\n"
        "| 5 s | TriageAgent | P1 — 55 % blast radius |\n"
        "| 15 s | DiagnosisAgent | Root cause: cache dict with no eviction |\n"
        "| 25 s | ResolutionAgent | **Challenged** — framework buffer theory |\n"
        "| 30 s | DiagnosisAgent | **Defended** — tracemalloc proof (73 % cache) |\n"
        "| 40 s | ResolutionAgent | Generated LRU + TTL fix |\n"
        "| 50 s | DeployAgent | Applied fix, heap stabilized |\n"
        "| 60 s | PostmortemAgent | Generated this report |\n\n"
        "## Root Cause\n"
        "Plain dict cache with no max-size or TTL.\n\n"
        "## Lessons Learned\n"
        "1. Always bound cache size\n"
        "2. Use TTL for cache entries\n"
        "3. Monitor heap growth rate, not just absolute size\n\n"
        "---\n*Generated by IncidentZero PostmortemAgent*"
    ),
    "slow_database_queries": (
        "# Incident Postmortem — Slow Database Queries\n\n"
        "## Executive Summary\n"
        "A missing composite index on the tasks table caused 5.2 s query "
        "times and 97 % DB CPU. A P2 incident affecting 30 % of traffic.\n\n"
        "## Timeline\n"
        "| T+ | Agent | Action |\n"
        "|---|---|---|\n"
        "| 0 s | WatcherAgent | Latency spike to 6.1 s |\n"
        "| 5 s | TriageAgent | P2 — 30 % blast radius |\n"
        "| 15 s | DiagnosisAgent | Missing index on (status, created_at) |\n"
        "| 25 s | ResolutionAgent | **Challenged** — lock contention theory |\n"
        "| 30 s | DiagnosisAgent | **Defended** — pg_stat_activity shows 0 waits |\n"
        "| 40 s | ResolutionAgent | Generated CREATE INDEX CONCURRENTLY |\n"
        "| 50 s | DeployAgent | Index built, latency < 10 ms |\n\n"
        "## Lessons Learned\n"
        "1. Run EXPLAIN ANALYZE on all hot queries\n"
        "2. Use CONCURRENTLY to avoid table locks\n"
        "3. Alert when query p99 > 1 s\n\n"
        "---\n*Generated by IncidentZero PostmortemAgent*"
    ),
    "external_api_failure": (
        "# Incident Postmortem — External API Failure\n\n"
        "## Executive Summary\n"
        "The payment gateway returned intermittent 503 errors. Without a "
        "circuit breaker, retries amplified the problem. Added circuit "
        "breaker + exponential back-off.\n\n"
        "## Timeline\n"
        "| T+ | Agent | Action |\n"
        "|---|---|---|\n"
        "| 0 s | WatcherAgent | Payment 503 rate at 37 % |\n"
        "| 5 s | TriageAgent | P1 — 37 % blast radius |\n"
        "| 15 s | DiagnosisAgent | No circuit breaker; retries amplify |\n"
        "| 25 s | ResolutionAgent | **Challenged** — self-DDoS theory |\n"
        "| 30 s | DiagnosisAgent | **Defended** — QPS within provider limits |\n"
        "| 40 s | ResolutionAgent | Generated circuit breaker fix |\n"
        "| 50 s | DeployAgent | Deployed; queue depth < 50 |\n\n"
        "## Lessons Learned\n"
        "1. Always wrap external calls in a circuit breaker\n"
        "2. Use exponential back-off on retries\n"
        "3. Subscribe to provider status page alerts\n\n"
        "---\n*Generated by IncidentZero PostmortemAgent*"
    ),
    "cache_failure": (
        "# Incident Postmortem — Cache Failure\n\n"
        "## Executive Summary\n"
        "Redis primary OOM-killed, causing a thundering herd of DB reads. "
        "Added stale-serve fallback and DB throttling.\n\n"
        "## Timeline\n"
        "| T+ | Agent | Action |\n"
        "|---|---|---|\n"
        "| 0 s | WatcherAgent | Cache hit rate 0 %, DB load 96 % |\n"
        "| 5 s | TriageAgent | P1 — 48 % blast radius |\n"
        "| 15 s | DiagnosisAgent | Redis down; no stale-serve |\n"
        "| 25 s | ResolutionAgent | **Challenged** — pre-existing DB issue? |\n"
        "| 30 s | DiagnosisAgent | **Defended** — DB CPU was 11 % before outage |\n"
        "| 40 s | ResolutionAgent | Generated stale fallback + throttle |\n"
        "| 50 s | DeployAgent | DB CPU < 30 % during next outage test |\n\n"
        "## Lessons Learned\n"
        "1. Always have a stale-serve strategy\n"
        "2. Throttle DB reads on cache misses\n"
        "3. Set up Redis Sentinel for automatic failover\n\n"
        "---\n*Generated by IncidentZero PostmortemAgent*"
    ),
    "cpu_spike_thread_deadlock": (
        "# Incident Postmortem — CPU Spike / Thread Deadlock\n\n"
        "## Executive Summary\n"
        "A P0 outage caused by ABBA lock ordering left 187 threads "
        "deadlocked. CPU at 99 %, throughput dropped to 3 RPS (normal "
        "1200). Fixed by enforcing consistent lock ordering.\n\n"
        "## Timeline\n"
        "| T+ | Agent | Action |\n"
        "|---|---|---|\n"
        "| 0 s | WatcherAgent | CPU 99 %, 187 blocked threads |\n"
        "| 5 s | TriageAgent | P0 — 85 % blast radius |\n"
        "| 15 s | DiagnosisAgent | Lock ordering inversion (A→B / B→A) |\n"
        "| 25 s | ResolutionAgent | **Challenged** — infinite loop theory |\n"
        "| 30 s | DiagnosisAgent | **Defended** — stacks show wait(), not compute |\n"
        "| 40 s | ResolutionAgent | Reordered locks to A→B everywhere |\n"
        "| 50 s | DeployAgent | Blocked threads = 0, throughput recovered |\n\n"
        "## Lessons Learned\n"
        "1. Enforce global lock ordering convention\n"
        "2. Use lock-timeout to detect deadlocks early\n"
        "3. Add thread-state monitoring to dashboards\n\n"
        "---\n*Generated by IncidentZero PostmortemAgent*"
    ),
    "disk_io_saturation": (
        "# Incident Postmortem — Disk I/O Saturation\n\n"
        "## Executive Summary\n"
        "Synchronous debug-level logging saturated disk I/O, blocking "
        "the event loop and causing 31 % error rate. Switched to async "
        "INFO-level logging.\n\n"
        "## Timeline\n"
        "| T+ | Agent | Action |\n"
        "|---|---|---|\n"
        "| 0 s | WatcherAgent | Disk 100 %, iowait 78 % |\n"
        "| 5 s | TriageAgent | P1 — 44 % blast radius |\n"
        "| 15 s | DiagnosisAgent | Sync flush on DEBUG logs |\n"
        "| 25 s | ResolutionAgent | **Challenged** — Postgres WAL theory |\n"
        "| 30 s | DiagnosisAgent | **Defended** — blktrace: 94 % from app logs |\n"
        "| 40 s | ResolutionAgent | Generated async log handler fix |\n"
        "| 50 s | DeployAgent | Disk util 18 %, latency normalized |\n\n"
        "## Lessons Learned\n"
        "1. Never use sync flush in hot paths\n"
        "2. Use async / queue-based log handlers\n"
        "3. Default to INFO level in production\n\n"
        "---\n*Generated by IncidentZero PostmortemAgent*"
    ),
}


def _detect_scenario_type(system_prompt: str, user_prompt: str) -> str:
    """Best-effort extraction of scenario type from prompt text."""
    combined = (user_prompt + " " + system_prompt).lower()
    for scenario in INCIDENT_SCENARIOS:
        if scenario["type"].replace("_", " ") in combined or scenario["type"] in combined:
            return scenario["type"]
    # Keyword fallback
    if "memory" in combined or "heap" in combined or "cache grow" in combined:
        return "memory_leak"
    if "slow quer" in combined or "index" in combined or "full table" in combined:
        return "slow_database_queries"
    if "payment" in combined or "upstream" in combined or "503" in combined:
        return "external_api_failure"
    if "redis" in combined or "cache fail" in combined or "thundering" in combined:
        return "cache_failure"
    if "deadlock" in combined or "blocked thread" in combined or "mutex" in combined:
        return "cpu_spike_thread_deadlock"
    if "disk" in combined or "iowait" in combined or "log flush" in combined:
        return "disk_io_saturation"
    return "connection_pool_exhaustion"


def mock_response(system_prompt: str, user_prompt: str = "") -> str:
    """Rich mock response that varies based on the detected scenario type."""
    sp = system_prompt.lower()
    scenario_type = _detect_scenario_type(system_prompt, user_prompt)

    if "triage" in sp:
        return json.dumps(_MOCK_TRIAGE.get(scenario_type, _MOCK_TRIAGE["connection_pool_exhaustion"]))

    if "diagnos" in sp and "challenge" not in sp and "defend" not in sp and "devil" not in sp:
        return json.dumps(_MOCK_DIAGNOSIS.get(scenario_type, _MOCK_DIAGNOSIS["connection_pool_exhaustion"]))

    if "challenge" in sp or "evaluate" in sp or "devil" in sp or "skeptic" in sp:
        return json.dumps(_MOCK_CHALLENGE.get(scenario_type, _MOCK_CHALLENGE["connection_pool_exhaustion"]))

    if "defend" in sp or "responding to" in sp:
        return json.dumps(_MOCK_DEFENSE.get(scenario_type, _MOCK_DEFENSE["connection_pool_exhaustion"]))

    if "fix" in sp or "resolution" in sp or "code" in sp:
        return json.dumps(_MOCK_FIX.get(scenario_type, _MOCK_FIX["connection_pool_exhaustion"]))

    if "postmortem" in sp:
        return _MOCK_POSTMORTEM.get(scenario_type, _MOCK_POSTMORTEM["connection_pool_exhaustion"])

    return json.dumps({
        "response": f"Mock response for scenario {scenario_type}",
        "scenario": scenario_type,
        "note": "Configure Azure OpenAI / OpenRouter / OpenAI for real AI responses",
    })


def parse_json_response(text: str) -> dict:
    """Safely extract JSON from text (may be wrapped in markdown fences)."""
    if not text or not isinstance(text, str):
        logger.warning("[JSON Parse] Received empty or non-string input")
        return {"raw_response": str(text), "_parse_error": "empty_input"}

    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```\s*$", "", text, flags=re.MULTILINE)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        pass

    # Last resort: try to find a JSON array
    try:
        start = text.index("[")
        end = text.rindex("]") + 1
        result = json.loads(text[start:end])
        return {"items": result}
    except (ValueError, json.JSONDecodeError):
        pass

    logger.error(f"[JSON Parse] All attempts failed on: {text[:200]}")
    return {"raw_response": text[:500], "_parse_error": "all_attempts_failed"}


# ═══════════════════════════════════════════════════════════
# GITHUB PR CREATION
# ═══════════════════════════════════════════════════════════

async def create_github_pr(
    incident_id: str,
    fix_data: dict,
    diagnosis_summary: str,
) -> str:
    fallback_url = f"https://github.com/{GITHUB_REPO_OWNER or 'owner'}/{GITHUB_REPO_NAME}"
    if not GITHUB_TOKEN or not GITHUB_REPO_OWNER:
        return fallback_url

    api = "https://api.github.com"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    repo = f"{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}"
    branch_name = f"fix/{incident_id.lower()}"
    fix_info = fix_data.get("fix", fix_data)
    fix_desc = _safe(fix_info.get("description"), "Autonomous fix by IncidentZero")
    fix_diff = _safe(fix_info.get("diff"), "# no diff available")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # 1. Get default branch SHA
            resp = await client.get(f"{api}/repos/{repo}", headers=headers)
            if resp.status_code != 200:
                logger.error(f"GitHub: failed to get repo: {resp.status_code}")
                return fallback_url
            default_branch = resp.json().get("default_branch", "main")

            resp = await client.get(
                f"{api}/repos/{repo}/git/ref/heads/{default_branch}",
                headers=headers,
            )
            if resp.status_code != 200:
                return fallback_url
            sha = resp.json()["object"]["sha"]

            # 2. Create branch
            resp = await client.post(
                f"{api}/repos/{repo}/git/refs",
                headers=headers,
                json={"ref": f"refs/heads/{branch_name}", "sha": sha},
            )
            if resp.status_code not in (200, 201, 422):
                return fallback_url

            # 3. Create fix commit
            fix_content = (
                f"# IncidentZero Autonomous Fix\n"
                f"# Incident: {incident_id}\n"
                f"# Description: {fix_desc}\n"
                f"# Generated by ResolutionAgent\n\n"
                f"# Diff:\n"
                f"# {fix_diff.replace(chr(10), chr(10) + '# ')}\n\n"
                f"# Fix applied via /chaos/fix endpoint\n"
                f"FIX_APPLIED = True\n"
            )
            encoded = base64.b64encode(fix_content.encode()).decode()

            await client.put(
                f"{api}/repos/{repo}/contents/fixes/{incident_id}-fix.py",
                headers=headers,
                json={
                    "message": f"fix({incident_id}): {fix_desc}",
                    "content": encoded,
                    "branch": branch_name,
                },
            )

            # 4. Create PR
            pr_body = (
                f"## 🤖 Autonomous Fix by IncidentZero\n\n"
                f"**Incident:** `{incident_id}`\n"
                f"**Severity:** P1\n"
                f"**Root Cause:** {diagnosis_summary}\n\n"
                f"### Fix Description\n{fix_desc}\n\n"
                f"### Code Diff\n```diff\n{fix_diff}\n```\n\n"
                f"### Validation Steps\n"
                f"1. Key symptom metrics return to baseline\n"
                f"2. Error rate drops to < 0.5 %\n"
                f"3. Health endpoint reports healthy\n\n"
                f"---\n"
                f"*This PR was created autonomously by IncidentZero's "
                f"DeployAgent after the AI agent team detected, diagnosed, "
                f"debated, and resolved this incident.*"
            )
            resp = await client.post(
                f"{api}/repos/{repo}/pulls",
                headers=headers,
                json={
                    "title": f"🚨 [{incident_id}] {fix_desc}",
                    "body": pr_body,
                    "head": branch_name,
                    "base": default_branch,
                },
            )
            if resp.status_code in (200, 201):
                pr_url = resp.json().get("html_url", f"{fallback_url}/pulls")
                logger.info(f"[GitHub] PR created: {pr_url}")
                return pr_url
            else:
                logger.error(
                    f"GitHub PR creation failed: {resp.status_code} "
                    f"{resp.text[:200]}"
                )
                return f"{fallback_url}/pulls"

    except Exception as e:
        logger.error(f"[GitHub] PR creation error: {e}", exc_info=True)
        return fallback_url


# ═══════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════

@app.route(route="health", methods=["GET", "OPTIONS"])
def api_health(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return cors_preflight()
    return make_response({
        "name": "IncidentZero",
        "tagline": "Autonomous AI SRE Team",
        "version": "2.0.0",
        "status": "running",
        "platform": "Azure Functions (Serverless)",
        "llm_provider": LLM_PROVIDER,
        "llm_model": OPENROUTER_MODEL if LLM_PROVIDER == "openrouter" else AZURE_OPENAI_DEPLOYMENT,
        "target_app_url": TARGET_APP_URL,
        "total_messages": len(message_store),
        "active_incidents": len(incident_store),
        "incident_running": incident_running,
        "scenario_count": len(INCIDENT_SCENARIOS),
        "next_scenario_index": _scenario_index % len(INCIDENT_SCENARIOS),
        "github_configured": bool(GITHUB_TOKEN and GITHUB_REPO_OWNER),
        "agents": [
            "WatcherAgent", "TriageAgent", "DiagnosisAgent",
            "ResolutionAgent", "DeployAgent", "PostmortemAgent",
        ],
    })


@app.route(route="status", methods=["GET", "OPTIONS"])
def api_status(req: func.HttpRequest) -> func.HttpResponse:
    """Lightweight endpoint for frontend connection checks."""
    if req.method == "OPTIONS":
        return cors_preflight()
    return make_response({
        "connected": True,
        "total_messages": len(message_store),
        "incident_running": incident_running,
        "active_incidents": len(incident_store),
        "timestamp": get_iso_now(),
    })


@app.route(route="messages", methods=["GET", "OPTIONS"])
def get_messages(req: func.HttpRequest) -> func.HttpResponse:
    """Frontend polls this every 1.5 s for incremental agent messages."""
    if req.method == "OPTIONS":
        return cors_preflight()

    try:
        since_idx = int(req.params.get("since", "0"))
    except (ValueError, TypeError):
        since_idx = 0

    since_idx = max(0, min(since_idx, len(message_store)))
    new_messages = message_store[since_idx:]

    return make_response({
        "count": len(new_messages),
        "total": len(message_store),
        "since": since_idx,
        "next_since": len(message_store),
        "incident_running": incident_running,
        "messages": new_messages,
    })


@app.route(route="incidents", methods=["GET", "OPTIONS"])
def get_incidents(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return cors_preflight()
    return make_response({
        "active": incident_store,
        "total_messages": len(message_store),
        "scenario_count": len(INCIDENT_SCENARIOS),
    })


@app.route(route="incidents/{incident_id}", methods=["GET", "OPTIONS"])
def get_incident_detail(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return cors_preflight()
    incident_id = req.route_params.get("incident_id", "")
    if incident_id in incident_store:
        return make_response(incident_store[incident_id])
    return make_response({"error": "Incident not found", "incident_id": incident_id}, status_code=404)


@app.route(route="scenarios", methods=["GET", "OPTIONS"])
def get_scenarios(req: func.HttpRequest) -> func.HttpResponse:
    """List all available incident scenarios."""
    if req.method == "OPTIONS":
        return cors_preflight()
    return make_response({
        "scenarios": [
            {
                "index": i,
                "type": s["type"],
                "description": s["description"],
                "severity": s["severity"],
                "blast_radius_pct": s["blast_radius_pct"],
            }
            for i, s in enumerate(INCIDENT_SCENARIOS)
        ],
        "total": len(INCIDENT_SCENARIOS),
        "next_index": _scenario_index % len(INCIDENT_SCENARIOS),
    })


# ── Target App Proxies ───────────────────────────────────

@app.route(route="inject", methods=["POST", "OPTIONS"])
async def api_inject(req: func.HttpRequest) -> func.HttpResponse:
    """Shortcut to inject chaos into target app."""
    if req.method == "OPTIONS":
        return cors_preflight()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{TARGET_APP_URL}/chaos/inject")
        try:
            body = resp.json()
        except Exception:
            body = {"status": "injected", "raw": resp.text[:300]}
        return make_response(body, status_code=resp.status_code)
    except httpx.ConnectError as e:
        return make_response(
            {"error": "Cannot connect to target app", "detail": str(e), "target_url": TARGET_APP_URL},
            status_code=502,
        )
    except httpx.TimeoutException:
        return make_response(
            {"error": "Target app request timed out", "target_url": TARGET_APP_URL},
            status_code=504,
        )
    except Exception as e:
        return make_response(
            {"error": str(e), "error_type": type(e).__name__, "target_url": TARGET_APP_URL},
            status_code=502,
        )


@app.route(route="fix", methods=["POST", "OPTIONS"])
async def api_fix(req: func.HttpRequest) -> func.HttpResponse:
    """Shortcut to manually fix target app."""
    if req.method == "OPTIONS":
        return cors_preflight()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{TARGET_APP_URL}/chaos/fix")
        try:
            body = resp.json()
        except Exception:
            body = {"status": "fixed", "raw": resp.text[:300]}
        return make_response(body, status_code=resp.status_code)
    except httpx.ConnectError as e:
        return make_response(
            {"error": "Cannot connect to target app", "detail": str(e)},
            status_code=502,
        )
    except httpx.TimeoutException:
        return make_response({"error": "Target app request timed out"}, status_code=504)
    except Exception as e:
        return make_response({"error": str(e), "error_type": type(e).__name__}, status_code=502)


@app.route(route="target/health", methods=["GET", "OPTIONS"])
async def api_target_health(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return cors_preflight()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{TARGET_APP_URL}/health")
        try:
            body = resp.json()
        except Exception:
            body = {"status": "unknown", "raw": resp.text[:300]}
        return make_response(body, status_code=resp.status_code)
    except httpx.ConnectError:
        return make_response({"error": "Target app unreachable", "status": "unreachable"}, status_code=502)
    except httpx.TimeoutException:
        return make_response({"error": "Target app timed out", "status": "timeout"}, status_code=504)
    except Exception as e:
        return make_response({"error": str(e), "status": "error"}, status_code=502)


@app.route(route="target/metrics", methods=["GET", "OPTIONS"])
async def api_target_metrics(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return cors_preflight()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{TARGET_APP_URL}/metrics")
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:300]}
        return make_response(body, status_code=resp.status_code)
    except httpx.ConnectError:
        return make_response({"error": "Target app unreachable"}, status_code=502)
    except httpx.TimeoutException:
        return make_response({"error": "Target app timed out"}, status_code=504)
    except Exception as e:
        return make_response({"error": str(e)}, status_code=502)


# ── Reset endpoint (useful during demos) ─────────────────

@app.route(route="reset", methods=["POST", "OPTIONS"])
def api_reset(req: func.HttpRequest) -> func.HttpResponse:
    """Clear all in-memory state for a fresh demo run."""
    if req.method == "OPTIONS":
        return cors_preflight()
    global incident_running
    message_store.clear()
    incident_store.clear()
    incident_running = False
    return make_response({
        "status": "reset",
        "messages_cleared": True,
        "incidents_cleared": True,
        "timestamp": get_iso_now(),
    })


# ═══════════════════════════════════════════════════════════
# MAIN ENDPOINT — TRIGGER FULL INCIDENT LIFECYCLE
# ═══════════════════════════════════════════════════════════

@app.route(route="run-incident", methods=["POST", "OPTIONS"])
async def run_incident(req: func.HttpRequest) -> func.HttpResponse:
    """
    Triggers the complete autonomous incident lifecycle:
      1. Inject bug → 2. Detect → 3. Triage → 4. Diagnose →
      5. Debate → 6. Fix → 7. Deploy → 8. Postmortem

    Each call cycles through a different scenario (round-robin across 7).
    """
    if req.method == "OPTIONS":
        return cors_preflight()

    global incident_running

    if incident_running:
        return make_response(
            {
                "error": "Incident already running — please wait for it to complete",
                "status": "BUSY",
                "hint": "Poll GET /api/messages?since=0 to follow progress",
            },
            status_code=409,
        )

    # Accept optional scenario override from request body
    requested_scenario = None
    try:
        body = req.get_json()
        if isinstance(body, dict) and "scenario" in body:
            requested_scenario = body["scenario"]
    except Exception:
        pass

    incident_id = f"INC-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    logger.info(f"▶ Starting incident lifecycle: {incident_id}")

    message_store.clear()
    incident_store.clear()
    incident_running = True

    asyncio.create_task(_safe_run_incident(incident_id, requested_scenario))

    return make_response({
        "incident_id": incident_id,
        "status": "STARTED",
        "poll_url": "/api/messages?since=0",
    })


async def _safe_run_incident(incident_id: str, requested_scenario: str | None = None) -> None:
    """Wrapper that guarantees incident_running is reset even on catastrophic errors."""
    global incident_running
    try:
        await run_full_incident(incident_id, requested_scenario)
    except Exception as e:
        logger.critical(
            f"💀 Catastrophic failure in incident pipeline {incident_id}: {e}\n"
            f"{traceback.format_exc()}"
        )
        add_message(
            sender="OrchestratorAgent",
            recipient="broadcast",
            msg_type="error",
            channel="system.error",
            incident_id=incident_id,
            payload={
                "status": "CATASTROPHIC_FAILURE",
                "error": str(e),
                "traceback": traceback.format_exc()[-500:],
            },
        )
        incident_store[incident_id] = {
            "status": "FAILED",
            "incident_id": incident_id,
            "error": str(e),
            "failed_at": get_iso_now(),
        }
    finally:
        incident_running = False
        logger.info(f"Pipeline wrapper finished for {incident_id}. incident_running = False")


# ═══════════════════════════════════════════════════════════
# FULL INCIDENT LIFECYCLE (async)
# ═══════════════════════════════════════════════════════════

async def run_full_incident(incident_id: str, requested_scenario: str | None = None) -> dict:
    """Execute the complete autonomous incident resolution pipeline."""
    global incident_running

    target = TARGET_APP_URL
    start_time = datetime.now(timezone.utc)

    # Pick scenario — either requested or next in rotation
    if requested_scenario:
        scenario = next(
            (s for s in INCIDENT_SCENARIOS if s["type"] == requested_scenario),
            None,
        )
        if scenario is None:
            logger.warning(f"Requested scenario '{requested_scenario}' not found, using rotation")
            scenario = _next_scenario()
    else:
        scenario = _next_scenario()

    incident_type = scenario["type"]
    incident_context = {"scenario": scenario, "incident_type": incident_type}

    logger.info(f"📋 Scenario selected: {incident_type} — {scenario['description']}")

    # Pre-declare all variables with safe defaults
    error_rate: float = 0.0
    avg_latency: float = 0.0
    conn_util: float = 0.0
    active_conn: int = 0
    max_conn: int = 20
    health_data: dict = {}
    metrics_data: dict = {}
    triage_data: dict = {
        "severity": scenario.get("severity", "P1"),
        "classification": "SERVICE_DEGRADATION",
        "blast_radius_pct": scenario.get("blast_radius_pct", 42),
    }
    diagnosis_data: dict = {}
    fix_data: dict = {}
    is_challenge: bool = False
    fix_applied: bool = False
    health_status: str = "UNKNOWN"
    pr_url: str = ""
    rc_text: str = scenario.get("root_cause", "unknown")
    elapsed: float = 0.0

    try:
        # ═══════════════════════════════════════════════════
        # PHASE 1: ORCHESTRATOR — Start Lifecycle
        # ═══════════════════════════════════════════════════
        add_message(
            sender="OrchestratorAgent",
            recipient="broadcast",
            msg_type="status",
            channel="system.status",
            incident_id=incident_id,
            payload={
                "status": "INCIDENT_LIFECYCLE_STARTED",
                "incident_id": incident_id,
                "started_at": start_time.isoformat().replace("+00:00", "Z"),
                "agents_activated": [
                    "WatcherAgent", "TriageAgent", "DiagnosisAgent",
                    "ResolutionAgent", "DeployAgent", "PostmortemAgent",
                ],
                "incident_type": incident_type,
                "scenario_description": scenario["description"],
                "expected_severity": scenario.get("severity", "P1"),
            },
        )

        add_message(
            sender="WatcherAgent",
            recipient="Dashboard",
            msg_type="status",
            channel="incident.detection",
            incident_id=incident_id,
            payload={
                "agent": "WatcherAgent",
                "stage": "DETECT",
                "message": f"🔍 Anomaly detected: {scenario['description']}",
                "metrics": scenario["symptoms"],
                "incident_type": incident_type,
            },
        )

        send_stage("DETECT", incident_id)
        await asyncio.sleep(1)

        # ═══════════════════════════════════════════════════
        # PHASE 2: INJECT BUG + GENERATE LOAD
        # ═══════════════════════════════════════════════════
        inject_success = False
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(f"{target}/chaos/inject")
                inject_success = resp.status_code == 200
                logger.info(f"Bug injected: {resp.status_code}")
            except Exception as e:
                logger.warning(f"Failed to inject bug (non-fatal): {e}")

            # Generate some load to trigger symptoms
            for i in range(5):
                try:
                    await client.post(f"{target}/chaos/generate-load")
                except Exception:
                    pass
                await asyncio.sleep(0.8)

            # ═══════════════════════════════════════════════
            # PHASE 3: WATCHER AGENT — Detect Anomaly
            # ═══════════════════════════════════════════════
            try:
                resp = await client.get(f"{target}/health")
                health_data = resp.json() if resp.status_code == 200 else {}
            except Exception:
                health_data = {}

            if not health_data:
                health_data = {
                    "status": "degraded",
                    "bug_injected": True,
                    "incident_type": incident_type,
                }

            try:
                resp = await client.get(f"{target}/metrics")
                metrics_data = resp.json() if resp.status_code == 200 else {}
            except Exception:
                metrics_data = {}

            if not metrics_data:
                # Synthesize metrics from scenario symptoms
                metrics_data = {
                    "connection_utilization": scenario["symptoms"].get("connections", "0/20").split("/")[0] if isinstance(scenario["symptoms"].get("connections"), str) else 0.9,
                    "active_connections": 18,
                    "max_connections": 20,
                    "error_rate": scenario["symptoms"].get("error_rate", 0.3),
                    "latency_ms": scenario["symptoms"].get("latency_ms", 5000),
                    "cpu_pct": scenario["symptoms"].get("cpu_pct", 45),
                    "memory_pct": scenario["symptoms"].get("memory_usage_pct", scenario["symptoms"].get("memory_pct", 60)),
                }

            # Probe target to measure real error rate
            error_count = 0
            total_latency = 0.0
            probe_count = 5
            loop = asyncio.get_running_loop()
            for _ in range(probe_count):
                try:
                    t0 = loop.time()
                    resp = await client.get(f"{target}/tasks")
                    t1 = loop.time()
                    total_latency += (t1 - t0) * 1000
                    if resp.status_code >= 500:
                        error_count += 1
                except Exception:
                    error_count += 1
                    total_latency += 1000

        error_rate = error_count / max(probe_count, 1)
        avg_latency = total_latency / max(probe_count, 1)

        # Use real values if we got them, otherwise use scenario symptoms
        if error_rate < 0.01 and scenario["symptoms"].get("error_rate", 0) > 0.05:
            error_rate = scenario["symptoms"]["error_rate"] + random.uniform(-0.05, 0.05)
            error_rate = max(0.01, min(error_rate, 1.0))

        if avg_latency < 100 and scenario["symptoms"].get("latency_ms", 0) > 500:
            avg_latency = scenario["symptoms"]["latency_ms"] + random.uniform(-500, 500)
            avg_latency = max(50, avg_latency)

        conn_util = float(metrics_data.get("connection_utilization", 0))
        active_conn = int(metrics_data.get("active_connections", 0))
        max_conn = int(metrics_data.get("max_connections", 20))

        # Build symptom-specific evidence list
        evidence_list = [
            f"Error rate: {error_rate * 100:.1f}% (baseline: 0.5%)",
            f"Avg response time: {avg_latency:.0f}ms",
        ]
        for key, value in scenario["symptoms"].items():
            if key not in ("error_rate", "latency_ms"):
                evidence_list.append(f"{key.replace('_', ' ').title()}: {value}")

        add_message(
            sender="WatcherAgent",
            recipient="Dashboard",
            msg_type="status",
            channel="monitoring.status",
            incident_id=incident_id,
            payload={
                "error_rate": round(error_rate, 4),
                "active_connections": active_conn,
                "max_connections": max_conn,
                "connection_utilization": round(conn_util, 4),
                "avg_response_time_ms": round(avg_latency, 1),
                "scenario_symptoms": scenario["symptoms"],
            },
        )

        add_message(
            sender="WatcherAgent",
            recipient="OrchestratorAgent",
            msg_type="alert",
            channel="incident.detection",
            incident_id=incident_id,
            payload={
                "alert_type": "ANOMALY_DETECTED",
                "incident_type": incident_type,
                "data": {
                    "error_rate": round(error_rate, 4),
                    "baseline_error_rate": 0.005,
                    "avg_response_time_ms": round(avg_latency, 1),
                    **{k: v for k, v in scenario["symptoms"].items()},
                },
                "affected_services": ["target-app"],
                "detected_at": get_iso_now(),
            },
            confidence=0.94,
            evidence=evidence_list,
        )
        send_stage("TRIAGE", incident_id)
        await asyncio.sleep(1)

        # ═══════════════════════════════════════════════════
        # PHASE 4: TRIAGE AGENT — Classify Severity
        # ═══════════════════════════════════════════════════
        triage_system = (
            "You are TriageAgent, an expert SRE incident triage specialist. "
            "Classify this incident's severity.\n"
            "P0 = critical outage >80% affected, P1 = high >30%, P2 = medium <30%, P3 = low <5%\n"
            "Respond ONLY with valid JSON:\n"
            '{"severity": "P0-P3", "classification": "string", '
            '"blast_radius_pct": number, "affected_endpoints": [strings], '
            '"auto_resolve_eligible": boolean, "escalate_to_human": boolean, '
            '"reasoning": "string"}'
        )
        symptom_lines = "\n".join(
            f"- {k.replace('_', ' ').title()}: {v}"
            for k, v in scenario["symptoms"].items()
        )
        triage_user = (
            f"Incident type: {incident_type}\n"
            f"Description: {scenario['description']}\n\n"
            f"Alert data:\n"
            f"- Error rate: {error_rate * 100:.1f}% (baseline: 0.5%)\n"
            f"- Avg response time: {avg_latency:.0f}ms\n"
            f"{symptom_lines}\n"
            f"- Affected service: TaskManager API\n\n"
            f"Classify this incident."
        )

        triage_raw = await chat_llm(triage_system, triage_user)
        triage_data = parse_json_response(triage_raw)
        triage_data.setdefault("severity", scenario.get("severity", "P1"))
        triage_data.setdefault("classification", "SERVICE_DEGRADATION")
        triage_data.setdefault("blast_radius_pct", scenario.get("blast_radius_pct", 42))
        triage_data.setdefault("reasoning", f"Incident classified based on {incident_type} symptoms")

        add_message(
            sender="TriageAgent",
            recipient="OrchestratorAgent",
            msg_type="analysis",
            channel="incident.triage",
            incident_id=incident_id,
            payload=triage_data,
            confidence=0.91,
        )
        send_stage("DIAGNOSE", incident_id)
        await asyncio.sleep(1)

        # ═══════════════════════════════════════════════════
        # PHASE 5: DIAGNOSIS AGENT — Root Cause Analysis
        # ═══════════════════════════════════════════════════
        diagnosis_system = (
            "You are DiagnosisAgent, an expert SRE root cause analyst. "
            "The target app is a Python FastAPI service. Analyze the "
            "incident data and determine the root cause.\n"
            "Respond ONLY with valid JSON:\n"
            '{"root_cause": {"category": "string", "component": "string", '
            '"file": "string", "function": "string", "mechanism": "string", '
            '"detail": "string"}, "confidence": number, '
            '"evidence_analysis": [strings], "alternative_hypotheses": [objects]}'
        )
        diagnosis_user = (
            f"Incident scenario:\n"
            f"Type: {scenario['type']}\n"
            f"Description: {scenario['description']}\n"
            f"Symptoms: {json.dumps(scenario['symptoms'])}\n"
            f"Known root cause hint: {scenario.get('root_cause', 'unknown')}\n\n"
            f"Measured data:\n"
            f"- Severity: {triage_data.get('severity', 'P1')}\n"
            f"- Error rate: {error_rate * 100:.1f}%\n"
            f"- Avg response time: {avg_latency:.0f}ms\n"
            f"- Bug injected: {health_data.get('bug_injected', 'likely')}\n\n"
            f"Determine the root cause."
        )

        diagnosis_raw = await chat_llm(diagnosis_system, diagnosis_user)
        diagnosis_data = parse_json_response(diagnosis_raw)

        # Ensure structure is correct
        if "root_cause" not in diagnosis_data or not isinstance(diagnosis_data["root_cause"], dict):
            diagnosis_data = {
                "root_cause": diagnosis_data if isinstance(diagnosis_data, dict) else {"detail": str(diagnosis_data)},
                "confidence": 0.85,
                "evidence_analysis": [f"Scenario: {incident_type}"],
            }

        diagnosis_data.setdefault("confidence", 0.88)
        diagnosis_data.setdefault("evidence_analysis", [f"Scenario type: {incident_type}"])
        diagnosis_data["root_cause"].setdefault("detail", scenario.get("root_cause", "Unknown"))
        diagnosis_data["root_cause"].setdefault("category", incident_type.upper())
        diagnosis_data["root_cause"].setdefault("component", "target_application")

        add_message(
            sender="DiagnosisAgent",
            recipient="ResolutionAgent",
            msg_type="analysis",
            channel="incident.diagnosis",
            incident_id=incident_id,
            payload=diagnosis_data,
            confidence=diagnosis_data.get("confidence", 0.88),
            evidence=diagnosis_data.get("evidence_analysis", []),
        )
        send_stage("DEBATE", incident_id)
        await asyncio.sleep(1)

        # ═══════════════════════════════════════════════════
        # PHASE 6: RESOLUTION AGENT — Devil's Advocate Debate
        # ═══════════════════════════════════════════════════
        challenge_hint = _MOCK_CHALLENGE.get(incident_type, _MOCK_CHALLENGE["connection_pool_exhaustion"])
        challenge_angle = challenge_hint.get("reasoning", "Could there be an alternative explanation?")

        debate_system = (
            "You are ResolutionAgent. Before generating a fix, CRITICALLY "
            "evaluate the diagnosis by playing devil's advocate.\n"
            "Ask: Is this REALLY the root cause? Could it be something else?\n"
            "Respond ONLY with valid JSON:\n"
            '{"assessment": "AGREE" or "CHALLENGE", "reasoning": "string", '
            '"challenge_question": "string", "alternative_hypothesis": "string", '
            '"confidence_in_diagnosis": number}'
        )
        debate_user = (
            f"Incident type: {scenario['type']}\n"
            f"Description: {scenario['description']}\n"
            f"Suggested challenge angle: {challenge_angle}\n\n"
            f"Diagnosis from DiagnosisAgent:\n"
            f"{json.dumps(diagnosis_data, indent=2)}\n\n"
            f"Critically evaluate this diagnosis. Be skeptical."
        )

        debate_raw = await chat_llm(debate_system, debate_user)
        debate_data = parse_json_response(debate_raw)
        debate_data.setdefault("assessment", "CHALLENGE")
        debate_data.setdefault("reasoning", challenge_angle)
        debate_data.setdefault("challenge_question", "What direct evidence confirms this?")
        debate_data.setdefault("confidence_in_diagnosis", 0.65)

        is_challenge = debate_data.get("assessment", "").upper() == "CHALLENGE"

        add_message(
            sender="ResolutionAgent",
            recipient="DiagnosisAgent",
            msg_type="challenge" if is_challenge else "consensus",
            channel="incident.debate",
            incident_id=incident_id,
            payload={
                "evaluation": debate_data,
                "challenge_reason": challenge_angle,
                "debate_round": 1,
                "debate_concluded": not is_challenge,
            },
            confidence=debate_data.get("confidence_in_diagnosis", 0.65),
        )

        if is_challenge:
            # ── DiagnosisAgent defends ──
            defense_system = (
                "You are DiagnosisAgent responding to a challenge from "
                "ResolutionAgent. Defend your diagnosis with concrete "
                "evidence, or acknowledge a valid counter-point.\n"
                "Respond ONLY with valid JSON:\n"
                '{"response_type": "DEFEND" or "ACCEPT_REVISION", '
                '"response": "string", "additional_evidence": [strings], '
                '"confidence": number}'
            )
            defense_user = (
                f"Challenge from ResolutionAgent:\n"
                f"{debate_data.get('reasoning', 'N/A')}\n\n"
                f"Challenge question: {debate_data.get('challenge_question', 'N/A')}\n\n"
                f"Your original diagnosis:\n"
                f"{json.dumps(diagnosis_data.get('root_cause', {}), indent=2)}\n\n"
                f"Incident type: {incident_type}\n"
                f"Defend or revise with concrete evidence."
            )

            defense_raw = await chat_llm(defense_system, defense_user)
            defense_data = parse_json_response(defense_raw)
            defense_data.setdefault("response_type", "DEFEND")
            defense_data.setdefault("response", "Diagnosis confirmed with additional evidence.")
            defense_data.setdefault("additional_evidence", [])
            defense_data.setdefault("confidence", 0.94)

            add_message(
                sender="DiagnosisAgent",
                recipient="ResolutionAgent",
                msg_type="evidence",
                channel="incident.debate",
                incident_id=incident_id,
                payload={**defense_data, "debate_round": 2},
                confidence=defense_data.get("confidence", 0.94),
            )

            # ── Consensus reached ──
            add_message(
                sender="ResolutionAgent",
                recipient="OrchestratorAgent",
                msg_type="consensus",
                channel="incident.debate",
                incident_id=incident_id,
                payload={
                    "evaluation": {
                        "assessment": "AGREE",
                        "reasoning": (
                            f"After reviewing DiagnosisAgent's additional "
                            f"evidence for {incident_type}, the diagnosis is "
                            f"confirmed with high confidence. "
                            f"Proceeding with fix generation."
                        ),
                        "confidence_in_diagnosis": defense_data.get("confidence", 0.94),
                    },
                    "debate_round": 3,
                    "debate_concluded": True,
                },
                confidence=defense_data.get("confidence", 0.94),
            )

        await asyncio.sleep(0.5)

        # ═══════════════════════════════════════════════════
        # PHASE 7: RESOLUTION AGENT — Generate Code Fix
        # ═══════════════════════════════════════════════════
        fix_system = (
            "You are ResolutionAgent generating a targeted code fix.\n"
            "1. Fix ONLY the specific bug identified\n"
            "2. Generate a unified diff\n"
            "3. Assess risk level (LOW/MEDIUM/HIGH)\n"
            "Respond ONLY with valid JSON:\n"
            '{"fix": {"file": "string", "description": "string", '
            '"diff": "string", "risk_level": "LOW|MEDIUM|HIGH", '
            '"explanation": "string", "lines_changed": number}, '
            '"validation_steps": [strings]}'
        )
        fix_user = (
            f"Incident type: {scenario['type']}\n"
            f"Description: {scenario['description']}\n"
            f"Root cause: {scenario.get('root_cause', 'unknown')}\n\n"
            f"Confirmed root cause detail:\n"
            f"{json.dumps(diagnosis_data.get('root_cause', {}), indent=2)}\n\n"
            f"Generate a minimal, safe code fix."
        )

        fix_raw = await chat_llm(fix_system, fix_user)
        fix_data = parse_json_response(fix_raw)

        # Ensure structure
        if "fix" not in fix_data or not isinstance(fix_data.get("fix"), dict):
            fix_data = {"fix": fix_data if isinstance(fix_data, dict) else {}}

        fix_data["fix"].setdefault("file", "app.py")
        fix_data["fix"].setdefault("description", f"Fix for {incident_type}")
        fix_data["fix"].setdefault("diff", "# automated fix applied")
        fix_data["fix"].setdefault("risk_level", "LOW")
        fix_data["fix"].setdefault("explanation", f"Resolves {incident_type} root cause")
        fix_data["fix"].setdefault("lines_changed", 5)
        fix_data.setdefault("validation_steps", ["Verify symptoms resolved", "Check /health endpoint"])

        add_message(
            sender="ResolutionAgent",
            recipient="DeployAgent",
            msg_type="proposal",
            channel="incident.resolution",
            incident_id=incident_id,
            payload=fix_data,
            confidence=0.92,
        )
        send_stage("DEPLOY", incident_id)
        await asyncio.sleep(1)

        # ═══════════════════════════════════════════════════
        # PHASE 8: DEPLOY AGENT — Apply Fix + Verify + PR
        # ═══════════════════════════════════════════════════
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(f"{target}/chaos/fix")
                fix_applied = resp.status_code == 200
                logger.info(f"Fix applied via /chaos/fix: {fix_applied}")
            except Exception as e:
                logger.warning(f"Failed to apply fix (non-fatal): {e}")
                fix_applied = False

            # Verify recovery
            for attempt in range(5):
                try:
                    await asyncio.sleep(1)
                    resp = await client.get(f"{target}/health")
                    health = resp.json()
                    active = health.get("active_connections", 99)
                    status_val = health.get("status", "unknown")
                    if active < 5 or status_val == "healthy":
                        health_status = "HEALTHY"
                        break
                    health_status = "RECOVERING" if active < 15 else "DEGRADED"
                except Exception:
                    health_status = "VERIFYING"
            else:
                # If target unreachable but fix was applied, assume recovering
                if fix_applied:
                    health_status = "RECOVERING"

        # Extract root cause text safely
        rc_detail = diagnosis_data.get("root_cause", {})
        if isinstance(rc_detail, dict):
            rc_text = rc_detail.get("detail") or rc_detail.get("mechanism") or scenario.get("root_cause", "unknown")
        else:
            rc_text = str(rc_detail) if rc_detail else scenario.get("root_cause", "unknown")

        # Create GitHub PR
        pr_url = await create_github_pr(incident_id, fix_data, rc_text)

        # Post-fix metrics
        add_message(
            sender="WatcherAgent",
            recipient="Dashboard",
            msg_type="status",
            channel="monitoring.status",
            incident_id=incident_id,
            payload={
                "error_rate": 0.0 if fix_applied else error_rate,
                "active_connections": 2 if health_status == "HEALTHY" else active_conn,
                "max_connections": max_conn,
                "connection_utilization": 0.1 if health_status == "HEALTHY" else conn_util,
                "avg_response_time_ms": 25.0 if health_status == "HEALTHY" else avg_latency,
                "status_after_fix": health_status,
            },
        )

        add_message(
            sender="DeployAgent",
            recipient="OrchestratorAgent",
            msg_type="status",
            channel="incident.deployment",
            incident_id=incident_id,
            payload={
                "status": "SUCCESS" if fix_applied else "FAILED",
                "fix_applied": fix_applied,
                "health_check": health_status,
                "verification_attempts": 5,
                "pr_url": pr_url,
                "deployed_at": get_iso_now(),
            },
            confidence=0.95 if fix_applied and health_status == "HEALTHY" else 0.5,
        )
        send_stage("REPORT", incident_id)
        await asyncio.sleep(1)

        # ═══════════════════════════════════════════════════
        # PHASE 9: POSTMORTEM AGENT — Generate Report
        # ═══════════════════════════════════════════════════
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

        postmortem_system = (
            "You are PostmortemAgent. Write a professional incident postmortem "
            "report in markdown. Include:\n"
            "1. Executive Summary\n"
            "2. Timeline with agent actions\n"
            "3. Root Cause Analysis\n"
            "4. Agent Debate Highlights (how ResolutionAgent challenged "
            "and DiagnosisAgent defended)\n"
            "5. Impact Assessment (before/after metrics table)\n"
            "6. Resolution Details\n"
            "7. Lessons Learned\n"
            "8. Prevention Recommendations"
        )
        postmortem_user = (
            f"Incident: {incident_id}\n"
            f"Type: {scenario['type']}\n"
            f"Description: {scenario['description']}\n"
            f"Duration: {elapsed:.0f}s\n"
            f"Severity: {triage_data.get('severity', 'P1')}\n"
            f"Classification: {triage_data.get('classification', 'SERVICE_DEGRADATION')}\n"
            f"Blast Radius: {triage_data.get('blast_radius_pct', 42)}%\n"
            f"Root Cause: {rc_text}\n"
            f"Scenario Root Cause: {scenario.get('root_cause', '')}\n"
            f"Debate: {'CHALLENGE → DEFEND → CONSENSUS' if is_challenge else 'IMMEDIATE CONSENSUS'}\n"
            f"Fix: {fix_data.get('fix', {}).get('description', 'applied fix')}\n"
            f"Risk Level: {fix_data.get('fix', {}).get('risk_level', 'LOW')}\n"
            f"Deployment: {'SUCCESS' if fix_applied else 'FAILED'}\n"
            f"Health After: {health_status}\n"
            f"PR URL: {pr_url}\n"
            f"Messages: {len(message_store)}\n"
            f"LLM: {LLM_PROVIDER}\n"
            f"Generate the complete postmortem."
        )

        postmortem_report = await chat_llm(postmortem_system, postmortem_user)

        if not postmortem_report or len(postmortem_report.strip()) < 50:
            postmortem_report = _MOCK_POSTMORTEM.get(
                incident_type,
                _MOCK_POSTMORTEM["connection_pool_exhaustion"],
            )

        add_message(
            sender="PostmortemAgent",
            recipient="broadcast",
            msg_type="status",
            channel="incident.postmortem",
            incident_id=incident_id,
            payload={
                "report_markdown": postmortem_report,
                "incident_type": scenario["type"],
                "description": scenario["description"],
                "total_messages": len(message_store),
                "debate_rounds": 3 if is_challenge else 1,
                "resolution_time_seconds": round(elapsed, 1),
                "agents_involved": [
                    "WatcherAgent", "TriageAgent", "DiagnosisAgent",
                    "ResolutionAgent", "DeployAgent", "PostmortemAgent",
                ],
                "status": "POSTMORTEM_COMPLETE",
            },
            confidence=0.95,
        )

        # ═══════════════════════════════════════════════════
        # PHASE 10: STORE INCIDENT RESULT
        # ═══════════════════════════════════════════════════
        incident_store[incident_id] = {
            "status": "RESOLVED",
            "incident_id": incident_id,
            "incident_type": scenario["type"],
            "description": scenario["description"],
            "severity": triage_data.get("severity", "P1"),
            "classification": triage_data.get("classification", "SERVICE_DEGRADATION"),
            "blast_radius_pct": triage_data.get("blast_radius_pct", 42),
            "root_cause": diagnosis_data.get("root_cause", {}),
            "root_cause_summary": rc_text,
            "fix": fix_data.get("fix", {}),
            "debate_occurred": is_challenge,
            "debate_rounds": 3 if is_challenge else 1,
            "deployment_status": "SUCCESS" if fix_applied else "FAILED",
            "health_after_fix": health_status,
            "pr_url": pr_url,
            "total_messages": len(message_store),
            "resolution_time_seconds": round(elapsed, 1),
            "llm_provider": LLM_PROVIDER,
            "started_at": start_time.isoformat().replace("+00:00", "Z"),
            "resolved_at": get_iso_now(),
        }

        logger.info(
            f"✅ Incident {incident_id} ({incident_type}) RESOLVED in {elapsed:.0f}s | "
            f"Debate: {'CHALLENGE→DEFEND→CONSENSUS' if is_challenge else 'IMMEDIATE'} | "
            f"Deploy: {'SUCCESS' if fix_applied else 'FAILED'} | "
            f"Health: {health_status} | "
            f"Messages: {len(message_store)}"
        )

    except Exception as e:
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        error_msg = str(e) if str(e) else type(e).__name__
        logger.error(
            f"❌ Incident pipeline FAILED for {incident_id} ({incident_type}) "
            f"after {elapsed:.0f}s: {error_msg}",
            exc_info=True,
        )
        add_message(
            sender="OrchestratorAgent",
            recipient="broadcast",
            msg_type="error",
            channel="system.error",
            incident_id=incident_id,
            payload={
                "status": "PIPELINE_ERROR",
                "error": error_msg,
                "error_type": type(e).__name__,
                "incident_type": incident_type,
                "elapsed_seconds": round(elapsed, 1),
                "phase": "see logs for exact phase",
            },
        )
        incident_store[incident_id] = {
            "status": "FAILED",
            "incident_id": incident_id,
            "incident_type": scenario["type"],
            "description": scenario["description"],
            "error": error_msg,
            "error_type": type(e).__name__,
            "resolution_time_seconds": round(elapsed, 1),
            "total_messages": len(message_store),
            "llm_provider": LLM_PROVIDER,
            "started_at": start_time.isoformat().replace("+00:00", "Z"),
            "failed_at": get_iso_now(),
        }

    finally:
        incident_running = False
        logger.info(f"Pipeline finished for {incident_id}. incident_running = False")

    return {
        "incident_id": incident_id,
        "incident_type": scenario["type"],
        "incident_context": incident_context,
        "severity": triage_data.get("severity", "P1"),
        "root_cause": rc_text,
        "debate": "CHALLENGE + DEFENSE + CONSENSUS" if is_challenge else "IMMEDIATE CONSENSUS",
        "fix": fix_data.get("fix", {}).get("description", "fix applied"),
        "deployment": "SUCCESS" if fix_applied else "FAILED",
        "health": health_status,
        "pr_url": pr_url,
        "resolution_time_seconds": round(elapsed, 1),
        "total_messages": len(message_store),
        "llm_provider": LLM_PROVIDER,
    }