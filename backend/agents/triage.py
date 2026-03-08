"""
TriageAgent — Classifies incident severity and determines response strategy.
"""

from .base_agent import BaseAgent
from mcp.protocol import MCPMessage, MessageType
from services.llm import chat_json


class TriageAgent(BaseAgent):
    def __init__(self):
        super().__init__("TriageAgent")

    async def process(self, message: MCPMessage) -> None:
        alert_data = message.payload.get("data", message.payload.get("alert", {}))
        self.logger.info("📋 TriageAgent: Classifying incident severity...")

        system_prompt = """You are TriageAgent, an expert SRE incident triage specialist.

Given monitoring alert data, classify the incident severity.

Severity levels:
- P0 (critical): Complete outage, revenue loss, >80% users affected
- P1 (high): Major degradation, >30% users affected
- P2 (medium): Partial degradation, <30% users affected
- P3 (low): Minor issue, <5% users affected

Respond ONLY with valid JSON in this exact format:
{
    "severity": "P0 or P1 or P2 or P3",
    "classification": "brief category like SERVICE_DEGRADATION",
    "blast_radius_pct": 42,
    "affected_endpoints": ["/tasks"],
    "auto_resolve_eligible": true,
    "escalate_to_human": false,
    "reasoning": "brief explanation"
}"""

        user_prompt = f"""Alert data:
- Error rate: {alert_data.get('error_rate', 0)*100:.1f}%
- Connection utilization: {alert_data.get('connection_utilization', 0)*100:.1f}%
- Active connections: {alert_data.get('active_connections', '?')}/{alert_data.get('max_connections', '?')}
- Avg response time: {alert_data.get('avg_response_time_ms', '?')}ms

Classify this incident."""

        result = await chat_json(system_prompt, user_prompt)

        # Ensure required fields exist
        result.setdefault("severity", "P1")
        result.setdefault("classification", "SERVICE_DEGRADATION")
        result.setdefault("blast_radius_pct", 40)
        result.setdefault("auto_resolve_eligible", True)

        self.logger.info(f"📋 Triage result: {result.get('severity')} — {result.get('classification')}")

        await self.send_message(
            recipient="OrchestratorAgent",
            message_type=MessageType.ANALYSIS,
            channel="incident.triage",
            incident_id=message.incident_id,
            payload=result,
            confidence=0.91,
        )