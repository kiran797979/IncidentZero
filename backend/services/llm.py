"""
LLM Service — Supports Azure OpenAI (primary) and OpenAI (fallback).
This is the ONLY file that talks to the AI model.
Includes retry logic, timeout handling, and comprehensive mock responses.
"""

import json
import logging
import asyncio
from typing import Optional

from config import config

logger = logging.getLogger("services.llm")

# ─── Initialize Client ────────────────────────────────────
client = None
MODEL = None
PROVIDER = "mock"


def _initialize_client():
    """Initialize the AI client based on available configuration."""
    global client, MODEL, PROVIDER

    # Priority 1: Azure OpenAI
    if config.has_azure_openai and config.USE_AZURE:
        try:
            from openai import AsyncAzureOpenAI

            client = AsyncAzureOpenAI(
                api_key=config.AZURE_OPENAI_KEY,
                api_version=config.AZURE_OPENAI_API_VERSION,
                azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
                timeout=60.0,
                max_retries=2,
            )
            MODEL = config.AZURE_OPENAI_DEPLOYMENT
            PROVIDER = "azure_openai"
            logger.info(
                "✅ Using Azure OpenAI — endpoint: %s, model: %s",
                config.AZURE_OPENAI_ENDPOINT[:40] + "...",
                MODEL,
            )
            return
        except ImportError:
            logger.warning("openai package not installed — cannot use Azure OpenAI")
        except Exception as exc:
            logger.warning("Failed to init Azure OpenAI client: %s", exc)

    # Priority 2: Direct OpenAI
    if config.has_openai:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=config.OPENAI_API_KEY,
                timeout=60.0,
                max_retries=2,
            )
            MODEL = "gpt-4o"
            PROVIDER = "openai"
            logger.info("✅ Using OpenAI (fallback) — model: %s", MODEL)
            return
        except ImportError:
            logger.warning("openai package not installed — cannot use OpenAI")
        except Exception as exc:
            logger.warning("Failed to init OpenAI client: %s", exc)

    # Priority 3: Mock
    logger.warning(
        "⚠️ No AI API key configured! Using mock responses. "
        "Set AZURE_OPENAI_KEY or OPENAI_API_KEY for real AI."
    )
    client = None
    MODEL = None
    PROVIDER = "mock"


# Initialize on module load
_initialize_client()


def get_provider_info() -> dict:
    """Return current AI provider status — used by /api/status."""
    return {
        "provider": PROVIDER,
        "model": MODEL or "mock",
        "client_initialized": client is not None,
    }


# ─── Main Chat Function ──────────────────────────────────
async def chat(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 2000,
    retries: int = 3,
) -> str:
    """
    Send a chat completion request to the LLM.
    Falls back to mock response if client is None or all retries fail.
    """
    if client is None:
        logger.info("LLM client is None — returning mock response")
        return _mock_response(system_prompt, user_prompt)

    last_error = None

    for attempt in range(1, retries + 1):
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
            if result is None:
                result = ""
            logger.info(
                "LLM response received (%d chars, attempt %d, provider: %s)",
                len(result),
                attempt,
                PROVIDER,
            )
            return result

        except Exception as exc:
            last_error = exc
            error_name = type(exc).__name__
            logger.warning(
                "LLM API error (attempt %d/%d, %s): %s — %s",
                attempt,
                retries,
                PROVIDER,
                error_name,
                str(exc)[:200],
            )
            if attempt < retries:
                wait_time = 2.0 * attempt
                logger.info("Retrying in %.1fs...", wait_time)
                await asyncio.sleep(wait_time)

    logger.error(
        "LLM failed after %d attempts (%s: %s) — using mock response",
        retries,
        type(last_error).__name__,
        str(last_error)[:200],
    )
    return _mock_response(system_prompt, user_prompt)


# ─── JSON Chat Function ──────────────────────────────────
async def chat_json(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 2000,
) -> dict:
    """Send a chat request and parse JSON response."""
    raw = await chat(
        system_prompt,
        user_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    parsed = _extract_json(raw)
    if parsed is not None:
        return parsed

    logger.warning("Could not parse JSON from LLM response — returning wrapped raw")
    return {"raw_response": raw, "_parse_error": True}


def _extract_json(text: str) -> Optional[dict]:
    """Try multiple strategies to extract JSON from LLM response text."""
    if not text or not text.strip():
        return None

    cleaned = text.strip()

    # Strategy 1: Direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Strip markdown code fences
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first line (```json or ```) and last line (```)
        inner_lines = []
        started = False
        for line in lines:
            if not started and line.strip().startswith("```"):
                started = True
                continue
            if started and line.strip() == "```":
                break
            if started:
                inner_lines.append(line)
        if inner_lines:
            try:
                return json.loads("\n".join(inner_lines))
            except json.JSONDecodeError:
                pass

    # Strategy 3: Find first { ... last }
    try:
        start = cleaned.index("{")
        end = cleaned.rindex("}") + 1
        return json.loads(cleaned[start:end])
    except (ValueError, json.JSONDecodeError):
        pass

    # Strategy 4: Find first [ ... last ]
    try:
        start = cleaned.index("[")
        end = cleaned.rindex("]") + 1
        return json.loads(cleaned[start:end])
    except (ValueError, json.JSONDecodeError):
        pass

    return None


# ─── Mock Responses ───────────────────────────────────────
def _mock_response(system_prompt: str, user_prompt: str) -> str:
    """
    Fallback mock responses when no API key is configured.
    Returns realistic responses matching each agent's expected format.
    Uses system_prompt to identify the calling agent unambiguously.
    """
    sys_lower = system_prompt.lower()

    # 1. TriageAgent — "You are TriageAgent..."
    if "triageagent" in sys_lower:
        return json.dumps(
            {
                "severity": "P1",
                "classification": "SERVICE_DEGRADATION",
                "blast_radius_pct": 42,
                "affected_endpoints": ["/tasks", "/tasks/{id}"],
                "auto_resolve_eligible": True,
                "escalate_to_human": False,
                "reasoning": (
                    "Error rate spike to 42% with connection pool exhaustion "
                    "indicates P1 service degradation. Multiple endpoints affected "
                    "but system is not fully down."
                ),
            },
            indent=2,
        )

    # 2. DiagnosisAgent responding to challenge — "You are DiagnosisAgent responding to a challenge..."
    if "diagnosisagent" in sys_lower and "responding to a challenge" in sys_lower:
        return json.dumps(
            {
                "response": (
                    "The connection leak is definitively in the endpoint handler, "
                    "not middleware. Evidence: 1) Only /tasks endpoints show elevated "
                    "error rates while /health remains fast. 2) The chaos/status "
                    "endpoint confirms bug injection is active. 3) Connection count "
                    "rises proportionally to /tasks request volume, not total "
                    "request volume."
                ),
                "additional_evidence": [
                    "Endpoint-specific error rates isolate the leak to task handlers",
                    "Middleware would affect all endpoints equally",
                    "Connection acquisition timestamps correlate with /tasks calls",
                ],
                "confidence_after_challenge": 0.92,
            },
            indent=2,
        )

    # 3. ResolutionAgent debate evaluation — "You are ResolutionAgent...devil's advocate" or "evaluating"
    if "resolutionagent" in sys_lower and ("devil" in sys_lower or "evaluating" in sys_lower):
        return json.dumps(
            {
                "assessment": "CHALLENGE",
                "confidence_adjustment": -0.15,
                "reasoning": (
                    "While connection pool exhaustion is evident, I'm not fully "
                    "convinced the leak originates in the endpoint handler's finally "
                    "block. The evidence shows 100% pool utilization, but we haven't "
                    "ruled out that a middleware layer or background task could be "
                    "holding connections. The error rate correlation is suggestive "
                    "but not conclusive — we need endpoint-specific evidence."
                ),
                "confidence_in_diagnosis": 0.65,
                "challenge_question": (
                    "Have we ruled out that the connection leak could be in a "
                    "middleware layer rather than the endpoint handler itself? "
                    "Can you show endpoint-specific error rates proving only /tasks "
                    "routes are affected?"
                ),
                "alternative_hypothesis": (
                    "The leak could originate from a background task or middleware "
                    "that acquires connections but fails to release them under "
                    "high concurrency, rather than the finally block in handlers."
                ),
                "evidence_gaps": [
                    "No per-endpoint error breakdown provided",
                    "Connection hold duration not measured",
                    "No middleware connection audit"
                ],
            },
            indent=2,
        )

    # 4. DiagnosisAgent root cause analysis — "You are DiagnosisAgent, an expert SRE root cause..."
    if "diagnosisagent" in sys_lower:
        return json.dumps(
            {
                "root_cause": {
                    "category": "RESOURCE_EXHAUSTION",
                    "component": "database_connection_pool",
                    "file": "app.py",
                    "function": "list_tasks / create_task",
                    "mechanism": (
                        "Database connections not released in finally block when "
                        "errors occur. The finally block has conditional logic that "
                        "skips pool.release() in error cases, causing connection "
                        "pool exhaustion under load."
                    ),
                    "detail": "Connection pool leak in error handling path",
                },
                "confidence": 0.88,
                "evidence_analysis": [
                    "Connection utilization at 95%+ indicates pool exhaustion",
                    "Error rate spike correlates with rising connection count",
                    "Pattern matches known connection leak anti-pattern",
                    "Health endpoint shows active_connections near max_connections",
                ],
                "alternative_hypotheses": [
                    {
                        "category": "SLOW_QUERIES",
                        "confidence": 0.08,
                        "reason": "Query times appear normal in metrics",
                    },
                    {
                        "category": "EXTERNAL_DEPENDENCY",
                        "confidence": 0.04,
                        "reason": "No external service calls in affected endpoints",
                    },
                ],
            },
            indent=2,
        )

    # 5. ResolutionAgent fix generation — "You are ResolutionAgent, an expert developer..."
    if "resolutionagent" in sys_lower:
        return json.dumps(
            {
                "fix": {
                    "file": "app.py",
                    "description": "Ensure connections are always released in finally block",
                    "diff": (
                        "--- a/app.py\n"
                        "+++ b/app.py\n"
                        "@@ -45,8 +45,4 @@ async def list_tasks():\n"
                        "     finally:\n"
                        "-        if conn and BUG_INJECTED:\n"
                        "-            if random.random() < 0.3:\n"
                        "-                pool.release(conn)\n"
                        "-            else:\n"
                        "-                pass  # LEAKED\n"
                        "+        if conn is not None:\n"
                        "+            pool.release(conn)  # Always release connection"
                    ),
                    "risk_level": "LOW",
                    "explanation": (
                        "The bug was in the finally block where connections were "
                        "only released 30% of the time when BUG_INJECTED was True. "
                        "The fix ensures connections are ALWAYS released regardless "
                        "of any flag, following the standard resource cleanup pattern."
                    ),
                    "lines_changed": 4,
                    "rollback_plan": "Revert to previous finally block behavior",
                },
                "validation_steps": [
                    "Verify connection count drops to normal after requests",
                    "Test /tasks endpoint returns 200 consistently",
                    "Confirm no 500 errors under sustained load",
                    "Check connection pool utilization stays below 50%",
                ],
            },
            indent=2,
        )

    # 6. PostmortemAgent report — "You are PostmortemAgent..."
    if "postmortemagent" in sys_lower:
        return (
            "# Incident Postmortem\n\n"
            "## Executive Summary\n\n"
            "A connection pool leak in the TaskManager API caused a 42% error "
            "rate for approximately 2 minutes. The IncidentZero AI SRE team "
            "detected, diagnosed, and resolved the issue autonomously without "
            "human intervention.\n\n"
            "## Impact\n\n"
            "- **Duration:** ~2 minutes\n"
            "- **Error Rate Peak:** 42%\n"
            "- **Affected Endpoints:** /tasks, /tasks/{id}\n"
            "- **Users Affected:** Estimated 42% of active users\n"
            "- **Data Loss:** None\n\n"
            "## Timeline\n\n"
            "| Time | Event |\n"
            "|---|---|\n"
            "| T+0s | WatcherAgent detected error rate spike (0.5% -> 42%) |\n"
            "| T+10s | TriageAgent classified as P1 — 42% blast radius |\n"
            "| T+25s | DiagnosisAgent identified root cause: connection pool leak |\n"
            "| T+35s | ResolutionAgent challenged diagnosis — reached consensus |\n"
            "| T+45s | ResolutionAgent generated validated code fix |\n"
            "| T+60s | DeployAgent applied fix and created GitHub PR |\n"
            "| T+90s | Application fully recovered — health checks passing |\n\n"
            "## Root Cause\n\n"
            "Connections were not being released in the `finally` block of "
            "request handlers. When the chaos bug was active, the `finally` "
            "block contained conditional logic that skipped `pool.release()` "
            "70% of the time, causing rapid connection pool exhaustion.\n\n"
            "## Resolution\n\n"
            "Fixed the `finally` block to unconditionally release connections "
            "after each request, regardless of success or failure. This follows "
            "the standard resource cleanup pattern.\n\n"
            "## Agent Debate Summary\n\n"
            "The ResolutionAgent challenged the DiagnosisAgent's finding, asking "
            "whether the leak could be in a middleware layer. The DiagnosisAgent "
            "responded with endpoint-specific evidence showing only /tasks routes "
            "were affected, confirming the handler-level leak. Both agents reached "
            "consensus with 92% confidence before the fix was generated.\n\n"
            "## Lessons Learned\n\n"
            "1. **Always release resources in `finally` blocks without conditions**\n"
            "2. Connection pool monitoring should alert at 75% utilization\n"
            "3. Automated resolution reduced MTTR from hours to under 2 minutes\n"
            "4. Agent debate system prevented potential misdiagnosis\n\n"
            "## Prevention\n\n"
            "- Add linting rules to detect conditional resource release in finally blocks\n"
            "- Implement connection pool high-watermark alerts\n"
            "- Add integration tests for connection cleanup under error conditions\n\n"
            "---\n\n"
            "*Report generated by IncidentZero PostmortemAgent*\n"
        )

    # Generic fallback
    return json.dumps(
        {
            "response": "Mock AI response — configure AZURE_OPENAI_KEY or OPENAI_API_KEY for real AI.",
            "provider": "mock",
        },
        indent=2,
    )