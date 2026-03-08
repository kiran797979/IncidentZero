"""
PostmortemAgent — Generates comprehensive incident postmortem reports
after an incident is resolved. Collects all agent messages, debate
transcripts, and resolution details to create a full report.
"""

import json
import logging
from datetime import datetime

from .base_agent import BaseAgent
from mcp.protocol import MCPMessage, MessageType
from mcp.channel import mcp_bus
from services.llm import chat

logger = logging.getLogger("PostmortemAgent")


class PostmortemAgent(BaseAgent):
    def __init__(self):
        super().__init__("PostmortemAgent")

    async def process(self, message: MCPMessage) -> None:
        """Generate a postmortem report for the resolved incident"""
        self.logger.info("📋 PostmortemAgent: Generating postmortem report...")
        incident_id = message.incident_id

        # ── Step 1: Gather all messages from this incident ───────
        all_messages = mcp_bus.get_incident_messages(incident_id)
        debate_messages = mcp_bus.get_debate_messages(incident_id)

        # ── Step 2: Build activity log summary ───────────────────
        activity_log = self._build_activity_log(all_messages)
        debate_log = self._build_debate_log(debate_messages)

        # ── Step 3: Extract key incident data ────────────────────
        deployment_data = message.payload
        incident_summary = self._build_incident_summary(
            incident_id=incident_id,
            all_messages=all_messages,
            debate_messages=debate_messages,
            deployment_data=deployment_data,
        )

        # ── Step 4: Generate report using LLM ────────────────────
        report = await self._generate_report(
            incident_id=incident_id,
            incident_summary=incident_summary,
            activity_log=activity_log,
            debate_log=debate_log,
            deployment_data=deployment_data,
        )

        self.logger.info(
            f"📋 Postmortem complete: {len(all_messages)} messages, "
            f"{len(debate_messages)} debate rounds"
        )

        # ── Step 5: Publish the report ───────────────────────────
        await self.send_message(
            recipient="broadcast",
            message_type=MessageType.STATUS,
            channel="incident.postmortem",
            incident_id=incident_id,
            payload={
                "report_markdown": report,
                "total_messages": len(all_messages),
                "debate_rounds": len(debate_messages),
                "agents_involved": self._get_agents_involved(all_messages),
                "status": "POSTMORTEM_COMPLETE",
                "generated_at": datetime.utcnow().isoformat() + "Z",
            },
            confidence=0.95,
        )

    def _build_activity_log(self, messages: list) -> list:
        """Build a structured summary of all agent activity"""
        activity_log = []
        for msg in messages:
            # Skip noisy monitoring status messages
            if msg.channel == "monitoring.status":
                continue

            entry = {
                "timestamp": msg.timestamp,
                "agent": msg.sender,
                "type": msg.message_type.value,
                "channel": msg.channel,
                "recipient": msg.recipient,
                "confidence": msg.confidence,
            }

            # Extract key info from payload based on channel
            payload = msg.payload
            if msg.channel == "incident.detection":
                alert_data = payload.get("data", payload.get("alert", {}))
                entry["summary"] = (
                    f"Anomaly detected: error_rate="
                    f"{alert_data.get('error_rate', 'N/A')}, "
                    f"conn_util="
                    f"{alert_data.get('connection_utilization', 'N/A')}"
                )
            elif msg.channel == "incident.triage":
                entry["summary"] = (
                    f"Severity: {payload.get('severity', 'N/A')} — "
                    f"{payload.get('classification', 'N/A')} — "
                    f"Blast radius: {payload.get('blast_radius_pct', 'N/A')}%"
                )
            elif msg.channel == "incident.diagnosis":
                root_cause = payload.get("root_cause", {})
                if isinstance(root_cause, dict):
                    entry["summary"] = (
                        f"Root cause: {root_cause.get('detail', '')} — "
                        f"{root_cause.get('mechanism', '')}"
                    )
                else:
                    entry["summary"] = f"Root cause: {str(root_cause)[:200]}"
            elif msg.channel == "incident.debate":
                if msg.message_type.value == "challenge":
                    eval_data = payload.get("evaluation", {})
                    entry["summary"] = (
                        f"CHALLENGE: {eval_data.get('reasoning', '')[:200]}"
                    )
                elif msg.message_type.value == "consensus":
                    eval_data = payload.get("evaluation", {})
                    entry["summary"] = (
                        f"CONSENSUS: {eval_data.get('reasoning', '')[:200]}"
                    )
                elif msg.message_type.value == "evidence":
                    entry["summary"] = (
                        f"EVIDENCE: {payload.get('response', '')[:200]}"
                    )
                else:
                    entry["summary"] = str(payload)[:200]
            elif msg.channel == "incident.resolution":
                fix = payload.get("fix", {})
                entry["summary"] = (
                    f"Fix: {fix.get('description', 'N/A')} — "
                    f"Risk: {fix.get('risk_level', 'N/A')}"
                )
            elif msg.channel == "incident.deployment":
                entry["summary"] = (
                    f"Deployment: {payload.get('status', 'N/A')} — "
                    f"Health: {payload.get('health_check', 'N/A')}"
                )
            else:
                entry["summary"] = str(payload)[:200]

            activity_log.append(entry)

        return activity_log

    def _build_debate_log(self, debate_messages: list) -> list:
        """Build a structured summary of the agent debate"""
        debate_log = []
        for msg in debate_messages:
            entry = {
                "timestamp": msg.timestamp,
                "agent": msg.sender,
                "type": msg.message_type.value,
                "confidence": msg.confidence,
            }

            payload = msg.payload
            if msg.message_type.value == "challenge":
                eval_data = payload.get("evaluation", {})
                entry["content"] = eval_data.get("reasoning", str(payload)[:300])
                entry["assessment"] = eval_data.get("assessment", "UNKNOWN")
                entry["challenge_question"] = eval_data.get(
                    "challenge_question", None
                )
            elif msg.message_type.value == "consensus":
                eval_data = payload.get("evaluation", {})
                entry["content"] = eval_data.get("reasoning", str(payload)[:300])
                entry["assessment"] = eval_data.get("assessment", "AGREE")
            elif msg.message_type.value == "evidence":
                entry["content"] = payload.get("response", str(payload)[:300])
                entry["response_type"] = payload.get("response_type", "UNKNOWN")
            else:
                entry["content"] = str(payload)[:300]

            debate_log.append(entry)

        return debate_log

    def _build_incident_summary(
        self,
        incident_id: str,
        all_messages: list,
        debate_messages: list,
        deployment_data: dict,
    ) -> dict:
        """Build a high-level incident summary"""
        # Find key timestamps
        first_msg = all_messages[0] if all_messages else None
        last_msg = all_messages[-1] if all_messages else None

        # Find triage data
        triage_data = {}
        diagnosis_data = {}
        fix_data = {}

        for msg in all_messages:
            if msg.channel == "incident.triage":
                triage_data = msg.payload
            elif msg.channel == "incident.diagnosis":
                diagnosis_data = msg.payload
            elif msg.channel == "incident.resolution":
                fix_data = msg.payload

        return {
            "incident_id": incident_id,
            "started_at": first_msg.timestamp if first_msg else "unknown",
            "resolved_at": last_msg.timestamp if last_msg else "unknown",
            "total_agent_messages": len(all_messages),
            "debate_rounds": len(debate_messages),
            "severity": triage_data.get("severity", "unknown"),
            "classification": triage_data.get("classification", "unknown"),
            "blast_radius_pct": triage_data.get("blast_radius_pct", "unknown"),
            "root_cause": diagnosis_data.get("root_cause", {}),
            "fix_description": fix_data.get("fix", {}).get("description", "unknown"),
            "fix_risk_level": fix_data.get("fix", {}).get("risk_level", "unknown"),
            "deployment_status": deployment_data.get("status", "unknown"),
            "health_after_fix": deployment_data.get("health_check", "unknown"),
            "pr_url": deployment_data.get("pr_url", "N/A"),
        }

    def _get_agents_involved(self, messages: list) -> list:
        """Get list of unique agents that participated"""
        agents = set()
        for msg in messages:
            if msg.channel != "monitoring.status":
                agents.add(msg.sender)
        return sorted(list(agents))

    async def _generate_report(
        self,
        incident_id: str,
        incident_summary: dict,
        activity_log: list,
        debate_log: list,
        deployment_data: dict,
    ) -> str:
        """Use LLM to generate a professional postmortem report"""

        system_prompt = (
            "You are PostmortemAgent, an expert SRE who writes clear, "
            "professional incident postmortem reports.\n\n"
            "Generate a comprehensive postmortem report in markdown format.\n\n"
            "Include ALL of these sections:\n\n"
            "1. **Executive Summary** (2-3 sentences — what happened, impact, resolution)\n"
            "2. **Incident Overview** (table with: ID, Severity, Classification, Duration, Status)\n"
            "3. **Timeline** (bullet points with timestamps showing each agent action)\n"
            "4. **Root Cause Analysis** (detailed technical explanation)\n"
            "5. **Agent Debate Highlights** (summarize the key debate between agents — "
            "what was challenged, what evidence was presented, how consensus was reached)\n"
            "6. **Resolution** (what fix was applied, risk level, PR link)\n"
            "7. **Impact Assessment** (users affected, blast radius, duration)\n"
            "8. **Lessons Learned** (3 bullet points)\n"
            "9. **Prevention Recommendations** (3 bullet points for future prevention)\n"
            "10. **Agents Involved** (table of agents and their contributions)\n\n"
            "Write in clear, professional language. Use markdown formatting.\n"
            "The report should be thorough but concise — suitable for an engineering team review."
        )

        user_prompt = (
            f"Generate a postmortem report for this incident.\n\n"
            f"## Incident Summary\n"
            f"{json.dumps(incident_summary, indent=2)}\n\n"
            f"## Agent Activity Log ({len(activity_log)} events)\n"
            f"{json.dumps(activity_log[:15], indent=2)}\n\n"
            f"## Agent Debate Log ({len(debate_log)} rounds)\n"
            f"{json.dumps(debate_log, indent=2)}\n\n"
            f"## Deployment Result\n"
            f"{json.dumps(deployment_data, indent=2)}\n\n"
            f"Generate the complete postmortem report now."
        )

        try:
            report = await chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.3,
                max_tokens=3000,
            )
            return report

        except Exception as e:
            self.logger.error(f"LLM error generating postmortem: {e}")
            # Fallback: generate a basic report without LLM
            return self._generate_fallback_report(
                incident_id=incident_id,
                incident_summary=incident_summary,
                activity_log=activity_log,
                debate_log=debate_log,
                deployment_data=deployment_data,
            )

    def _generate_fallback_report(
        self,
        incident_id: str,
        incident_summary: dict,
        activity_log: list,
        debate_log: list,
        deployment_data: dict,
    ) -> str:
        """Generate a basic postmortem if LLM is unavailable"""

        # Build timeline string
        timeline_lines = ""
        for event in activity_log:
            timestamp = event.get("timestamp", "")
            # Extract just the time portion
            time_str = timestamp[-13:-1] if len(timestamp) > 13 else timestamp
            agent = event.get("agent", "Unknown")
            summary = event.get("summary", "N/A")
            timeline_lines += f"- **{time_str}** | {agent} | {summary}\n"

        # Build debate string
        debate_lines = ""
        if debate_log:
            for entry in debate_log:
                agent = entry.get("agent", "Unknown")
                msg_type = entry.get("type", "unknown").upper()
                content = entry.get("content", "N/A")
                # Truncate long content
                if len(content) > 200:
                    content = content[:200] + "..."
                debate_lines += f"- **{agent}** ({msg_type}): {content}\n"
        else:
            debate_lines = "- No debate occurred — agents agreed on root cause.\n"

        # Build root cause string
        root_cause = incident_summary.get("root_cause", {})
        if isinstance(root_cause, dict):
            rc_detail = root_cause.get("detail", "Unknown")
            rc_mechanism = root_cause.get("mechanism", "Unknown")
            rc_component = root_cause.get("component", "Unknown")
            rc_file = root_cause.get("file", "Unknown")
        else:
            rc_detail = str(root_cause)
            rc_mechanism = "N/A"
            rc_component = "N/A"
            rc_file = "N/A"

        # Build agents involved
        agents_seen = set()
        for event in activity_log:
            agents_seen.add(event.get("agent", "Unknown"))
        agents_table = ""
        agent_roles = {
            "WatcherAgent": "Continuous monitoring and anomaly detection",
            "OrchestratorAgent": "Agent coordination and workflow management",
            "TriageAgent": "Severity classification and impact assessment",
            "DiagnosisAgent": "Root cause analysis with evidence gathering",
            "ResolutionAgent": "Code fix generation and diagnosis validation",
            "DeployAgent": "Fix deployment and GitHub PR creation",
            "PostmortemAgent": "Incident report generation",
        }
        for agent in sorted(agents_seen):
            role = agent_roles.get(agent, "Supporting role")
            agents_table += f"| {agent} | {role} |\n"

        report = (
            f"# Incident Postmortem: {incident_id}\n\n"
            f"---\n\n"
            f"## 1. Executive Summary\n\n"
            f"A **{incident_summary.get('severity', 'P1')}** incident was detected "
            f"by IncidentZero's autonomous AI SRE team. The incident was classified "
            f"as **{incident_summary.get('classification', 'SERVICE_DEGRADATION')}** "
            f"affecting approximately **{incident_summary.get('blast_radius_pct', 'N/A')}%** "
            f"of users. The AI agents collaborated to diagnose the root cause, "
            f"debated the findings, generated a fix, and deployed it — all autonomously.\n\n"
            f"## 2. Incident Overview\n\n"
            f"| Field | Value |\n"
            f"|---|---|\n"
            f"| Incident ID | {incident_id} |\n"
            f"| Severity | {incident_summary.get('severity', 'N/A')} |\n"
            f"| Classification | {incident_summary.get('classification', 'N/A')} |\n"
            f"| Started | {incident_summary.get('started_at', 'N/A')} |\n"
            f"| Resolved | {incident_summary.get('resolved_at', 'N/A')} |\n"
            f"| Deployment Status | {deployment_data.get('status', 'N/A')} |\n"
            f"| Health After Fix | {deployment_data.get('health_check', 'N/A')} |\n"
            f"| Agent Messages | {incident_summary.get('total_agent_messages', 0)} |\n"
            f"| Debate Rounds | {incident_summary.get('debate_rounds', 0)} |\n"
            f"| PR URL | {deployment_data.get('pr_url', 'N/A')} |\n\n"
            f"## 3. Timeline\n\n"
            f"{timeline_lines}\n"
            f"## 4. Root Cause Analysis\n\n"
            f"**Category:** {root_cause.get('category', 'N/A') if isinstance(root_cause, dict) else 'N/A'}\n\n"
            f"**Component:** {rc_component}\n\n"
            f"**File:** {rc_file}\n\n"
            f"**Summary:** {rc_detail}\n\n"
            f"**Mechanism:** {rc_mechanism}\n\n"
            f"## 5. Agent Debate Highlights\n\n"
            f"The following debate occurred between agents to validate the diagnosis:\n\n"
            f"{debate_lines}\n"
            f"## 6. Resolution\n\n"
            f"**Fix:** {incident_summary.get('fix_description', 'N/A')}\n\n"
            f"**Risk Level:** {incident_summary.get('fix_risk_level', 'N/A')}\n\n"
            f"**Deployment:** {deployment_data.get('status', 'N/A')}\n\n"
            f"**GitHub PR:** {deployment_data.get('pr_url', 'N/A')}\n\n"
            f"## 7. Impact Assessment\n\n"
            f"- **Blast Radius:** {incident_summary.get('blast_radius_pct', 'N/A')}% of users\n"
            f"- **Severity:** {incident_summary.get('severity', 'N/A')}\n"
            f"- **Resolution:** Fully autonomous — no human intervention required\n\n"
            f"## 8. Lessons Learned\n\n"
            f"- Always release resources (connections, file handles) in `finally` blocks unconditionally\n"
            f"- Connection pool monitoring should alert before reaching maximum capacity\n"
            f"- Autonomous AI agents can significantly reduce Mean Time to Resolution (MTTR)\n\n"
            f"## 9. Prevention Recommendations\n\n"
            f"- Add connection pool utilization alerts at 70% threshold\n"
            f"- Implement automated connection leak detection in CI/CD pipeline\n"
            f"- Add mandatory code review rules for resource management patterns\n\n"
            f"## 10. Agents Involved\n\n"
            f"| Agent | Role |\n"
            f"|---|---|\n"
            f"{agents_table}\n"
            f"---\n\n"
            f"*Generated by IncidentZero PostmortemAgent at "
            f"{datetime.utcnow().isoformat()}Z*\n"
        )

        return report 