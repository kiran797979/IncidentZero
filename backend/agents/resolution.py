"""
ResolutionAgent — Generates code fixes.
ALSO debates with DiagnosisAgent before fixing (the key differentiator).

Flow:
  1. Receive diagnosis from DiagnosisAgent
  2. Critically evaluate (challenge or agree) — DEBATE
  3. Wait for DiagnosisAgent response if challenged
  4. Generate targeted code fix
  5. Send fix to DeployAgent
"""

import json
import asyncio
from typing import Optional

from .base_agent import BaseAgent
from mcp.protocol import MCPMessage, MessageType
from services.llm import chat_json

import logging

logger = logging.getLogger("ResolutionAgent")


class ResolutionAgent(BaseAgent):
    def __init__(self):
        super().__init__("ResolutionAgent")
        self._debate_rounds: dict = {}
        self._max_debate_rounds = 2
        self._challenge_responses: dict = {}

    async def process(self, diagnosis_message: MCPMessage) -> None:
        """Main entry: receive diagnosis → debate → generate fix."""
        self.logger.info("🔧 ResolutionAgent: Evaluating diagnosis...")
        incident_id = diagnosis_message.incident_id

        # Initialize debate tracking
        self._debate_rounds[incident_id] = 0

        # Step 1: Challenge the diagnosis (creates the debate)
        debate_result = await self._challenge_diagnosis(diagnosis_message)

        # Step 2: If we challenged and got a response, evaluate it
        if debate_result and debate_result.get("assessment") == "CHALLENGE":
            # Wait briefly for DiagnosisAgent response
            response = await self._wait_for_challenge_response(incident_id)
            if response:
                await self._evaluate_challenge_response(
                    diagnosis_message, debate_result, response
                )

        # Step 3: Generate the fix
        await self._generate_fix(diagnosis_message)

    async def _challenge_diagnosis(self, diagnosis_message: MCPMessage) -> Optional[dict]:
        """Critically evaluate the diagnosis — this creates the debate."""
        diagnosis = diagnosis_message.payload
        incident_id = diagnosis_message.incident_id

        system_prompt = (
            "You are ResolutionAgent, a senior SRE who critically evaluates "
            "diagnoses before allowing fixes to be deployed to production.\n\n"
            "Your job is to play devil's advocate: Is this REALLY the root cause? "
            "Could it be something else? Are there gaps in the evidence?\n\n"
            "If the diagnosis seems solid with strong evidence, AGREE and explain why.\n"
            "If you have legitimate doubts, CHALLENGE with a specific question.\n\n"
            "Respond ONLY with valid JSON:\n"
            "{\n"
            '    "assessment": "AGREE or CHALLENGE",\n'
            '    "reasoning": "detailed explanation of your assessment",\n'
            '    "confidence_in_diagnosis": 0.0 to 1.0,\n'
            '    "challenge_question": "specific question if challenging, null if agreeing",\n'
            '    "alternative_hypothesis": "alternative root cause if challenging, null if agreeing",\n'
            '    "evidence_gaps": ["list of missing evidence, empty if none"]\n'
            "}"
        )

        user_prompt = (
            "Diagnosis from DiagnosisAgent:\n"
            + json.dumps(diagnosis, indent=2)
            + "\n\n"
            "Known facts about the target application:\n"
            "- FastAPI app with a ConnectionPool class (max 20 connections)\n"
            "- acquire() and release() methods on the pool\n"
            "- /tasks endpoints (GET and POST) use the pool\n"
            "- A finally block handles connection cleanup after each request\n"
            "- A chaos injection endpoint can activate a bug\n"
            "- When the bug is active, the finally block conditionally skips release()\n\n"
            "Critically evaluate this diagnosis. Consider:\n"
            "1. Does the evidence strongly support the claimed root cause?\n"
            "2. Could the symptoms be explained by something else?\n"
            "3. Is the confidence level justified by the evidence?\n"
            "4. Are there any gaps in the analysis?"
        )

        try:
            result = await chat_json(system_prompt, user_prompt, temperature=0.5)
        except Exception as exc:
            self.logger.error("LLM error during debate evaluation: %s", exc)
            result = {
                "assessment": "AGREE",
                "reasoning": "Unable to perform detailed evaluation due to LLM error. Proceeding with diagnosis.",
                "confidence_in_diagnosis": 0.7,
                "challenge_question": None,
                "alternative_hypothesis": None,
                "evidence_gaps": [],
            }

        # Handle parse errors from chat_json
        if result.get("_parse_error"):
            result = {
                "assessment": "AGREE",
                "reasoning": "Could not parse evaluation response. Proceeding with diagnosis.",
                "confidence_in_diagnosis": 0.7,
                "challenge_question": None,
                "alternative_hypothesis": None,
                "evidence_gaps": [],
            }

        assessment = result.get("assessment", "AGREE").upper()
        is_challenge = assessment == "CHALLENGE"

        self.logger.info(
            "%s Debate: %s diagnosis (confidence: %.0f%%)",
            "⚔️" if is_challenge else "🤝",
            "CHALLENGING" if is_challenge else "AGREEING with",
            result.get("confidence_in_diagnosis", 0.7) * 100,
        )

        self._debate_rounds[incident_id] = self._debate_rounds.get(incident_id, 0) + 1

        await self.send_message(
            recipient="DiagnosisAgent",
            message_type=MessageType.CHALLENGE if is_challenge else MessageType.CONSENSUS,
            channel="incident.debate",
            incident_id=incident_id,
            payload={
                "evaluation": result,
                "original_diagnosis": diagnosis,
                "debate_round": self._debate_rounds.get(incident_id, 1),
            },
            confidence=0.7 if is_challenge else 0.9,
            parent_message_id=diagnosis_message.message_id,
        )

        return result

    async def _wait_for_challenge_response(
        self, incident_id: str, timeout: float = 10.0
    ) -> Optional[dict]:
        """Wait for DiagnosisAgent to respond to our challenge."""
        self.logger.info("⏳ Waiting for DiagnosisAgent response to challenge...")
        elapsed = 0.0
        interval = 0.5
        while elapsed < timeout:
            if incident_id in self._challenge_responses:
                response = self._challenge_responses.pop(incident_id)
                self.logger.info("📨 Received challenge response from DiagnosisAgent")
                return response
            await asyncio.sleep(interval)
            elapsed += interval
        self.logger.info("⏰ No challenge response received within %.0fs — proceeding", timeout)
        return None

    def receive_challenge_response(self, incident_id: str, response: dict) -> None:
        """Called by orchestrator when DiagnosisAgent responds to a challenge."""
        self._challenge_responses[incident_id] = response

    async def _evaluate_challenge_response(
        self,
        diagnosis_message: MCPMessage,
        original_evaluation: dict,
        diagnosis_response: dict,
    ) -> None:
        """Evaluate DiagnosisAgent's response to our challenge."""
        incident_id = diagnosis_message.incident_id
        self._debate_rounds[incident_id] = self._debate_rounds.get(incident_id, 0) + 1

        # Check if we've reached max debate rounds
        if self._debate_rounds.get(incident_id, 0) >= self._max_debate_rounds:
            self.logger.info(
                "🤝 Max debate rounds (%d) reached — reaching consensus",
                self._max_debate_rounds,
            )
            await self.send_message(
                recipient="DiagnosisAgent",
                message_type=MessageType.CONSENSUS,
                channel="incident.debate",
                incident_id=incident_id,
                payload={
                    "evaluation": {
                        "assessment": "AGREE",
                        "reasoning": (
                            "After " + str(self._debate_rounds[incident_id])
                            + " rounds of debate, the diagnosis is accepted. "
                            + "DiagnosisAgent provided sufficient evidence: "
                            + str(diagnosis_response.get("response", ""))[:200]
                        ),
                        "confidence_in_diagnosis": 0.85,
                    },
                    "debate_round": self._debate_rounds[incident_id],
                    "debate_concluded": True,
                },
                confidence=0.85,
            )
            return

        # Evaluate the response
        system_prompt = (
            "You are ResolutionAgent evaluating DiagnosisAgent's response to your challenge.\n\n"
            "Based on the new evidence provided, decide:\n"
            "- AGREE: The response adequately addresses your concerns\n"
            "- CHALLENGE: You still have significant doubts\n\n"
            "Respond ONLY with valid JSON:\n"
            "{\n"
            '    "assessment": "AGREE or CHALLENGE",\n'
            '    "reasoning": "explanation",\n'
            '    "confidence_in_diagnosis": 0.0 to 1.0\n'
            "}"
        )

        user_prompt = (
            "Your original challenge:\n"
            + json.dumps(original_evaluation, indent=2)
            + "\n\nDiagnosisAgent's response:\n"
            + json.dumps(diagnosis_response, indent=2)
            + "\n\nDo you accept the diagnosis now?"
        )

        try:
            result = await chat_json(system_prompt, user_prompt, temperature=0.3)
        except Exception:
            result = {
                "assessment": "AGREE",
                "reasoning": "Accepting diagnosis after challenge response.",
                "confidence_in_diagnosis": 0.8,
            }

        if result.get("_parse_error"):
            result = {
                "assessment": "AGREE",
                "reasoning": "Accepting diagnosis after challenge response.",
                "confidence_in_diagnosis": 0.8,
            }

        is_still_challenging = result.get("assessment", "AGREE").upper() == "CHALLENGE"

        await self.send_message(
            recipient="DiagnosisAgent",
            message_type=MessageType.CHALLENGE if is_still_challenging else MessageType.CONSENSUS,
            channel="incident.debate",
            incident_id=incident_id,
            payload={
                "evaluation": result,
                "debate_round": self._debate_rounds[incident_id],
                "debate_concluded": not is_still_challenging,
            },
            confidence=result.get("confidence_in_diagnosis", 0.8),
        )

    async def _generate_fix(self, diagnosis_message: MCPMessage) -> None:
        """Generate a targeted code fix based on the diagnosis."""
        self.logger.info("🔧 ResolutionAgent: Generating code fix...")
        diagnosis = diagnosis_message.payload
        incident_id = diagnosis_message.incident_id
        root_cause = diagnosis.get("root_cause", {})

        system_prompt = (
            "You are ResolutionAgent, an expert developer who generates "
            "minimal, safe code fixes for production incidents.\n\n"
            "Rules:\n"
            "1. Fix ONLY the specific bug — no refactoring, no extra changes\n"
            "2. The fix must be backward compatible\n"
            "3. Generate a unified diff format\n"
            "4. Add a brief code comment explaining the fix\n"
            "5. Prefer the simplest possible fix\n\n"
            "The target app is a FastAPI application with a ConnectionPool.\n"
            "The bug is in the finally blocks of /tasks endpoint handlers.\n"
            "The current buggy code conditionally releases connections based on\n"
            "a BUG_INJECTED flag, which causes connection pool exhaustion.\n\n"
            "The correct fix: always release the connection in the finally block,\n"
            "unconditionally, using standard resource cleanup pattern.\n\n"
            "Respond ONLY with valid JSON:\n"
            "{\n"
            '    "fix": {\n'
            '        "file": "app.py",\n'
            '        "description": "brief description of the fix",\n'
            '        "diff": "unified diff content",\n'
            '        "risk_level": "LOW or MEDIUM or HIGH",\n'
            '        "explanation": "plain English explanation for the PR",\n'
            '        "lines_changed": 4,\n'
            '        "rollback_plan": "how to revert if fix fails"\n'
            "    },\n"
            '    "validation_steps": ["list of things to verify after deployment"]\n'
            "}"
        )

        user_prompt = (
            "Root cause analysis:\n"
            + json.dumps(root_cause, indent=2)
            + "\n\n"
            "Debate conclusion: "
            + str(self._debate_rounds.get(incident_id, 0))
            + " rounds of debate completed.\n\n"
            "Generate a targeted, safe, minimal code fix."
        )

        try:
            result = await chat_json(system_prompt, user_prompt)
        except Exception as exc:
            self.logger.error("LLM error during fix generation: %s", exc)
            result = {}

        # Ensure fix structure exists with complete fallback
        if "fix" not in result or result.get("_parse_error"):
            self.logger.info("Using fallback fix (LLM response incomplete)")
            result = self._build_fallback_fix()

        # Validate fix structure has all required fields
        fix = result.get("fix", {})
        if not isinstance(fix, dict):
            self.logger.warning("Fix is not a dict — using fallback")
            result = self._build_fallback_fix()
            fix = result["fix"]

        required_fields = ["file", "description", "diff", "risk_level", "explanation"]
        missing = [f for f in required_fields if not fix.get(f)]
        if missing:
            self.logger.warning("Fix missing fields %s — using fallback", missing)
            result = self._build_fallback_fix()
            fix = result["fix"]

        description = fix.get("description", "N/A")
        risk_level = fix.get("risk_level", "UNKNOWN")
        lines_changed = fix.get("lines_changed", "N/A")

        self.logger.info(
            "🔧 Fix generated: %s (risk: %s, lines: %s)",
            description[:60],
            risk_level,
            lines_changed,
        )

        await self.send_message(
            recipient="DeployAgent",
            message_type=MessageType.PROPOSAL,
            channel="incident.resolution",
            incident_id=incident_id,
            payload=result,
            confidence=0.92,
        )

    def _build_fallback_fix(self) -> dict:
        """Build a complete fallback fix when LLM fails or returns incomplete data."""
        return {
            "fix": {
                "file": "app.py",
                "description": "Fix connection pool leak in finally blocks",
                "diff": (
                    "--- a/app.py\n"
                    "+++ b/app.py\n"
                    "@@ -60,10 +60,4 @@ async def list_tasks():\n"
                    "     finally:\n"
                    "-        if conn is not None:\n"
                    "-            if BUG_INJECTED:\n"
                    "-                if random.random() < 0.3:\n"
                    "-                    pool.release(conn)\n"
                    "-                else:\n"
                    "-                    pass  # Connection LEAKED here\n"
                    "-            else:\n"
                    "-                pool.release(conn)\n"
                    "+        if conn is not None:\n"
                    "+            pool.release(conn)  # Always release — fix leak\n"
                ),
                "risk_level": "LOW",
                "explanation": (
                    "Connections were conditionally released based on the "
                    "BUG_INJECTED flag. When the flag was active, connections "
                    "were only released 30% of the time, causing rapid pool "
                    "exhaustion. The fix ensures unconditional release in the "
                    "finally block, following the standard resource cleanup pattern."
                ),
                "lines_changed": 4,
                "rollback_plan": (
                    "Revert the finally block to the previous conditional "
                    "release logic. This will re-introduce the leak but restore "
                    "the original behavior."
                ),
            },
            "validation_steps": [
                "Verify connection count drops to near zero after requests complete",
                "Test GET /tasks returns 200 consistently under load",
                "Test POST /tasks returns 201 consistently",
                "Confirm no 500 errors after 50 consecutive requests",
                "Check connection pool utilization stays below 50%",
            ],
        }