"""
MDP v1 Protocol implementation for MycoBrain device communication.

This module provides COBS framing, CRC16 validation, and message parsing
for the Mycosoft Device Protocol used by MycoBrain and other devices.
"""

from .mdp_v1 import (
    MDPFrame,
    MDPMessage,
    MDPMessageType,
    cobs_decode,
    cobs_encode,
    crc16_ccitt,
    decode_mdp_frame,
    encode_mdp_frame,
    parse_ndjson_telemetry,
    validate_crc,
)
from .mycorrhizae import (
    MycorrhizaeChannel,
    MycorrhizaeMessage,
    MycorrhizaeProtocol,
)

__all__ = [
    # MDP v1
    "MDPFrame",
    "MDPMessage",
    "MDPMessageType",
    "cobs_encode",
    "cobs_decode",
    "crc16_ccitt",
    "validate_crc",
    "encode_mdp_frame",
    "decode_mdp_frame",
    "parse_ndjson_telemetry",
    # Mycorrhizae Protocol
    "MycorrhizaeChannel",
    "MycorrhizaeMessage",
    "MycorrhizaeProtocol",
]


