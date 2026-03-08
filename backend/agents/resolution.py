"""
ResolutionAgent — Generates code fixes.
ALSO debates with DiagnosisAgent before fixing (the key differentiator).
"""

import json

from .base_agent import BaseAgent
from mcp.protocol import MCPMessage, MessageType
from services.llm import chat_json


class ResolutionAgent(BaseAgent):
    def __init__(self):
        super().__init__("ResolutionAgent")

    async def process(self, diagnosis_message: MCPMessage) -> None:
        """Main entry: receive diagnosis → debate → generate fix"""
        self.logger.info("🔧 ResolutionAgent: Evaluating diagnosis...")

        # Step 1: Challenge the diagnosis (creates debate)
        await self._challenge_diagnosis(diagnosis_message)

        # Step 2: Generate the fix
        await self._generate_fix(diagnosis_message)

    async def _challenge_diagnosis(self, diagnosis_message: MCPMessage) -> None:
        """Critically evaluate the diagnosis — this creates the debate"""
        diagnosis = diagnosis_message.payload

        system_prompt = """You are ResolutionAgent. Before generating a fix, critically 
evaluate the diagnosis from DiagnosisAgent.

Play devil's advocate: Is this REALLY the root cause? Could it be something else?

If the diagnosis seems solid, agree and explain why.
If you have doubts, raise a specific challenge.

Respond ONLY with valid JSON:
{
    "assessment": "AGREE or CHALLENGE",
    "reasoning": "detailed explanation of your assessment",
    "challenge_question": "specific question if challenging, null if agreeing",
    "alternative_hypothesis": "alternative root cause if challenging, null if agreeing"
}"""

        user_prompt = f"""Diagnosis from DiagnosisAgent:
{json.dumps(diagnosis, indent=2)}

The target app has:
- ConnectionPool with max 20 connections
- acquire() and release() methods  
- /tasks endpoints using the pool
- A finally block for connection cleanup

Critically evaluate this diagnosis."""

        result = await chat_json(system_prompt, user_prompt, temperature=0.5)

        is_challenge = result.get("assessment") == "CHALLENGE"

        await self.send_message(
            recipient="DiagnosisAgent",
            message_type=MessageType.CHALLENGE if is_challenge else MessageType.CONSENSUS,
            channel="incident.debate",
            incident_id=diagnosis_message.incident_id,
            payload={
                "evaluation": result,
                "original_diagnosis": diagnosis,
            },
            confidence=0.7 if is_challenge else 0.9,
            parent_message_id=diagnosis_message.message_id,
        )

    async def _generate_fix(self, diagnosis_message: MCPMessage) -> None:
        """Generate a targeted code fix based on the diagnosis"""
        self.logger.info("🔧 ResolutionAgent: Generating code fix...")
        diagnosis = diagnosis_message.payload
        root_cause = diagnosis.get("root_cause", {})

        system_prompt = """You are ResolutionAgent, an expert developer who generates 
minimal, safe code fixes for production incidents.

Rules:
1. Fix ONLY the specific bug — no refactoring
2. The fix must be backward compatible  
3. Generate a unified diff
4. Add a code comment explaining the fix

The target app's bug is in the finally blocks of /tasks endpoints.
The current buggy code conditionally releases connections based on
a BUG_INJECTED flag, which causes leaks.

The correct fix: always release the connection in finally, unconditionally.

Respond ONLY with valid JSON:
{
    "fix": {
        "file": "app.py",
        "description": "brief description",
        "diff": "unified diff content",
        "risk_level": "LOW or MEDIUM or HIGH",
        "explanation": "plain English explanation for the PR",
        "lines_changed": 4
    },
    "validation_steps": ["list of things to verify"]
}"""

        user_prompt = f"""Root cause:
{json.dumps(root_cause, indent=2)}

Generate a targeted, safe code fix."""

        result = await chat_json(system_prompt, user_prompt)

        # Ensure fix structure exists
        if "fix" not in result:
            result = {
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
                        "-                    pass  # LEAKED\n"
                        "-            else:\n"
                        "-                pool.release(conn)\n"
                        "+        if conn is not None:\n"
                        "+            pool.release(conn)  # Always release\n"
                    ),
                    "risk_level": "LOW",
                    "explanation": "Connections were conditionally released, causing pool exhaustion. Fix ensures unconditional release.",
                    "lines_changed": 4,
                },
                "validation_steps": [
                    "Connection count returns to normal",
                    "/tasks returns 200",
                    "No 500 errors under load",
                ],
            }

        self.logger.info(
            f"🔧 Fix generated: {result.get('fix', {}).get('description', 'N/A')}"
        )

        await self.send_message(
            recipient="DeployAgent",
            message_type=MessageType.PROPOSAL,
            channel="incident.resolution",
            incident_id=diagnosis_message.incident_id,
            payload=result,
            confidence=0.92,
        )