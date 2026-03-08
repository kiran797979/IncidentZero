"""Base class for all IncidentZero agents"""

from abc import ABC, abstractmethod
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
        evidence: list = None,
        parent_message_id: str = None,
    ) -> MCPMessage:
        """Send a message through the MCP bus"""
        message = MCPMessage(
            sender=self.name,
            recipient=recipient,
            message_type=message_type,
            channel=channel,
            payload=payload,
            incident_id=incident_id,
            confidence=confidence,
            evidence=evidence or [],
            parent_message_id=parent_message_id,
        )
        await mcp_bus.publish(message)
        return message

    @abstractmethod
    async def process(self, message: MCPMessage) -> None:
        """Process an incoming message — implemented by each agent"""
        pass