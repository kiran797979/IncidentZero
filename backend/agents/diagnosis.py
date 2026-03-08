"""
DiagnosisAgent — Analyzes logs, metrics, and code to find root cause.
"""

import httpx
import json

from .base_agent import BaseAgent
from mcp.protocol import MCPMessage, MessageType
from services.llm import chat_json
from config import config


class DiagnosisAgent(BaseAgent):
    def __init__(self):
        super().__init__("DiagnosisAgent")

    async def process(self, message: MCPMessage) -> None:
        self.logger.info("🔍 DiagnosisAgent: Analyzing root cause...")

        # Step 1: Gather live evidence
        evidence = await self._gather_evidence()

        system_prompt = """You are DiagnosisAgent, an expert SRE root cause analyst.

The target application is a FastAPI app (app.py) with:
- A ConnectionPool class with acquire() and release() methods (max 20 connections)
- /tasks GET and POST endpoints that use the connection pool
- A finally block that should release connections but may have a bug

Analyze the evidence and determine the root cause.

Respond ONLY with valid JSON:
{
    "root_cause": {
        "category": "RESOURCE_EXHAUSTION or MEMORY_LEAK or CODE_BUG",
        "component": "specific component name",
        "file": "app.py",
        "function": "affected function name",
        "mechanism": "detailed explanation of HOW the failure occurs",
        "detail": "one-line summary"
    },
    "confidence": 0.88,
    "evidence_analysis": ["list of evidence interpretations"],
    "alternative_hypotheses": [
        {"category": "...", "confidence": 0.1, "reason": "..."}
    ]
}"""

        user_prompt = f"""Incident data from triage:
{json.dumps(message.payload, indent=2)}

Live evidence from target app:
{json.dumps(evidence, indent=2)}

Find the root cause."""

        result = await chat_json(system_prompt, user_prompt)

        # Ensure required structure
        if "root_cause" not in result:
            result = {
                "root_cause": result,
                "confidence": 0.85,
                "evidence_analysis": [],
                "alternative_hypotheses": [],
            }

        self.logger.info(
            f"🎯 Root cause: {result.get('root_cause', {}).get('detail', 'unknown')}"
        )

        await self.send_message(
            recipient="ResolutionAgent",
            message_type=MessageType.ANALYSIS,
            channel="incident.diagnosis",
            incident_id=message.incident_id,
            payload=result,
            confidence=result.get("confidence", 0.85),
            evidence=result.get("evidence_analysis", []),
        )

    async def respond_to_challenge(self, challenge_message: MCPMessage) -> None:
        """Respond when ResolutionAgent challenges the diagnosis"""
        self.logger.info("💬 DiagnosisAgent: Responding to challenge...")

        system_prompt = """You are DiagnosisAgent responding to a challenge from ResolutionAgent.

Be intellectually honest. If they raise a valid point, update your diagnosis.
If your original analysis is correct, defend it with evidence.

Respond ONLY with valid JSON:
{
    "response_type": "DEFEND or ACCEPT_REVISION",
    "response": "your detailed response to the challenge",
    "updated_diagnosis": null,
    "additional_evidence": ["any new evidence"],
    "confidence": 0.9
}"""

        user_prompt = f"""Challenge from ResolutionAgent:
{json.dumps(challenge_message.payload, indent=2)}

Respond to this challenge."""

        result = await chat_json(system_prompt, user_prompt, temperature=0.4)

        await self.send_message(
            recipient="ResolutionAgent",
            message_type=MessageType.EVIDENCE,
            channel="incident.debate",
            incident_id=challenge_message.incident_id,
            payload=result,
            confidence=result.get("confidence", 0.85),
            parent_message_id=challenge_message.message_id,
        )

    async def _gather_evidence(self) -> dict:
        """Gather live data from the target application"""
        evidence = {}
        async with httpx.AsyncClient(timeout=10) as client:
            for endpoint in ["/health", "/metrics", "/chaos/status"]:
                try:
                    resp = await client.get(f"{config.TARGET_APP_URL}{endpoint}")
                    evidence[endpoint.strip("/")] = resp.json()
                except Exception as e:
                    evidence[endpoint.strip("/")] = {"error": str(e)}
        return evidence