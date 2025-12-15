"""MDP v1 (Mycosoft Device Protocol) helpers.

This module provides COBS framing, CRC16-CCITT, and a small frame envelope.
It is intended for MAS ingestion agents and tooling.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import struct


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def cobs_encode(data: bytes) -> bytes:
    if not data:
        return b"\x01\x00"

    out = bytearray()
    idx = 0
    while idx < len(data):
        next_zero = data.find(b"\x00", idx)
        if next_zero == -1:
            next_zero = len(data)

        block_len = next_zero - idx
        while block_len >= 254:
            out.append(255)
            out.extend(data[idx : idx + 254])
            idx += 254
            block_len -= 254

        out.append(block_len + 1)
        out.extend(data[idx:next_zero])
        idx = next_zero + 1

    out.append(0)
    return bytes(out)


def cobs_decode(frame: bytes) -> bytes:
    if not frame or frame[-1] != 0:
        raise ValueError("Invalid COBS frame")

    out = bytearray()
    i = 0
    end = len(frame) - 1
    while i < end:
        code = frame[i]
        if code == 0:
            break
        i += 1
        block_end = i + code - 1
        out.extend(frame[i:block_end])
        i = block_end
        if code != 255 and i < end:
            out.append(0)
    return bytes(out)


MDP_MSG_TELEMETRY = 0
MDP_MSG_EVENT = 1
MDP_MSG_COMMAND = 2
MDP_MSG_ACK = 3


@dataclass
class MDPFrame:
    message_type: int
    sequence_number: int
    timestamp: datetime
    payload: bytes

    def encode(self) -> bytes:
        ts_ms = int(self.timestamp.timestamp() * 1000)
        header = struct.pack(">BQQ", self.message_type, self.sequence_number, ts_ms)
        body = header + self.payload
        crc = crc16_ccitt(body)
        raw = body + struct.pack(">H", crc)
        return cobs_encode(raw)

    @classmethod
    def decode(cls, frame: bytes) -> "MDPFrame":
        raw = cobs_decode(frame)
        if len(raw) < 1 + 8 + 8 + 2:
            raise ValueError("Frame too short")

        body, crc_bytes = raw[:-2], raw[-2:]
        crc_expected = struct.unpack(">H", crc_bytes)[0]
        crc_actual = crc16_ccitt(body)
        if crc_actual != crc_expected:
            raise ValueError("CRC mismatch")

        message_type = body[0]
        sequence_number = struct.unpack(">Q", body[1:9])[0]
        ts_ms = struct.unpack(">Q", body[9:17])[0]
        payload = body[17:]
        return cls(
            message_type=message_type,
            sequence_number=sequence_number,
            timestamp=datetime.fromtimestamp(ts_ms / 1000.0),
            payload=payload,
        )
