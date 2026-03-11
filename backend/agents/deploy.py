"""
DeployAgent — Applies fix to running application and creates GitHub PR.
Handles retries, branch conflicts, and health verification.
"""

import httpx
import base64
import asyncio
from datetime import datetime

from .base_agent import BaseAgent
from mcp.protocol import MCPMessage, MessageType
from config import config

import logging

logger = logging.getLogger("DeployAgent")


class DeployAgent(BaseAgent):
    def __init__(self):
        super().__init__("DeployAgent")
        self.max_health_retries = 5
        self.health_retry_delay = 2.0

    async def process(self, message: MCPMessage) -> None:
        self.logger.info("🚀 DeployAgent: Deploying fix...")
        fix_data = message.payload.get("fix", {})
        incident_id = message.incident_id

        deploy_start = datetime.utcnow()

        # Step 1: Apply fix to running target app (with retries)
        fix_applied = await self._apply_fix_to_target()

        # Step 2: Verify health after fix (with retries)
        health_status = "UNKNOWN"
        if fix_applied:
            health_status = await self._verify_health_with_retries()
        else:
            health_status = "FIX_FAILED"

        # Step 3: Create GitHub PR (if token configured)
        pr_url = await self._create_github_pr(incident_id, fix_data)

        deploy_end = datetime.utcnow()
        deploy_duration = (deploy_end - deploy_start).total_seconds()

        overall_success = fix_applied and health_status == "HEALTHY"

        self.logger.info(
            "%s Fix applied: %s | Health: %s | PR: %s | Duration: %.1fs",
            "✅" if overall_success else "❌",
            fix_applied,
            health_status,
            pr_url[:60] if pr_url else "none",
            deploy_duration,
        )

        await self.send_message(
            recipient="OrchestratorAgent",
            message_type=MessageType.STATUS,
            channel="incident.deployment",
            incident_id=incident_id,
            payload={
                "status": "SUCCESS" if overall_success else "PARTIAL" if fix_applied else "FAILED",
                "fix_applied": fix_applied,
                "health_check": health_status,
                "pr_url": pr_url,
                "deployed_at": deploy_end.isoformat() + "Z",
                "deploy_duration_seconds": round(deploy_duration, 2),
                "target_app_url": config.TARGET_APP_URL,
                "github_repo": config.GITHUB_REPO_OWNER + "/" + config.GITHUB_REPO_NAME if config.GITHUB_REPO_OWNER else "not configured",
            },
            confidence=0.95 if overall_success else 0.6 if fix_applied else 0.2,
        )

    async def _apply_fix_to_target(self) -> bool:
        """Call the target app's /chaos/fix endpoint with retries."""
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(
                        f"{config.TARGET_APP_URL}/chaos/fix"
                    )
                    if resp.status_code == 200:
                        self.logger.info("✅ Fix applied to target app (attempt %d)", attempt + 1)
                        return True
                    self.logger.warning(
                        "Fix endpoint returned %d (attempt %d)",
                        resp.status_code,
                        attempt + 1,
                    )
            except httpx.TimeoutException:
                self.logger.warning("Fix request timed out (attempt %d)", attempt + 1)
            except httpx.RequestError as exc:
                self.logger.warning("Fix request failed (attempt %d): %s", attempt + 1, exc)

            if attempt < 2:
                await asyncio.sleep(2.0)

        self.logger.error("❌ Failed to apply fix after 3 attempts")
        return False

    async def _verify_health_with_retries(self) -> str:
        """Check target app health with retries to allow recovery time."""
        for attempt in range(self.max_health_retries):
            status = await self._check_health()
            if status == "HEALTHY":
                self.logger.info(
                    "✅ Health verified HEALTHY (attempt %d)", attempt + 1
                )
                return "HEALTHY"
            self.logger.info(
                "⏳ Health check: %s (attempt %d/%d)",
                status,
                attempt + 1,
                self.max_health_retries,
            )
            if attempt < self.max_health_retries - 1:
                await asyncio.sleep(self.health_retry_delay)

        self.logger.warning("⚠️ Health not fully recovered after %d checks", self.max_health_retries)
        return status

    async def _check_health(self) -> str:
        """Single health check against the target app."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{config.TARGET_APP_URL}/health")
                if resp.status_code != 200:
                    return "ERROR_" + str(resp.status_code)
                health = resp.json()
                active = health.get("active_connections", 99)
                status = health.get("status", "unknown")
                if status == "healthy" and active < 5:
                    return "HEALTHY"
                elif active < 10:
                    return "RECOVERING"
                else:
                    return "DEGRADED"
        except httpx.TimeoutException:
            return "TIMEOUT"
        except httpx.RequestError:
            return "UNREACHABLE"
        except Exception:
            return "UNKNOWN"

    async def _create_github_pr(self, incident_id: str, fix_data: dict) -> str:
        """Create a GitHub Pull Request with the fix."""
        if not config.GITHUB_TOKEN:
            self.logger.warning("No GitHub token — skipping PR creation")
            return "GitHub token not configured"

        if not config.GITHUB_REPO_OWNER or not config.GITHUB_REPO_NAME:
            self.logger.warning("No GitHub repo configured — skipping PR creation")
            return "GitHub repo not configured"

        headers = {
            "Authorization": f"token {config.GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        base_url = (
            f"https://api.github.com/repos/"
            f"{config.GITHUB_REPO_OWNER}/{config.GITHUB_REPO_NAME}"
        )
        branch_name = f"fix/{incident_id}"
        timestamp_suffix = datetime.utcnow().strftime("%H%M%S")

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                # Step 1: Get default branch SHA
                sha = await self._get_main_branch_sha(client, base_url, headers)
                if not sha:
                    return "GitHub error: cannot get main branch"

                # Step 2: Create branch (handle already exists)
                branch_created = await self._create_branch(
                    client, base_url, headers, branch_name, sha
                )
                if not branch_created:
                    # Branch might already exist from a previous attempt — add suffix
                    branch_name = f"fix/{incident_id}-{timestamp_suffix}"
                    branch_created = await self._create_branch(
                        client, base_url, headers, branch_name, sha
                    )
                    if not branch_created:
                        return "GitHub error: cannot create branch"

                # Step 3: Commit fix file
                fix_content = self._build_fix_file(incident_id, fix_data)
                file_committed = await self._commit_file(
                    client,
                    base_url,
                    headers,
                    file_path=f"fixes/{incident_id}.md",
                    content=fix_content,
                    message=f"fix({incident_id}): {fix_data.get('description', 'Auto-fix by IncidentZero')}",
                    branch=branch_name,
                )
                if not file_committed:
                    return "GitHub error: cannot commit fix file"

                # Step 4: Create Pull Request
                pr_url = await self._create_pull_request(
                    client, base_url, headers, incident_id, fix_data, branch_name
                )
                return pr_url

            except httpx.TimeoutException:
                self.logger.error("GitHub API timed out")
                return "GitHub error: timeout"
            except httpx.RequestError as exc:
                self.logger.error("GitHub API request failed: %s", exc)
                return "GitHub error: " + str(exc)
            except Exception as exc:
                self.logger.error("GitHub PR unexpected error: %s", exc)
                return "GitHub error: " + str(exc)

    async def _get_main_branch_sha(
        self, client: httpx.AsyncClient, base_url: str, headers: dict
    ) -> str:
        """Get the SHA of the main branch."""
        # Try 'main' first, then 'master'
        for branch in ("main", "master"):
            resp = await client.get(
                f"{base_url}/git/ref/heads/{branch}", headers=headers
            )
            if resp.status_code == 200:
                sha = resp.json()["object"]["sha"]
                self.logger.info("Found default branch '%s' at SHA %s", branch, sha[:8])
                return sha

        self.logger.error("Cannot find main or master branch")
        return ""

    async def _create_branch(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict,
        branch_name: str,
        sha: str,
    ) -> bool:
        """Create a new branch from the given SHA."""
        resp = await client.post(
            f"{base_url}/git/refs",
            headers=headers,
            json={
                "ref": f"refs/heads/{branch_name}",
                "sha": sha,
            },
        )
        if resp.status_code == 201:
            self.logger.info("Created branch: %s", branch_name)
            return True
        if resp.status_code == 422:
            self.logger.warning("Branch '%s' already exists", branch_name)
            return False
        self.logger.error(
            "Failed to create branch '%s': %d — %s",
            branch_name,
            resp.status_code,
            resp.text[:200],
        )
        return False

    async def _commit_file(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict,
        file_path: str,
        content: str,
        message: str,
        branch: str,
    ) -> bool:
        """Commit a file to the given branch."""
        content_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")

        # Check if file already exists (need SHA to update)
        existing_sha = None
        resp = await client.get(
            f"{base_url}/contents/{file_path}",
            headers=headers,
            params={"ref": branch},
        )
        if resp.status_code == 200:
            existing_sha = resp.json().get("sha")

        payload = {
            "message": message,
            "content": content_b64,
            "branch": branch,
        }
        if existing_sha:
            payload["sha"] = existing_sha

        resp = await client.put(
            f"{base_url}/contents/{file_path}",
            headers=headers,
            json=payload,
        )
        if resp.status_code in (200, 201):
            self.logger.info("Committed file: %s", file_path)
            return True
        self.logger.error(
            "Failed to commit '%s': %d — %s",
            file_path,
            resp.status_code,
            resp.text[:200],
        )
        return False

    async def _create_pull_request(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict,
        incident_id: str,
        fix_data: dict,
        branch_name: str,
    ) -> str:
        """Create the Pull Request on GitHub."""
        pr_body = self._build_pr_body(incident_id, fix_data)
        pr_title = (
            "🤖 Auto-Fix: "
            + incident_id
            + " — "
            + fix_data.get("description", "Incident fix")
        )

        resp = await client.post(
            f"{base_url}/pulls",
            headers=headers,
            json={
                "title": pr_title,
                "body": pr_body,
                "head": branch_name,
                "base": "main",
            },
        )

        if resp.status_code == 201:
            pr_url = resp.json().get("html_url", "PR created")
            self.logger.info("✅ GitHub PR created: %s", pr_url)
            return pr_url

        # If 'main' doesn't exist as base, try 'master'
        if resp.status_code == 422 and "base" in resp.text.lower():
            resp = await client.post(
                f"{base_url}/pulls",
                headers=headers,
                json={
                    "title": pr_title,
                    "body": pr_body,
                    "head": branch_name,
                    "base": "master",
                },
            )
            if resp.status_code == 201:
                pr_url = resp.json().get("html_url", "PR created")
                self.logger.info("✅ GitHub PR created (master base): %s", pr_url)
                return pr_url

        self.logger.error(
            "Failed to create PR: %d — %s",
            resp.status_code,
            resp.text[:200],
        )
        return "PR creation failed: " + str(resp.status_code)

    def _build_fix_file(self, incident_id: str, fix_data: dict) -> str:
        """Build markdown content for the fix file committed to GitHub."""
        description = fix_data.get("description", "N/A")
        explanation = fix_data.get("explanation", "N/A")
        diff = fix_data.get("diff", "N/A")
        risk_level = fix_data.get("risk_level", "N/A")
        target_file = fix_data.get("file", "N/A")
        timestamp = datetime.utcnow().isoformat() + "Z"

        diff_block = "```diff\n" + diff + "\n```"

        content = (
            "# Incident Fix: " + incident_id + "\n\n"
            "**Generated by IncidentZero AI SRE Team**\n\n"
            "| Field | Value |\n"
            "|---|---|\n"
            "| Timestamp | " + timestamp + " |\n"
            "| Target File | `" + target_file + "` |\n"
            "| Risk Level | **" + risk_level + "** |\n\n"
            "---\n\n"
            "## Description\n\n"
            + description + "\n\n"
            "## Explanation\n\n"
            + explanation + "\n\n"
            "## Code Diff\n\n"
            + diff_block + "\n\n"
            "---\n\n"
            "*Auto-generated by IncidentZero — Autonomous AI SRE Team*\n"
        )

        return content

    def _build_pr_body(self, incident_id: str, fix_data: dict) -> str:
        """Build the Pull Request body with full incident context."""
        description = fix_data.get("description", "N/A")
        explanation = fix_data.get("explanation", "N/A")
        diff = fix_data.get("diff", "N/A")
        risk_level = fix_data.get("risk_level", "N/A")
        lines_changed = fix_data.get("lines_changed", "N/A")
        target_file = fix_data.get("file", "N/A")

        diff_block = "```diff\n" + diff + "\n```"

        repo_url = ""
        if config.GITHUB_REPO_OWNER and config.GITHUB_REPO_NAME:
            repo_url = (
                "https://github.com/"
                + config.GITHUB_REPO_OWNER
                + "/"
                + config.GITHUB_REPO_NAME
            )

        body = (
            "## \U0001f6a8 Incident: " + incident_id + "\n\n"
            "### \U0001f4dd Description\n\n"
            + description + "\n\n"
            "### \U0001f3af Root Cause\n\n"
            + explanation + "\n\n"
            "### \U0001f527 Fix Applied\n\n"
            "**File:** `" + target_file + "`\n\n"
            + diff_block + "\n\n"
            "### \u26a1 Risk Assessment\n\n"
            "| Metric | Value |\n"
            "|---|---|\n"
            "| Risk Level | **" + risk_level + "** |\n"
            "| Lines Changed | " + str(lines_changed) + " |\n"
            "| Target File | `" + target_file + "` |\n"
            "| Auto-Generated | Yes |\n\n"
            "### \U0001f916 Agents Involved\n\n"
            "| Agent | Role |\n"
            "|---|---|\n"
            "| \U0001f441\ufe0f WatcherAgent | Detected anomaly |\n"
            "| \U0001f4cb TriageAgent | Classified severity |\n"
            "| \U0001f50d DiagnosisAgent | Identified root cause |\n"
            "| \U0001f527 ResolutionAgent | Generated this fix |\n"
            "| \U0001f680 DeployAgent | Created this PR |\n"
            "| \U0001f4cb PostmortemAgent | Will generate report |\n\n"
            "### \u2694\ufe0f Agent Debate\n\n"
            "ResolutionAgent critically evaluated the diagnosis from "
            "DiagnosisAgent before generating this fix. Agents debated "
            "the root cause to ensure the fix addresses the actual problem.\n\n"
            "---\n\n"
            "*\U0001f916 Auto-generated by [IncidentZero]"
            "(" + repo_url + ")"
            " — Autonomous AI SRE Team*\n"
        )

        return body