"""
MDP v1 (Mycosoft Device Protocol) Implementation

This module implements the COBS-framed, CRC16-validated protocol used by
MycoBrain devices for reliable telemetry, commands, and events.

Protocol Structure:
    [0x00] [COBS-encoded payload] [0x00]
    
    Payload before COBS encoding:
    [seq:2] [type:1] [timestamp:4] [data:N] [crc16:2]

Message Types:
    0x01: Telemetry (device → server)
    0x02: Command (server → device)  
    0x03: Event (device → server)
    0x04: ACK (bidirectional)
    0x05: NACK (bidirectional)
    0x06: Heartbeat (device → server)
    0x07: Discovery (device → server)
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple, Union


class MDPMessageType(IntEnum):
    """MDP v1 message type identifiers."""
    TELEMETRY = 0x01
    COMMAND = 0x02
    EVENT = 0x03
    ACK = 0x04
    NACK = 0x05
    HEARTBEAT = 0x06
    DISCOVERY = 0x07


# CRC16-CCITT lookup table (polynomial 0x1021)
_CRC16_TABLE: List[int] = []


def _init_crc16_table() -> None:
    """Initialize CRC16-CCITT lookup table."""
    global _CRC16_TABLE
    if _CRC16_TABLE:
        return
    
    polynomial = 0x1021
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ polynomial
            else:
                crc = crc << 1
        _CRC16_TABLE.append(crc & 0xFFFF)


_init_crc16_table()


def crc16_ccitt(data: bytes, initial: int = 0xFFFF) -> int:
    """
    Calculate CRC16-CCITT checksum.
    
    Args:
        data: Bytes to checksum
        initial: Initial CRC value (default 0xFFFF)
        
    Returns:
        16-bit CRC value
    """
    crc = initial
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC16_TABLE[((crc >> 8) ^ byte) & 0xFF]
    return crc


def validate_crc(payload: bytes) -> bool:
    """
    Validate CRC16 at the end of a payload.
    
    Assumes last 2 bytes are CRC16 (big-endian).
    
    Args:
        payload: Full payload including CRC16
        
    Returns:
        True if CRC matches
    """
    if len(payload) < 3:
        return False
    
    data = payload[:-2]
    received_crc = struct.unpack(">H", payload[-2:])[0]
    calculated_crc = crc16_ccitt(data)
    
    return received_crc == calculated_crc


def cobs_encode(data: bytes) -> bytes:
    """
    Encode data using Consistent Overhead Byte Stuffing (COBS).
    
    COBS eliminates all zero bytes from the data, allowing 0x00
    to be used as an unambiguous frame delimiter.
    
    Args:
        data: Raw data to encode
        
    Returns:
        COBS-encoded data (does NOT include frame delimiters)
    """
    if not data:
        return b'\x01'
    
    output = bytearray()
    block_start = 0
    
    for i, byte in enumerate(data):
        if byte == 0:
            # Found zero - emit block length + block data
            block_len = i - block_start + 1
            output.append(block_len)
            output.extend(data[block_start:i])
            block_start = i + 1
    
    # Handle final block
    remaining = len(data) - block_start
    if remaining > 0 or block_start == len(data):
        output.append(remaining + 1)
        output.extend(data[block_start:])
    
    return bytes(output)


def cobs_decode(data: bytes) -> bytes:
    """
    Decode COBS-encoded data.
    
    Args:
        data: COBS-encoded data (without frame delimiters)
        
    Returns:
        Decoded original data
        
    Raises:
        ValueError: If data is malformed
    """
    if not data:
        return b''
    
    output = bytearray()
    i = 0
    
    while i < len(data):
        code = data[i]
        i += 1
        
        if code == 0:
            raise ValueError("Zero byte in COBS data")
        
        # Copy block (code - 1 bytes)
        block_len = code - 1
        if i + block_len > len(data):
            raise ValueError("COBS block extends past end of data")
        
        output.extend(data[i:i + block_len])
        i += block_len
        
        # Add implicit zero unless this was the last block or code was 0xFF
        if i < len(data) and code != 0xFF:
            output.append(0)
    
    # Remove trailing zero if present
    if output and output[-1] == 0:
        output = output[:-1]
    
    return bytes(output)


@dataclass
class MDPMessage:
    """Parsed MDP message with decoded payload."""
    
    sequence_number: int
    message_type: MDPMessageType
    timestamp_ms: int
    payload: Dict[str, Any]
    raw_data: bytes = field(default=b'', repr=False)
    crc_valid: bool = True
    
    @property
    def timestamp(self) -> datetime:
        """Convert device timestamp to datetime (UTC)."""
        return datetime.fromtimestamp(self.timestamp_ms / 1000.0, tz=timezone.utc)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "sequence_number": self.sequence_number,
            "message_type": self.message_type.name,
            "timestamp_ms": self.timestamp_ms,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
            "crc_valid": self.crc_valid,
        }


@dataclass
class MDPFrame:
    """Raw MDP frame container."""
    
    raw_frame: bytes
    cobs_payload: bytes
    decoded_payload: bytes
    message: Optional[MDPMessage] = None
    decode_error: Optional[str] = None
    
    @property
    def is_valid(self) -> bool:
        """Check if frame was successfully decoded."""
        return self.message is not None and self.decode_error is None


def decode_mdp_frame(frame: bytes) -> MDPFrame:
    """
    Decode a complete MDP frame.
    
    Expected format: [0x00] [COBS payload] [0x00]
    
    Args:
        frame: Complete frame including delimiters
        
    Returns:
        MDPFrame with decoded message or error
    """
    result = MDPFrame(
        raw_frame=frame,
        cobs_payload=b'',
        decoded_payload=b'',
    )
    
    # Strip frame delimiters
    if frame.startswith(b'\x00'):
        frame = frame[1:]
    if frame.endswith(b'\x00'):
        frame = frame[:-1]
    
    if not frame:
        result.decode_error = "Empty frame"
        return result
    
    result.cobs_payload = frame
    
    # COBS decode
    try:
        decoded = cobs_decode(frame)
        result.decoded_payload = decoded
    except ValueError as e:
        result.decode_error = f"COBS decode error: {e}"
        return result
    
    # Validate minimum length: seq(2) + type(1) + timestamp(4) + crc(2) = 9
    if len(decoded) < 9:
        result.decode_error = f"Payload too short: {len(decoded)} bytes"
        return result
    
    # Validate CRC
    crc_valid = validate_crc(decoded)
    
    # Parse header
    try:
        seq_num = struct.unpack(">H", decoded[0:2])[0]
        msg_type = MDPMessageType(decoded[2])
        timestamp_ms = struct.unpack(">I", decoded[3:7])[0]
    except (struct.error, ValueError) as e:
        result.decode_error = f"Header parse error: {e}"
        return result
    
    # Parse JSON payload (between header and CRC)
    json_data = decoded[7:-2]
    
    try:
        if json_data:
            payload = json.loads(json_data.decode('utf-8'))
        else:
            payload = {}
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        result.decode_error = f"JSON decode error: {e}"
        return result
    
    result.message = MDPMessage(
        sequence_number=seq_num,
        message_type=msg_type,
        timestamp_ms=timestamp_ms,
        payload=payload,
        raw_data=decoded,
        crc_valid=crc_valid,
    )
    
    return result


def encode_mdp_frame(
    message_type: MDPMessageType,
    payload: Dict[str, Any],
    sequence_number: int,
    timestamp_ms: Optional[int] = None,
) -> bytes:
    """
    Encode a message into an MDP frame.
    
    Args:
        message_type: Type of message
        payload: JSON-serializable payload
        sequence_number: Sequence number (0-65535)
        timestamp_ms: Optional timestamp (defaults to now)
        
    Returns:
        Complete COBS-encoded frame with delimiters
    """
    if timestamp_ms is None:
        timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    
    # Build payload
    header = struct.pack(">H", sequence_number & 0xFFFF)
    header += struct.pack("B", message_type)
    header += struct.pack(">I", timestamp_ms & 0xFFFFFFFF)
    
    json_data = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    
    # Calculate CRC
    pre_crc = header + json_data
    crc = crc16_ccitt(pre_crc)
    
    # Complete payload
    full_payload = pre_crc + struct.pack(">H", crc)
    
    # COBS encode
    cobs_data = cobs_encode(full_payload)
    
    # Add frame delimiters
    return b'\x00' + cobs_data + b'\x00'


def parse_ndjson_telemetry(line: str) -> Optional[Dict[str, Any]]:
    """
    Parse a line of NDJSON telemetry from Gateway firmware.
    
    Expected format (from Gateway NDJSON output):
    {"ts":1734567890123,"dev":"ABC123","type":"telemetry","data":{...}}
    
    Args:
        line: Single line of NDJSON
        
    Returns:
        Parsed telemetry dict or None if invalid
    """
    line = line.strip()
    if not line:
        return None
    
    try:
        data = json.loads(line)
        
        # Validate required fields
        if not isinstance(data, dict):
            return None
        
        # Normalize common variations
        result = {
            "timestamp_ms": data.get("ts") or data.get("timestamp_ms") or data.get("timestamp"),
            "device_serial": data.get("dev") or data.get("device") or data.get("serial"),
            "message_type": data.get("type") or data.get("msg_type") or "telemetry",
            "sequence": data.get("seq") or data.get("sequence") or 0,
        }
        
        # Extract nested data
        payload = data.get("data") or data.get("payload") or {}
        if isinstance(payload, dict):
            result["payload"] = payload
        else:
            result["payload"] = {"raw": payload}
        
        # Include any extra top-level fields
        for key in data:
            if key not in ("ts", "timestamp_ms", "timestamp", "dev", "device", 
                          "serial", "type", "msg_type", "seq", "sequence", 
                          "data", "payload"):
                result["payload"][key] = data[key]
        
        return result
        
    except json.JSONDecodeError:
        return None


# Command builders for common operations
class CommandBuilder:
    """Helper class to build common MDP commands."""
    
    @staticmethod
    def set_mosfet(mosfet_num: int, state: bool) -> Dict[str, Any]:
        """Build MOSFET control command."""
        return {
            "cmd": "mosfet",
            "target": f"M{mosfet_num}",
            "state": state,
        }
    
    @staticmethod
    def set_telemetry_interval(interval_ms: int) -> Dict[str, Any]:
        """Build telemetry interval command."""
        return {
            "cmd": "set_interval",
            "interval_ms": max(100, min(interval_ms, 3600000)),
        }
    
    @staticmethod
    def request_i2c_scan() -> Dict[str, Any]:
        """Build I2C scan request command."""
        return {
            "cmd": "i2c_scan",
        }
    
    @staticmethod
    def firmware_update(url: str, side: str = "A") -> Dict[str, Any]:
        """Build firmware update command."""
        return {
            "cmd": "ota_update",
            "url": url,
            "side": side.upper(),
        }
    
    @staticmethod
    def reboot(side: str = "A") -> Dict[str, Any]:
        """Build reboot command."""
        return {
            "cmd": "reboot",
            "side": side.upper(),
        }
    
    @staticmethod
    def set_lora_config(
        frequency_mhz: float,
        spreading_factor: int = 7,
        bandwidth_khz: float = 125.0,
    ) -> Dict[str, Any]:
        """Build LoRa configuration command."""
        return {
            "cmd": "lora_config",
            "frequency_mhz": frequency_mhz,
            "sf": spreading_factor,
            "bw_khz": bandwidth_khz,
        }


