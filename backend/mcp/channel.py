"""
MCP Message Bus — Routes messages between agents.
Also pushes messages to frontend via WebSocket callbacks.
Maintains message history for API queries and postmortem generation.
"""

from typing import Callable, Dict, List, Optional
import logging
import asyncio

from .protocol import MCPMessage, MessageType

logger = logging.getLogger("mcp.channel")


class MCPChannel:
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._message_log: List[MCPMessage] = []
        self._ws_callbacks: List[Callable] = []
        self._max_log_size = 5000

    def subscribe(self, channel: str, callback: Callable):
        """Subscribe a callback to a channel."""
        if channel not in self._subscribers:
            self._subscribers[channel] = []
        self._subscribers[channel].append(callback)
        logger.info("Subscribed to channel: %s", channel)

    def unsubscribe(self, channel: str, callback: Callable):
        """Remove a callback from a channel."""
        if channel in self._subscribers:
            try:
                self._subscribers[channel].remove(callback)
            except ValueError:
                pass

    def on_websocket_message(self, callback: Callable):
        """Register callback to push messages to frontend."""
        self._ws_callbacks.append(callback)

    async def publish(self, message: MCPMessage):
        """Publish a message — notifies subscribers and WebSocket clients."""
        # Store in log
        self._message_log.append(message)

        # Trim log if too large
        if len(self._message_log) > self._max_log_size:
            self._message_log = self._message_log[-self._max_log_size:]

        logger.info(
            "[MCP] %s → %s | %s | %s",
            message.sender,
            message.recipient,
            message.message_type.value,
            message.channel,
        )

        # Notify channel subscribers
        channel = message.channel
        if channel in self._subscribers:
            for callback in self._subscribers[channel]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(message)
                    else:
                        callback(message)
                except Exception as exc:
                    logger.error(
                        "Subscriber error on %s: %s",
                        channel,
                        str(exc)[:100],
                    )

        # Notify broadcast subscribers
        if "broadcast" in self._subscribers and channel != "broadcast":
            for callback in self._subscribers["broadcast"]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(message)
                    else:
                        callback(message)
                except Exception as exc:
                    logger.error(
                        "Broadcast subscriber error: %s",
                        str(exc)[:100],
                    )

        # Push to WebSocket clients (frontend dashboard)
        for ws_cb in self._ws_callbacks:
            try:
                await ws_cb(message.to_dict())
            except Exception as exc:
                logger.error(
                    "WebSocket callback error: %s",
                    str(exc)[:100],
                )

    # ─── Query Methods ────────────────────────────────────

    def get_incident_messages(self, incident_id: str) -> List[MCPMessage]:
        """Get all messages for a specific incident."""
        if not incident_id:
            return []
        return [m for m in self._message_log if m.incident_id == incident_id]

    def get_debate_messages(self, incident_id: str) -> List[MCPMessage]:
        """Get debate messages for a specific incident."""
        if not incident_id:
            return []
        return [
            m
            for m in self._message_log
            if m.incident_id == incident_id
            and m.channel == "incident.debate"
        ]

    def get_messages_by_channel(self, channel: str) -> List[MCPMessage]:
        """Get all messages on a specific channel."""
        return [m for m in self._message_log if m.channel == channel]

    def get_messages_by_sender(self, sender: str) -> List[MCPMessage]:
        """Get all messages from a specific agent."""
        return [m for m in self._message_log if m.sender == sender]

    def get_messages_by_type(
        self, message_type: MessageType
    ) -> List[MCPMessage]:
        """Get all messages of a specific type."""
        return [
            m for m in self._message_log if m.message_type == message_type
        ]

    def get_all_messages(self) -> List[MCPMessage]:
        """Get all messages."""
        return list(self._message_log)

    def get_recent_messages(self, count: int = 50) -> List[MCPMessage]:
        """Get the most recent N messages."""
        return self._message_log[-count:]

    def get_incident_timeline(self, incident_id: str) -> List[dict]:
        """Get a formatted timeline for an incident."""
        messages = self.get_incident_messages(incident_id)
        timeline = []
        for msg in messages:
            timeline.append({
                "time": msg.timestamp,
                "agent": msg.sender,
                "type": msg.message_type.value,
                "channel": msg.channel,
                "summary": self._summarize_message(msg),
            })
        return timeline

    def get_incident_stats(self, incident_id: str) -> dict:
        """Get statistics for an incident — used by postmortem."""
        messages = self.get_incident_messages(incident_id)
        debate_msgs = self.get_debate_messages(incident_id)

        agents_involved = list(set(m.sender for m in messages))
        channels_used = list(set(m.channel for m in messages))
        message_types = {}
        for m in messages:
            key = m.message_type.value
            message_types[key] = message_types.get(key, 0) + 1

        return {
            "total_messages": len(messages),
            "debate_rounds": len(debate_msgs),
            "agents_involved": agents_involved,
            "channels_used": channels_used,
            "message_types": message_types,
        }

    def _summarize_message(self, msg: MCPMessage) -> str:
        """Create a short summary of a message for timeline display."""
        payload = msg.payload or {}
        channel = msg.channel

        if channel == "incident.detection":
            return "Anomaly detected: " + str(payload.get("alert_type", "unknown"))
        if channel == "incident.triage":
            return "Classified as " + str(payload.get("severity", "?"))
        if channel == "incident.diagnosis":
            rc = payload.get("root_cause", {})
            if isinstance(rc, dict):
                return "Root cause: " + str(rc.get("detail", "identified"))
            return "Root cause identified"
        if channel == "incident.debate":
            return msg.message_type.value.upper() + " from " + msg.sender
        if channel == "incident.resolution":
            fix = payload.get("fix", {})
            if isinstance(fix, dict):
                return "Fix: " + str(fix.get("description", "generated"))
            return "Fix generated"
        if channel == "incident.deployment":
            return "Deployment: " + str(payload.get("status", "unknown"))
        if channel == "incident.postmortem":
            return "Postmortem report generated"

        return msg.message_type.value + " on " + channel

    def clear(self):
        """Clear all message history."""
        self._message_log.clear()
        logger.info("Message log cleared")

    @property
    def message_count(self) -> int:
        """Get total message count."""
        return len(self._message_log)


# Global singleton message bus
mcp_bus = MCPChannel()