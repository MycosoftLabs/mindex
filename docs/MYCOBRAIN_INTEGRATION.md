# MycoBrain Integration with MINDEX

This document describes the complete integration between **MycoBrain V1** hardware and the **MINDEX** data platform, including the **Mycorrhizae Protocol** for NatureOS bridging and **MAS** (Mycosoft Agent Service) coordination.

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [MDP v1 Protocol](#mdp-v1-protocol)
3. [Device Registration](#device-registration)
4. [Telemetry Ingestion](#telemetry-ingestion)
5. [Command Queue](#command-queue)
6. [Mycorrhizae Protocol](#mycorrhizae-protocol)
7. [NatureOS Integration](#natureos-integration)
8. [MAS Agent Integration](#mas-agent-integration)
9. [Database Schema](#database-schema)
10. [API Reference](#api-reference)
11. [Configuration](#configuration)
12. [Troubleshooting](#troubleshooting)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           MYCOSOFT ECOSYSTEM                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐               │
│  │  MycoBrain   │    │  Mushroom 1  │    │  SporeBase   │    Devices    │
│  │   (ESP32)    │    │   (ESP32)    │    │   (ESP32)    │               │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘               │
│         │ MDP v1            │                    │                       │
│         │ LoRa/UART         │ WiFi               │ WiFi                  │
│         ▼                   ▼                    ▼                       │
│  ┌──────────────────────────────────────────────────────────────┐       │
│  │                    MAS - INGESTION AGENTS                     │       │
│  │  • COBS decode      • CRC validation      • NDJSON parse     │       │
│  │  • Device auth      • Rate limiting       • Batching          │       │
│  └──────────────────────────────┬───────────────────────────────┘       │
│                                 │                                        │
│                                 ▼                                        │
│  ┌──────────────────────────────────────────────────────────────┐       │
│  │                         MINDEX API                            │       │
│  │  /mycobrain/devices      - Device registration & management  │       │
│  │  /mycobrain/telemetry    - Sensor data ingestion             │       │
│  │  /mycobrain/commands     - Bi-directional command queue      │       │
│  │  /mycobrain/mycorrhizae  - Protocol bridge to NatureOS       │       │
│  └──────────────────────────────┬───────────────────────────────┘       │
│                                 │                                        │
│         ┌───────────────────────┼───────────────────────┐               │
│         ▼                       ▼                       ▼               │
│  ┌─────────────┐    ┌─────────────────────┐    ┌─────────────────┐     │
│  │  PostgreSQL │    │ Mycorrhizae Protocol│    │    NatureOS     │     │
│  │   + PostGIS │    │   (Pub/Sub Bridge)  │    │   Dashboard     │     │
│  └─────────────┘    └─────────────────────┘    └─────────────────┘     │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### MycoBrain Hardware

MycoBrain V1 is a dual-ESP32-S3 board with:

| Component | Purpose |
|-----------|---------|
| **Side-A (Sensor MCU)** | I²C sensors, analog inputs (AI1-AI4), MOSFET outputs (M1-M4) |
| **Side-B (Router MCU)** | SX1262 LoRa radio, UART routing, command acknowledgement |
| **SX1262 LoRa** | Long-range (5-15km) telemetry transmission |
| **BME688** | Temperature, humidity, pressure, gas/IAQ |

---

## MDP v1 Protocol

The **Mycosoft Device Protocol v1** provides reliable, framed communication between devices and the MINDEX platform.

### Frame Structure

```
┌───────┬───────────────────────┬───────┐
│ 0x00  │   COBS-encoded data   │ 0x00  │
└───────┴───────────────────────┴───────┘
         Frame delimiters
```

### Payload Structure (before COBS encoding)

```
┌─────────┬──────┬────────────┬──────────────┬─────────┐
│ seq (2) │ type │ timestamp  │   JSON data  │ CRC16   │
│  bytes  │ (1)  │   (4 ms)   │   (N bytes)  │ (2)     │
└─────────┴──────┴────────────┴──────────────┴─────────┘
```

### Message Types

| Type | Value | Direction | Description |
|------|-------|-----------|-------------|
| `TELEMETRY` | 0x01 | Device → Server | Sensor readings, status updates |
| `COMMAND` | 0x02 | Server → Device | Control commands (MOSFET, config) |
| `EVENT` | 0x03 | Device → Server | One-time events (button press, alert) |
| `ACK` | 0x04 | Bidirectional | Positive acknowledgement |
| `NACK` | 0x05 | Bidirectional | Negative acknowledgement |
| `HEARTBEAT` | 0x06 | Device → Server | Keep-alive signal |
| `DISCOVERY` | 0x07 | Device → Server | Device announcement |

### Python Library Usage

```python
from mindex_api.protocols import (
    cobs_encode,
    cobs_decode,
    crc16_ccitt,
    encode_mdp_frame,
    decode_mdp_frame,
    MDPMessageType,
    CommandBuilder,
)

# Encode a command frame
frame = encode_mdp_frame(
    message_type=MDPMessageType.COMMAND,
    payload=CommandBuilder.set_mosfet(1, True),
    sequence_number=42,
)

# Decode an incoming frame
result = decode_mdp_frame(incoming_bytes)
if result.is_valid:
    print(f"Sequence: {result.message.sequence_number}")
    print(f"Payload: {result.message.payload}")
```

---

## Device Registration

### Register a New Device

```http
POST /mycobrain/devices
Content-Type: application/json
X-API-Key: your-api-key

{
  "serial_number": "MCB-2024-001234",
  "device_type": "mycobrain_v1",
  "name": "Fruiting Chamber #1",
  "location_name": "Greenhouse A",
  "purpose": "Lion's Mane cultivation",
  "telemetry_interval_ms": 5000,
  "analog_channels": {
    "AI1": {"label": "Substrate Moisture", "unit": "%", "min": 0, "max": 100},
    "AI2": {"label": "CO2 Level", "unit": "ppm", "min": 400, "max": 5000}
  },
  "lora_config": {
    "frequency_mhz": 915.0,
    "spreading_factor": 7,
    "bandwidth_khz": 125.0
  },
  "taxon_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### Generate Device API Key

```http
POST /mycobrain/devices/{device_id}/api-key
X-API-Key: your-api-key
```

Response:
```json
{
  "device_id": "...",
  "serial_number": "MCB-2024-001234",
  "api_key": "mcb_AbCdEf123456789...",
  "api_key_prefix": "mcb_AbCdEf12",
  "created_at": "2024-12-15T10:30:00Z"
}
```

> ⚠️ **Important**: The full API key is only shown once. Store it securely!

---

## Telemetry Ingestion

### Single Reading

```http
POST /mycobrain/telemetry/ingest
Content-Type: application/json
X-API-Key: device-api-key

{
  "serial_number": "MCB-2024-001234",
  "sequence_number": 1234,
  "device_timestamp_ms": 1734270000000,
  "payload": {
    "bme688": {
      "temperature_c": 24.5,
      "humidity_percent": 85.2,
      "pressure_hpa": 1013.25,
      "gas_resistance_ohms": 125000,
      "iaq_index": 42
    },
    "analog": [
      {"channel": "AI1", "voltage": 2.1, "calibrated_value": 65.0, "calibrated_unit": "%"},
      {"channel": "AI2", "voltage": 1.8, "calibrated_value": 1200, "calibrated_unit": "ppm"}
    ],
    "mosfet_states": {"M1": true, "M2": false, "M3": false, "M4": true},
    "usb_power": true,
    "battery_v": 4.1
  }
}
```

### Batch Ingestion

For high-frequency data or Gateway aggregation:

```http
POST /mycobrain/telemetry/ingest/batch
Content-Type: application/json
X-API-Key: gateway-api-key

{
  "items": [
    {"serial_number": "MCB-001", "payload": {...}},
    {"serial_number": "MCB-002", "payload": {...}},
    ...
  ]
}
```

### NDJSON Gateway Format

The Gateway firmware outputs NDJSON that can be streamed directly:

```json
{"ts":1734270000000,"dev":"MCB-001","type":"telemetry","data":{"temp":24.5,"hum":85.2}}
{"ts":1734270001000,"dev":"MCB-001","type":"telemetry","data":{"temp":24.6,"hum":85.1}}
```

Parse with:
```python
from mindex_api.protocols import parse_ndjson_telemetry

for line in stream:
    data = parse_ndjson_telemetry(line)
    if data:
        # Send to MINDEX API
```

---

## Command Queue

Commands are queued in MINDEX and polled by ingestion agents for delivery to devices.

### Queue a Command

```http
POST /mycobrain/commands
Content-Type: application/json

{
  "device_id": "550e8400-e29b-41d4-a716-446655440000",
  "command_type": "mosfet_control",
  "command_payload": {
    "cmd": "mosfet",
    "target": "M1",
    "value": true
  },
  "priority": 1,
  "max_retries": 3
}
```

### Shortcut Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /{device_id}/mosfet` | Toggle MOSFET output |
| `POST /{device_id}/telemetry-interval` | Set reporting interval |
| `POST /{device_id}/i2c-scan` | Request I²C bus scan |
| `POST /{device_id}/reboot` | Reboot device |
| `POST /{device_id}/firmware-update` | OTA firmware update |

### Poll Pending Commands (Agent)

```http
GET /mycobrain/commands/pending/{device_id}?limit=10
```

### Acknowledge Command

```http
POST /mycobrain/commands/{command_id}/ack

{
  "command_id": "...",
  "success": true,
  "response_payload": {"executed_at": 1734270005000}
}
```

### Command Flow

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   NatureOS   │     │    MINDEX    │     │  MAS Agent   │     │  MycoBrain   │
│   Dashboard  │     │     API      │     │  (Gateway)   │     │   Device     │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │                    │
       │  1. User clicks    │                    │                    │
       │     "Turn on fan"  │                    │                    │
       │ ──────────────────>│                    │                    │
       │                    │                    │                    │
       │                    │  2. Queue command  │                    │
       │                    │     status=pending │                    │
       │                    │ ──────────────────>│                    │
       │                    │                    │                    │
       │                    │                    │  3. Poll pending   │
       │                    │                    │<────────────────── │
       │                    │                    │                    │
       │                    │                    │  4. MDP COMMAND    │
       │                    │                    │ ──────────────────>│
       │                    │                    │                    │
       │                    │                    │  5. MDP ACK        │
       │                    │                    │<────────────────── │
       │                    │                    │                    │
       │                    │  6. ACK command    │                    │
       │                    │<────────────────── │                    │
       │                    │                    │                    │
       │  7. UI update      │                    │                    │
       │<────────────────── │                    │                    │
```

---

## Mycorrhizae Protocol

The **Mycorrhizae Protocol** bridges MINDEX data to NatureOS and external consumers via pub/sub channels.

### Channel Types

| Type | Description | Example |
|------|-------------|---------|
| `device` | Direct device telemetry | `device.MCB-001` |
| `aggregate` | Combined data from multiple devices | `aggregate.environmental` |
| `computed` | AI/ML derived insights | `insight.growth_prediction` |

### List Channels

```http
GET /mycobrain/mycorrhizae/channels
```

### Publish Message

```http
POST /mycobrain/mycorrhizae/publish

{
  "channel_name": "aggregate.environmental",
  "message_type": "telemetry",
  "device_serial": "MCB-001",
  "payload": {
    "temperature_c": 24.5,
    "humidity_percent": 85.2
  }
}
```

### Subscribe via SSE

```javascript
const eventSource = new EventSource('/mycobrain/mycorrhizae/stream/device.MCB-001');

eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Telemetry:', data.payload);
};
```

### Python Usage

```python
from mindex_api.protocols import (
    get_protocol,
    MycorrhizaeMessage,
    MycorrhizaeChannel,
    ChannelType,
)

protocol = get_protocol()

# Subscribe to a channel
def on_telemetry(msg: MycorrhizaeMessage):
    print(f"Device {msg.device_serial}: {msg.payload}")

protocol.subscribe("device.MCB-001", on_telemetry)

# Publish a message
msg = MycorrhizaeMessage(
    channel="aggregate.environmental",
    device_serial="MCB-001",
    payload={"temperature_c": 24.5},
)
protocol.publish(msg)
```

---

## NatureOS Integration

NatureOS consumes MINDEX data via the Mycorrhizae Protocol to power dashboards and widgets.

### Widget Configuration

```http
POST /mycobrain/devices/{device_id}/widget

{
  "widget_type": "mycobrain_dashboard",
  "display_name": "Fruiting Chamber #1",
  "layout_config": {
    "show_temperature": true,
    "show_humidity": true,
    "show_mosfet_controls": true,
    "chart_duration_hours": 24
  },
  "bound_streams": ["temperature", "humidity", "iaq", "AI1", "AI2"],
  "refresh_interval_ms": 5000,
  "visibility": "shared",
  "shared_with": ["team-id-1", "team-id-2"]
}
```

### Real-time Data Flow

1. Device sends telemetry via MDP v1
2. MAS Agent ingests into MINDEX
3. MINDEX publishes to Mycorrhizae channel
4. NatureOS widget subscribes via SSE
5. Dashboard updates in real-time

---

## MAS Agent Integration

MAS (Mycosoft Agent Service) agents handle the bridge between physical devices and MINDEX.

### Agent Architecture

```python
# Example MAS Device Agent structure
class MycoBrainAgent:
    def __init__(self, serial_port: str, mindex_api_key: str):
        self.serial = serial.Serial(serial_port, 115200)
        self.api = MINDEXClient(api_key=mindex_api_key)
        self.tx_queue = asyncio.Queue()
        self.pending_acks = {}
    
    async def run(self):
        await asyncio.gather(
            self._read_loop(),
            self._write_loop(),
            self._poll_commands(),
        )
    
    async def _read_loop(self):
        """Read and decode incoming MDP frames."""
        buffer = bytearray()
        while True:
            data = await asyncio.to_thread(self.serial.read, 256)
            buffer.extend(data)
            
            # Extract complete frames
            while b'\x00' in buffer[1:]:
                end = buffer.index(b'\x00', 1)
                frame = bytes(buffer[:end+1])
                buffer = buffer[end+1:]
                
                result = decode_mdp_frame(frame)
                if result.is_valid:
                    await self._handle_message(result.message)
    
    async def _handle_message(self, msg: MDPMessage):
        if msg.message_type == MDPMessageType.TELEMETRY:
            await self.api.ingest_telemetry(msg.to_dict())
        elif msg.message_type == MDPMessageType.ACK:
            self._resolve_pending_ack(msg)
    
    async def _poll_commands(self):
        """Poll MINDEX for pending commands."""
        while True:
            commands = await self.api.get_pending_commands(self.device_id)
            for cmd in commands:
                frame = encode_mdp_frame(
                    MDPMessageType.COMMAND,
                    cmd.command_payload,
                    cmd.sequence_number,
                )
                await self.tx_queue.put(frame)
                self.pending_acks[cmd.sequence_number] = cmd.id
            await asyncio.sleep(1)
```

### Environment Configuration

```bash
# .env for MAS Agent
MAS_MINDEX_API_URL=https://api.mindex.mycosoft.org
MAS_MINDEX_API_KEY=mcb_AbCdEf123...
MAS_DEVICE_SERIAL_PORT=/dev/ttyUSB0
MAS_LORA_FREQUENCY_MHZ=915.0
```

---

## Database Schema

### Schema: `mycobrain`

```sql
-- Device registry
mycobrain.device
  ├── id (uuid)
  ├── telemetry_device_id → telemetry.device
  ├── serial_number (unique)
  ├── device_type (enum)
  ├── firmware_version_a/b
  ├── api_key_hash
  ├── i2c_addresses (jsonb)
  ├── analog_channels (jsonb)
  ├── mosfet_states (jsonb)
  ├── lora_* (config fields)
  ├── telemetry_interval_ms
  ├── last_seen_at
  └── location_name, purpose

-- MDP frame logging
mycobrain.mdp_frame
  ├── id, device_id
  ├── sequence_number, message_type
  ├── raw_cobs_frame, decoded_payload
  ├── crc16_valid
  └── received_at

-- Command queue
mycobrain.command_queue
  ├── id, device_id
  ├── command_type, command_payload
  ├── priority, status (enum)
  ├── retry_count, max_retries
  ├── scheduled_at, sent_at, acked_at
  └── response_payload, error_message

-- Sensor readings
mycobrain.bme688_reading
  ├── device_id, stream_id
  ├── temperature_c, humidity_percent
  ├── pressure_hpa, gas_resistance_ohms
  ├── iaq_index, altitude_m, dew_point_c
  └── recorded_at, device_timestamp_ms

mycobrain.analog_reading
  ├── device_id, stream_id, channel
  ├── raw_adc_count, voltage
  ├── calibrated_value, calibrated_unit
  └── recorded_at

-- Automation
mycobrain.automation_rule
  ├── device_id, name, enabled
  ├── trigger_stream, trigger_operator
  ├── trigger_value, trigger_duration_ms
  ├── action_type, action_target
  └── cooldown_ms, last_triggered_at

-- NatureOS integration
mycobrain.natureos_widget
mycobrain.mycorrhizae_subscription
```

### Apply Migration

```bash
# Using the migration script
python scripts/apply_migrations.py

# Or manually
psql -d mindex -f migrations/0002_mycobrain.sql
```

---

## API Reference

### Device Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/mycobrain/devices` | List all devices |
| POST | `/mycobrain/devices` | Register new device |
| GET | `/mycobrain/devices/{id}` | Get device by ID |
| GET | `/mycobrain/devices/serial/{serial}` | Get device by serial |
| POST | `/mycobrain/devices/{id}/api-key` | Generate API key |
| GET | `/mycobrain/devices/{id}/readings` | Get latest readings |

### Telemetry Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/mycobrain/telemetry/ingest` | Ingest single reading |
| POST | `/mycobrain/telemetry/ingest/batch` | Batch ingest |

### Command Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/mycobrain/commands` | Queue command |
| GET | `/mycobrain/commands` | List commands |
| GET | `/mycobrain/commands/pending/{id}` | Get pending (for agents) |
| POST | `/mycobrain/commands/{id}/ack` | Acknowledge command |
| POST | `/mycobrain/commands/{id}/mosfet` | Control MOSFET |
| POST | `/mycobrain/commands/{id}/reboot` | Reboot device |

### Mycorrhizae Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/mycobrain/mycorrhizae/channels` | List channels |
| POST | `/mycobrain/mycorrhizae/publish` | Publish message |
| GET | `/mycobrain/mycorrhizae/channels/{name}/messages` | Get recent |
| GET | `/mycobrain/mycorrhizae/stream/{name}` | SSE stream |

---

## Configuration

### Environment Variables

```bash
# MycoBrain Settings
MYCOBRAIN_DEVICE_TIMEOUT_SECONDS=120
MYCOBRAIN_DEVICE_OFFLINE_SECONDS=600
MYCOBRAIN_MAX_BATCH_SIZE=1000
MYCOBRAIN_TELEMETRY_RETENTION_DAYS=90
MYCOBRAIN_COMMAND_DEFAULT_TTL_SECONDS=3600
MYCOBRAIN_COMMAND_MAX_RETRIES=3

# MDP Protocol
MDP_ENABLE_RAW_FRAME_LOGGING=false
MDP_CRC_STRICT_MODE=true

# Mycorrhizae Protocol
MYCORRHIZAE_DEFAULT_CHANNEL_BUFFER=100
MYCORRHIZAE_MAX_MESSAGE_TTL_SECONDS=86400

# NatureOS Integration
NATUREOS_API_ENDPOINT=https://natureos.mycosoft.org/api
NATUREOS_WEBHOOK_SECRET=your-secret

# MAS Integration
MAS_API_ENDPOINT=https://mas.mycosoft.org/api
MAS_DEVICE_AGENT_ENABLED=false
```

---

## Troubleshooting

### Device Not Appearing Online

1. Check `last_seen_at` in device record
2. Verify LoRa frequency matches Gateway configuration
3. Ensure API key is valid (check `api_key_prefix`)
4. Review MAS agent logs for CRC failures

### CRC Validation Failures

```python
# Temporarily disable strict mode for debugging
MDP_CRC_STRICT_MODE=false
MDP_ENABLE_RAW_FRAME_LOGGING=true
```

Check logs for raw COBS frames and decode manually:

```python
from mindex_api.protocols import cobs_decode, crc16_ccitt

raw = bytes.fromhex("...")  # From logs
decoded = cobs_decode(raw)
crc_ok = crc16_ccitt(decoded[:-2]) == int.from_bytes(decoded[-2:], 'big')
```

### Commands Not Reaching Device

1. Verify command status is `pending` (not `expired`)
2. Check `expires_at` timestamp
3. Ensure agent is polling `/commands/pending/{device_id}`
4. Review agent's ACK handling

### Mycorrhizae Channel Empty

1. Verify telemetry is being ingested successfully
2. Check channel exists in `list_channels`
3. Confirm publisher is calling `protocol.publish()`
4. For SSE, ensure no proxy buffering (check headers)

---

## Best Practices

### Telemetry

- **Best-effort delivery**: Treat telemetry as lossy; don't block on failures
- **Sequence numbers**: Use for deduplication, not ordering
- **Batching**: Aggregate 10-100 readings per API call from Gateways

### Commands

- **Reliable delivery**: Always wait for ACK, implement retry logic
- **Idempotency**: Commands should be safe to retry (use command IDs)
- **TTL**: Set appropriate expiration (shorter for real-time, longer for scheduled)

### Security

- **API keys**: Rotate device API keys periodically
- **TLS**: Always use HTTPS in production
- **Rate limiting**: Implement at Gateway/MAS level

---

## Related Documentation

- [MycoBrain Hardware README](https://github.com/mycosoft/mycobrain)
- [MINDEX API Docs](/docs)
- [NatureOS Developer Guide](https://docs.mycosoft.org/natureos)
- [MAS Agent Documentation](https://docs.mycosoft.org/mas)

---

*Last updated: December 2024 • MINDEX v0.2.0 • MycoBrain Integration*


