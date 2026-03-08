"""
WatcherAgent — Continuously monitors the target application.
Detects anomalies and triggers incident alerts.
"""

import asyncio
import httpx
from datetime import datetime
import logging

from .base_agent import BaseAgent
from mcp.protocol import MessageType
from config import config

logger = logging.getLogger("WatcherAgent")


class WatcherAgent(BaseAgent):
    def __init__(self):
        super().__init__("WatcherAgent")
        self.baseline = {
            "error_rate": 0.01,
            "connection_utilization": 0.3,
            "response_time_ms": 100,
        }
        self.is_monitoring = False
        self.alert_sent = False

    async def start_monitoring(self):
        """Main monitoring loop"""
        self.is_monitoring = True
        self.alert_sent = False
        logger.info("👁️ WatcherAgent: Monitoring started")

        while self.is_monitoring:
            try:
                await self._check_health()
            except Exception as e:
                logger.error(f"Health check error: {e}")

            await asyncio.sleep(config.POLLING_INTERVAL_SECONDS)

    def stop_monitoring(self):
        self.is_monitoring = False
        self.alert_sent = False
        logger.info("WatcherAgent: Monitoring stopped")

    def reset(self):
        """Reset alert state so new incidents can be detected"""
        self.alert_sent = False

    async def _check_health(self):
        """Poll the target app and check for anomalies"""
        async with httpx.AsyncClient(timeout=10) as client:
            # ── Fetch health data ───────────────────────
            try:
                health_resp = await client.get(f"{config.TARGET_APP_URL}/health")
                health = health_resp.json()
            except Exception:
                if not self.alert_sent:
                    await self._trigger_alert("APP_UNREACHABLE", {
                        "error_rate": 1.0,
                        "connection_utilization": 0,
                    })
                return

            # ── Fetch metrics ───────────────────────────
            try:
                metrics_resp = await client.get(f"{config.TARGET_APP_URL}/metrics")
                metrics = metrics_resp.json()
            except Exception:
                metrics = {}

            # ── Run synthetic requests to detect errors ─
            error_count = 0
            total_checks = 5
            response_times = []

            for _ in range(total_checks):
                try:
                    start = datetime.utcnow()
                    resp = await client.get(f"{config.TARGET_APP_URL}/tasks")
                    elapsed_ms = (datetime.utcnow() - start).total_seconds() * 1000
                    response_times.append(elapsed_ms)
                    if resp.status_code >= 500:
                        error_count += 1
                except Exception:
                    error_count += 1
                    response_times.append(99999)

            error_rate = error_count / total_checks
            avg_response_ms = sum(response_times) / len(response_times) if response_times else 0
            conn_util = metrics.get("connection_utilization", 0)
            active_conn = metrics.get("active_connections", 0)
            max_conn = metrics.get("max_connections", 20)

            # ── Send status update (always) ─────────────
            await self.send_message(
                recipient="broadcast",
                message_type=MessageType.STATUS,
                channel="monitoring.status",
                payload={
                    "error_rate": round(error_rate, 3),
                    "connection_utilization": round(conn_util, 3),
                    "active_connections": active_conn,
                    "max_connections": max_conn,
                    "avg_response_time_ms": round(avg_response_ms, 1),
                    "bug_injected": health.get("bug_injected", False),
                    "status": "healthy" if error_rate < 0.1 and conn_util < 0.8 else "degraded",
                },
                confidence=0.99,
            )

            # ── Detect anomaly ──────────────────────────
            is_anomaly = (
                error_rate > self.baseline["error_rate"] * 5
                or conn_util > 0.75
                or avg_response_ms > self.baseline["response_time_ms"] * 10
            )

            if is_anomaly and not self.alert_sent:
                await self._trigger_alert("ANOMALY_DETECTED", {
                    "error_rate": error_rate,
                    "baseline_error_rate": self.baseline["error_rate"],
                    "connection_utilization": conn_util,
                    "active_connections": active_conn,
                    "max_connections": max_conn,
                    "avg_response_time_ms": round(avg_response_ms, 1),
                })

    async def _trigger_alert(self, alert_type: str, data: dict):
        """Send an incident alert to the OrchestratorAgent"""
        self.alert_sent = True
        incident_id = f"INC-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
        logger.warning(f"🚨 ALERT: {alert_type} — Incident {incident_id}")

        await self.send_message(
            recipient="OrchestratorAgent",
            message_type=MessageType.ALERT,
            channel="incident.detection",
            incident_id=incident_id,
            payload={
                "alert_type": alert_type,
                "data": data,
                "affected_services": ["target-app"],
                "detected_at": datetime.utcnow().isoformat() + "Z",
            },
            confidence=0.94,
            evidence=[
                f"Error rate: {data.get('error_rate', 0)*100:.1f}%",
                f"Connection utilization: {data.get('connection_utilization', 0)*100:.1f}%",
                f"Active connections: {data.get('active_connections', 'N/A')}/{data.get('max_connections', 'N/A')}",
            ],
        )

    async def process(self, message):
        pass