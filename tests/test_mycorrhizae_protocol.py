"""
Tests for Mycorrhizae Protocol implementation.

Tests channel management, message routing, and pub/sub functionality.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from mindex_api.protocols.mycorrhizae import (
    ChannelType,
    MessageFormat,
    MycorrhizaeChannel,
    MycorrhizaeMessage,
    MycorrhizaeProtocol,
    get_protocol,
)


class TestMycorrhizaeMessage:
    """Tests for MycorrhizaeMessage class."""

    def test_message_defaults(self):
        """Message should have sensible defaults."""
        msg = MycorrhizaeMessage()
        assert msg.id is not None
        assert msg.source_type == "mindex"
        assert msg.message_type == "telemetry"
        assert msg.ttl_seconds == 3600

    def test_message_to_ndjson(self):
        """Message should serialize to valid NDJSON."""
        msg = MycorrhizaeMessage(
            channel="test.channel",
            device_serial="MCB-001",
            payload={"temperature": 24.5},
        )
        
        ndjson = msg.to_ndjson()
        
        assert isinstance(ndjson, str)
        assert '"channel":"test.channel"' in ndjson
        assert '"temperature":24.5' in ndjson
        assert "\n" not in ndjson  # Single line

    def test_message_from_ndjson(self):
        """Message should deserialize from NDJSON."""
        original = MycorrhizaeMessage(
            channel="test.channel",
            device_serial="MCB-001",
            payload={"humidity": 85.2},
        )
        
        ndjson = original.to_ndjson()
        restored = MycorrhizaeMessage.from_ndjson(ndjson)
        
        assert restored.channel == original.channel
        assert restored.device_serial == original.device_serial
        assert restored.payload == original.payload

    def test_message_to_dict(self):
        """Message should convert to dictionary."""
        msg = MycorrhizaeMessage(
            channel="test.channel",
            source_type="device",
            payload={"test": True},
        )
        
        d = msg.to_dict()
        
        assert d["channel"] == "test.channel"
        assert d["source_type"] == "device"
        assert d["payload"] == {"test": True}
        assert "id" in d
        assert "timestamp" in d


class TestMycorrhizaeChannel:
    """Tests for MycorrhizaeChannel class."""

    def test_channel_defaults(self):
        """Channel should have sensible defaults."""
        channel = MycorrhizaeChannel(
            name="test.channel",
            channel_type=ChannelType.DEVICE,
        )
        
        assert channel.format == MessageFormat.NDJSON
        assert channel.buffer_size == 100
        assert channel.message_count == 0

    def test_channel_to_dict(self):
        """Channel should convert to dictionary."""
        channel = MycorrhizaeChannel(
            name="aggregate.environmental",
            channel_type=ChannelType.AGGREGATE,
            description="All environmental sensors",
        )
        
        d = channel.to_dict()
        
        assert d["name"] == "aggregate.environmental"
        assert d["type"] == "aggregate"
        assert d["description"] == "All environmental sensors"

    def test_channel_with_device_binding(self):
        """Channel can be bound to specific devices."""
        device_id = uuid4()
        channel = MycorrhizaeChannel(
            name="device.MCB-001",
            channel_type=ChannelType.DEVICE,
            device_ids={device_id},
        )
        
        assert device_id in channel.device_ids


class TestMycorrhizaeProtocol:
    """Tests for MycorrhizaeProtocol routing."""

    @pytest.fixture
    def protocol(self):
        """Create a fresh protocol instance for each test."""
        return MycorrhizaeProtocol()

    def test_register_channel(self, protocol):
        """Channel registration should work."""
        channel = MycorrhizaeChannel(
            name="test.channel",
            channel_type=ChannelType.DEVICE,
        )
        
        protocol.register_channel(channel)
        
        assert protocol.get_channel("test.channel") is not None
        assert len(protocol.list_channels()) == 1

    def test_get_nonexistent_channel(self, protocol):
        """Getting nonexistent channel should return None."""
        assert protocol.get_channel("does.not.exist") is None

    def test_subscribe_and_publish(self, protocol):
        """Subscribers should receive published messages."""
        received = []
        
        channel = MycorrhizaeChannel(
            name="test.pubsub",
            channel_type=ChannelType.DEVICE,
        )
        protocol.register_channel(channel)
        
        def callback(msg):
            received.append(msg)
        
        protocol.subscribe("test.pubsub", callback)
        
        msg = MycorrhizaeMessage(
            channel="test.pubsub",
            payload={"value": 42},
        )
        count = protocol.publish(msg)
        
        assert count == 1
        assert len(received) == 1
        assert received[0].payload["value"] == 42

    def test_publish_to_nonexistent_channel(self, protocol):
        """Publishing to nonexistent channel should return 0."""
        msg = MycorrhizaeMessage(
            channel="does.not.exist",
            payload={},
        )
        count = protocol.publish(msg)
        assert count == 0

    def test_unsubscribe(self, protocol):
        """Unsubscribing should stop message delivery."""
        received = []
        
        channel = MycorrhizaeChannel(
            name="test.unsub",
            channel_type=ChannelType.DEVICE,
        )
        protocol.register_channel(channel)
        
        def callback(msg):
            received.append(msg)
        
        protocol.subscribe("test.unsub", callback)
        protocol.unsubscribe("test.unsub", callback)
        
        msg = MycorrhizaeMessage(channel="test.unsub", payload={})
        protocol.publish(msg)
        
        assert len(received) == 0

    def test_message_buffering(self, protocol):
        """Channel should buffer recent messages."""
        channel = MycorrhizaeChannel(
            name="test.buffer",
            channel_type=ChannelType.DEVICE,
            buffer_size=5,
        )
        protocol.register_channel(channel)
        
        # Publish 10 messages
        for i in range(10):
            msg = MycorrhizaeMessage(
                channel="test.buffer",
                payload={"index": i},
            )
            protocol.publish(msg)
        
        # Should only keep last 5
        recent = protocol.get_recent_messages("test.buffer", limit=10)
        assert len(recent) == 5
        assert recent[0].payload["index"] == 5
        assert recent[-1].payload["index"] == 9

    def test_channel_stats_updated(self, protocol):
        """Publishing should update channel statistics."""
        channel = MycorrhizaeChannel(
            name="test.stats",
            channel_type=ChannelType.DEVICE,
        )
        protocol.register_channel(channel)
        
        assert channel.message_count == 0
        assert channel.last_message_at is None
        
        msg = MycorrhizaeMessage(channel="test.stats", payload={})
        protocol.publish(msg)
        
        assert channel.message_count == 1
        assert channel.last_message_at is not None

    def test_multiple_subscribers(self, protocol):
        """Multiple subscribers should all receive messages."""
        received1 = []
        received2 = []
        
        channel = MycorrhizaeChannel(
            name="test.multi",
            channel_type=ChannelType.DEVICE,
        )
        protocol.register_channel(channel)
        
        protocol.subscribe("test.multi", lambda m: received1.append(m))
        protocol.subscribe("test.multi", lambda m: received2.append(m))
        
        msg = MycorrhizaeMessage(channel="test.multi", payload={"test": True})
        count = protocol.publish(msg)
        
        assert count == 2
        assert len(received1) == 1
        assert len(received2) == 1

    def test_subscriber_error_isolation(self, protocol):
        """Error in one subscriber shouldn't affect others."""
        received = []
        
        channel = MycorrhizaeChannel(
            name="test.error",
            channel_type=ChannelType.DEVICE,
        )
        protocol.register_channel(channel)
        
        def bad_callback(msg):
            raise RuntimeError("Callback error")
        
        def good_callback(msg):
            received.append(msg)
        
        protocol.subscribe("test.error", bad_callback)
        protocol.subscribe("test.error", good_callback)
        
        msg = MycorrhizaeMessage(channel="test.error", payload={})
        count = protocol.publish(msg)
        
        # Both callbacks were called
        assert count == 2
        # Good callback still received message
        assert len(received) == 1


class TestChannelFactories:
    """Tests for channel factory methods."""

    def test_create_device_channel(self):
        """Device channel factory should work."""
        device_id = uuid4()
        channel = MycorrhizaeProtocol.create_device_channel(
            device_serial="MCB-001",
            device_id=device_id,
        )
        
        assert channel.name == "device.MCB-001"
        assert channel.channel_type == ChannelType.DEVICE
        assert device_id in channel.device_ids

    def test_create_aggregate_channel(self):
        """Aggregate channel factory should work."""
        channel = MycorrhizaeProtocol.create_sensor_aggregate_channel(
            sensor_type="temperature",
            description="All temperature sensors",
        )
        
        assert channel.name == "aggregate.temperature"
        assert channel.channel_type == ChannelType.AGGREGATE
        assert "temperature" in channel.description

    def test_create_insight_channel(self):
        """Insight channel factory should work."""
        channel = MycorrhizaeProtocol.create_insight_channel(
            insight_type="growth_prediction",
        )
        
        assert channel.name == "insight.growth_prediction"
        assert channel.channel_type == ChannelType.COMPUTED


class TestGlobalProtocol:
    """Tests for the global protocol instance."""

    def test_get_protocol_singleton(self):
        """get_protocol should return same instance."""
        p1 = get_protocol()
        p2 = get_protocol()
        assert p1 is p2

    def test_default_channels_exist(self):
        """Default channels should be initialized."""
        protocol = get_protocol()
        channels = protocol.list_channels()
        
        # Check for expected default channels
        channel_names = {ch.name for ch in channels}
        assert "aggregate.environmental" in channel_names
        assert "system.device_status" in channel_names
        assert "system.alerts" in channel_names


