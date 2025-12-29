"""Versioned contract DTOs: MycoBrain device + telemetry ingestion."""

from ...schemas.mycobrain import (
    CommandCreateRequest,
    CommandResponse,
    TelemetryIngestRequest,
    TelemetryIngestResponse,
    TelemetryPayload,
    MycoBrainDeviceCreate,
    MycoBrainDeviceResponse,
)

# Aliases for backward compatibility
DeviceCommandCreate = CommandCreateRequest
DeviceCommandResponse = CommandResponse
MDPTelemetryIngestionRequest = TelemetryIngestRequest
MDPTelemetryIngestionResponse = TelemetryIngestResponse
MDPTelemetryPayload = TelemetryPayload

__all__ = [
    "MycoBrainDeviceCreate",
    "MycoBrainDeviceResponse",
    "TelemetryPayload",
    "TelemetryIngestRequest",
    "TelemetryIngestResponse",
    "CommandCreateRequest",
    "CommandResponse",
    # Aliases
    "DeviceCommandCreate",
    "DeviceCommandResponse",
    "MDPTelemetryPayload",
    "MDPTelemetryIngestionRequest",
    "MDPTelemetryIngestionResponse",
]

