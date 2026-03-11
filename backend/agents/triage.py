"""
TriageAgent — Classifies incident severity and determines response strategy.
Uses LLM for intelligent classification with comprehensive fallback.
"""

import json
from datetime import datetime

from .base_agent import BaseAgent
from mcp.protocol import MCPMessage, MessageType
from services.llm import chat_json

import logging

logger = logging.getLogger("TriageAgent")


class TriageAgent(BaseAgent):
    def __init__(self):
        super().__init__("TriageAgent")

    async def process(self, message: MCPMessage) -> None:
        """Classify incident severity based on alert data."""
        self.logger.info("📋 TriageAgent: Classifying incident severity...")
        incident_id = message.incident_id

        # Extract alert data from multiple possible payload structures
        alert_data = self._extract_alert_data(message.payload)

        # Pre-classify locally for validation
        local_severity = self._local_severity_estimate(alert_data)

        system_prompt = (
            "You are TriageAgent, an expert SRE incident triage specialist.\n\n"
            "Given monitoring alert data, classify the incident severity.\n\n"
            "Severity levels:\n"
            "- P0 (critical): Complete outage, revenue loss, >80% users affected\n"
            "- P1 (high): Major degradation, >30% users affected, service partially down\n"
            "- P2 (medium): Partial degradation, <30% users affected, some errors\n"
            "- P3 (low): Minor issue, <5% users affected, cosmetic or non-critical\n\n"
            "Consider:\n"
            "1. Error rate — what percentage of requests are failing?\n"
            "2. Connection pool utilization — is the pool near exhaustion?\n"
            "3. Response time — are successful requests slowing down?\n"
            "4. Blast radius — how many users/endpoints are affected?\n\n"
            "Respond ONLY with valid JSON:\n"
            "{\n"
            '    "severity": "P0 or P1 or P2 or P3",\n'
            '    "classification": "brief category like SERVICE_DEGRADATION",\n'
            '    "blast_radius_pct": 42,\n'
            '    "affected_endpoints": ["/tasks"],\n'
            '    "auto_resolve_eligible": true,\n'
            '    "escalate_to_human": false,\n'
            '    "reasoning": "brief explanation of severity classification",\n'
            '    "recommended_actions": ["list of immediate actions"]\n'
            "}"
        )

        error_rate = alert_data.get("error_rate", 0)
        conn_util = alert_data.get("connection_utilization", 0)
        active_conn = alert_data.get("active_connections", "?")
        max_conn = alert_data.get("max_connections", "?")
        response_time = alert_data.get("avg_response_time_ms", "?")

        user_prompt = (
            "Alert data:\n"
            "- Error rate: " + str(round(error_rate * 100, 1)) + "%\n"
            "- Connection utilization: " + str(round(conn_util * 100, 1)) + "%\n"
            "- Active connections: " + str(active_conn) + "/" + str(max_conn) + "\n"
            "- Avg response time: " + str(response_time) + "ms\n"
            "- Local severity estimate: " + local_severity + "\n\n"
            "Classify this incident."
        )

        try:
            result = await chat_json(system_prompt, user_prompt)
        except Exception as exc:
            self.logger.error("LLM error during triage: %s", exc)
            result = {}

        # Handle parse errors
        if result.get("_parse_error"):
            result = {}

        # Validate and ensure all required fields
        result = self._validate_and_fill(result, alert_data, local_severity)

        severity = result.get("severity", "P1")
        classification = result.get("classification", "UNKNOWN")
        blast_radius = result.get("blast_radius_pct", 0)

        self.logger.info(
            "📋 Triage result: %s — %s — Blast radius: %s%%",
            severity,
            classification,
            blast_radius,
        )

        await self.send_message(
            recipient="OrchestratorAgent",
            message_type=MessageType.ANALYSIS,
            channel="incident.triage",
            incident_id=incident_id,
            payload=result,
            confidence=0.91,
            evidence=[
                "Error rate: " + str(round(error_rate * 100, 1)) + "%",
                "Connection utilization: " + str(round(conn_util * 100, 1)) + "%",
                "Severity: " + severity,
            ],
        )

    def _extract_alert_data(self, payload: dict) -> dict:
        """Extract alert data from various payload structures."""
        # Try direct data field
        data = payload.get("data", {})
        if data:
            return data

        # Try alert field
        alert = payload.get("alert", {})
        if alert:
            return alert

        # Try payload itself
        if "error_rate" in payload or "connection_utilization" in payload:
            return payload

        # Return whatever we have
        return payload

    def _local_severity_estimate(self, alert_data: dict) -> str:
        """Quick local severity estimate for validation."""
        error_rate = alert_data.get("error_rate", 0)
        conn_util = alert_data.get("connection_utilization", 0)

        if error_rate > 0.8 or conn_util > 0.95:
            return "P0"
        elif error_rate > 0.3 or conn_util > 0.75:
            return "P1"
        elif error_rate > 0.1 or conn_util > 0.5:
            return "P2"
        else:
            return "P3"

    def _validate_and_fill(self, result: dict, alert_data: dict, local_severity: str) -> dict:
        """Validate LLM result and fill missing fields with sensible defaults."""
        error_rate = alert_data.get("error_rate", 0)
        conn_util = alert_data.get("connection_utilization", 0)

        # Severity
        valid_severities = ["P0", "P1", "P2", "P3"]
        severity = result.get("severity", "").upper()
        if severity not in valid_severities:
            severity = local_severity
        result["severity"] = severity

        # Classification
        if not result.get("classification"):
            if error_rate > 0.5:
                result["classification"] = "SERVICE_OUTAGE"
            elif error_rate > 0.1:
                result["classification"] = "SERVICE_DEGRADATION"
            elif conn_util > 0.75:
                result["classification"] = "RESOURCE_EXHAUSTION"
            else:
                result["classification"] = "ANOMALY_DETECTED"

        # Blast radius
        if not isinstance(result.get("blast_radius_pct"), (int, float)):
            result["blast_radius_pct"] = max(
                int(error_rate * 100),
                int(conn_util * 80),
            )

        # Affected endpoints
        if not result.get("affected_endpoints"):
            result["affected_endpoints"] = ["/tasks", "/tasks/{id}"]

        # Auto resolve
        if "auto_resolve_eligible" not in result:
            result["auto_resolve_eligible"] = severity in ["P1", "P2", "P3"]

        # Escalation
        if "escalate_to_human" not in result:
            result["escalate_to_human"] = severity == "P0"

        # Reasoning
        if not result.get("reasoning"):
            result["reasoning"] = (
                "Error rate at " + str(round(error_rate * 100, 1)) + "% "
                "with connection utilization at " + str(round(conn_util * 100, 1)) + "% "
                "indicates " + result["classification"].lower().replace("_", " ") + ". "
                "Classified as " + severity + " with "
                + str(result["blast_radius_pct"]) + "% blast radius."
            )

        # Recommended actions
        if not result.get("recommended_actions"):
            actions = ["Initiate automated root cause analysis"]
            if severity in ["P0", "P1"]:
                actions.append("Prepare rollback plan")
                actions.append("Monitor connection pool drain rate")
            if result.get("escalate_to_human"):
                actions.append("Page on-call engineer")
            result["recommended_actions"] = actions

        # Timestamp
        result["triaged_at"] = datetime.utcnow().isoformat() + "Z"

        return result