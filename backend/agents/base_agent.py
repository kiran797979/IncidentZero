"""
BaseAgent — Abstract base class for all IncidentZero agents.
Provides MCP message sending and standard logging.
"""

from abc import ABC, abstractmethod
from typing import Optional, List
import logging

from mcp.protocol import MCPMessage, MessageType
from mcp.channel import mcp_bus


class BaseAgent(ABC):
    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(name)

    async def send_message(
        self,
        recipient: str,
        message_type: MessageType,
        channel: str,
        payload: dict,
        incident_id: str = "",
        confidence: float = 0.0,
        evidence: Optional[List[str]] = None,
        parent_message_id: Optional[str] = None,
    ) -> MCPMessage:
        """Send a message through the MCP bus."""
        if not isinstance(payload, dict):
            self.logger.warning(
                "Payload is not a dict (%s) — wrapping it",
                type(payload).__name__,
            )
            payload = {"data": payload}

        message = MCPMessage(
            sender=self.name,
            recipient=recipient,
            message_type=message_type,
            channel=channel,
            payload=payload,
            incident_id=incident_id,
            confidence=confidence,
            evidence=evidence if evidence is not None else [],
            parent_message_id=parent_message_id,
        )

        try:
            await mcp_bus.publish(message)
        except Exception as exc:
            self.logger.error(
                "Failed to publish MCP message on %s: %s",
                channel,
                str(exc)[:100],
            )

        return message

    @abstractmethod
    async def process(self, message: MCPMessage) -> None:
        """Process an incoming message — implemented by each agent."""
        pass

    def __repr__(self) -> str:
        return self.name