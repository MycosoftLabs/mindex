"""Offline integrity/framing contracts for MINDEX's seven-byte MDP dialect."""
import binascii
import struct

import pytest

from mindex_api.protocols.mdp_v1 import (
    MDPFrame, MDPMessage, MDPMessageType, cobs_decode, cobs_encode,
    crc16_ccitt, decode_mdp_frame, encode_mdp_frame,
)


def sample():
    return encode_mdp_frame(MDPMessageType.TELEMETRY, {"v": 1}, 0x1234, 0x01020304)


def framed(decoded):
    return b"\x00" + cobs_encode(decoded) + b"\x00"


def with_independent_crc(body):
    return body + struct.pack(">H", binascii.crc_hqx(body, 0xFFFF))


@pytest.mark.parametrize("offset", [-2, -1, 0, 1, 3, 4, 5, 6])
def test_crc_and_header_corruption_is_invalid_but_diagnostic_message_remains(offset):
    decoded = bytearray(cobs_decode(sample()[1:-1]))
    decoded[offset] ^= 1
    result = decode_mdp_frame(framed(bytes(decoded)))
    assert result.message is not None
    assert result.message.crc_valid is False
    assert result.message.raw_data == bytes(decoded)
    assert result.message.payload == {"v": 1}
    assert result.is_valid is False


def test_valid_json_corruption_is_not_a_valid_frame():
    decoded = cobs_decode(sample()[1:-1])
    changed = decoded[:-2].replace(b'"v":1', b'"v":2') + decoded[-2:]
    result = decode_mdp_frame(framed(changed))
    assert result.message is not None and result.message.payload == {"v": 2}
    assert result.message.crc_valid is False
    assert result.is_valid is False


def test_constructed_message_cannot_bypass_integrity_flag():
    message = MDPMessage(1, MDPMessageType.EVENT, 0, {}, crc_valid=False)
    assert MDPFrame(b"", b"", b"", message=message).is_valid is False


@pytest.mark.parametrize("encoded", [b"\x02\x00", b"\x03A\x00", b"\x04A\x00B"])
def test_literal_zero_inside_cobs_block_is_malformed(encoded):
    with pytest.raises(ValueError, match="Zero byte"):
        cobs_decode(encoded)


@pytest.mark.parametrize("timestamp", [0, 0x01020004])
def test_malformed_cobs_cannot_be_accepted_even_with_correct_decoded_crc(timestamp):
    valid = encode_mdp_frame(MDPMessageType.TELEMETRY, {"v": 1}, 0, timestamp)
    decoded = cobs_decode(valid[1:-1])
    assert b"\x00" in decoded and len(decoded) < 254
    # Deliberately claim all bytes as one nonzero block, without encoding zeros.
    result = decode_mdp_frame(b"\x00" + bytes([len(decoded) + 1]) + decoded + b"\x00")
    assert result.message is None
    assert result.is_valid is False
    assert "COBS decode error" in result.decode_error


@pytest.mark.parametrize("encoded", [b"\x02", b"\x03A", b"\xff" + b"A" * 253])
def test_truncated_cobs_blocks_rejected(encoded):
    with pytest.raises(ValueError, match="past end"):
        cobs_decode(encoded)


def test_every_truncated_decoded_frame_is_invalid():
    decoded = cobs_decode(sample()[1:-1])
    for cut in range(len(decoded)):
        result = decode_mdp_frame(framed(decoded[:cut]))
        assert result.is_valid is False, cut


@pytest.mark.parametrize("raw", [
    bytes(range(256)), bytes(range(256)) * 3, b"\x00" * 512,
    b"X" * 253, b"X" * 254, b"X" * 255, b"X" * 508, b"X" * 509,
])
def test_cobs_boundary_and_all_byte_values_round_trip(raw):
    encoded = cobs_encode(raw)
    assert b"\x00" not in encoded
    assert cobs_decode(encoded) == raw


def test_independent_big_endian_wire_vector_and_crc():
    assert binascii.crc_hqx(b"123456789", 0xFFFF) == crc16_ccitt(b"123456789") == 0x29B1
    body = bytes.fromhex("12340101020304") + b'{"v":1}'
    decoded = with_independent_crc(body)
    assert b"\x00" not in decoded
    expected = b"\x00" + bytes([len(decoded) + 1]) + decoded + b"\x00"
    assert sample() == expected
    result = decode_mdp_frame(expected)
    assert result.is_valid and result.message.crc_valid
    assert result.message.sequence_number == 0x1234
    assert result.message.timestamp_ms == 0x01020304
    assert result.message.payload == {"v": 1}


@pytest.mark.parametrize("message_type", list(MDPMessageType))
def test_each_message_type_round_trip_is_crc_valid(message_type):
    payload = {"bytes": list(range(256)), "name": message_type.name}
    result = decode_mdp_frame(encode_mdp_frame(message_type, payload, 42, 123456))
    assert result.is_valid and result.message.crc_valid
    assert result.message.message_type == message_type
    assert result.message.payload == payload


@pytest.mark.parametrize("sequence,timestamp,expected_sequence,expected_timestamp", [
    (0, 0, 0, 0), (65535, 0xFFFFFFFF, 65535, 0xFFFFFFFF),
    (65536, 0x100000000, 0, 0),
])
def test_existing_sequence_and_timestamp_wrap_contract(sequence, timestamp, expected_sequence, expected_timestamp):
    result = decode_mdp_frame(encode_mdp_frame(MDPMessageType.HEARTBEAT, {}, sequence, timestamp))
    assert result.is_valid
    assert result.message.sequence_number == expected_sequence
    assert result.message.timestamp_ms == expected_timestamp


@pytest.mark.parametrize("json_body", [b"{", b"\xff"])
def test_correct_crc_does_not_make_invalid_json_valid(json_body):
    body = bytes.fromhex("00010100000000") + json_body
    result = decode_mdp_frame(framed(with_independent_crc(body)))
    assert result.is_valid is False and result.message is None
    assert "JSON decode error" in result.decode_error


def test_correct_crc_does_not_make_unknown_type_valid():
    body = bytes.fromhex("0001ff00000000") + b"{}"
    result = decode_mdp_frame(framed(with_independent_crc(body)))
    assert result.is_valid is False and result.message is None
    assert "Header parse error" in result.decode_error


def test_minimum_frame_with_empty_json_keeps_existing_behavior():
    body = bytes.fromhex("00010600000000")
    result = decode_mdp_frame(framed(with_independent_crc(body)))
    assert result.is_valid and result.message.payload == {}
