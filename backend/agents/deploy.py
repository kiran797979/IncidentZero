"""
DeployAgent — Applies fix to running application and creates GitHub PR.
"""

import httpx
import base64
from datetime import datetime

from .base_agent import BaseAgent
from mcp.protocol import MCPMessage, MessageType
from config import config

import logging

logger = logging.getLogger("DeployAgent")


class DeployAgent(BaseAgent):
    def __init__(self):
        super().__init__("DeployAgent")

    async def process(self, message: MCPMessage) -> None:
        self.logger.info("🚀 DeployAgent: Deploying fix...")
        fix_data = message.payload.get("fix", {})
        incident_id = message.incident_id

        # Step 1: Apply fix to running target app
        fix_applied = await self._apply_fix_to_target()

        # Step 2: Create GitHub PR (if token configured)
        pr_url = await self._create_github_pr(incident_id, fix_data)

        # Step 3: Verify health after fix
        health_status = await self._verify_health()

        self.logger.info(
            f"{'✅' if fix_applied else '❌'} Fix applied: {fix_applied}, "
            f"Health: {health_status}"
        )

        await self.send_message(
            recipient="OrchestratorAgent",
            message_type=MessageType.STATUS,
            channel="incident.deployment",
            incident_id=incident_id,
            payload={
                "status": "SUCCESS" if fix_applied else "FAILED",
                "fix_applied": fix_applied,
                "pr_url": pr_url,
                "health_check": health_status,
                "deployed_at": datetime.utcnow().isoformat() + "Z",
            },
            confidence=0.95 if fix_applied else 0.3,
        )

    async def _apply_fix_to_target(self) -> bool:
        """Call the target app's /chaos/fix endpoint to apply the fix"""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{config.TARGET_APP_URL}/chaos/fix")
                return resp.status_code == 200
        except Exception as e:
            self.logger.error(f"Failed to apply fix: {e}")
            return False

    async def _verify_health(self) -> str:
        """Check if the target app is healthy after fix"""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{config.TARGET_APP_URL}/health")
                health = resp.json()
                active = health.get("active_connections", 99)
                if active < 5:
                    return "HEALTHY"
                else:
                    return "DEGRADED"
        except Exception:
            return "UNREACHABLE"

    async def _create_github_pr(self, incident_id: str, fix_data: dict) -> str:
        """Create a GitHub Pull Request with the fix"""
        if not config.GITHUB_TOKEN:
            self.logger.warning("No GitHub token — skipping PR creation")
            return "GitHub token not configured"

        headers = {
            "Authorization": f"token {config.GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }
        base_url = (
            f"https://api.github.com/repos/"
            f"{config.GITHUB_REPO_OWNER}/{config.GITHUB_REPO_NAME}"
        )
        branch_name = f"fix/{incident_id}"

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                # Get default branch SHA
                resp = await client.get(
                    f"{base_url}/git/ref/heads/main", headers=headers
                )
                if resp.status_code != 200:
                    self.logger.error(
                        f"GitHub: Can't get main branch: {resp.status_code}"
                    )
                    return f"GitHub error: {resp.status_code}"

                sha = resp.json()["object"]["sha"]

                # Create branch
                resp = await client.post(
                    f"{base_url}/git/refs",
                    headers=headers,
                    json={
                        "ref": f"refs/heads/{branch_name}",
                        "sha": sha,
                    },
                )

                # Create fix file in repo
                fix_content = self._build_fix_file(incident_id, fix_data)
                content_b64 = base64.b64encode(fix_content.encode()).decode()

                resp = await client.put(
                    f"{base_url}/contents/fixes/{incident_id}.md",
                    headers=headers,
                    json={
                        "message": (
                            f"fix({incident_id}): "
                            f"{fix_data.get('description', 'Auto-fix')}"
                        ),
                        "content": content_b64,
                        "branch": branch_name,
                    },
                )

                # Create Pull Request
                pr_body = self._build_pr_body(incident_id, fix_data)
                resp = await client.post(
                    f"{base_url}/pulls",
                    headers=headers,
                    json={
                        "title": (
                            f"🤖 Auto-Fix: {incident_id} — "
                            f"{fix_data.get('description', 'Incident fix')}"
                        ),
                        "body": pr_body,
                        "head": branch_name,
                        "base": "main",
                    },
                )

                if resp.status_code == 201:
                    pr_url = resp.json().get("html_url", "PR created")
                    self.logger.info(f"✅ GitHub PR created: {pr_url}")
                    return pr_url
                else:
                    return f"PR creation: {resp.status_code}"

            except Exception as e:
                self.logger.error(f"GitHub PR error: {e}")
                return f"Error: {str(e)}"

    def _build_fix_file(self, incident_id: str, fix_data: dict) -> str:
        """Build markdown content for the fix file committed to GitHub"""
        description = fix_data.get("description", "N/A")
        explanation = fix_data.get("explanation", "N/A")
        diff = fix_data.get("diff", "N/A")
        risk_level = fix_data.get("risk_level", "N/A")
        timestamp = datetime.utcnow().isoformat() + "Z"

        # NOTE: We avoid triple backticks inside f-strings by using
        # string concatenation instead
        diff_block = "```diff\n" + diff + "\n```"

        content = (
            f"# Incident Fix: {incident_id}\n\n"
            f"**Generated by IncidentZero AI SRE Team**\n\n"
            f"**Timestamp:** {timestamp}\n\n"
            f"---\n\n"
            f"## Description\n\n"
            f"{description}\n\n"
            f"## Explanation\n\n"
            f"{explanation}\n\n"
            f"## Code Diff\n\n"
            f"{diff_block}\n\n"
            f"## Risk Level\n\n"
            f"**{risk_level}**\n\n"
            f"---\n\n"
            f"*Auto-generated by IncidentZero — Autonomous AI SRE Team*\n"
        )

        return content

    def _build_pr_body(self, incident_id: str, fix_data: dict) -> str:
        """Build the Pull Request body with full incident context"""
        description = fix_data.get("description", "N/A")
        explanation = fix_data.get("explanation", "N/A")
        diff = fix_data.get("diff", "N/A")
        risk_level = fix_data.get("risk_level", "N/A")
        lines_changed = fix_data.get("lines_changed", "N/A")

        diff_block = "```diff\n" + diff + "\n```"

        body = (
            f"## 🚨 Incident: {incident_id}\n\n"
            f"### 📝 Description\n\n"
            f"{description}\n\n"
            f"### 🎯 Root Cause\n\n"
            f"{explanation}\n\n"
            f"### 🔧 Fix Applied\n\n"
            f"{diff_block}\n\n"
            f"### ⚡ Risk Assessment\n\n"
            f"| Metric | Value |\n"
            f"|---|---|\n"
            f"| Risk Level | **{risk_level}** |\n"
            f"| Lines Changed | {lines_changed} |\n"
            f"| Auto-Generated | Yes |\n\n"
            f"### 🤖 Agents Involved\n\n"
            f"| Agent | Role |\n"
            f"|---|---|\n"
            f"| 👁️ WatcherAgent | Detected anomaly |\n"
            f"| 📋 TriageAgent | Classified severity |\n"
            f"| 🔍 DiagnosisAgent | Identified root cause |\n"
            f"| 🔧 ResolutionAgent | Generated this fix |\n"
            f"| 🚀 DeployAgent | Created this PR |\n"
            f"| 📋 PostmortemAgent | Will generate report |\n\n"
            f"### ⚔️ Agent Debate\n\n"
            f"ResolutionAgent critically evaluated the diagnosis from "
            f"DiagnosisAgent before generating this fix. Agents debated "
            f"the root cause to ensure the fix addresses the actual problem.\n\n"
            f"---\n\n"
            f"*🤖 Auto-generated by [IncidentZero]"
            f"(https://github.com/{config.GITHUB_REPO_OWNER}/{config.GITHUB_REPO_NAME})"
            f" — Autonomous AI SRE Team*\n"
        )

        return body