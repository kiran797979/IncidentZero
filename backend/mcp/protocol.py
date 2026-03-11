"""
MCP (Model Context Protocol) message schema.
This defines how agents communicate with each other.
Every message in the system uses this format.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List
import uuid
import json


class MessageType(str, Enum):
    """Types of messages agents can send."""
    ALERT = "alert"
    ANALYSIS = "analysis"
    PROPOSAL = "proposal"
    CHALLENGE = "challenge"
    EVIDENCE = "evidence"
    CONSENSUS = "consensus"
    ACTION = "action"
    STATUS = "status"


@dataclass
class MCPMessage:
    """Standard message format for agent-to-agent communication."""

    # Required fields
    sender: str
    recipient: str
    message_type: MessageType
    channel: str
    payload: dict

    # Optional context fields
    confidence: float = 0.0
    evidence: List[str] = field(default_factory=list)
    incident_id: str = ""
    parent_message_id: Optional[str] = None

    # Auto-generated fields
    message_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: str = field(
        default_factory=lambda: datetime.utcnow().isoformat() + "Z"
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        msg_type = self.message_type
        if isinstance(msg_type, MessageType):
            msg_type_value = msg_type.value
        else:
            msg_type_value = str(msg_type)

        return {
            "message_id": self.message_id,
            "incident_id": self.incident_id,
            "sender": self.sender,
            "recipient": self.recipient,
            "message_type": msg_type_value,
            "channel": self.channel,
            "payload": self._safe_payload(),
            "confidence": self.confidence,
            "evidence": self.evidence if isinstance(self.evidence, list) else [],
            "parent_message_id": self.parent_message_id,
            "timestamp": self.timestamp,
        }

    def _safe_payload(self) -> dict:
        """Ensure payload is always a serializable dict."""
        if not isinstance(self.payload, dict):
            return {"data": str(self.payload)}
        # Deep check — ensure no non-serializable objects
        try:
            json.dumps(self.payload)
            return self.payload
        except (TypeError, ValueError):
            return {"data": str(self.payload), "_serialization_fallback": True}

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2, default=str)

    @classmethod
    def from_dict(cls, data: dict) -> "MCPMessage":
        """Create an MCPMessage from a dictionary."""
        msg_type_str = data.get("message_type", "status")
        try:
            msg_type = MessageType(msg_type_str)
        except ValueError:
            msg_type = MessageType.STATUS

        return cls(
            sender=data.get("sender", "unknown"),
            recipient=data.get("recipient", "unknown"),
            message_type=msg_type,
            channel=data.get("channel", "unknown"),
            payload=data.get("payload", {}),
            confidence=data.get("confidence", 0.0),
            evidence=data.get("evidence", []),
            incident_id=data.get("incident_id", ""),
            parent_message_id=data.get("parent_message_id"),
            message_id=data.get("message_id", str(uuid.uuid4())[:8]),
            timestamp=data.get("timestamp", datetime.utcnow().isoformat() + "Z"),
        )

    def __repr__(self) -> str:
        return (
            "MCPMessage("
            + self.sender
            + " → "
            + self.recipient
            + " | "
            + self.message_type.value
            + " | "
            + self.channel
            + ")"
        )