"""
IncidentZero Target Application — Azure Functions
TaskManager API with 7 injectable failure scenarios.

Endpoints:
  GET  /health                  Health check with full system status
  GET  /metrics                 Detailed system metrics
  GET  /tasks                   List tasks (affected by active bugs)
  POST /tasks                   Create task (affected by active bugs)
  GET  /tasks/{id}              Get single task
  DELETE /tasks/{id}            Delete task
  POST /chaos/inject            Inject a failure scenario
  POST /chaos/fix               Fix active failure + reset state
  GET  /chaos/status            Current chaos state
  POST /chaos/generate-load     Generate simulated load
  GET  /chaos/scenarios         List all injectable scenarios
"""

import azure.functions as func
import json
import random
import logging
import time
import math
from datetime import datetime, timezone
from typing import Any

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("target-app")


# ═══════════════════════════════════════════════════════════
# CORS HEADERS
# ═══════════════════════════════════════════════════════════

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, DELETE, PUT, PATCH, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With",
    "Access-Control-Max-Age": "86400",
}


def make_response(data: dict, status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(data, default=str),
        status_code=status_code,
        mimetype="application/json",
        headers=CORS_HEADERS,
    )


def cors_preflight() -> func.HttpResponse:
    return func.HttpResponse(status_code=204, headers=CORS_HEADERS)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ═══════════════════════════════════════════════════════════
# SIMULATED INFRASTRUCTURE COMPONENTS
# ═══════════════════════════════════════════════════════════

class ConnectionPool:
    """Simulated database connection pool."""

    def __init__(self, max_size: int = 20):
        self.max_size = max_size
        self.active = 0
        self.total_acquired = 0
        self.total_released = 0
        self.total_leaked = 0
        self.total_timeouts = 0

    def acquire(self):
        if self.active >= self.max_size:
            self.total_timeouts += 1
            raise ConnectionPoolExhaustedError(
                f"ConnectionPool exhausted: {self.active}/{self.max_size} "
                f"(leaked: {self.total_leaked})"
            )
        self.active += 1
        self.total_acquired += 1
        return {"connection_id": self.total_acquired, "acquired_at": time.time()}

    def release(self, conn):
        if self.active > 0:
            self.active -= 1
            self.total_released += 1

    def leak(self):
        """Record a leaked connection (not released)."""
        self.total_leaked += 1

    def reset(self):
        self.active = 0
        self.total_leaked = 0
        self.total_timeouts = 0

    @property
    def utilization(self) -> float:
        return round(self.active / self.max_size, 4) if self.max_size > 0 else 0.0

    @property
    def status(self) -> str:
        pct = self.utilization
        if pct >= 0.95:
            return "exhausted"
        if pct >= 0.8:
            return "critical"
        if pct >= 0.6:
            return "warning"
        return "healthy"

    def to_dict(self) -> dict:
        return {
            "active_connections": self.active,
            "max_connections": self.max_size,
            "utilization": self.utilization,
            "utilization_pct": round(self.utilization * 100, 1),
            "status": self.status,
            "total_acquired": self.total_acquired,
            "total_released": self.total_released,
            "total_leaked": self.total_leaked,
            "total_timeouts": self.total_timeouts,
        }


class ConnectionPoolExhaustedError(Exception):
    pass


class MemorySimulator:
    """Simulated application memory / cache."""

    def __init__(self):
        self.cache_entries: int = 0
        self.heap_mb: float = 380.0
        self.baseline_heap_mb: float = 380.0
        self.max_heap_mb: float = 2048.0
        self.gc_pause_ms: float = 5.0
        self.leak_active: bool = False

    def add_to_cache(self, count: int = 1):
        """Simulate adding entries to unbounded cache."""
        self.cache_entries += count
        if self.leak_active:
            # Heap grows with every cached item
            growth = count * random.uniform(0.3, 0.8)
            self.heap_mb = min(self.heap_mb + growth, self.max_heap_mb)
            # GC pauses increase as heap grows
            heap_ratio = self.heap_mb / self.max_heap_mb
            self.gc_pause_ms = 5.0 + (heap_ratio ** 2) * 900

    def reset(self):
        self.cache_entries = 0
        self.heap_mb = self.baseline_heap_mb
        self.gc_pause_ms = 5.0
        self.leak_active = False

    @property
    def usage_pct(self) -> float:
        return round((self.heap_mb / self.max_heap_mb) * 100, 1)

    @property
    def status(self) -> str:
        if self.usage_pct >= 90:
            return "critical"
        if self.usage_pct >= 70:
            return "warning"
        return "healthy"

    def to_dict(self) -> dict:
        return {
            "heap_mb": round(self.heap_mb, 1),
            "max_heap_mb": self.max_heap_mb,
            "usage_pct": self.usage_pct,
            "gc_pause_ms": round(self.gc_pause_ms, 1),
            "cache_entries": self.cache_entries,
            "status": self.status,
        }


class DatabaseSimulator:
    """Simulated database query engine."""

    def __init__(self):
        self.has_index: bool = True
        self.total_rows: int = 2_400_000
        self.query_count: int = 0
        self.slow_query_active: bool = False
        self.avg_query_ms: float = 4.0
        self.db_cpu_pct: float = 12.0

    def execute_query(self) -> dict:
        self.query_count += 1
        if self.slow_query_active and not self.has_index:
            # Full table scan
            query_time = random.uniform(4500, 5800)
            rows_scanned = self.total_rows
            self.db_cpu_pct = min(99, self.db_cpu_pct + random.uniform(0.5, 2.0))
            self.avg_query_ms = query_time
            return {
                "query_time_ms": round(query_time, 1),
                "rows_scanned": rows_scanned,
                "plan": "Seq Scan",
                "status": "slow",
            }
        else:
            query_time = random.uniform(2, 8)
            self.avg_query_ms = query_time
            self.db_cpu_pct = max(8, min(20, self.db_cpu_pct + random.uniform(-1, 1)))
            return {
                "query_time_ms": round(query_time, 1),
                "rows_scanned": random.randint(10, 100),
                "plan": "Index Scan",
                "status": "normal",
            }

    def reset(self):
        self.has_index = True
        self.slow_query_active = False
        self.avg_query_ms = 4.0
        self.db_cpu_pct = 12.0
        self.query_count = 0

    def to_dict(self) -> dict:
        return {
            "avg_query_ms": round(self.avg_query_ms, 1),
            "db_cpu_pct": round(self.db_cpu_pct, 1),
            "total_rows": self.total_rows,
            "has_index": self.has_index,
            "slow_query_active": self.slow_query_active,
            "total_queries": self.query_count,
        }


class ExternalAPISimulator:
    """Simulated upstream payment gateway."""

    def __init__(self):
        self.failure_active: bool = False
        self.failure_rate: float = 0.0
        self.timeout_count: int = 0
        self.success_count: int = 0
        self.retry_queue_depth: int = 0
        self.circuit_open: bool = False

    def call(self) -> dict:
        if self.failure_active:
            if random.random() < self.failure_rate:
                self.timeout_count += 1
                self.retry_queue_depth = min(
                    self.retry_queue_depth + random.randint(3, 8),
                    5000,
                )
                return {
                    "status_code": 503,
                    "error": "Service Unavailable",
                    "latency_ms": random.randint(5000, 30000),
                    "retryable": True,
                }
            else:
                self.success_count += 1
                self.retry_queue_depth = max(0, self.retry_queue_depth - 1)
                return {
                    "status_code": 200,
                    "latency_ms": random.randint(80, 300),
                }
        else:
            self.success_count += 1
            return {"status_code": 200, "latency_ms": random.randint(50, 150)}

    def reset(self):
        self.failure_active = False
        self.failure_rate = 0.0
        self.timeout_count = 0
        self.retry_queue_depth = 0
        self.circuit_open = False

    @property
    def status(self) -> str:
        if self.circuit_open:
            return "circuit_open"
        if self.failure_active:
            return "degraded"
        return "healthy"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "failure_active": self.failure_active,
            "failure_rate": self.failure_rate,
            "timeout_count": self.timeout_count,
            "success_count": self.success_count,
            "retry_queue_depth": self.retry_queue_depth,
            "circuit_open": self.circuit_open,
        }


class CacheSimulator:
    """Simulated Redis cache layer."""

    def __init__(self):
        self.available: bool = True
        self.hit_rate: float = 0.94
        self.total_hits: int = 0
        self.total_misses: int = 0
        self.db_read_fallback_count: int = 0

    def get(self, key: str) -> dict:
        if not self.available:
            self.total_misses += 1
            self.db_read_fallback_count += 1
            self.hit_rate = 0.0
            return {"hit": False, "source": "db_fallback", "latency_ms": random.randint(200, 800)}
        if random.random() < self.hit_rate:
            self.total_hits += 1
            return {"hit": True, "source": "cache", "latency_ms": random.randint(1, 5)}
        self.total_misses += 1
        return {"hit": False, "source": "db", "latency_ms": random.randint(20, 80)}

    def reset(self):
        self.available = True
        self.hit_rate = 0.94
        self.total_misses = 0
        self.db_read_fallback_count = 0

    @property
    def status(self) -> str:
        if not self.available:
            return "down"
        return "healthy"

    def to_dict(self) -> dict:
        total = self.total_hits + self.total_misses
        return {
            "status": self.status,
            "available": self.available,
            "hit_rate": round(self.total_hits / total, 3) if total > 0 else self.hit_rate,
            "total_hits": self.total_hits,
            "total_misses": self.total_misses,
            "db_fallback_count": self.db_read_fallback_count,
        }


class ThreadSimulator:
    """Simulated thread pool / worker threads."""

    def __init__(self, max_threads: int = 200):
        self.max_threads = max_threads
        self.active_threads: int = 12
        self.blocked_threads: int = 0
        self.deadlock_active: bool = False
        self.throughput_rps: float = 1200.0
        self.cpu_pct: float = 22.0

    def process(self) -> dict:
        if self.deadlock_active:
            # Threads get stuck
            if self.blocked_threads < 187:
                self.blocked_threads = min(
                    self.blocked_threads + random.randint(5, 15),
                    187,
                )
            self.active_threads = self.max_threads
            self.cpu_pct = min(99.5, 90 + random.uniform(0, 10))
            self.throughput_rps = max(1, 1200 - (self.blocked_threads * 6.3))
            if random.random() < 0.65:
                raise ThreadDeadlockError(
                    f"Thread deadlock: {self.blocked_threads}/{self.max_threads} "
                    f"threads blocked on mutex"
                )
            return {
                "status": "degraded",
                "latency_ms": random.randint(8000, 15000),
                "thread_state": "partially_blocked",
            }
        else:
            self.active_threads = random.randint(8, 25)
            self.blocked_threads = 0
            self.cpu_pct = random.uniform(15, 35)
            self.throughput_rps = random.uniform(1000, 1400)
            return {
                "status": "normal",
                "latency_ms": random.randint(5, 30),
                "thread_state": "healthy",
            }

    def reset(self):
        self.deadlock_active = False
        self.active_threads = 12
        self.blocked_threads = 0
        self.cpu_pct = 22.0
        self.throughput_rps = 1200.0

    @property
    def status(self) -> str:
        if self.blocked_threads > 100:
            return "deadlocked"
        if self.blocked_threads > 20:
            return "contention"
        return "healthy"

    def to_dict(self) -> dict:
        return {
            "active_threads": self.active_threads,
            "max_threads": self.max_threads,
            "blocked_threads": self.blocked_threads,
            "deadlock_active": self.deadlock_active,
            "cpu_pct": round(self.cpu_pct, 1),
            "throughput_rps": round(self.throughput_rps, 1),
            "status": self.status,
        }


class ThreadDeadlockError(Exception):
    pass


class DiskIOSimulator:
    """Simulated disk I/O subsystem."""

    def __init__(self):
        self.saturation_active: bool = False
        self.disk_util_pct: float = 15.0
        self.iowait_pct: float = 2.0
        self.log_queue_depth: int = 0
        self.write_throughput_mbps: float = 120.0
        self.log_level: str = "INFO"

    def write_log(self) -> dict:
        if self.saturation_active:
            self.log_queue_depth = min(
                self.log_queue_depth + random.randint(50, 200),
                80000,
            )
            self.disk_util_pct = min(100, 95 + random.uniform(0, 5))
            self.iowait_pct = min(95, 70 + random.uniform(0, 15))
            self.write_throughput_mbps = max(0.1, random.uniform(0.2, 0.8))
            # Simulated blocking delay
            if random.random() < 0.31:
                raise DiskIOSaturationError(
                    f"Disk I/O saturated: util={self.disk_util_pct:.0f}%, "
                    f"iowait={self.iowait_pct:.0f}%, "
                    f"queue={self.log_queue_depth}"
                )
            return {
                "status": "degraded",
                "block_ms": random.randint(500, 3000),
            }
        else:
            self.disk_util_pct = random.uniform(10, 25)
            self.iowait_pct = random.uniform(1, 5)
            self.log_queue_depth = random.randint(0, 50)
            self.write_throughput_mbps = random.uniform(80, 150)
            return {"status": "normal", "block_ms": 0}

    def reset(self):
        self.saturation_active = False
        self.disk_util_pct = 15.0
        self.iowait_pct = 2.0
        self.log_queue_depth = 0
        self.write_throughput_mbps = 120.0
        self.log_level = "INFO"

    @property
    def status(self) -> str:
        if self.disk_util_pct >= 95:
            return "saturated"
        if self.disk_util_pct >= 70:
            return "warning"
        return "healthy"

    def to_dict(self) -> dict:
        return {
            "disk_util_pct": round(self.disk_util_pct, 1),
            "iowait_pct": round(self.iowait_pct, 1),
            "log_queue_depth": self.log_queue_depth,
            "write_throughput_mbps": round(self.write_throughput_mbps, 2),
            "log_level": self.log_level,
            "saturation_active": self.saturation_active,
            "status": self.status,
        }


class DiskIOSaturationError(Exception):
    pass


# ═══════════════════════════════════════════════════════════
# GLOBAL STATE
# ═══════════════════════════════════════════════════════════

tasks_db: dict = {}
task_counter: int = 0

pool = ConnectionPool(max_size=20)
memory = MemorySimulator()
database = DatabaseSimulator()
external_api = ExternalAPISimulator()
cache = CacheSimulator()
threads = ThreadSimulator()
disk_io = DiskIOSimulator()

# Active chaos state
active_scenario: str = ""          # "", or one of the 7 scenario type strings
injection_time: str = ""           # ISO timestamp of when chaos was injected
request_count_since_inject: int = 0

SCENARIO_TYPES = [
    "connection_pool_exhaustion",
    "memory_leak",
    "slow_database_queries",
    "external_api_failure",
    "cache_failure",
    "cpu_spike_thread_deadlock",
    "disk_io_saturation",
]

SCENARIO_DESCRIPTIONS = {
    "connection_pool_exhaustion": {
        "title": "Connection Pool Exhaustion",
        "description": "70% of database connections will not be released, causing pool exhaustion",
        "affected_components": ["connection_pool"],
        "expected_symptoms": ["rising error rate", "connection timeouts", "500 errors"],
    },
    "memory_leak": {
        "title": "Memory Leak (Unbounded Cache)",
        "description": "Cache grows without eviction, consuming heap memory until OOM",
        "affected_components": ["memory", "cache"],
        "expected_symptoms": ["rising heap usage", "increasing GC pauses", "eventual OOM"],
    },
    "slow_database_queries": {
        "title": "Slow Database Queries",
        "description": "Missing index causes full table scans on 2.4M row table",
        "affected_components": ["database"],
        "expected_symptoms": ["5s+ query times", "high DB CPU", "elevated latency"],
    },
    "external_api_failure": {
        "title": "External API Failure",
        "description": "Upstream payment gateway returns intermittent 503 errors",
        "affected_components": ["external_api"],
        "expected_symptoms": ["503 errors", "timeout spikes", "retry queue growth"],
    },
    "cache_failure": {
        "title": "Cache Failure (Redis Down)",
        "description": "Redis primary node goes offline, causing thundering herd on database",
        "affected_components": ["cache", "database"],
        "expected_symptoms": ["0% cache hit rate", "DB CPU spike", "elevated latency"],
    },
    "cpu_spike_thread_deadlock": {
        "title": "CPU Spike / Thread Deadlock",
        "description": "Lock ordering inversion causes thread deadlock and CPU spike",
        "affected_components": ["threads"],
        "expected_symptoms": ["99% CPU", "blocked threads", "near-zero throughput"],
    },
    "disk_io_saturation": {
        "title": "Disk I/O Saturation",
        "description": "Synchronous debug-level log flushing saturates disk I/O",
        "affected_components": ["disk_io"],
        "expected_symptoms": ["100% disk util", "high iowait", "request timeouts"],
    },
}


# ═══════════════════════════════════════════════════════════
# CHAOS ENGINE — inject / fix / status
# ═══════════════════════════════════════════════════════════

def _inject_scenario(scenario_type: str) -> dict:
    """Activate a specific failure scenario. Returns status dict."""
    global active_scenario, injection_time, request_count_since_inject

    if scenario_type not in SCENARIO_TYPES:
        return {"error": f"Unknown scenario: {scenario_type}", "valid_types": SCENARIO_TYPES}

    # Reset everything first so only one scenario is active
    _fix_all()

    active_scenario = scenario_type
    injection_time = utcnow_iso()
    request_count_since_inject = 0

    if scenario_type == "connection_pool_exhaustion":
        pass  # Leak logic is in the request handlers

    elif scenario_type == "memory_leak":
        memory.leak_active = True
        # Pre-fill some cache to show immediate impact
        memory.add_to_cache(500_000)
        memory.heap_mb = random.uniform(1600, 1800)
        memory.gc_pause_ms = random.uniform(700, 900)

    elif scenario_type == "slow_database_queries":
        database.slow_query_active = True
        database.has_index = False
        database.db_cpu_pct = random.uniform(85, 97)

    elif scenario_type == "external_api_failure":
        external_api.failure_active = True
        external_api.failure_rate = random.uniform(0.33, 0.42)
        external_api.retry_queue_depth = random.randint(800, 1500)

    elif scenario_type == "cache_failure":
        cache.available = False
        cache.hit_rate = 0.0
        database.db_cpu_pct = random.uniform(88, 97)

    elif scenario_type == "cpu_spike_thread_deadlock":
        threads.deadlock_active = True
        threads.blocked_threads = random.randint(150, 187)
        threads.cpu_pct = random.uniform(96, 99.5)
        threads.throughput_rps = random.uniform(1, 8)
        threads.active_threads = 200

    elif scenario_type == "disk_io_saturation":
        disk_io.saturation_active = True
        disk_io.log_level = "DEBUG"
        disk_io.disk_util_pct = 100.0
        disk_io.iowait_pct = random.uniform(72, 82)
        disk_io.log_queue_depth = random.randint(30000, 50000)
        disk_io.write_throughput_mbps = random.uniform(0.2, 0.6)

    desc = SCENARIO_DESCRIPTIONS[scenario_type]
    logger.warning(f"🔴 CHAOS INJECTED: {desc['title']} — {desc['description']}")

    return {
        "status": "bug_injected",
        "scenario": scenario_type,
        "title": desc["title"],
        "description": desc["description"],
        "affected_components": desc["affected_components"],
        "expected_symptoms": desc["expected_symptoms"],
        "injected_at": injection_time,
    }


def _fix_all() -> dict:
    """Deactivate all failure scenarios and reset all components."""
    global active_scenario, injection_time, request_count_since_inject

    previous = active_scenario
    active_scenario = ""
    injection_time = ""
    request_count_since_inject = 0

    pool.reset()
    memory.reset()
    database.reset()
    external_api.reset()
    cache.reset()
    threads.reset()
    disk_io.reset()

    logger.info(f"🟢 ALL BUGS FIXED. Previous scenario: {previous or 'none'}")

    return {
        "status": "bug_fixed",
        "previous_scenario": previous or "none",
        "all_components_reset": True,
        "active_connections": pool.active,
        "fixed_at": utcnow_iso(),
    }


def _simulate_request_side_effects():
    """Apply side effects to each request based on active scenario."""
    global request_count_since_inject
    request_count_since_inject += 1

    if active_scenario == "memory_leak":
        memory.add_to_cache(random.randint(100, 500))

    elif active_scenario == "slow_database_queries":
        database.execute_query()

    elif active_scenario == "cache_failure":
        cache.get("request_data")
        # DB load increases with each request during cache outage
        database.db_cpu_pct = min(99, database.db_cpu_pct + random.uniform(0, 0.5))

    elif active_scenario == "disk_io_saturation":
        disk_io.write_log()


def _get_request_latency_ms() -> float:
    """Calculate simulated request latency based on active scenario."""
    base = random.uniform(5, 25)

    if active_scenario == "connection_pool_exhaustion":
        if pool.utilization > 0.8:
            return base + random.uniform(500, 2000)
        return base + random.uniform(50, 200)

    elif active_scenario == "memory_leak":
        gc_factor = memory.gc_pause_ms / 50
        return base + random.uniform(100, 500) * gc_factor

    elif active_scenario == "slow_database_queries":
        return base + database.avg_query_ms

    elif active_scenario == "external_api_failure":
        return base + random.uniform(200, 7000)

    elif active_scenario == "cache_failure":
        return base + random.uniform(300, 1200)

    elif active_scenario == "cpu_spike_thread_deadlock":
        return base + random.uniform(5000, 15000)

    elif active_scenario == "disk_io_saturation":
        return base + random.uniform(1000, 5000)

    return base


def _should_error() -> bool:
    """Determine if the current request should return an error."""
    if not active_scenario:
        return False

    if active_scenario == "connection_pool_exhaustion":
        return pool.active >= pool.max_size

    elif active_scenario == "memory_leak":
        return memory.usage_pct > 95 and random.random() < 0.4

    elif active_scenario == "slow_database_queries":
        return random.random() < 0.08  # Occasional timeouts

    elif active_scenario == "external_api_failure":
        return random.random() < external_api.failure_rate

    elif active_scenario == "cache_failure":
        return random.random() < 0.29  # DB overload errors

    elif active_scenario == "cpu_spike_thread_deadlock":
        return random.random() < 0.65  # Most requests fail

    elif active_scenario == "disk_io_saturation":
        return random.random() < 0.31  # I/O blocking

    return False


def _get_error_response() -> tuple[dict, int]:
    """Return an appropriate error for the active scenario."""
    if active_scenario == "connection_pool_exhaustion":
        return {
            "error": f"ConnectionPool exhausted: {pool.active}/{pool.max_size}",
            "error_type": "CONNECTION_POOL_EXHAUSTION",
            "active_connections": pool.active,
            "leaked_connections": pool.total_leaked,
        }, 503

    elif active_scenario == "memory_leak":
        return {
            "error": "OutOfMemoryError: heap space exhausted",
            "error_type": "MEMORY_EXHAUSTION",
            "heap_mb": round(memory.heap_mb, 1),
            "max_heap_mb": memory.max_heap_mb,
            "cache_entries": memory.cache_entries,
        }, 503

    elif active_scenario == "slow_database_queries":
        return {
            "error": "Query execution timeout after 30s",
            "error_type": "QUERY_TIMEOUT",
            "avg_query_ms": round(database.avg_query_ms, 1),
            "db_cpu_pct": round(database.db_cpu_pct, 1),
        }, 504

    elif active_scenario == "external_api_failure":
        return {
            "error": "Upstream payment service unavailable (503)",
            "error_type": "UPSTREAM_FAILURE",
            "upstream_status": 503,
            "retry_queue_depth": external_api.retry_queue_depth,
            "timeout_count": external_api.timeout_count,
        }, 502

    elif active_scenario == "cache_failure":
        return {
            "error": "Cache unavailable — database overloaded with fallback reads",
            "error_type": "CACHE_OUTAGE",
            "cache_status": "down",
            "db_cpu_pct": round(database.db_cpu_pct, 1),
            "fallback_count": cache.db_read_fallback_count,
        }, 503

    elif active_scenario == "cpu_spike_thread_deadlock":
        return {
            "error": f"Thread deadlock: {threads.blocked_threads}/{threads.max_threads} blocked",
            "error_type": "THREAD_DEADLOCK",
            "blocked_threads": threads.blocked_threads,
            "cpu_pct": round(threads.cpu_pct, 1),
            "throughput_rps": round(threads.throughput_rps, 1),
        }, 503

    elif active_scenario == "disk_io_saturation":
        return {
            "error": f"Request blocked on disk I/O (util={disk_io.disk_util_pct:.0f}%)",
            "error_type": "DISK_IO_SATURATION",
            "disk_util_pct": round(disk_io.disk_util_pct, 1),
            "iowait_pct": round(disk_io.iowait_pct, 1),
            "log_queue_depth": disk_io.log_queue_depth,
        }, 503

    return {"error": "Unknown server error", "error_type": "UNKNOWN"}, 500


# ═══════════════════════════════════════════════════════════
# CONNECTION POOL LEAK LOGIC (scenario-specific)
# ═══════════════════════════════════════════════════════════

def _acquire_connection():
    """Acquire a connection. Raises if pool is exhausted."""
    return pool.acquire()


def _release_connection(conn):
    """Release connection — may leak if connection_pool_exhaustion is active."""
    if conn is None:
        return

    if active_scenario == "connection_pool_exhaustion":
        if random.random() < 0.3:
            pool.release(conn)
        else:
            pool.leak()
            logger.warning(
                f"🔴 CONNECTION LEAKED! Active: {pool.active}/{pool.max_size} "
                f"(total leaked: {pool.total_leaked})"
            )
    else:
        pool.release(conn)


# ═══════════════════════════════════════════════════════════
# HEALTH CHECK
# ═══════════════════════════════════════════════════════════

@app.route(route="health", methods=["GET", "OPTIONS"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return cors_preflight()

    # Determine overall status
    issues = []

    if active_scenario == "connection_pool_exhaustion" and pool.utilization > 0.7:
        issues.append(f"connection_pool: {pool.status}")
    if active_scenario == "memory_leak" and memory.usage_pct > 70:
        issues.append(f"memory: {memory.status}")
    if active_scenario == "slow_database_queries" and database.db_cpu_pct > 70:
        issues.append(f"database: slow queries (CPU {database.db_cpu_pct:.0f}%)")
    if active_scenario == "external_api_failure" and external_api.failure_active:
        issues.append(f"external_api: {external_api.status}")
    if active_scenario == "cache_failure" and not cache.available:
        issues.append(f"cache: {cache.status}")
    if active_scenario == "cpu_spike_thread_deadlock" and threads.blocked_threads > 50:
        issues.append(f"threads: {threads.status}")
    if active_scenario == "disk_io_saturation" and disk_io.disk_util_pct > 80:
        issues.append(f"disk_io: {disk_io.status}")

    if not issues:
        overall_status = "healthy"
    elif len(issues) == 1 and "warning" in str(issues):
        overall_status = "degraded"
    else:
        overall_status = "degraded" if active_scenario else "healthy"

    return make_response({
        "status": overall_status,
        "timestamp": utcnow_iso(),
        "active_scenario": active_scenario or None,
        "bug_injected": bool(active_scenario),
        "issues": issues,
        # Key metrics (always present for easy frontend consumption)
        "active_connections": pool.active,
        "max_connections": pool.max_size,
        "connection_utilization": pool.utilization,
        "memory_usage_pct": memory.usage_pct,
        "db_cpu_pct": round(database.db_cpu_pct, 1),
        "cache_status": cache.status,
        "thread_status": threads.status,
        "disk_status": disk_io.status,
        "cpu_pct": round(threads.cpu_pct, 1),
        "request_count_since_inject": request_count_since_inject,
    })


# ═══════════════════════════════════════════════════════════
# METRICS
# ═══════════════════════════════════════════════════════════

@app.route(route="metrics", methods=["GET", "OPTIONS"])
def metrics(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return cors_preflight()

    return make_response({
        "timestamp": utcnow_iso(),
        "active_scenario": active_scenario or None,
        "bug_injected": bool(active_scenario),
        "injection_time": injection_time or None,
        "request_count_since_inject": request_count_since_inject,
        "total_tasks": len(tasks_db),
        # Component metrics
        "connection_pool": pool.to_dict(),
        "memory": memory.to_dict(),
        "database": database.to_dict(),
        "external_api": external_api.to_dict(),
        "cache": cache.to_dict(),
        "threads": threads.to_dict(),
        "disk_io": disk_io.to_dict(),
        # Flattened top-level for backward compatibility
        "active_connections": pool.active,
        "max_connections": pool.max_size,
        "connection_utilization": pool.utilization,
        "pool_status": pool.status,
    })


# ═══════════════════════════════════════════════════════════
# TASK ENDPOINTS
# ═══════════════════════════════════════════════════════════

@app.route(route="tasks", methods=["GET", "OPTIONS"])
def list_tasks(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return cors_preflight()

    conn = None
    try:
        # Apply side effects for active scenario
        _simulate_request_side_effects()

        # Check for scenario-specific errors
        if _should_error():
            error_body, error_code = _get_error_response()
            return make_response(error_body, status_code=error_code)

        # Acquire connection (may fail for pool exhaustion)
        conn = _acquire_connection()

        # Simulate query delay
        latency = _get_request_latency_ms()

        result = list(tasks_db.values())
        return make_response({
            "tasks": result,
            "count": len(result),
            "latency_ms": round(latency, 1),
            "scenario_active": active_scenario or None,
        })

    except ConnectionPoolExhaustedError as e:
        logger.error(f"Pool exhausted in list_tasks: {e}")
        return make_response({
            "error": str(e),
            "error_type": "CONNECTION_POOL_EXHAUSTION",
            "active_connections": pool.active,
            "max_connections": pool.max_size,
        }, status_code=503)

    except ThreadDeadlockError as e:
        logger.error(f"Thread deadlock in list_tasks: {e}")
        return make_response({
            "error": str(e),
            "error_type": "THREAD_DEADLOCK",
            "blocked_threads": threads.blocked_threads,
        }, status_code=503)

    except DiskIOSaturationError as e:
        logger.error(f"Disk I/O saturated in list_tasks: {e}")
        return make_response({
            "error": str(e),
            "error_type": "DISK_IO_SATURATION",
            "disk_util_pct": round(disk_io.disk_util_pct, 1),
        }, status_code=503)

    except Exception as e:
        logger.error(f"Unexpected error in list_tasks: {e}")
        return make_response({
            "error": str(e) or "Internal server error",
            "error_type": type(e).__name__,
        }, status_code=500)

    finally:
        _release_connection(conn)


@app.route(route="tasks", methods=["POST"])
def create_task(req: func.HttpRequest) -> func.HttpResponse:
    global task_counter

    conn = None
    try:
        _simulate_request_side_effects()

        if _should_error():
            error_body, error_code = _get_error_response()
            return make_response(error_body, status_code=error_code)

        conn = _acquire_connection()

        try:
            body = req.get_json()
        except (ValueError, Exception):
            body = {}

        task_counter += 1
        new_task = {
            "id": task_counter,
            "title": body.get("title", f"Task-{task_counter}"),
            "description": body.get("description", ""),
            "status": "pending",
            "priority": body.get("priority", "medium"),
            "created_at": utcnow_iso(),
        }
        tasks_db[task_counter] = new_task

        latency = _get_request_latency_ms()

        return make_response({
            **new_task,
            "latency_ms": round(latency, 1),
        }, status_code=201)

    except ConnectionPoolExhaustedError as e:
        return make_response({
            "error": str(e),
            "error_type": "CONNECTION_POOL_EXHAUSTION",
        }, status_code=503)

    except (ThreadDeadlockError, DiskIOSaturationError) as e:
        return make_response({
            "error": str(e),
            "error_type": type(e).__name__,
        }, status_code=503)

    except Exception as e:
        logger.error(f"Error creating task: {e}")
        return make_response({
            "error": str(e) or "Internal server error",
            "error_type": type(e).__name__,
        }, status_code=500)

    finally:
        _release_connection(conn)


@app.route(route="tasks/{task_id}", methods=["GET", "OPTIONS"])
def get_task(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return cors_preflight()

    task_id_str = req.route_params.get("task_id", "")
    try:
        task_id = int(task_id_str)
    except (ValueError, TypeError):
        return make_response({"error": f"Invalid task ID: {task_id_str}"}, status_code=400)

    conn = None
    try:
        _simulate_request_side_effects()

        if _should_error():
            error_body, error_code = _get_error_response()
            return make_response(error_body, status_code=error_code)

        conn = _acquire_connection()

        if task_id in tasks_db:
            return make_response(tasks_db[task_id])
        return make_response({"error": f"Task {task_id} not found"}, status_code=404)

    except (ConnectionPoolExhaustedError, ThreadDeadlockError, DiskIOSaturationError) as e:
        return make_response({
            "error": str(e),
            "error_type": type(e).__name__,
        }, status_code=503)

    except Exception as e:
        return make_response({"error": str(e) or "Internal server error"}, status_code=500)

    finally:
        _release_connection(conn)


@app.route(route="tasks/{task_id}", methods=["DELETE"])
def delete_task(req: func.HttpRequest) -> func.HttpResponse:
    task_id_str = req.route_params.get("task_id", "")
    try:
        task_id = int(task_id_str)
    except (ValueError, TypeError):
        return make_response({"error": f"Invalid task ID: {task_id_str}"}, status_code=400)

    conn = None
    try:
        conn = _acquire_connection()

        if task_id in tasks_db:
            deleted = tasks_db.pop(task_id)
            return make_response({"deleted": deleted, "remaining": len(tasks_db)})
        return make_response({"error": f"Task {task_id} not found"}, status_code=404)

    except ConnectionPoolExhaustedError as e:
        return make_response({"error": str(e)}, status_code=503)

    except Exception as e:
        return make_response({"error": str(e) or "Internal server error"}, status_code=500)

    finally:
        _release_connection(conn)


# ═══════════════════════════════════════════════════════════
# CHAOS ENDPOINTS
# ═══════════════════════════════════════════════════════════

@app.route(route="chaos/inject", methods=["POST", "OPTIONS"])
def chaos_inject(req: func.HttpRequest) -> func.HttpResponse:
    """
    Inject a failure scenario.

    Body (optional):
      {"scenario": "memory_leak"}

    If no scenario is specified, picks one at random.
    """
    if req.method == "OPTIONS":
        return cors_preflight()

    # Parse optional scenario from body
    scenario_type = None
    try:
        body = req.get_json()
        if isinstance(body, dict):
            scenario_type = body.get("scenario")
    except Exception:
        pass

    # Also check query parameter
    if not scenario_type:
        scenario_type = req.params.get("scenario")

    # Default: pick a random scenario
    if not scenario_type:
        scenario_type = random.choice(SCENARIO_TYPES)

    result = _inject_scenario(scenario_type)

    if "error" in result:
        return make_response(result, status_code=400)

    return make_response(result)


@app.route(route="chaos/fix", methods=["POST", "OPTIONS"])
def chaos_fix(req: func.HttpRequest) -> func.HttpResponse:
    """Fix all active failure scenarios and reset all components."""
    if req.method == "OPTIONS":
        return cors_preflight()

    result = _fix_all()
    return make_response(result)


@app.route(route="chaos/status", methods=["GET", "OPTIONS"])
def chaos_status(req: func.HttpRequest) -> func.HttpResponse:
    """Return current chaos state and all component metrics."""
    if req.method == "OPTIONS":
        return cors_preflight()

    return make_response({
        "active_scenario": active_scenario or None,
        "bug_injected": bool(active_scenario),
        "injection_time": injection_time or None,
        "request_count_since_inject": request_count_since_inject,
        "scenario_info": SCENARIO_DESCRIPTIONS.get(active_scenario) if active_scenario else None,
        "components": {
            "connection_pool": pool.to_dict(),
            "memory": memory.to_dict(),
            "database": database.to_dict(),
            "external_api": external_api.to_dict(),
            "cache": cache.to_dict(),
            "threads": threads.to_dict(),
            "disk_io": disk_io.to_dict(),
        },
    })


@app.route(route="chaos/scenarios", methods=["GET", "OPTIONS"])
def chaos_scenarios(req: func.HttpRequest) -> func.HttpResponse:
    """List all available injectable failure scenarios."""
    if req.method == "OPTIONS":
        return cors_preflight()

    scenarios_list = []
    for stype in SCENARIO_TYPES:
        desc = SCENARIO_DESCRIPTIONS[stype]
        scenarios_list.append({
            "type": stype,
            "title": desc["title"],
            "description": desc["description"],
            "affected_components": desc["affected_components"],
            "expected_symptoms": desc["expected_symptoms"],
            "is_active": active_scenario == stype,
        })

    return make_response({
        "scenarios": scenarios_list,
        "total": len(scenarios_list),
        "active_scenario": active_scenario or None,
    })


@app.route(route="chaos/generate-load", methods=["POST", "OPTIONS"])
def chaos_generate_load(req: func.HttpRequest) -> func.HttpResponse:
    """
    Generate simulated load against the current scenario.
    Produces 10 internal requests and returns aggregated results.
    """
    if req.method == "OPTIONS":
        return cors_preflight()

    results = []
    error_count = 0
    total_latency = 0.0

    for i in range(10):
        conn = None
        try:
            _simulate_request_side_effects()

            if _should_error():
                error_body, error_code = _get_error_response()
                error_count += 1
                latency = _get_request_latency_ms()
                total_latency += latency
                results.append({
                    "request": i,
                    "status": "error",
                    "status_code": error_code,
                    "error_type": error_body.get("error_type", "UNKNOWN"),
                    "latency_ms": round(latency, 1),
                })
                continue

            conn = _acquire_connection()
            latency = _get_request_latency_ms()
            total_latency += latency
            results.append({
                "request": i,
                "status": "success",
                "status_code": 200,
                "latency_ms": round(latency, 1),
            })

        except (ConnectionPoolExhaustedError, ThreadDeadlockError, DiskIOSaturationError) as e:
            error_count += 1
            latency = _get_request_latency_ms()
            total_latency += latency
            results.append({
                "request": i,
                "status": "error",
                "error_type": type(e).__name__,
                "detail": str(e)[:200],
                "latency_ms": round(latency, 1),
            })

        except Exception as e:
            error_count += 1
            results.append({
                "request": i,
                "status": "error",
                "error_type": type(e).__name__,
                "detail": str(e)[:200],
                "latency_ms": 1000,
            })
            total_latency += 1000

        finally:
            _release_connection(conn)

    avg_latency = total_latency / 10 if results else 0

    return make_response({
        "load_test_results": results,
        "summary": {
            "total_requests": 10,
            "success_count": 10 - error_count,
            "error_count": error_count,
            "error_rate": round(error_count / 10, 2),
            "avg_latency_ms": round(avg_latency, 1),
        },
        "scenario": active_scenario or "none",
        "pool_status": pool.to_dict(),
    })


# ═══════════════════════════════════════════════════════════
# SEED DATA (optional — gives the task list some initial content)
# ═══════════════════════════════════════════════════════════

def _seed_tasks():
    """Pre-populate a few tasks so the API isn't empty on first call."""
    global task_counter
    sample_tasks = [
        ("Deploy v2.4.1 to production", "Rolling update for payment service", "in_progress", "high"),
        ("Investigate latency spike", "p99 jumped from 50ms to 800ms on /checkout", "pending", "critical"),
        ("Rotate database credentials", "Quarterly rotation per security policy", "pending", "medium"),
        ("Update monitoring dashboards", "Add connection pool metrics panel", "completed", "low"),
        ("Review PR #847", "Memory optimization for cache layer", "in_progress", "medium"),
    ]
    for title, desc, status, priority in sample_tasks:
        task_counter += 1
        tasks_db[task_counter] = {
            "id": task_counter,
            "title": title,
            "description": desc,
            "status": status,
            "priority": priority,
            "created_at": utcnow_iso(),
        }


_seed_tasks()
logger.info(
    f"🚀 Target app initialized | "
    f"{len(tasks_db)} seed tasks | "
    f"{len(SCENARIO_TYPES)} injectable scenarios | "
    f"Pool: {pool.max_size} connections"
)