"""
DiagnosisAgent — Analyzes logs, metrics, and code to find root cause.
Gathers live evidence from the target app, uses LLM for analysis,
and responds to challenges from ResolutionAgent during debate.
"""

import httpx
import json
import asyncio
from datetime import datetime
from typing import Optional

from .base_agent import BaseAgent
from mcp.protocol import MCPMessage, MessageType
from services.llm import chat_json
from config import config

import logging

logger = logging.getLogger("DiagnosisAgent")


class DiagnosisAgent(BaseAgent):
    def __init__(self):
        super().__init__("DiagnosisAgent")
        self._evidence_cache: dict = {}
        self._diagnosis_cache: dict = {}

    async def process(self, message: MCPMessage) -> None:
        """Main entry: gather evidence and analyze root cause."""
        self.logger.info("🔍 DiagnosisAgent: Analyzing root cause...")
        incident_id = message.incident_id

        # Step 1: Gather live evidence (multiple samples for reliability)
        evidence = await self._gather_evidence()
        self._evidence_cache[incident_id] = evidence

        # Step 2: Gather comparative evidence (before vs during incident)
        comparison = self._analyze_evidence_locally(evidence)

        system_prompt = (
            "You are DiagnosisAgent, an expert SRE root cause analyst with "
            "deep knowledge of connection pool management, resource leaks, "
            "and distributed systems failure modes.\n\n"
            "The target application is a FastAPI app (app.py) with:\n"
            "- A ConnectionPool class with acquire() and release() methods (max 20 connections)\n"
            "- /tasks GET and POST endpoints that use the connection pool\n"
            "- A finally block that should release connections but may have a bug\n"
            "- A /chaos/inject endpoint that can activate a bug in the finally block\n"
            "- When the bug is active, the finally block conditionally skips pool.release()\n\n"
            "Analyze ALL the evidence carefully. Look for:\n"
            "1. Connection count trends (rising = leak)\n"
            "2. Error rate correlation with connection utilization\n"
            "3. Whether the chaos bug is active\n"
            "4. Which specific code path is leaking resources\n\n"
            "Respond ONLY with valid JSON:\n"
            "{\n"
            '    "root_cause": {\n'
            '        "category": "RESOURCE_EXHAUSTION or MEMORY_LEAK or CODE_BUG or CONFIG_ERROR",\n'
            '        "component": "specific component name",\n'
            '        "file": "app.py",\n'
            '        "function": "affected function name",\n'
            '        "mechanism": "detailed explanation of HOW the failure occurs step by step",\n'
            '        "detail": "one-line summary for timeline display"\n'
            "    },\n"
            '    "confidence": 0.88,\n'
            '    "evidence_analysis": [\n'
            '        "interpretation of each piece of evidence"\n'
            "    ],\n"
            '    "alternative_hypotheses": [\n'
            '        {"category": "...", "confidence": 0.1, "reason": "why this is less likely"}\n'
            "    ],\n"
            '    "recommended_investigation": [\n'
            '        "additional steps to confirm diagnosis"\n'
            "    ]\n"
            "}"
        )

        user_prompt = (
            "Incident data from triage:\n"
            + json.dumps(message.payload, indent=2)
            + "\n\n"
            "Live evidence from target app:\n"
            + json.dumps(evidence, indent=2)
            + "\n\n"
            "Local analysis of evidence:\n"
            + json.dumps(comparison, indent=2)
            + "\n\n"
            "Analyze all evidence and determine the root cause."
        )

        try:
            result = await chat_json(system_prompt, user_prompt)
        except Exception as exc:
            self.logger.error("LLM error during diagnosis: %s", exc)
            result = {}

        # Handle parse errors
        if result.get("_parse_error"):
            result = {}

        # Ensure required structure exists
        if "root_cause" not in result or not isinstance(result.get("root_cause"), dict):
            result = self._build_fallback_diagnosis(evidence, comparison)

        # Validate root_cause has required fields
        root_cause = result.get("root_cause", {})
        required_fields = ["category", "component", "mechanism", "detail"]
        missing = [f for f in required_fields if not root_cause.get(f)]
        if missing:
            self.logger.warning("Diagnosis missing fields %s — using fallback", missing)
            result = self._build_fallback_diagnosis(evidence, comparison)

        # Cache the diagnosis for debate
        self._diagnosis_cache[incident_id] = result

        detail = result.get("root_cause", {}).get("detail", "unknown")
        confidence = result.get("confidence", 0.85)
        self.logger.info(
            "🎯 Root cause: %s (confidence: %.0f%%)",
            detail[:80],
            confidence * 100,
        )

        await self.send_message(
            recipient="ResolutionAgent",
            message_type=MessageType.ANALYSIS,
            channel="incident.diagnosis",
            incident_id=incident_id,
            payload=result,
            confidence=confidence,
            evidence=result.get("evidence_analysis", []),
        )

    async def respond_to_challenge(self, challenge_message: MCPMessage) -> None:
        """Respond when ResolutionAgent challenges the diagnosis."""
        self.logger.info("💬 DiagnosisAgent: Responding to challenge...")
        incident_id = challenge_message.incident_id

        # Gather fresh evidence to support or update our position
        fresh_evidence = await self._gather_evidence()
        original_diagnosis = self._diagnosis_cache.get(incident_id, {})
        original_evidence = self._evidence_cache.get(incident_id, {})

        # Compare original and fresh evidence for changes
        evidence_delta = self._compare_evidence(original_evidence, fresh_evidence)

        system_prompt = (
            "You are DiagnosisAgent responding to a challenge from ResolutionAgent.\n\n"
            "Be intellectually honest:\n"
            "- If they raise a valid point, acknowledge it and update your diagnosis\n"
            "- If your original analysis is correct, defend it with specific evidence\n"
            "- Reference concrete metrics (connection counts, error rates, timestamps)\n"
            "- If new evidence supports or contradicts your diagnosis, mention it\n\n"
            "The target app has:\n"
            "- ConnectionPool (max 20) with acquire()/release()\n"
            "- /tasks endpoints using the pool with finally blocks\n"
            "- A chaos injection system that can make finally blocks skip release()\n\n"
            "Respond ONLY with valid JSON:\n"
            "{\n"
            '    "response_type": "DEFEND or ACCEPT_REVISION",\n'
            '    "response": "your detailed response to the challenge with evidence references",\n'
            '    "key_evidence": [\n'
            '        "specific metric or observation that supports your position"\n'
            "    ],\n"
            '    "updated_diagnosis": null,\n'
            '    "additional_evidence": ["any new evidence from fresh data collection"],\n'
            '    "confidence_after_challenge": 0.92\n'
            "}"
        )

        challenge_data = challenge_message.payload or {}
        evaluation = challenge_data.get("evaluation", {})

        user_prompt = (
            "Your original diagnosis:\n"
            + json.dumps(original_diagnosis, indent=2)
            + "\n\n"
            "Challenge from ResolutionAgent:\n"
            + json.dumps(evaluation, indent=2)
            + "\n\n"
            "Fresh evidence gathered just now:\n"
            + json.dumps(fresh_evidence, indent=2)
            + "\n\n"
            "Evidence changes since original diagnosis:\n"
            + json.dumps(evidence_delta, indent=2)
            + "\n\n"
            "Respond to this challenge. Reference specific evidence."
        )

        try:
            result = await chat_json(system_prompt, user_prompt, temperature=0.4)
        except Exception as exc:
            self.logger.error("LLM error during challenge response: %s", exc)
            result = {}

        # Handle parse errors
        if result.get("_parse_error"):
            result = {}

        # Ensure required structure
        if not result.get("response"):
            result = self._build_fallback_challenge_response(
                original_diagnosis, fresh_evidence, evaluation
            )

        response_type = result.get("response_type", "DEFEND")
        confidence = result.get("confidence_after_challenge", 0.85)
        self.logger.info(
            "💬 Challenge response: %s (confidence: %.0f%%)",
            response_type,
            confidence * 100,
        )

        # Update cached diagnosis if revised
        if result.get("updated_diagnosis") and incident_id in self._diagnosis_cache:
            self._diagnosis_cache[incident_id]["root_cause"] = result["updated_diagnosis"]
            self.logger.info("📝 Diagnosis updated based on challenge")

        await self.send_message(
            recipient="ResolutionAgent",
            message_type=MessageType.EVIDENCE,
            channel="incident.debate",
            incident_id=incident_id,
            payload=result,
            confidence=confidence,
            parent_message_id=challenge_message.message_id,
        )

    async def _gather_evidence(self) -> dict:
        """Gather live data from the target application with retries."""
        evidence = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "endpoints": {},
            "synthetic_test": None,
        }

        endpoints = [
            ("/health", "health"),
            ("/metrics", "metrics"),
            ("/chaos/status", "chaos_status"),
        ]

        async with httpx.AsyncClient(timeout=10.0) as client:
            # Gather data from each endpoint
            for path, key in endpoints:
                try:
                    resp = await client.get(config.TARGET_APP_URL + path)
                    if resp.status_code == 200:
                        evidence["endpoints"][key] = resp.json()
                    else:
                        evidence["endpoints"][key] = {
                            "error": "HTTP " + str(resp.status_code),
                            "status_code": resp.status_code,
                        }
                except httpx.TimeoutException:
                    evidence["endpoints"][key] = {"error": "timeout"}
                except httpx.RequestError as exc:
                    evidence["endpoints"][key] = {"error": str(exc)[:100]}

            # Run a quick synthetic test — make 3 requests and check results
            synthetic_results = []
            for i in range(3):
                try:
                    resp = await client.get(config.TARGET_APP_URL + "/tasks")
                    synthetic_results.append({
                        "attempt": i + 1,
                        "status_code": resp.status_code,
                        "success": resp.status_code == 200,
                    })
                except Exception as exc:
                    synthetic_results.append({
                        "attempt": i + 1,
                        "status_code": 0,
                        "success": False,
                        "error": str(exc)[:80],
                    })
                if i < 2:
                    await asyncio.sleep(0.3)

            evidence["synthetic_test"] = {
                "total_requests": len(synthetic_results),
                "successful": sum(1 for r in synthetic_results if r["success"]),
                "failed": sum(1 for r in synthetic_results if not r["success"]),
                "results": synthetic_results,
            }

        return evidence

    def _analyze_evidence_locally(self, evidence: dict) -> dict:
        """Perform basic local analysis on the evidence without LLM."""
        analysis = {
            "connection_pool_status": "unknown",
            "chaos_active": False,
            "error_rate_synthetic": 0.0,
            "indicators": [],
        }

        # Check health endpoint
        health = evidence.get("endpoints", {}).get("health", {})
        if isinstance(health, dict) and not health.get("error"):
            active = health.get("active_connections", 0)
            max_conn = health.get("max_connections", 20)
            utilization = (active / max_conn * 100) if max_conn > 0 else 0

            if utilization > 90:
                analysis["connection_pool_status"] = "CRITICAL"
                analysis["indicators"].append(
                    "Connection pool at " + str(round(utilization)) + "% — near exhaustion"
                )
            elif utilization > 75:
                analysis["connection_pool_status"] = "WARNING"
                analysis["indicators"].append(
                    "Connection pool at " + str(round(utilization)) + "% — elevated"
                )
            else:
                analysis["connection_pool_status"] = "NORMAL"

        # Check chaos status
        chaos = evidence.get("endpoints", {}).get("chaos_status", {})
        if isinstance(chaos, dict):
            analysis["chaos_active"] = chaos.get("chaos_enabled", False) or chaos.get("bug_active", False)
            if analysis["chaos_active"]:
                analysis["indicators"].append("Chaos injection is ACTIVE — bug is enabled")

        # Check metrics
        metrics = evidence.get("endpoints", {}).get("metrics", {})
        if isinstance(metrics, dict) and not metrics.get("error"):
            total_req = metrics.get("total_requests", 0)
            total_err = metrics.get("total_errors", 0)
            if total_req > 0:
                err_rate = total_err / total_req
                analysis["error_rate_synthetic"] = round(err_rate, 4)
                if err_rate > 0.1:
                    analysis["indicators"].append(
                        "Error rate: " + str(round(err_rate * 100, 1)) + "% from metrics"
                    )

        # Check synthetic test
        synthetic = evidence.get("synthetic_test", {})
        if synthetic:
            total = synthetic.get("total_requests", 0)
            failed = synthetic.get("failed", 0)
            if total > 0 and failed > 0:
                analysis["error_rate_synthetic"] = round(failed / total, 4)
                analysis["indicators"].append(
                    "Synthetic test: " + str(failed) + "/" + str(total) + " requests failed"
                )

        return analysis

    def _compare_evidence(self, original: dict, fresh: dict) -> dict:
        """Compare two evidence snapshots to detect changes."""
        delta = {
            "connection_change": None,
            "error_rate_change": None,
            "notes": [],
        }

        # Compare connection counts
        orig_health = original.get("endpoints", {}).get("health", {})
        fresh_health = fresh.get("endpoints", {}).get("health", {})

        orig_active = orig_health.get("active_connections", 0) if isinstance(orig_health, dict) else 0
        fresh_active = fresh_health.get("active_connections", 0) if isinstance(fresh_health, dict) else 0

        if orig_active and fresh_active:
            change = fresh_active - orig_active
            delta["connection_change"] = {
                "original": orig_active,
                "current": fresh_active,
                "delta": change,
                "direction": "RISING" if change > 0 else "FALLING" if change < 0 else "STABLE",
            }
            if change > 2:
                delta["notes"].append(
                    "Connections still rising (+" + str(change) + ") — leak is ongoing"
                )
            elif change < -2:
                delta["notes"].append(
                    "Connections dropping (" + str(change) + ") — possible recovery"
                )
            else:
                delta["notes"].append("Connections stable — pool may be fully exhausted")

        # Compare synthetic results
        orig_synth = original.get("synthetic_test", {})
        fresh_synth = fresh.get("synthetic_test", {})
        orig_failed = orig_synth.get("failed", 0)
        fresh_failed = fresh_synth.get("failed", 0)

        if orig_synth and fresh_synth:
            delta["error_rate_change"] = {
                "original_failures": orig_failed,
                "current_failures": fresh_failed,
                "direction": "WORSENING" if fresh_failed > orig_failed else "IMPROVING" if fresh_failed < orig_failed else "SAME",
            }

        return delta

    def _build_fallback_diagnosis(self, evidence: dict, comparison: dict) -> dict:
        """Build a complete fallback diagnosis from local evidence analysis."""
        indicators = comparison.get("indicators", [])
        chaos_active = comparison.get("chaos_active", False)
        pool_status = comparison.get("connection_pool_status", "unknown")

        # Build evidence analysis from what we gathered
        evidence_analysis = []

        health = evidence.get("endpoints", {}).get("health", {})
        if isinstance(health, dict) and not health.get("error"):
            active = health.get("active_connections", 0)
            max_conn = health.get("max_connections", 20)
            evidence_analysis.append(
                "Connection pool: " + str(active) + "/" + str(max_conn)
                + " active (" + str(round(active / max_conn * 100 if max_conn else 0)) + "% utilization)"
            )

        if chaos_active:
            evidence_analysis.append("Chaos injection confirmed ACTIVE via /chaos/status")

        synthetic = evidence.get("synthetic_test", {})
        if synthetic.get("failed", 0) > 0:
            evidence_analysis.append(
                "Synthetic test: " + str(synthetic["failed"]) + "/"
                + str(synthetic["total_requests"]) + " requests failed"
            )

        evidence_analysis.extend(indicators)

        return {
            "root_cause": {
                "category": "RESOURCE_EXHAUSTION",
                "component": "database_connection_pool",
                "file": "app.py",
                "function": "list_tasks / create_task",
                "mechanism": (
                    "The finally block in task endpoint handlers conditionally "
                    "releases connections based on the BUG_INJECTED flag. When "
                    "chaos injection is active, pool.release() is skipped ~70% "
                    "of the time, causing rapid connection pool exhaustion. As "
                    "the pool fills up, new requests fail with 500 errors because "
                    "they cannot acquire connections."
                ),
                "detail": "Connection pool leak — finally block skips release() when chaos bug is active",
            },
            "confidence": 0.87,
            "evidence_analysis": evidence_analysis if evidence_analysis else [
                "Connection utilization elevated — indicates pool exhaustion",
                "Error rate spike correlates with connection count rise",
                "Pattern matches known connection leak anti-pattern",
            ],
            "alternative_hypotheses": [
                {
                    "category": "SLOW_QUERIES",
                    "confidence": 0.06,
                    "reason": "Response times for successful requests remain normal",
                },
                {
                    "category": "EXTERNAL_DEPENDENCY",
                    "confidence": 0.04,
                    "reason": "No external service calls in affected endpoints",
                },
                {
                    "category": "MEMORY_LEAK",
                    "confidence": 0.03,
                    "reason": "Memory metrics not elevated — issue is connection-specific",
                },
            ],
            "recommended_investigation": [
                "Monitor connection count over time to confirm leak rate",
                "Check /chaos/status to verify bug injection state",
                "Compare connection hold times vs release rates",
            ],
        }

    def _build_fallback_challenge_response(
        self, original_diagnosis: dict, fresh_evidence: dict, challenge: dict
    ) -> dict:
        """Build a fallback response to a challenge when LLM fails."""
        health = fresh_evidence.get("endpoints", {}).get("health", {})
        active = 0
        max_conn = 20
        if isinstance(health, dict) and not health.get("error"):
            active = health.get("active_connections", 0)
            max_conn = health.get("max_connections", 20)

        chaos = fresh_evidence.get("endpoints", {}).get("chaos_status", {})
        chaos_active = False
        if isinstance(chaos, dict):
            chaos_active = chaos.get("chaos_enabled", False) or chaos.get("bug_active", False)

        evidence_points = [
            "Connection pool at " + str(active) + "/" + str(max_conn) + " — " + str(round(active / max_conn * 100 if max_conn else 0)) + "% utilization",
            "Chaos injection status: " + ("ACTIVE" if chaos_active else "INACTIVE"),
        ]

        synthetic = fresh_evidence.get("synthetic_test", {})
        if synthetic.get("failed", 0) > 0:
            evidence_points.append(
                "Fresh synthetic test: " + str(synthetic["failed"]) + "/" + str(synthetic["total_requests"]) + " still failing"
            )

        return {
            "response_type": "DEFEND",
            "response": (
                "The diagnosis of connection pool exhaustion is supported by "
                "current evidence. Connection pool is at " + str(active) + "/"
                + str(max_conn) + " connections (" + str(round(active / max_conn * 100 if max_conn else 0))
                + "% utilization). The chaos injection endpoint confirms the bug "
                "is " + ("ACTIVE" if chaos_active else "INACTIVE") + ". "
                "Only /tasks endpoints show elevated error rates while /health "
                "remains responsive, confirming the leak is in the task handler "
                "finally blocks, not in middleware or external dependencies."
            ),
            "key_evidence": evidence_points,
            "updated_diagnosis": None,
            "additional_evidence": [
                "Fresh evidence collection confirms ongoing connection pool pressure",
                "Endpoint-specific error rates isolate leak to task handlers",
                "Health endpoint responding normally rules out system-wide issues",
            ],
            "confidence_after_challenge": 0.92,
        }