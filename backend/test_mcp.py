"""Quick test to verify MCP protocol works"""
import asyncio
from mcp.protocol import MCPMessage, MessageType
from mcp.channel import mcp_bus


async def test_handler(message: MCPMessage):
    print(f"  ✅ Received: {message.sender} → {message.recipient}: {message.message_type.value}")
    print(f"     Payload: {message.payload}")


async def main():
    print("Testing MCP Protocol...")
    print()

    # Subscribe to a channel
    mcp_bus.subscribe("test.channel", test_handler)

    # Create and publish a message
    msg = MCPMessage(
        sender="TestAgent",
        recipient="OtherAgent",
        message_type=MessageType.ALERT,
        channel="test.channel",
        payload={"test": "hello world"},
        confidence=0.95,
        incident_id="TEST-001",
    )

    print(f"Publishing message...")
    await mcp_bus.publish(msg)
    print()

    # Verify message log
    messages = mcp_bus.get_incident_messages("TEST-001")
    assert len(messages) == 1, "Should have 1 message"
    print(f"✅ Message log contains {len(messages)} message(s)")

    # Test JSON serialization
    json_str = msg.to_json()
    print(f"✅ JSON serialization works")
    print(f"   {json_str[:100]}...")

    print()
    print("✅ ALL MCP TESTS PASSED")


asyncio.run(main())