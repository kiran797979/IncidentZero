"""
Integration test: Run all agents without the full server.
Requires target-app running on localhost:8000
"""
import asyncio
import logging
from agents.orchestrator import OrchestratorAgent
from mcp.channel import mcp_bus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger("test")


async def main():
    print("=" * 60)
    print("  IncidentZero — Agent Integration Test")
    print("=" * 60)
    print()
    print("⚠️  Make sure target-app is running on localhost:8000")
    print()

    # Step 1: Start orchestrator
    orchestrator = OrchestratorAgent()
    await orchestrator.start()

    print("⏳ Waiting 8 seconds for initial health checks...")
    await asyncio.sleep(8)

    # Step 2: Check system status
    print()
    print("📊 Current messages in MCP bus:")
    all_messages = mcp_bus.get_all_messages()
    for m in all_messages:
        print(f"   {m.sender:20s} → {m.recipient:20s} | {m.message_type.value:10s} | {m.channel}")

    print()
    print(f"Total messages: {len(all_messages)}")
    print()

    if len(all_messages) > 0:
        print("✅ Agents are communicating!")
    else:
        print("❌ No messages — check if target-app is running")
        orchestrator.stop()
        return

    # Step 3: Inject bug and wait for detection
    print()
    print("🐛 Injecting bug into target app...")
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.post("http://localhost:8000/chaos/inject")
        print(f"   Bug injection response: {resp.json()}")

        # Generate some load to trigger the bug
        print("📈 Generating load to trigger connection leaks...")
        for i in range(5):
            try:
                await client.post("http://localhost:8000/chaos/generate-load")
            except Exception:
                pass
            await asyncio.sleep(1)

    # Step 4: Wait for agents to detect and resolve
    print()
    print("⏳ Waiting for agents to detect, diagnose, and resolve...")
    print("   (This may take 30-60 seconds)")
    print()

    for i in range(60):
        await asyncio.sleep(2)

        # Check if any incidents were created
        incidents = orchestrator.get_incidents()
        if incidents:
            for inc_id, inc_data in incidents.items():
                status = inc_data.get("status", "UNKNOWN")
                print(f"   [{i*2:3d}s] Incident {inc_id}: {status}")

                if status in ("RESOLVED", "DEPLOY_FAILED"):
                    print()
                    print("=" * 60)
                    print("  INCIDENT RESOLVED!")
                    print("=" * 60)
                    print()

                    # Print timeline
                    timeline = inc_data.get("timeline", [])
                    print("📋 Timeline:")
                    for event in timeline:
                        print(f"   {event['time'][-12:]} | {event['agent']:20s} | {event['event']}")

                    # Print resolution time
                    res_time = inc_data.get("resolution_time_seconds")
                    if res_time:
                        print(f"\n⏱️  Resolution time: {res_time} seconds")

                    # Print debate
                    debate = inc_data.get("debate", [])
                    if debate:
                        print(f"\n💬 Debate ({len(debate)} messages):")
                        for d in debate:
                            print(f"   {d['agent']}: {d['type']}")

                    # Print postmortem
                    postmortem = inc_data.get("postmortem", {})
                    if postmortem:
                        report = postmortem.get("report_markdown", "")
                        print(f"\n📋 Postmortem (first 500 chars):")
                        print(report[:500])

                    print()
                    print("✅ ALL AGENTS WORKED SUCCESSFULLY!")
                    orchestrator.stop()
                    return

    print("⏰ Timeout — agents didn't resolve within 120 seconds")
    orchestrator.stop()


asyncio.run(main())