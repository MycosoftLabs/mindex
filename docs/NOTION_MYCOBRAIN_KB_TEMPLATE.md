## MycoBrain Knowledge Base (Notion) - Template

### Database: MycoBrain Knowledge Base
Create a Notion database with these properties:
- Name (title)
- Type (select): Hardware, Firmware, Protocol, Integration, Troubleshooting, Roadmap
- Area (multi-select): MINDEX, NatureOS, MAS, MycoBrain HW, Side-A FW, Side-B FW, Gateway
- Version (text)
- Status (select): Draft, Active, Deprecated
- Owner (people)
- Links (url)
- Tags (multi-select)

### Suggested page templates

#### Hardware specification
- Overview
- Pin mapping
- Power domains
- Bill of materials
- Known errata
- Bring-up checklist

#### Firmware documentation (Side-A / Side-B / Gateway)
- Build + flash steps
- Configuration
- Telemetry output
- Command handling
- Version history
- Troubleshooting

#### Protocol reference (MDP v1)
- Framing: COBS
- Integrity: CRC16
- Envelope fields: message type, sequence number, timestamp
- Message types: telemetry, event, command, ack
- Example frames

#### MINDEX integration guide
- DB schema notes
- API endpoints
- Idempotency (seq + timestamp)
- Device authentication
- Command/setpoint flows

#### NatureOS integration guide
- UI widgets
- Real-time charts
- Command controls
- Device registration and management

#### MAS agent guide
- MDP decoding/encoding
- Retries + ACK handling
- Polling pending commands
- Emitting events to bus

### Living roadmap
Create a second database (Roadmap) or a view filtered by Type=Roadmap with:
- Milestone
- Owner
- Target date
- Dependencies
- Notes / decisions

### Recommended embeds
- Repository links (firmware, protocol, MINDEX)
- Example NDJSON telemetry samples
- Diagrams (pin maps, architecture)

