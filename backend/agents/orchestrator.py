"""
OrchestratorAgent — The brain of IncidentZero.
Coordinates all agents through the incident lifecycle.

Flow:
  WatcherAgent detects → TriageAgent classifies → DiagnosisAgent analyzes
  → ResolutionAgent debates & fixes → DeployAgent deploys → PostmortemAgent reports
"""

import asyncio
from datetime import datetime
import logging

from .base_agent import BaseAgent
from .watcher import WatcherAgent
from .triage import TriageAgent
from .diagnosis import DiagnosisAgent
from .resolution import ResolutionAgent
from .deploy import DeployAgent
from .postmortem import PostmortemAgent
from mcp.protocol import MCPMessage, MessageType
from mcp.channel import mcp_bus

logger = logging.getLogger("OrchestratorAgent")


class OrchestratorAgent(BaseAgent):
    def __init__(self):
        super().__init__("OrchestratorAgent")

        # Initialize all agents
        self.watcher = WatcherAgent()
        self.triage = TriageAgent()
        self.diagnosis = DiagnosisAgent()
        self.resolution = ResolutionAgent()
        self.deploy = DeployAgent()
        self.postmortem = PostmortemAgent()

        # Incident tracking
        self.active_incidents: dict = {}
        self.resolved_incidents: list = []

        # Flag to prevent duplicate processing
        self._processing = False

        # Subscribe to all channels
        mcp_bus.subscribe("incident.detection", self._on_alert)
        mcp_bus.subscribe("incident.triage", self._on_triage_complete)
        mcp_bus.subscribe("incident.diagnosis", self._on_diagnosis_complete)
        mcp_bus.subscribe("incident.debate", self._on_debate_message)
        mcp_bus.subscribe("incident.resolution", self._on_fix_generated)
        mcp_bus.subscribe("incident.deployment", self._on_deployment_complete)
        mcp_bus.subscribe("incident.postmortem", self._on_postmortem_complete)

    async def start(self):
        """Start the orchestrator and begin monitoring"""
        logger.info("🚀 OrchestratorAgent starting...")

        await self.send_message(
            recipient="broadcast",
            message_type=MessageType.STATUS,
            channel="system.status",
            payload={
                "status": "ORCHESTRATOR_READY",
                "agents_online": [
                    "WatcherAgent",
                    "TriageAgent",
                    "DiagnosisAgent",
                    "ResolutionAgent",
                    "DeployAgent",
                    "PostmortemAgent",
                ],
                "started_at": datetime.utcnow().isoformat() + "Z",
            },
        )

        # Start WatcherAgent monitoring loop
        asyncio.create_task(self.watcher.start_monitoring())
        logger.info("✅ All agents online. Monitoring started.")

    def stop(self):
        """Stop all agents"""
        self.watcher.stop_monitoring()
        logger.info("🛑 OrchestratorAgent stopped")

    # ─── Event Handlers ──────────────────────────────────

    async def _on_alert(self, message: MCPMessage):
        """Handle: WatcherAgent detected an anomaly"""
        if message.sender == self.name:
            return  # Ignore own messages

        incident_id = message.incident_id
        if not incident_id or incident_id in self.active_incidents:
            return  # Already processing

        logger.info(f"🚨 NEW INCIDENT: {incident_id}")

        self.active_incidents[incident_id] = {
            "status": "DETECTED",
            "started_at": datetime.utcnow().isoformat() + "Z",
            "alert": message.payload,
            "timeline": [
                {
                    "time": datetime.utcnow().isoformat() + "Z",
                    "event": "Incident detected by WatcherAgent",
                    "agent": "WatcherAgent",
                }
            ],
        }

        # Activate TriageAgent
        self.active_incidents[incident_id]["status"] = "TRIAGING"
        await self.send_message(
            recipient="TriageAgent",
            message_type=MessageType.ACTION,
            channel="incident.orchestration",
            incident_id=incident_id,
            payload={"action": "TRIAGE_INCIDENT"},
        )

        # Trigger triage processing
        await self.triage.process(message)

    async def _on_triage_complete(self, message: MCPMessage):
        """Handle: TriageAgent classified the incident"""
        if message.sender == self.name:
            return

        incident_id = message.incident_id
        if incident_id not in self.active_incidents:
            return

        severity = message.payload.get("severity", "P2")
        logger.info(f"📋 Triage complete: {severity}")

        self.active_incidents[incident_id]["status"] = "DIAGNOSING"
        self.active_incidents[incident_id]["triage"] = message.payload
        self.active_incidents[incident_id]["timeline"].append({
            "time": datetime.utcnow().isoformat() + "Z",
            "event": f"Classified as {severity} by TriageAgent",
            "agent": "TriageAgent",
        })

        # Activate DiagnosisAgent
        await self.diagnosis.process(message)

    async def _on_diagnosis_complete(self, message: MCPMessage):
        """Handle: DiagnosisAgent found root cause"""
        if message.sender == self.name:
            return

        incident_id = message.incident_id
        if incident_id not in self.active_incidents:
            return

        root_cause = message.payload.get("root_cause", {})
        detail = root_cause.get("detail", "unknown") if isinstance(root_cause, dict) else str(root_cause)
        logger.info(f"🎯 Diagnosis complete: {detail}")

        self.active_incidents[incident_id]["status"] = "RESOLVING"
        self.active_incidents[incident_id]["diagnosis"] = message.payload
        self.active_incidents[incident_id]["timeline"].append({
            "time": datetime.utcnow().isoformat() + "Z",
            "event": f"Root cause identified: {detail}",
            "agent": "DiagnosisAgent",
        })

        # Forward to ResolutionAgent (will debate then fix)
        await self.resolution.process(message)

    async def _on_debate_message(self, message: MCPMessage):
        """Handle: Agents debating about the diagnosis"""
        if message.sender == self.name:
            return

        incident_id = message.incident_id
        if incident_id not in self.active_incidents:
            return

        logger.info(
            f"💬 Debate: {message.sender} → {message.message_type.value}"
        )

        # Track debate in timeline
        if "debate" not in self.active_incidents[incident_id]:
            self.active_incidents[incident_id]["debate"] = []

        self.active_incidents[incident_id]["debate"].append({
            "time": message.timestamp,
            "agent": message.sender,
            "type": message.message_type.value,
            "content": message.payload,
        })

        self.active_incidents[incident_id]["timeline"].append({
            "time": message.timestamp,
            "event": f"Debate: {message.sender} — {message.message_type.value}",
            "agent": message.sender,
        })

        # If DiagnosisAgent is challenged, forward the challenge
        if (
            message.recipient == "DiagnosisAgent"
            and message.message_type == MessageType.CHALLENGE
        ):
            await self.diagnosis.respond_to_challenge(message)

    async def _on_fix_generated(self, message: MCPMessage):
        """Handle: ResolutionAgent generated a fix"""
        if message.sender == self.name:
            return

        incident_id = message.incident_id
        if incident_id not in self.active_incidents:
            return

        logger.info("🔧 Fix generated — deploying")

        self.active_incidents[incident_id]["status"] = "DEPLOYING"
        self.active_incidents[incident_id]["fix"] = message.payload
        self.active_incidents[incident_id]["timeline"].append({
            "time": datetime.utcnow().isoformat() + "Z",
            "event": "Fix generated by ResolutionAgent",
            "agent": "ResolutionAgent",
        })

        # Forward to DeployAgent
        await self.deploy.process(message)

    async def _on_deployment_complete(self, message: MCPMessage):
        """Handle: DeployAgent deployed the fix"""
        if message.sender == self.name:
            return

        incident_id = message.incident_id
        if incident_id not in self.active_incidents:
            return

        status = message.payload.get("status", "UNKNOWN")
        logger.info(f"🚀 Deployment: {status}")

        self.active_incidents[incident_id]["status"] = "RESOLVED" if status == "SUCCESS" else "DEPLOY_FAILED"
        self.active_incidents[incident_id]["deployment"] = message.payload
        self.active_incidents[incident_id]["resolved_at"] = datetime.utcnow().isoformat() + "Z"
        self.active_incidents[incident_id]["timeline"].append({
            "time": datetime.utcnow().isoformat() + "Z",
            "event": f"Deployment {status}",
            "agent": "DeployAgent",
        })

        # Calculate resolution time
        started = self.active_incidents[incident_id].get("started_at", "")
        if started:
            try:
                start_dt = datetime.fromisoformat(started.replace("Z", ""))
                duration = (datetime.utcnow() - start_dt).total_seconds()
                self.active_incidents[incident_id]["resolution_time_seconds"] = round(duration, 1)
                logger.info(f"⏱️ Resolved in {duration:.1f} seconds")
            except Exception:
                pass

        # Generate postmortem
        await self.postmortem.process(message)

        # Reset watcher for next incident
        self.watcher.reset()

    async def _on_postmortem_complete(self, message: MCPMessage):
        """Handle: PostmortemAgent generated the report"""
        if message.sender == self.name:
            return

        incident_id = message.incident_id
        if incident_id not in self.active_incidents:
            return

        self.active_incidents[incident_id]["postmortem"] = message.payload
        self.active_incidents[incident_id]["timeline"].append({
            "time": datetime.utcnow().isoformat() + "Z",
            "event": "Postmortem report generated",
            "agent": "PostmortemAgent",
        })

        logger.info(f"✅ Incident {incident_id} fully resolved with postmortem")

        # Move to resolved list
        self.resolved_incidents.append(self.active_incidents[incident_id])

    # ─── API Methods ─────────────────────────────────────

    def get_incidents(self) -> dict:
        """Get all active incidents"""
        return self.active_incidents

    def get_incident(self, incident_id: str) -> dict:
        """Get a specific incident"""
        return self.active_incidents.get(incident_id)

    def get_all_resolved(self) -> list:
        """Get all resolved incidents"""
        return self.resolved_incidents

    async def process(self, message: MCPMessage):
        pass  # Orchestrator is event-driven