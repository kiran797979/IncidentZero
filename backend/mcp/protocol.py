"""
MCP (Model Context Protocol) message schema.
This defines how agents communicate with each other.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List
import uuid
import json


class MessageType(str, Enum):
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
    """Standard message format for agent-to-agent communication"""

    sender: str
    recipient: str
    message_type: MessageType
    channel: str
    payload: dict

    # Optional fields
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
        return {
            "message_id": self.message_id,
            "incident_id": self.incident_id,
            "sender": self.sender,
            "recipient": self.recipient,
            "message_type": self.message_type.value,
            "channel": self.channel,
            "payload": self.payload,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "parent_message_id": self.parent_message_id,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)