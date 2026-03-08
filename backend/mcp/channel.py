"""
MCP Message Bus — Routes messages between agents.
Also pushes messages to frontend via WebSocket callbacks.
"""

from typing import Callable, Dict, List
import logging
import asyncio

from .protocol import MCPMessage

logger = logging.getLogger("mcp.channel")


class MCPChannel:
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._message_log: List[MCPMessage] = []
        self._ws_callbacks: List[Callable] = []

    def subscribe(self, channel: str, callback: Callable):
        """Subscribe a callback to a channel"""
        if channel not in self._subscribers:
            self._subscribers[channel] = []
        self._subscribers[channel].append(callback)
        logger.info(f"Subscribed to channel: {channel}")

    def on_websocket_message(self, callback: Callable):
        """Register callback to push messages to frontend"""
        self._ws_callbacks.append(callback)

    async def publish(self, message: MCPMessage):
        """Publish a message — notifies subscribers and WebSocket clients"""
        self._message_log.append(message)

        logger.info(
            f"[MCP] {message.sender} → {message.recipient} "
            f"| {message.message_type.value} "
            f"| {message.channel}"
        )

        # Notify channel subscribers
        channel = message.channel
        if channel in self._subscribers:
            for callback in self._subscribers[channel]:
                try:
                    await callback(message)
                except Exception as e:
                    logger.error(f"Subscriber error on {channel}: {e}")

        # Notify broadcast subscribers
        if "broadcast" in self._subscribers and channel != "broadcast":
            for callback in self._subscribers["broadcast"]:
                try:
                    await callback(message)
                except Exception as e:
                    logger.error(f"Broadcast subscriber error: {e}")

        # Push to WebSocket clients (frontend dashboard)
        for ws_cb in self._ws_callbacks:
            try:
                await ws_cb(message.to_dict())
            except Exception as e:
                logger.error(f"WebSocket callback error: {e}")

    def get_incident_messages(self, incident_id: str) -> List[MCPMessage]:
        """Get all messages for a specific incident"""
        return [m for m in self._message_log if m.incident_id == incident_id]

    def get_debate_messages(self, incident_id: str) -> List[MCPMessage]:
        """Get debate messages for a specific incident"""
        return [
            m
            for m in self._message_log
            if m.incident_id == incident_id and m.channel == "incident.debate"
        ]

    def get_all_messages(self) -> List[MCPMessage]:
        """Get all messages (for debugging)"""
        return self._message_log

    def clear(self):
        """Clear message log"""
        self._message_log.clear()


# Global singleton message bus
mcp_bus = MCPChannel()