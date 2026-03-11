"""
OrchestratorAgent — The brain of IncidentZero.
Coordinates all agents through the incident lifecycle.

Flow:
  WatcherAgent detects → TriageAgent classifies → DiagnosisAgent analyzes
  → ResolutionAgent debates & fixes → DeployAgent deploys → PostmortemAgent reports

States:
  DETECTED → TRIAGING → DIAGNOSING → DEBATING → RESOLVING → DEPLOYING → RESOLVED
"""

import asyncio
from datetime import datetime
from typing import Optional
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

# Valid state transitions
VALID_TRANSITIONS = {
    "DETECTED": ["TRIAGING"],
    "TRIAGING": ["DIAGNOSING"],
    "DIAGNOSING": ["DEBATING", "RESOLVING"],
    "DEBATING": ["RESOLVING"],
    "RESOLVING": ["DEPLOYING"],
    "DEPLOYING": ["RESOLVED", "DEPLOY_FAILED"],
    "DEPLOY_FAILED": ["RESOLVED"],
    "RESOLVED": [],
}


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

        # Lock to prevent duplicate incident processing
        self._incident_locks: dict = {}
        self._started = False

        # Subscribe to all channels
        mcp_bus.subscribe("incident.detection", self._on_alert)
        mcp_bus.subscribe("incident.triage", self._on_triage_complete)
        mcp_bus.subscribe("incident.diagnosis", self._on_diagnosis_complete)
        mcp_bus.subscribe("incident.debate", self._on_debate_message)
        mcp_bus.subscribe("incident.resolution", self._on_fix_generated)
        mcp_bus.subscribe("incident.deployment", self._on_deployment_complete)
        mcp_bus.subscribe("incident.postmortem", self._on_postmortem_complete)

    async def start(self):
        """Start the orchestrator and begin monitoring."""
        if self._started:
            logger.warning("OrchestratorAgent already started — ignoring duplicate start")
            return

        self._started = True
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
        """Stop all agents."""
        self.watcher.stop_monitoring()
        self._started = False
        logger.info("🛑 OrchestratorAgent stopped")

    # ─── State Management ────────────────────────────────

    def _get_incident_status(self, incident_id: str) -> Optional[str]:
        """Get the current status of an incident."""
        incident = self.active_incidents.get(incident_id)
        if incident:
            return incident.get("status")
        return None

    def _transition_state(self, incident_id: str, new_state: str) -> bool:
        """
        Transition an incident to a new state.
        Returns True if transition is valid, False otherwise.
        """
        current = self._get_incident_status(incident_id)
        if current is None:
            logger.warning(
                "Cannot transition unknown incident %s to %s",
                incident_id,
                new_state,
            )
            return False

        allowed = VALID_TRANSITIONS.get(current, [])
        if new_state not in allowed:
            logger.warning(
                "Invalid state transition for %s: %s → %s (allowed: %s)",
                incident_id,
                current,
                new_state,
                allowed,
            )
            return False

        self.active_incidents[incident_id]["status"] = new_state
        logger.info(
            "📊 Incident %s: %s → %s",
            incident_id,
            current,
            new_state,
        )
        return True

    def _add_timeline_event(
        self, incident_id: str, event: str, agent: str
    ) -> None:
        """Append an event to the incident timeline."""
        if incident_id not in self.active_incidents:
            return
        self.active_incidents[incident_id]["timeline"].append(
            {
                "time": datetime.utcnow().isoformat() + "Z",
                "event": event,
                "agent": agent,
            }
        )

    def _get_lock(self, incident_id: str) -> asyncio.Lock:
        """Get or create a lock for an incident to prevent race conditions."""
        if incident_id not in self._incident_locks:
            self._incident_locks[incident_id] = asyncio.Lock()
        return self._incident_locks[incident_id]

    def _calculate_resolution_time(self, incident_id: str) -> Optional[float]:
        """Calculate how long the incident took to resolve."""
        incident = self.active_incidents.get(incident_id)
        if not incident:
            return None
        started = incident.get("started_at", "")
        if not started:
            return None
        try:
            start_dt = datetime.fromisoformat(started.replace("Z", "+00:00").replace("+00:00+00:00", "+00:00"))
            duration = (datetime.utcnow().replace(tzinfo=start_dt.tzinfo) - start_dt).total_seconds()
            return round(duration, 1)
        except Exception:
            try:
                start_dt = datetime.fromisoformat(started.replace("Z", ""))
                duration = (datetime.utcnow() - start_dt).total_seconds()
                return round(duration, 1)
            except Exception:
                return None

    # ─── Event Handlers ──────────────────────────────────

    async def _on_alert(self, message: MCPMessage):
        """Handle: WatcherAgent detected an anomaly."""
        if message.sender == self.name:
            return

        incident_id = message.incident_id
        if not incident_id:
            logger.warning("Alert received without incident_id — ignoring")
            return

        if incident_id in self.active_incidents:
            logger.info("Incident %s already being processed — ignoring duplicate alert", incident_id)
            return

        async with self._get_lock(incident_id):
            # Double-check after acquiring lock
            if incident_id in self.active_incidents:
                return

            logger.info("🚨 NEW INCIDENT: %s", incident_id)

            self.active_incidents[incident_id] = {
                "id": incident_id,
                "status": "DETECTED",
                "started_at": datetime.utcnow().isoformat() + "Z",
                "resolved_at": None,
                "resolution_time_seconds": None,
                "alert": message.payload,
                "triage": None,
                "diagnosis": None,
                "debate": [],
                "fix": None,
                "deployment": None,
                "postmortem": None,
                "timeline": [
                    {
                        "time": datetime.utcnow().isoformat() + "Z",
                        "event": "Incident detected by WatcherAgent",
                        "agent": "WatcherAgent",
                    }
                ],
            }

        # Transition to TRIAGING
        self._transition_state(incident_id, "TRIAGING")

        await self.send_message(
            recipient="TriageAgent",
            message_type=MessageType.ACTION,
            channel="incident.orchestration",
            incident_id=incident_id,
            payload={"action": "TRIAGE_INCIDENT"},
        )

        # Trigger triage processing
        try:
            await self.triage.process(message)
        except Exception as exc:
            logger.error("TriageAgent failed for %s: %s", incident_id, exc)
            self._add_timeline_event(
                incident_id,
                "TriageAgent error: " + str(exc)[:100],
                "OrchestratorAgent",
            )

    async def _on_triage_complete(self, message: MCPMessage):
        """Handle: TriageAgent classified the incident."""
        if message.sender == self.name:
            return

        incident_id = message.incident_id
        if incident_id not in self.active_incidents:
            logger.warning("Triage complete for unknown incident: %s", incident_id)
            return

        severity = message.payload.get("severity", "P2")
        classification = message.payload.get("classification", "UNKNOWN")
        logger.info("📋 Triage complete: %s (%s)", severity, classification)

        self.active_incidents[incident_id]["triage"] = message.payload
        self._transition_state(incident_id, "DIAGNOSING")
        self._add_timeline_event(
            incident_id,
            "Classified as " + severity + " (" + classification + ") by TriageAgent",
            "TriageAgent",
        )

        # Activate DiagnosisAgent
        try:
            await self.diagnosis.process(message)
        except Exception as exc:
            logger.error("DiagnosisAgent failed for %s: %s", incident_id, exc)
            self._add_timeline_event(
                incident_id,
                "DiagnosisAgent error: " + str(exc)[:100],
                "OrchestratorAgent",
            )

    async def _on_diagnosis_complete(self, message: MCPMessage):
        """Handle: DiagnosisAgent found root cause."""
        if message.sender == self.name:
            return

        incident_id = message.incident_id
        if incident_id not in self.active_incidents:
            logger.warning("Diagnosis complete for unknown incident: %s", incident_id)
            return

        root_cause = message.payload.get("root_cause", {})
        if isinstance(root_cause, dict):
            detail = root_cause.get("detail", root_cause.get("mechanism", "unknown"))
        else:
            detail = str(root_cause)[:100]

        confidence = message.confidence or 0.0
        logger.info("🎯 Diagnosis complete: %s (confidence: %.0f%%)", detail[:60], confidence * 100)

        self.active_incidents[incident_id]["diagnosis"] = message.payload
        self._add_timeline_event(
            incident_id,
            "Root cause identified: " + detail[:80] + " (confidence: " + str(round(confidence * 100)) + "%)",
            "DiagnosisAgent",
        )

        # Forward to ResolutionAgent (will debate then generate fix)
        self._transition_state(incident_id, "DEBATING")
        try:
            await self.resolution.process(message)
        except Exception as exc:
            logger.error("ResolutionAgent failed for %s: %s", incident_id, exc)
            self._add_timeline_event(
                incident_id,
                "ResolutionAgent error: " + str(exc)[:100],
                "OrchestratorAgent",
            )

    async def _on_debate_message(self, message: MCPMessage):
        """Handle: Agents debating about the diagnosis."""
        if message.sender == self.name:
            return

        incident_id = message.incident_id
        if incident_id not in self.active_incidents:
            return

        msg_type = message.message_type.value if hasattr(message.message_type, "value") else str(message.message_type)
        logger.info(
            "💬 Debate: %s → %s",
            message.sender,
            msg_type,
        )

        # Track debate in incident
        debate_entry = {
            "time": message.timestamp,
            "agent": message.sender,
            "type": msg_type,
            "content": message.payload,
        }
        self.active_incidents[incident_id]["debate"].append(debate_entry)

        self._add_timeline_event(
            incident_id,
            "Debate: " + message.sender + " — " + msg_type,
            message.sender,
        )

        # If DiagnosisAgent is challenged, forward the challenge
        if (
            message.recipient == "DiagnosisAgent"
            and message.message_type == MessageType.CHALLENGE
        ):
            try:
                await self.diagnosis.respond_to_challenge(message)
            except Exception as exc:
                logger.error(
                    "DiagnosisAgent failed to respond to challenge: %s", exc
                )

        # If DiagnosisAgent responded with evidence, forward to ResolutionAgent
        if (
            message.sender == "DiagnosisAgent"
            and message.recipient == "ResolutionAgent"
            and message.message_type == MessageType.EVIDENCE
        ):
            try:
                self.resolution.receive_challenge_response(incident_id, message.payload)
            except Exception as exc:
                logger.error(
                    "Failed to forward challenge response to ResolutionAgent: %s", exc
                )

    async def _on_fix_generated(self, message: MCPMessage):
        """Handle: ResolutionAgent generated a fix."""
        if message.sender == self.name:
            return

        incident_id = message.incident_id
        if incident_id not in self.active_incidents:
            logger.warning("Fix generated for unknown incident: %s", incident_id)
            return

        fix_data = message.payload.get("fix", {})
        description = fix_data.get("description", "unknown fix") if isinstance(fix_data, dict) else "fix generated"
        risk_level = fix_data.get("risk_level", "UNKNOWN") if isinstance(fix_data, dict) else "UNKNOWN"
        logger.info("🔧 Fix generated: %s (risk: %s) — deploying", description[:60], risk_level)

        # Transition from DEBATING or RESOLVING to DEPLOYING
        current_status = self._get_incident_status(incident_id)
        if current_status == "DEBATING":
            self._transition_state(incident_id, "RESOLVING")
        self._transition_state(incident_id, "DEPLOYING")

        self.active_incidents[incident_id]["fix"] = message.payload
        self._add_timeline_event(
            incident_id,
            "Fix generated: " + description[:60] + " (risk: " + risk_level + ")",
            "ResolutionAgent",
        )

        # Forward to DeployAgent
        try:
            await self.deploy.process(message)
        except Exception as exc:
            logger.error("DeployAgent failed for %s: %s", incident_id, exc)
            self._add_timeline_event(
                incident_id,
                "DeployAgent error: " + str(exc)[:100],
                "OrchestratorAgent",
            )

    async def _on_deployment_complete(self, message: MCPMessage):
        """Handle: DeployAgent deployed the fix."""
        if message.sender == self.name:
            return

        incident_id = message.incident_id
        if incident_id not in self.active_incidents:
            logger.warning("Deployment complete for unknown incident: %s", incident_id)
            return

        status = message.payload.get("status", "UNKNOWN")
        pr_url = message.payload.get("pr_url", "")
        health = message.payload.get("health_check", "UNKNOWN")
        logger.info(
            "🚀 Deployment: %s | Health: %s | PR: %s",
            status,
            health,
            pr_url[:60] if pr_url else "none",
        )

        self.active_incidents[incident_id]["deployment"] = message.payload

        if status == "SUCCESS":
            self._transition_state(incident_id, "RESOLVED")
        else:
            self._transition_state(incident_id, "DEPLOY_FAILED")

        self.active_incidents[incident_id]["resolved_at"] = datetime.utcnow().isoformat() + "Z"

        # Calculate resolution time
        duration = self._calculate_resolution_time(incident_id)
        if duration is not None:
            self.active_incidents[incident_id]["resolution_time_seconds"] = duration
            logger.info("⏱️ Resolved in %.1f seconds", duration)

        deploy_event = "Deployment " + status
        if health != "UNKNOWN":
            deploy_event = deploy_event + " (health: " + health + ")"
        if pr_url and pr_url.startswith("http"):
            deploy_event = deploy_event + " — PR: " + pr_url
        self._add_timeline_event(incident_id, deploy_event, "DeployAgent")

        # Generate postmortem
        try:
            await self.postmortem.process(message)
        except Exception as exc:
            logger.error("PostmortemAgent failed for %s: %s", incident_id, exc)
            self._add_timeline_event(
                incident_id,
                "PostmortemAgent error: " + str(exc)[:100],
                "OrchestratorAgent",
            )

        # Reset watcher for next incident
        try:
            self.watcher.reset()
        except Exception as exc:
            logger.warning("Failed to reset WatcherAgent: %s", exc)

    async def _on_postmortem_complete(self, message: MCPMessage):
        """Handle: PostmortemAgent generated the report."""
        if message.sender == self.name:
            return

        incident_id = message.incident_id
        if incident_id not in self.active_incidents:
            logger.warning("Postmortem for unknown incident: %s", incident_id)
            return

        self.active_incidents[incident_id]["postmortem"] = message.payload
        self._add_timeline_event(
            incident_id,
            "Postmortem report generated",
            "PostmortemAgent",
        )

        logger.info("📋 Incident %s fully resolved with postmortem", incident_id)

        # Move to resolved list but keep in active for API access
        resolved_copy = dict(self.active_incidents[incident_id])
        resolved_copy["completed_at"] = datetime.utcnow().isoformat() + "Z"
        self.resolved_incidents.append(resolved_copy)

        # Send final status message
        duration = self.active_incidents[incident_id].get("resolution_time_seconds")
        await self.send_message(
            recipient="broadcast",
            message_type=MessageType.STATUS,
            channel="system.status",
            incident_id=incident_id,
            payload={
                "status": "INCIDENT_RESOLVED",
                "incident_id": incident_id,
                "resolution_time_seconds": duration,
                "total_incidents_resolved": len(self.resolved_incidents),
            },
        )

        # Clean up lock
        if incident_id in self._incident_locks:
            del self._incident_locks[incident_id]

    # ─── API Methods ─────────────────────────────────────

    def get_incidents(self) -> dict:
        """Get all active incidents."""
        return dict(self.active_incidents)

    def get_incident(self, incident_id: str) -> Optional[dict]:
        """Get a specific incident by ID. Checks active then resolved."""
        if incident_id in self.active_incidents:
            return self.active_incidents[incident_id]
        for resolved in self.resolved_incidents:
            if resolved.get("id") == incident_id:
                return resolved
        return None

    def get_all_resolved(self) -> list:
        """Get all resolved incidents."""
        return list(self.resolved_incidents)

    def get_summary(self) -> dict:
        """Get a summary of all incidents — used by /api/status."""
        return {
            "active_count": len(self.active_incidents),
            "resolved_count": len(self.resolved_incidents),
            "active_ids": list(self.active_incidents.keys()),
            "agent_status": {
                "watcher": "monitoring" if self._started else "stopped",
                "triage": "standby",
                "diagnosis": "standby",
                "resolution": "standby",
                "deploy": "standby",
                "postmortem": "standby",
            },
        }

    async def process(self, message: MCPMessage) -> None:
        """Orchestrator is event-driven — this is a no-op."""
        pass