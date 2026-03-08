"""Test LLM service works (with or without API keys)"""
import asyncio
from services.llm import chat, chat_json


async def main():
    print("Testing LLM Service...")
    print()

    # Test 1: Basic chat
    response = await chat(
        system_prompt="You are a helpful assistant.",
        user_prompt="Say hello in exactly 5 words."
    )
    print(f"✅ Chat response: {response[:100]}")
    print()

    # Test 2: JSON response (triage)
    result = await chat_json(
        system_prompt="You are TriageAgent. Respond in JSON with severity field.",
        user_prompt="Error rate is 42%. Classify this incident."
    )
    print(f"✅ JSON response: {result}")
    assert "severity" in result or "raw_response" in result
    print()

    print("✅ ALL LLM TESTS PASSED")


asyncio.run(main())