## MycoBrain V1 integration (MINDEX)

This repo includes first-class support for MycoBrain V1 as a device type.

### What is supported
- Device registration and inventory (telemetry.device + telemetry.mycobrain_device)
- Telemetry ingestion (idempotent using MDP sequence number + timestamp)
- Normalized samples in telemetry.stream + telemetry.sample
- Downlink command queue (telemetry.device_command)
- Setpoints/schedules storage (telemetry.device_setpoint)

### Migration
Apply migrations including:
- migrations/0002_mycobrain_integration.sql

### API endpoints
All MycoBrain endpoints live under /mycobrain
- POST /mycobrain/devices
- POST /mycobrain/telemetry/ingest
- GET /mycobrain/devices/<id>/status
- POST /mycobrain/devices/<id>/commands

### Telemetry payload
Telemetry ingestion accepts JSON with:
- mdp_sequence_number
- mdp_timestamp
- device_serial_number
- bme688 fields (temperature, humidity, pressure, gas)
- ai1..ai4 voltage
- mosfet_states map

### MAS / Gateway
Recommended flow:
MycoBrain -> Gateway/LoRa -> MAS agent -> MINDEX /mycobrain/telemetry/ingest

### NatureOS
NatureOS should read MINDEX telemetry and create commands/setpoints via the MycoBrain endpoints.

See:
- MYCOBRAIN_INTEGRATION_SUMMARY.md
- docs/NOTION_MYCOBRAIN_KB_TEMPLATE.md
