from __future__ import annotations

from mindex_api.services.sine_acoustic.request_contract import read_sine_request_contract


class _FakeRequest:
    def __init__(self) -> None:
        self.query_params = {
            "require_model_evidence": "true",
            "allow_detector_only": "true",
            "semantic_fallback": "false",
        }

    async def json(self) -> dict[str, object]:
        return {
            "evidence_contract": {
                "require_registered_model_for_identification_summary": True,
                "requested_outputs": ["model_outputs", "sound_transcripts"],
            },
            "sine_request": {
                "target_domains": ["water", "air", "ground"],
                "sound_targets": ["whale_vocalization", "lightning_thunder"],
                "visualisation_quality": {"max_waveform_points": 8192},
            },
        }


async def test_read_sine_request_contract_preserves_model_requirements() -> None:
    contract = await read_sine_request_contract(_FakeRequest())

    assert contract["status"] == "provided"
    assert contract["body_status"] == "json"
    assert contract["requires_registered_model"] is True
    assert contract["allows_detector_only"] is True
    assert contract["requested_outputs"] == ["model_outputs", "sound_transcripts"]
    assert contract["target_domains"] == ["water", "air", "ground"]
    assert contract["sound_targets"] == ["whale_vocalization", "lightning_thunder"]
    assert contract["evidence_query"]["semantic_fallback"] == "false"
