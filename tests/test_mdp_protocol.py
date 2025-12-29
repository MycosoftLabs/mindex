"""
Tests for MDP v1 Protocol implementation.

Tests COBS encoding/decoding, CRC16 calculation, and MDP frame handling.
"""

import pytest

from mindex_api.protocols.mdp_v1 import (
    CommandBuilder,
    MDPMessageType,
    cobs_decode,
    cobs_encode,
    crc16_ccitt,
    decode_mdp_frame,
    encode_mdp_frame,
    parse_ndjson_telemetry,
    validate_crc,
)


class TestCRC16:
    """Tests for CRC16-CCITT implementation."""

    def test_crc16_empty(self):
        """Empty data should return initial value."""
        assert crc16_ccitt(b"") == 0xFFFF

    def test_crc16_known_values(self):
        """Test against known CRC16-CCITT values."""
        # "123456789" has a well-known CRC16-CCITT value
        assert crc16_ccitt(b"123456789") == 0x29B1

    def test_crc16_deterministic(self):
        """Same input should always produce same output."""
        data = b"Hello, MycoBrain!"
        crc1 = crc16_ccitt(data)
        crc2 = crc16_ccitt(data)
        assert crc1 == crc2

    def test_validate_crc_valid(self):
        """Valid CRC should pass validation."""
        data = b"test data"
        crc = crc16_ccitt(data)
        payload = data + crc.to_bytes(2, "big")
        assert validate_crc(payload) is True

    def test_validate_crc_invalid(self):
        """Invalid CRC should fail validation."""
        data = b"test data"
        payload = data + b"\x00\x00"  # Wrong CRC
        assert validate_crc(payload) is False

    def test_validate_crc_too_short(self):
        """Payload too short should fail."""
        assert validate_crc(b"ab") is False
        assert validate_crc(b"") is False


class TestCOBS:
    """Tests for COBS encoding/decoding."""

    def test_cobs_empty(self):
        """Empty data encodes to single byte."""
        encoded = cobs_encode(b"")
        assert encoded == b"\x01"

    def test_cobs_no_zeros(self):
        """Data without zeros has minimal overhead."""
        data = b"Hello"
        encoded = cobs_encode(data)
        # First byte is length + 1, then the data
        assert encoded[0] == len(data) + 1
        assert encoded[1:] == data

    def test_cobs_single_zero(self):
        """Single zero in middle is handled correctly."""
        data = b"A\x00B"
        encoded = cobs_encode(data)
        decoded = cobs_decode(encoded)
        assert decoded == data

    def test_cobs_multiple_zeros(self):
        """Multiple zeros are handled correctly."""
        data = b"\x00\x00\x00"
        encoded = cobs_encode(data)
        decoded = cobs_decode(encoded)
        assert decoded == data

    def test_cobs_roundtrip(self):
        """Encode then decode should return original data."""
        test_cases = [
            b"",
            b"\x00",
            b"Hello, World!",
            b"\x00\x00\x00",
            b"A\x00B\x00C",
            bytes(range(256)),  # All byte values
        ]
        for data in test_cases:
            encoded = cobs_encode(data)
            decoded = cobs_decode(encoded)
            assert decoded == data, f"Roundtrip failed for {data!r}"

    def test_cobs_no_zeros_in_output(self):
        """COBS encoded data should never contain zeros."""
        test_cases = [
            b"\x00",
            b"\x00\x00",
            b"A\x00B\x00C\x00D",
            bytes(range(256)),
        ]
        for data in test_cases:
            encoded = cobs_encode(data)
            assert b"\x00" not in encoded, f"Zero found in encoded {data!r}"

    def test_cobs_decode_invalid(self):
        """Invalid COBS data should raise ValueError."""
        with pytest.raises(ValueError):
            cobs_decode(b"\x00invalid")  # Zero in data


class TestMDPFrame:
    """Tests for MDP frame encoding/decoding."""

    def test_encode_decode_roundtrip(self):
        """Encoded frame should decode back to original message."""
        payload = {"temperature": 24.5, "humidity": 85.2}
        seq = 42
        
        frame = encode_mdp_frame(
            message_type=MDPMessageType.TELEMETRY,
            payload=payload,
            sequence_number=seq,
            timestamp_ms=1734270000000,
        )
        
        result = decode_mdp_frame(frame)
        
        assert result.is_valid
        assert result.message.sequence_number == seq
        assert result.message.message_type == MDPMessageType.TELEMETRY
        assert result.message.payload == payload
        assert result.message.crc_valid is True

    def test_encode_has_frame_delimiters(self):
        """Encoded frame should start and end with 0x00."""
        frame = encode_mdp_frame(
            message_type=MDPMessageType.HEARTBEAT,
            payload={},
            sequence_number=0,
        )
        assert frame[0:1] == b"\x00"
        assert frame[-1:] == b"\x00"

    def test_decode_empty_frame(self):
        """Empty frame should return error."""
        result = decode_mdp_frame(b"")
        assert not result.is_valid
        assert result.decode_error == "Empty frame"

    def test_decode_too_short(self):
        """Frame too short should return error."""
        result = decode_mdp_frame(b"\x00\x01\x02\x00")
        assert not result.is_valid
        assert "too short" in result.decode_error.lower()

    def test_all_message_types(self):
        """All message types should encode/decode correctly."""
        for msg_type in MDPMessageType:
            frame = encode_mdp_frame(
                message_type=msg_type,
                payload={"type": msg_type.name},
                sequence_number=msg_type.value,
            )
            result = decode_mdp_frame(frame)
            assert result.is_valid, f"Failed for {msg_type}"
            assert result.message.message_type == msg_type

    def test_sequence_number_wrapping(self):
        """Sequence number should wrap at 16 bits."""
        frame = encode_mdp_frame(
            message_type=MDPMessageType.TELEMETRY,
            payload={},
            sequence_number=65535,
        )
        result = decode_mdp_frame(frame)
        assert result.message.sequence_number == 65535
        
        frame2 = encode_mdp_frame(
            message_type=MDPMessageType.TELEMETRY,
            payload={},
            sequence_number=65536,  # Should wrap to 0
        )
        result2 = decode_mdp_frame(frame2)
        assert result2.message.sequence_number == 0


class TestNDJSONParsing:
    """Tests for NDJSON telemetry parsing."""

    def test_parse_standard_format(self):
        """Parse standard NDJSON format from Gateway."""
        line = '{"ts":1734270000000,"dev":"MCB-001","type":"telemetry","data":{"temp":24.5}}'
        result = parse_ndjson_telemetry(line)
        
        assert result is not None
        assert result["timestamp_ms"] == 1734270000000
        assert result["device_serial"] == "MCB-001"
        assert result["message_type"] == "telemetry"
        assert result["payload"]["temp"] == 24.5

    def test_parse_alternative_keys(self):
        """Parse with alternative key names."""
        line = '{"timestamp":1734270000000,"serial":"MCB-002","msg_type":"event","payload":{"btn":1}}'
        result = parse_ndjson_telemetry(line)
        
        assert result is not None
        assert result["timestamp_ms"] == 1734270000000
        assert result["device_serial"] == "MCB-002"
        assert result["message_type"] == "event"

    def test_parse_empty_line(self):
        """Empty line should return None."""
        assert parse_ndjson_telemetry("") is None
        assert parse_ndjson_telemetry("   ") is None
        assert parse_ndjson_telemetry("\n") is None

    def test_parse_invalid_json(self):
        """Invalid JSON should return None."""
        assert parse_ndjson_telemetry("not json") is None
        assert parse_ndjson_telemetry("{invalid}") is None

    def test_parse_non_object(self):
        """Non-object JSON should return None."""
        assert parse_ndjson_telemetry("[1,2,3]") is None
        assert parse_ndjson_telemetry('"string"') is None


class TestCommandBuilder:
    """Tests for command helper methods."""

    def test_mosfet_command(self):
        """MOSFET control command format."""
        cmd = CommandBuilder.set_mosfet(1, True)
        assert cmd["cmd"] == "mosfet"
        assert cmd["target"] == "M1"
        assert cmd["state"] is True

    def test_telemetry_interval_command(self):
        """Telemetry interval command with bounds."""
        cmd = CommandBuilder.set_telemetry_interval(5000)
        assert cmd["cmd"] == "set_interval"
        assert cmd["interval_ms"] == 5000
        
        # Test lower bound
        cmd_low = CommandBuilder.set_telemetry_interval(50)
        assert cmd_low["interval_ms"] == 100
        
        # Test upper bound
        cmd_high = CommandBuilder.set_telemetry_interval(9999999)
        assert cmd_high["interval_ms"] == 3600000

    def test_i2c_scan_command(self):
        """I2C scan request command."""
        cmd = CommandBuilder.request_i2c_scan()
        assert cmd["cmd"] == "i2c_scan"

    def test_reboot_command(self):
        """Reboot command format."""
        cmd = CommandBuilder.reboot("B")
        assert cmd["cmd"] == "reboot"
        assert cmd["side"] == "B"

    def test_firmware_update_command(self):
        """OTA update command format."""
        cmd = CommandBuilder.firmware_update("https://example.com/fw.bin", "A")
        assert cmd["cmd"] == "ota_update"
        assert cmd["url"] == "https://example.com/fw.bin"
        assert cmd["side"] == "A"

    def test_lora_config_command(self):
        """LoRa configuration command."""
        cmd = CommandBuilder.set_lora_config(915.0, spreading_factor=10, bandwidth_khz=250.0)
        assert cmd["cmd"] == "lora_config"
        assert cmd["frequency_mhz"] == 915.0
        assert cmd["sf"] == 10
        assert cmd["bw_khz"] == 250.0


