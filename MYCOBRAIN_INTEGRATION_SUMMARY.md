## MycoBrain V1 integration - summary

### What was added
- Database migration: migrations/0002_mycobrain_integration.sql
  - telemetry.device extensions (device_type, serial_number, firmware_version, api_key_hash, last_seen, mdp sequence)
  - telemetry.mycobrain_device (I2C addresses, analog channel labels, MOSFET states, LoRa config, protocol timestamps)
  - telemetry.device_command (queued downlink commands with retries/status)
  - telemetry.device_setpoint (setpoints/schedules)
  - telemetry.mdp_telemetry_log (idempotent ingestion via device+sequence+timestamp)
  - views: app.v_mycobrain_status, app.v_mycobrain_streams

- API + schemas
  - mindex_api/routers/mycobrain.py
  - mindex_api/schemas/mycobrain.py
  - Wired into FastAPI app via mindex_api/main.py and mindex_api/routers/__init__.py

- Protocol support
  - mindex_api/protocols/mdp_v1.py (COBS framing + CRC16 + frame helpers)

- Documentation
  - docs/MYCOBRAIN_INTEGRATION.md (full integration guide)
  - docs/NOTION_MYCOBRAIN_KB_TEMPLATE.md (Notion KB template)

### Key flows
- Telemetry: MycoBrain -> MAS/Gateway -> POST /mycobrain/telemetry/ingest -> streams + samples
- Commands: NatureOS/MAS -> POST /mycobrain/devices/<id>/commands -> MAS polls pending -> downlink via MDP
- Setpoints: NatureOS/MAS -> POST /mycobrain/devices/<id>/setpoints -> automation generates commands

### Notes
- Telemetry ingestion is designed to be idempotent using MDP sequence number + timestamp.
- Device auth supports per-device keys (stored as SHA-256 hash).
