"""
WatcherAgent — Continuously monitors the target application.
Detects anomalies in error rates, connection pool, and response times.
Triggers incident alerts when thresholds are breached.
"""

import asyncio
import httpx
from datetime import datetime
from typing import Optional
import logging

from .base_agent import BaseAgent
from mcp.protocol import MCPMessage, MessageType
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
        self._consecutive_failures = 0
        self._consecutive_anomalies = 0
        self._anomaly_threshold = 2
        self._check_count = 0
        self._last_metrics: Optional[dict] = None

    async def start_monitoring(self):
        """Main monitoring loop — runs until stop_monitoring() is called."""
        self.is_monitoring = True
        self.alert_sent = False
        self._consecutive_failures = 0
        self._consecutive_anomalies = 0
        self._check_count = 0
        logger.info("👁️ WatcherAgent: Monitoring started (interval: %ds)", config.POLLING_INTERVAL_SECONDS)

        while self.is_monitoring:
            try:
                await self._check_health()
                self._consecutive_failures = 0
            except asyncio.CancelledError:
                logger.info("WatcherAgent: Monitoring cancelled")
                break
            except Exception as exc:
                self._consecutive_failures += 1
                logger.error(
                    "Health check error (failure %d): %s",
                    self._consecutive_failures,
                    str(exc)[:100],
                )
                # After 3 consecutive failures, treat as outage
                if self._consecutive_failures >= 3 and not self.alert_sent:
                    await self._trigger_alert("APP_UNREACHABLE", {
                        "error_rate": 1.0,
                        "connection_utilization": 0,
                        "active_connections": 0,
                        "max_connections": 20,
                        "avg_response_time_ms": 99999,
                        "consecutive_failures": self._consecutive_failures,
                    })

            await asyncio.sleep(config.POLLING_INTERVAL_SECONDS)

    def stop_monitoring(self):
        """Stop the monitoring loop."""
        self.is_monitoring = False
        logger.info("WatcherAgent: Monitoring stopped")

    def reset(self):
        """Reset alert state so new incidents can be detected."""
        self.alert_sent = False
        self._consecutive_anomalies = 0
        self._consecutive_failures = 0
        self._check_count = 0
        logger.info("WatcherAgent: Alert state reset — ready for new incidents")

    async def _check_health(self):
        """Poll the target app and check for anomalies."""
        self._check_count += 1

        async with httpx.AsyncClient(timeout=10.0) as client:
            # ── Fetch health data ───────────────────────
            health = await self._fetch_health(client)
            if health is None:
                self._consecutive_failures += 1
                if self._consecutive_failures >= 3 and not self.alert_sent:
                    await self._trigger_alert("APP_UNREACHABLE", {
                        "error_rate": 1.0,
                        "connection_utilization": 0,
                        "active_connections": 0,
                        "max_connections": 20,
                        "avg_response_time_ms": 99999,
                    })
                return

            # ── Fetch metrics ───────────────────────────
            metrics = await self._fetch_metrics(client)

            # ── Run synthetic requests ──────────────────
            synthetic = await self._run_synthetic_checks(client, count=5)

            # ── Compute current state ───────────────────
            error_rate = synthetic["error_rate"]
            avg_response_ms = synthetic["avg_response_ms"]
            conn_util = metrics.get("connection_utilization", 0)
            active_conn = metrics.get("active_connections", health.get("active_connections", 0))
            max_conn = metrics.get("max_connections", health.get("max_connections", 20))

            if max_conn > 0 and conn_util == 0:
                conn_util = active_conn / max_conn

            current_metrics = {
                "error_rate": round(error_rate, 4),
                "connection_utilization": round(conn_util, 4),
                "active_connections": active_conn,
                "max_connections": max_conn,
                "avg_response_time_ms": round(avg_response_ms, 1),
                "bug_injected": health.get("bug_injected", False),
                "status": self._compute_status(error_rate, conn_util, avg_response_ms),
                "check_number": self._check_count,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }

            self._last_metrics = current_metrics

            # ── Send status update (always — powers dashboard metrics) ──
            await self.send_message(
                recipient="broadcast",
                message_type=MessageType.STATUS,
                channel="monitoring.status",
                payload=current_metrics,
                confidence=0.99,
            )

            # ── Detect anomaly ──────────────────────────
            anomaly_reasons = self._detect_anomalies(
                error_rate, conn_util, avg_response_ms
            )

            if anomaly_reasons:
                self._consecutive_anomalies += 1
                logger.warning(
                    "⚠️ Anomaly detected (consecutive: %d/%d): %s",
                    self._consecutive_anomalies,
                    self._anomaly_threshold,
                    ", ".join(anomaly_reasons),
                )

                # Require consecutive anomalies to avoid false positives
                if self._consecutive_anomalies >= self._anomaly_threshold and not self.alert_sent:
                    await self._trigger_alert("ANOMALY_DETECTED", {
                        "error_rate": error_rate,
                        "baseline_error_rate": self.baseline["error_rate"],
                        "connection_utilization": conn_util,
                        "active_connections": active_conn,
                        "max_connections": max_conn,
                        "avg_response_time_ms": round(avg_response_ms, 1),
                        "anomaly_reasons": anomaly_reasons,
                        "consecutive_anomalies": self._consecutive_anomalies,
                    })
            else:
                self._consecutive_anomalies = 0

    async def _fetch_health(self, client: httpx.AsyncClient) -> Optional[dict]:
        """Fetch health endpoint data."""
        try:
            resp = await client.get(config.TARGET_APP_URL + "/health")
            if resp.status_code == 200:
                return resp.json()
            logger.warning("Health endpoint returned %d", resp.status_code)
            return None
        except httpx.TimeoutException:
            logger.warning("Health endpoint timed out")
            return None
        except httpx.RequestError as exc:
            logger.warning("Health endpoint unreachable: %s", str(exc)[:80])
            return None

    async def _fetch_metrics(self, client: httpx.AsyncClient) -> dict:
        """Fetch metrics endpoint data."""
        try:
            resp = await client.get(config.TARGET_APP_URL + "/metrics")
            if resp.status_code == 200:
                return resp.json()
            return {}
        except Exception:
            return {}

    async def _run_synthetic_checks(
        self, client: httpx.AsyncClient, count: int = 5
    ) -> dict:
        """Run synthetic requests to measure real error rate and latency."""
        error_count = 0
        response_times = []

        for i in range(count):
            try:
                start = datetime.utcnow()
                resp = await client.get(config.TARGET_APP_URL + "/tasks")
                elapsed_ms = (datetime.utcnow() - start).total_seconds() * 1000
                response_times.append(elapsed_ms)
                if resp.status_code >= 500:
                    error_count += 1
            except httpx.TimeoutException:
                error_count += 1
                response_times.append(30000)
            except httpx.RequestError:
                error_count += 1
                response_times.append(99999)

            # Small delay between checks to avoid overwhelming target
            if i < count - 1:
                await asyncio.sleep(0.1)

        avg_ms = sum(response_times) / len(response_times) if response_times else 0
        p99_ms = sorted(response_times)[-1] if response_times else 0

        return {
            "error_rate": error_count / count if count > 0 else 0,
            "error_count": error_count,
            "total_checks": count,
            "avg_response_ms": avg_ms,
            "p99_response_ms": p99_ms,
            "response_times": response_times,
        }

    def _detect_anomalies(
        self, error_rate: float, conn_util: float, avg_response_ms: float
    ) -> list:
        """Check if current metrics breach anomaly thresholds."""
        reasons = []

        if error_rate > self.baseline["error_rate"] * 5:
            reasons.append(
                "Error rate " + str(round(error_rate * 100, 1)) + "% exceeds "
                + str(round(self.baseline['error_rate'] * 500, 1)) + "% threshold"
            )

        if conn_util > 0.75:
            reasons.append(
                "Connection utilization " + str(round(conn_util * 100, 1))
                + "% exceeds 75% threshold"
            )

        if avg_response_ms > self.baseline["response_time_ms"] * 10:
            reasons.append(
                "Response time " + str(round(avg_response_ms, 0))
                + "ms exceeds " + str(self.baseline["response_time_ms"] * 10) + "ms threshold"
            )

        return reasons

    def _compute_status(
        self, error_rate: float, conn_util: float, avg_response_ms: float
    ) -> str:
        """Compute overall system status string."""
        if error_rate > 0.5 or conn_util > 0.95:
            return "critical"
        if error_rate > 0.1 or conn_util > 0.75:
            return "degraded"
        if error_rate > 0.05 or conn_util > 0.5:
            return "warning"
        return "healthy"

    async def _trigger_alert(self, alert_type: str, data: dict):
        """Send an incident alert to the OrchestratorAgent."""
        self.alert_sent = True
        incident_id = "INC-" + datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        logger.warning("🚨 ALERT: %s — Incident %s", alert_type, incident_id)

        evidence = [
            "Error rate: " + str(round(data.get("error_rate", 0) * 100, 1)) + "%",
            "Connection utilization: " + str(round(data.get("connection_utilization", 0) * 100, 1)) + "%",
            "Active connections: " + str(data.get("active_connections", "N/A")) + "/" + str(data.get("max_connections", "N/A")),
            "Avg response time: " + str(round(data.get("avg_response_time_ms", 0), 1)) + "ms",
        ]

        anomaly_reasons = data.get("anomaly_reasons", [])
        if anomaly_reasons:
            evidence.extend(anomaly_reasons)

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
            evidence=evidence,
        )

    async def process(self, message: MCPMessage) -> None:
        """WatcherAgent is loop-driven, not message-driven."""
        pass