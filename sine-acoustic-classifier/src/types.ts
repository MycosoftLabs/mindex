/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

export interface DetectorStatus {
  frequency: 'ready' | 'pending' | 'unavailable';
  activity: 'ready' | 'pending' | 'unavailable';
  bird: 'ready' | 'pending' | 'unavailable';
  uav: 'ready' | 'pending' | 'unavailable';
  nps: 'ready' | 'pending' | 'unavailable';
  deep_signal: 'ready' | 'pending' | 'unavailable';
}

export interface AcousticBlob {
  id: string;
  category: string;
  title: string;
  filename: string;
  source_id: string;
  source_name: string;
  source_url: string;
  label_primary: string;
  label_secondary: string;
  acoustic_environment: 'air' | 'water';
  duration_sec: number;
  sample_rate_hz: number;
  channels: number;
  codec: string;
  mime_type: string;
  size_bytes: number;
  license: string;
  content_hash: string;
  stream_url: string;
  analysis_status: 'ready' | 'pending' | 'not_analyzed';
  detector_status: DetectorStatus;
}

export interface FrequencyDetection {
  start_sec: number;
  end_sec: number;
  peak_sec: number;
  freq_hz: number;
  confidence: number;
  type: string;
}

export interface ActivitySegment {
  start_sec: number;
  end_sec: number;
  label: string;
  confidence: number;
}

export interface BirdDetection {
  start_sec: number;
  end_sec: number;
  species: string;
  confidence: number;
  call_type: string;
}

export interface UavDetection {
  start_sec: number;
  end_sec: number;
  drone_class: string;
  confidence: number;
  rpm_estimate: number;
}

export interface NpsDetection {
  start_sec: number;
  end_sec: number;
  profile_code: string;
  confidence: number;
  label_primary: string;
}

export interface DeepSignalMatch {
  label: string;
  score: number;
  source: string;
  segment_start: number;
  segment_end: number;
}

export interface SoundTranscriptEntry {
  start_sec: number;
  end_sec: number;
  label: string;
  description: string;
  sound_source: string;
  confidence: number;
  frequency_range?: string;
}

export interface IdentificationSummary {
  top_label: string;
  category: string;
  type: string;
  confidence: number;
  ood_score: number; // Out-of-domain score
}

export interface AcousticAnalysis {
  ok: boolean;
  blob_id: string;
  model_version: string;
  frontend_version: string;
  identification_summary: IdentificationSummary;
  frequency_detections: FrequencyDetection[];
  activity_segments: ActivitySegment[];
  bird_detections: BirdDetection[];
  uav_detections: UavDetection[];
  nps_detections: NpsDetection[];
  deep_signal_matches: DeepSignalMatch[];
  sound_transcripts?: SoundTranscriptEntry[];
  diagnostics: {
    latency_ms: number;
    sample_rate_in: number;
    sample_rate_model: number;
    quantization: string;
  };
}

export interface WaveformData {
  times: number[];
  amplitudes: number[];
}

export interface SpectrogramData {
  times: number[];
  frequencies: number[];
  power_db: number[][]; // Bins over time
}

export interface AcousticVisualisation {
  ok: boolean;
  duration_sec: number;
  waveform: WaveformData;
  spectrogram: SpectrogramData;
}
