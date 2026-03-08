"""
LLM Service — Supports Azure OpenAI (primary) and OpenAI (fallback).
This is the ONLY file that talks to the AI model.
"""

import json
import logging
from openai import AsyncAzureOpenAI, AsyncOpenAI
from config import config

logger = logging.getLogger("services.llm")

# ─── Initialize Client ────────────────────────────────────
if config.AZURE_OPENAI_KEY and config.USE_AZURE:
    logger.info("Using Azure OpenAI")
    client = AsyncAzureOpenAI(
        api_key=config.AZURE_OPENAI_KEY,
        api_version=config.AZURE_OPENAI_API_VERSION,
        azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
    )
    MODEL = config.AZURE_OPENAI_DEPLOYMENT
elif config.OPENAI_API_KEY:
    logger.info("Using OpenAI (fallback)")
    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    MODEL = "gpt-4o"
else:
    logger.warning("⚠️ No AI API key configured! Using mock responses.")
    client = None
    MODEL = None


async def chat(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 2000,
) -> str:
    """Send a chat completion request to the LLM"""

    # If no client configured, return mock response
    if client is None:
        return _mock_response(system_prompt, user_prompt)

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        result = response.choices[0].message.content
        logger.info(f"LLM response received ({len(result)} chars)")
        return result

    except Exception as e:
        logger.error(f"LLM API error: {e}")
        return _mock_response(system_prompt, user_prompt)


async def chat_json(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
) -> dict:
    """Send a chat request and parse JSON response"""
    response = await chat(system_prompt, user_prompt, temperature)

    # Try to extract JSON from response
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        # Try to find JSON in the response
        try:
            start = response.index("{")
            end = response.rindex("}") + 1
            return json.loads(response[start:end])
        except (ValueError, json.JSONDecodeError):
            logger.warning("Could not parse JSON from LLM response")
            return {"raw_response": response}


def _mock_response(system_prompt: str, user_prompt: str) -> str:
    """Fallback mock responses when no API key is configured"""
    if "triage" in system_prompt.lower():
        return json.dumps({
            "severity": "P1",
            "classification": "SERVICE_DEGRADATION",
            "blast_radius_pct": 42,
            "affected_endpoints": ["/tasks", "/tasks/{id}"],
            "auto_resolve_eligible": True,
            "escalate_to_human": False,
            "reasoning": "Error rate spike to 42% with connection pool exhaustion indicates P1 service degradation."
        })
    elif "diagnosis" in system_prompt.lower() or "diagnos" in system_prompt.lower():
        return json.dumps({
            "root_cause": {
                "category": "RESOURCE_EXHAUSTION",
                "component": "database_connection_pool",
                "file": "app.py",
                "function": "list_tasks / create_task",
                "mechanism": "Database connections not released in finally block when errors occur. The finally block has conditional logic that skips pool.release() in error cases, causing connection pool exhaustion.",
                "detail": "Connection pool leak in error handling path"
            },
            "confidence": 0.88,
            "evidence_analysis": [
                "Connection utilization at 95%+ indicates pool exhaustion",
                "Error rate spike correlates with rising connection count",
                "Pattern matches known connection leak anti-pattern"
            ],
            "alternative_hypotheses": [
                {"category": "SLOW_QUERIES", "confidence": 0.12, "reason": "Query times appear normal"}
            ]
        })
    elif "resolution" in system_prompt.lower() or "fix" in system_prompt.lower():
        return json.dumps({
            "fix": {
                "file": "app.py",
                "description": "Ensure connections are always released in finally block",
                "diff": "--- a/app.py\n+++ b/app.py\n@@ -45,8 +45,4 @@ async def list_tasks():\n     finally:\n-        if conn and BUG_INJECTED:\n-            if random.random() < 0.3:\n-                pool.release(conn)\n-            else:\n-                pass  # LEAKED\n+        if conn is not None:\n+            pool.release(conn)  # Always release connection",
                "risk_level": "LOW",
                "explanation": "The bug was in the finally block where connections were only released 30% of the time when BUG_INJECTED was True. The fix ensures connections are ALWAYS released regardless of any flag.",
                "lines_changed": 4
            },
            "validation_steps": [
                "Verify connection count drops to normal after requests",
                "Test /tasks endpoint returns 200",
                "Confirm no 500 errors under load"
            ]
        })
    elif "challenge" in system_prompt.lower() or "evaluate" in system_prompt.lower():
        return json.dumps({
            "assessment": "AGREE",
            "reasoning": "The diagnosis correctly identifies connection pool exhaustion as the root cause. The evidence clearly shows connections climbing to max without being released. The connection hold time pattern confirms a leak rather than slow queries.",
            "challenge_question": None,
            "alternative_hypothesis": None
        })
    elif "postmortem" in system_prompt.lower():
        return """# Incident Postmortem

## Executive Summary
A connection pool leak in the TaskManager API caused 42% error rate for approximately 2 minutes. The IncidentZero AI SRE team detected, diagnosed, and resolved the issue autonomously.

## Timeline
- **T+0s**: WatcherAgent detected error rate spike (0.5% → 42%)
- **T+10s**: TriageAgent classified as P1 — 42% of users affected
- **T+25s**: DiagnosisAgent identified root cause: connection pool leak
- **T+35s**: ResolutionAgent generated and validated fix
- **T+45s**: DeployAgent created PR and applied fix
- **T+90s**: Application fully recovered

## Root Cause
Connections were not being released in the `finally` block of request handlers. When exceptions occurred, the connection release was skipped due to conditional logic in the error handling path.

## Resolution
Fixed the `finally` block to unconditionally release connections after each request, regardless of success or failure.

## Lessons Learned
- Always release resources in `finally` blocks without conditions
- Connection pool monitoring should alert before pool exhaustion
- Automated resolution reduced MTTR from hours to seconds
"""
    else:
        return json.dumps({"response": "Mock response - configure API keys for real AI responses"})