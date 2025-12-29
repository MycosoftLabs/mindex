"""
Mycorrhizae Protocol Implementation

The Mycorrhizae Protocol bridges MINDEX data to NatureOS and external consumers.
It provides publish/subscribe semantics for device telemetry, events, and computed insights.

Channel Types:
    - device: Direct device telemetry streams
    - aggregate: Combined data from multiple devices
    - computed: AI/ML derived insights and predictions

Message Formats:
    - ndjson: Newline-delimited JSON (default)
    - cbor: Compact Binary Object Representation
    - protobuf: Protocol Buffers (future)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
from uuid import UUID, uuid4


class ChannelType(str, Enum):
    """Mycorrhizae channel types."""
    DEVICE = "device"
    AGGREGATE = "aggregate"
    COMPUTED = "computed"


class MessageFormat(str, Enum):
    """Supported message formats."""
    NDJSON = "ndjson"
    CBOR = "cbor"
    PROTOBUF = "protobuf"


@dataclass
class MycorrhizaeMessage:
    """A message in the Mycorrhizae Protocol."""
    
    id: UUID = field(default_factory=uuid4)
    channel: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Source identification
    source_type: str = "mindex"  # mindex, device, mas_agent
    source_id: Optional[str] = None
    device_serial: Optional[str] = None
    
    # Message content
    message_type: str = "telemetry"  # telemetry, event, command, insight
    payload: Dict[str, Any] = field(default_factory=dict)
    
    # Routing metadata
    correlation_id: Optional[UUID] = None
    reply_to: Optional[str] = None
    ttl_seconds: int = 3600
    
    def to_ndjson(self) -> str:
        """Serialize to NDJSON format."""
        return json.dumps({
            "id": str(self.id),
            "channel": self.channel,
            "ts": int(self.timestamp.timestamp() * 1000),
            "source": {
                "type": self.source_type,
                "id": self.source_id,
                "device": self.device_serial,
            },
            "msg_type": self.message_type,
            "payload": self.payload,
            "meta": {
                "correlation_id": str(self.correlation_id) if self.correlation_id else None,
                "reply_to": self.reply_to,
                "ttl": self.ttl_seconds,
            }
        }, separators=(',', ':'))
    
    @classmethod
    def from_ndjson(cls, line: str) -> "MycorrhizaeMessage":
        """Deserialize from NDJSON format."""
        data = json.loads(line.strip())
        
        source = data.get("source", {})
        meta = data.get("meta", {})
        
        return cls(
            id=UUID(data["id"]) if "id" in data else uuid4(),
            channel=data.get("channel", ""),
            timestamp=datetime.fromtimestamp(data["ts"] / 1000.0, tz=timezone.utc) if "ts" in data else datetime.now(timezone.utc),
            source_type=source.get("type", "unknown"),
            source_id=source.get("id"),
            device_serial=source.get("device"),
            message_type=data.get("msg_type", "telemetry"),
            payload=data.get("payload", {}),
            correlation_id=UUID(meta["correlation_id"]) if meta.get("correlation_id") else None,
            reply_to=meta.get("reply_to"),
            ttl_seconds=meta.get("ttl", 3600),
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "channel": self.channel,
            "timestamp": self.timestamp.isoformat(),
            "source_type": self.source_type,
            "source_id": self.source_id,
            "device_serial": self.device_serial,
            "message_type": self.message_type,
            "payload": self.payload,
        }


@dataclass
class MycorrhizaeChannel:
    """A channel definition in the Mycorrhizae Protocol."""
    
    name: str
    channel_type: ChannelType
    description: str = ""
    
    # Source binding
    device_ids: Set[UUID] = field(default_factory=set)
    stream_pattern: Optional[str] = None
    
    # Channel configuration
    format: MessageFormat = MessageFormat.NDJSON
    buffer_size: int = 100
    
    # Schema definition (for validation)
    payload_schema: Optional[Dict[str, Any]] = None
    
    # Statistics
    message_count: int = 0
    last_message_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "type": self.channel_type.value,
            "description": self.description,
            "device_ids": [str(d) for d in self.device_ids],
            "stream_pattern": self.stream_pattern,
            "format": self.format.value,
            "message_count": self.message_count,
            "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
        }


# Type alias for subscription callbacks
SubscriptionCallback = Callable[[MycorrhizaeMessage], None]


class MycorrhizaeProtocol:
    """
    Mycorrhizae Protocol router and manager.
    
    Handles channel registration, message routing, and subscriber management.
    This is an in-memory implementation; production deployments should use
    Redis, NATS, or similar message brokers.
    """
    
    def __init__(self) -> None:
        self._channels: Dict[str, MycorrhizaeChannel] = {}
        self._subscribers: Dict[str, List[SubscriptionCallback]] = {}
        self._message_buffer: Dict[str, List[MycorrhizaeMessage]] = {}
    
    def register_channel(self, channel: MycorrhizaeChannel) -> None:
        """Register a new channel."""
        self._channels[channel.name] = channel
        self._subscribers.setdefault(channel.name, [])
        self._message_buffer.setdefault(channel.name, [])
    
    def get_channel(self, name: str) -> Optional[MycorrhizaeChannel]:
        """Get a channel by name."""
        return self._channels.get(name)
    
    def list_channels(self) -> List[MycorrhizaeChannel]:
        """List all registered channels."""
        return list(self._channels.values())
    
    def subscribe(self, channel_name: str, callback: SubscriptionCallback) -> bool:
        """
        Subscribe to a channel.
        
        Args:
            channel_name: Name of channel to subscribe to
            callback: Function to call when messages arrive
            
        Returns:
            True if subscription successful
        """
        if channel_name not in self._channels:
            return False
        
        self._subscribers[channel_name].append(callback)
        return True
    
    def unsubscribe(self, channel_name: str, callback: SubscriptionCallback) -> bool:
        """
        Unsubscribe from a channel.
        
        Args:
            channel_name: Name of channel
            callback: Previously registered callback
            
        Returns:
            True if unsubscription successful
        """
        if channel_name not in self._subscribers:
            return False
        
        try:
            self._subscribers[channel_name].remove(callback)
            return True
        except ValueError:
            return False
    
    def publish(self, message: MycorrhizaeMessage) -> int:
        """
        Publish a message to a channel.
        
        Args:
            message: Message to publish
            
        Returns:
            Number of subscribers that received the message
        """
        channel = self._channels.get(message.channel)
        if not channel:
            return 0
        
        # Update channel stats
        channel.message_count += 1
        channel.last_message_at = datetime.now(timezone.utc)
        
        # Buffer message
        buffer = self._message_buffer[message.channel]
        buffer.append(message)
        if len(buffer) > channel.buffer_size:
            buffer.pop(0)
        
        # Notify subscribers
        subscribers = self._subscribers.get(message.channel, [])
        for callback in subscribers:
            try:
                callback(message)
            except Exception:
                # Log but don't propagate subscriber errors
                pass
        
        return len(subscribers)
    
    def get_recent_messages(
        self, 
        channel_name: str, 
        limit: int = 50
    ) -> List[MycorrhizaeMessage]:
        """Get recent messages from a channel buffer."""
        buffer = self._message_buffer.get(channel_name, [])
        return buffer[-limit:]
    
    # Pre-defined channel factories for MycoBrain integration
    @staticmethod
    def create_device_channel(
        device_serial: str,
        device_id: UUID,
        description: str = "",
    ) -> MycorrhizaeChannel:
        """Create a device-specific channel."""
        return MycorrhizaeChannel(
            name=f"device.{device_serial}",
            channel_type=ChannelType.DEVICE,
            description=description or f"Telemetry from device {device_serial}",
            device_ids={device_id},
        )
    
    @staticmethod
    def create_sensor_aggregate_channel(
        sensor_type: str,
        description: str = "",
    ) -> MycorrhizaeChannel:
        """Create an aggregate channel for a sensor type."""
        return MycorrhizaeChannel(
            name=f"aggregate.{sensor_type}",
            channel_type=ChannelType.AGGREGATE,
            description=description or f"Aggregated {sensor_type} readings",
            stream_pattern=f".*\\.{sensor_type}$",
        )
    
    @staticmethod
    def create_insight_channel(
        insight_type: str,
        description: str = "",
    ) -> MycorrhizaeChannel:
        """Create a computed insight channel."""
        return MycorrhizaeChannel(
            name=f"insight.{insight_type}",
            channel_type=ChannelType.COMPUTED,
            description=description or f"AI-computed {insight_type} insights",
        )


# Global protocol instance
_protocol: Optional[MycorrhizaeProtocol] = None


def get_protocol() -> MycorrhizaeProtocol:
    """Get or create the global Mycorrhizae Protocol instance."""
    global _protocol
    if _protocol is None:
        _protocol = MycorrhizaeProtocol()
        _init_default_channels(_protocol)
    return _protocol


def _init_default_channels(protocol: MycorrhizaeProtocol) -> None:
    """Initialize default channels."""
    # Environmental aggregate channels
    protocol.register_channel(MycorrhizaeChannel(
        name="aggregate.environmental",
        channel_type=ChannelType.AGGREGATE,
        description="All environmental sensor readings (temperature, humidity, pressure)",
    ))
    
    protocol.register_channel(MycorrhizaeChannel(
        name="aggregate.substrate",
        channel_type=ChannelType.AGGREGATE,
        description="Substrate and growing medium sensor readings",
    ))
    
    # System channels
    protocol.register_channel(MycorrhizaeChannel(
        name="system.device_status",
        channel_type=ChannelType.DEVICE,
        description="Device online/offline status changes",
    ))
    
    protocol.register_channel(MycorrhizaeChannel(
        name="system.alerts",
        channel_type=ChannelType.COMPUTED,
        description="System alerts and threshold violations",
    ))
    
    # Insight channels
    protocol.register_channel(MycorrhizaeChannel(
        name="insight.growth_prediction",
        channel_type=ChannelType.COMPUTED,
        description="ML-based growth rate predictions",
    ))
    
    protocol.register_channel(MycorrhizaeChannel(
        name="insight.contamination_risk",
        channel_type=ChannelType.COMPUTED,
        description="Contamination risk assessments",
    ))


