/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import express from "express";
import path from "path";
import { createServer as createViteServer } from "vite";
import { mockAcousticBlobs } from "./src/data/acousticData";
import { GoogleGenAI, Type } from "@google/genai";
import dotenv from "dotenv";

dotenv.config();

const app = express();
const PORT = 3000;

app.use(express.json());

// Initialize Gemini client using Google GenAI SDK (only server-side)
const ai = process.env.GEMINI_API_KEY
  ? new GoogleGenAI({
      apiKey: process.env.GEMINI_API_KEY,
      httpOptions: {
        headers: {
          "User-Agent": "aistudio-build",
        },
      },
    })
  : null;

// Programmatic mathematical WAV Generator mimicking real acoustic structures
function generateWavBuffer(itemId: string, durationSec: number, sampleRate: number): Buffer {
  const numSamples = Math.floor(durationSec * sampleRate);
  const blockAlign = 2; // 1 channel * 16-bit
  const byteRate = sampleRate * blockAlign;
  const dataSize = numSamples * blockAlign;
  const formatSize = 16;
  const chunkSize = 36 + dataSize;

  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(chunkSize, 4);
  header.write("WAVE", 8);
  header.write("fmt ", 12);
  header.writeUInt32LE(formatSize, 16);
  header.writeUInt16LE(1, 20); // PCM format
  header.writeUInt16LE(1, 22); // Mono
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(byteRate, 28);
  header.writeUInt16LE(blockAlign, 32);
  header.writeUInt16LE(16, 34); // Bits per sample
  header.write("data", 36);
  header.writeUInt32LE(dataSize, 40);

  const data = Buffer.alloc(dataSize);
  for (let i = 0; i < numSamples; i++) {
    const t = i / sampleRate;
    let sampleVal = 0;

    if (itemId.includes("humpback")) {
      // Sweeping frequency whistles (Megaptera) + background ocean hum
      const whistle = Math.sin(2 * Math.PI * (280 + 130 * Math.sin(2 * Math.PI * 0.3 * t)) * t);
      const subHum = Math.sin(2 * Math.PI * 44 * t) * (0.8 + 0.2 * Math.sin(2 * Math.PI * 0.1 * t));
      sampleVal = whistle * 0.35 + subHum * 0.55;
    } else if (itemId.includes("canis")) {
      // Periodically barking dog - gated broad-band bursts
      const patternPeriod = 1.2;
      const tInPattern = t % patternPeriod;
      if (tInPattern < 0.25 || (tInPattern > 0.45 && tInPattern < 0.7)) {
        // Broadband canine voice harmonic excitation
        const bandNoise = (Math.random() - 0.5) * 0.35;
        const formants = Math.sin(2 * Math.PI * 320 * t) + Math.sin(2 * Math.PI * 750 * t);
        sampleVal = (bandNoise + formants * 0.15) * Math.sin(2 * Math.PI * 150 * t);
      }
    } else if (itemId.includes("uav") || itemId.includes("phantom")) {
      // Quadcopter Hum, Blade Passage pitch (120Hz fundamental) + dynamic harmonics
      const f0 = 120;
      const dronePitch = f0 + (itemId.includes("hover") ? 2 * Math.sin(2 * Math.PI * 1 * t) : 15 * t);
      const bpf = Math.sin(2 * Math.PI * dronePitch * t);
      const h1 = Math.sin(2 * Math.PI * 2 * dronePitch * t);
      const h2 = Math.sin(2 * Math.PI * 3 * dronePitch * t);
      const h3 = Math.sin(2 * Math.PI * 4 * dronePitch * t);
      const rotorMod = 0.8 + 0.15 * Math.sin(2 * Math.PI * 12 * t);
      sampleVal = (bpf * 0.45 + h1 * 0.25 + h2 * 0.15 + h3 * 0.1) * rotorMod;
    } else if (itemId.includes("turdus") || itemId.includes("robin")) {
      // Avian warble - high-pitched elegant whistles with rapid FM cycles
      const singerGate = (t % 1.5) < 1.0;
      if (singerGate) {
        const fmFreq = Math.sin(2 * Math.PI * 14 * t);
        const pitch = 2200 + 900 * fmFreq;
        sampleVal = Math.sin(2 * Math.PI * pitch * t) * 0.4;
      }
    } else if (itemId.includes("odontocete") || itemId.includes("clicks")) {
      // Odontocete echolocation click train overlayed with high dolphin whistle
      const clickInterval = 0.04 - 0.015 * Math.sin(2 * Math.PI * 0.4 * t);
      const moduloInClick = t % clickInterval;
      if (moduloInClick < 0.002) {
        sampleVal = Math.sin(2 * Math.PI * 18000 * t) * Math.exp(-2500 * moduloInClick) * 0.7;
      }
      const highWhistle = Math.sin(2 * Math.PI * (8000 + 2400 * Math.cos(2 * Math.PI * 0.8 * t)) * t) * 0.15;
      sampleVal += highWhistle;
    } else if (itemId.includes("ship") || itemId.includes("vessel") || itemId.includes("propeller")) {
      // Cavitating commercial ship - baseline broad white hiss and slow heavy motor rotation
      const motorBase = Math.sin(2 * Math.PI * 68 * t);
      const bladePassage = 0.7 + 0.3 * Math.sin(2 * Math.PI * 5.8 * t); // rhythmic modulation
      const whiteHiss = (Math.random() - 0.5) * 0.24 * bladePassage;
      sampleVal = motorBase * 0.35 * bladePassage + whiteHiss;
    } else if (itemId.includes("explosion") || itemId.includes("impulse")) {
      // Massive undersea explosive charge + immediate environmental reverb
      const impulseStart = 1.2;
      if (t > impulseStart) {
        const elapsed = t - impulseStart;
        const ringSound = Math.sin(2 * Math.PI * 55 * t) + Math.sin(2 * Math.PI * 85 * t);
        const broadbandShatter = (Math.random() - 0.5) * 0.5;
        sampleVal = (ringSound * 0.4 + broadbandShatter * 0.6) * Math.exp(-2.4 * elapsed);
      }
    } else {
      // Simple physics diagnostic tone
      sampleVal = Math.sin(2 * Math.PI * 440 * t) * 0.3;
    }

    sampleVal = Math.max(-1, Math.min(1, sampleVal));
    const sampleInt16 = Math.floor(sampleVal * 32767);
    data.writeInt16LE(sampleInt16, i * 2);
  }

  return Buffer.concat([header, data]);
}

// ---------------- API ENDPOINTS ----------------

// System Health Checks & Classifier Status
app.get("/api/mindex/health", (req, res) => {
  res.json({ ok: true, status: "healthy", db: "ok", remote_nas: true });
});

app.get("/api/mindex/sine/status", (req, res) => {
  res.json({
    ok: true,
    model_version: "SINE-Embed-v1.0.0",
    engine: "CRNN-ResNet PyTorch/ONNX Core",
    quantization: "FP16 (CUDA Accelerated / ARM Optimised)",
    detectors: [
      "frequency_fft",
      "activity_auditok",
      "bird_microsoft",
      "uav_rotor",
      "nps_discovery_match",
      "deep_signal_features",
    ],
    last_calibration_time: "2026-06-04T12:00:00Z",
  });
});

app.get("/api/mindex/sine/detectors", (req, res) => {
  res.json({
    ok: true,
    detectors: [
      { id: "frequency_fft", label: "FFT Dom. Frequency Detector", status: "ready" },
      { id: "activity_auditok", label: "Auditok Acoustic Activity Segments", status: "ready" },
      { id: "bird_microsoft", label: "Microsoft Bioacoustic Bird Classifier", status: "ready" },
      { id: "uav_rotor", label: "Quadcopter Rotor Harmonic Analyser", status: "ready" },
      { id: "nps_discovery_match", label: "NPS Ecological Profile Matcher", status: "ready" },
      { id: "deep_signal_features", label: "SINE-Embed-v1 512-d Neural Similarity Engine", status: "ready" },
    ],
  });
});

// Library Storage Details
app.get("/api/mindex/library/storage", (req, res) => {
  res.json({
    ok: true,
    remote_nas: true,
    policy: "ok",
    storage_type: "CIFS NAS Mounted Array",
    path: "/mnt/nas/mindex/Library/acoustic",
    total_space_tb: 7.2,
    free_space_tb: 5.86,
    mindex_database_host: "mindex-postgres-db",
    total_blobs: mockAcousticBlobs.length,
  });
});

// Library & Catalog Discovery
app.get("/api/natureos/mindex/library", (req, res) => {
  res.json({
    ok: true,
    category: "acoustic",
    total: mockAcousticBlobs.length,
    limit: 100,
    items: mockAcousticBlobs,
  });
});

app.get("/api/mindex/library/blobs", (req, res) => {
  res.json({
    ok: true,
    total: mockAcousticBlobs.length,
    items: mockAcousticBlobs,
  });
});

app.get("/api/mindex/library/blobs/:id", (req, res) => {
  const blob = mockAcousticBlobs.find((b) => b.id === req.params.id);
  if (!blob) {
    return res.status(404).json({ ok: false, error: "Blob item not loaded in SINE directory" });
  }
  res.json({ ok: true, blob });
});

// Programmatic streaming route with correct headers and range support for browsers
app.get("/api/mindex/library/blobs/:id/stream", (req, res) => {
  const blob = mockAcousticBlobs.find((b) => b.id === req.params.id);
  if (!blob) {
    return res.status(404).json({ ok: false, error: "Acoustic file not found in database catalog" });
  }

  // Synthesize customized wave content
  const sampleRate = blob.sample_rate_hz || 16000;
  const duration = blob.duration_sec || 5.0;
  const wavBuffer = generateWavBuffer(blob.id, duration, sampleRate);

  res.set("Content-Type", "audio/wav");
  res.set("Accept-Ranges", "bytes");
  res.set("Content-Length", wavBuffer.length.toString());

  // Handle range-requests from browser audio players
  const range = req.headers.range;
  if (range) {
    const parts = range.replace(/bytes=/, "").split("-");
    const start = parseInt(parts[0], 10);
    const end = parts[1] ? parseInt(parts[1], 10) : wavBuffer.length - 1;
    const chunksize = end - start + 1;
    const chunk = wavBuffer.subarray(start, end + 1);

    res.status(206);
    res.set("Content-Range", `bytes ${start}-${end}/${wavBuffer.length}`);
    res.set("Content-Length", chunksize.toString());
    res.send(chunk);
  } else {
    res.send(wavBuffer);
  }
});

// Acoustic Visualisation generation (waveform envelopes & complete spectrogram matrices)
app.get("/api/mindex/sine/blobs/:id/visualisation", (req, res) => {
  const blob = mockAcousticBlobs.find((b) => b.id === req.params.id);
  if (!blob) {
    return res.status(404).json({ ok: false, error: "Acoustic blob not found" });
  }

  const durationSec = blob.duration_sec;
  const steps = 140;

  // Assemble realistic, mathematically mapped waveform amplitude envelopes
  const times: number[] = [];
  const amplitudes: number[] = [];
  for (let i = 0; i < steps; i++) {
    const t = (i / steps) * durationSec;
    times.push(t);

    let amp = 0.05 + 0.1 * Math.sin(2 * Math.PI * 0.1 * t);
    if (blob.id.includes("humpback")) {
      amp = 0.2 + 0.15 * Math.sin(Math.PI * (t / durationSec)) + 0.1 * Math.sin(2 * Math.PI * 0.55 * t);
    } else if (blob.id.includes("canis")) {
      const p = t % 1.2;
      amp = p < 0.25 || (p > 0.45 && p < 0.7) ? 0.65 + 0.25 * Math.sin(140 * t) : 0.02;
    } else if (blob.id.includes("uav")) {
      amp = 0.55 + 0.1 * Math.sin(2 * Math.PI * 12 * t);
    } else if (blob.id.includes("turdus")) {
      amp = (t % 1.5) < 1.0 ? 0.45 + 0.3 * Math.cos(2 * Math.PI * 8 * t) : 0.01;
    } else if (blob.id.includes("odontocete")) {
      amp = (t % 0.04) < 0.002 ? 0.75 : 0.08 + 0.05 * Math.cos(2 * Math.PI * t);
    } else if (blob.id.includes("container")) {
      amp = 0.4 + 0.15 * Math.sin(2 * Math.PI * 5.8 * t);
    } else if (blob.id.includes("explosion")) {
      amp = t > 1.2 ? 0.9 * Math.exp(-2.2 * (t - 1.2)) + 0.02 : 0.01;
    }
    amplitudes.push(Math.max(-1.0, Math.min(1.0, amp)));
  }

  // Log-spaced frequencies for authentic spectrogram columns
  const frequencies = [60, 120, 250, 500, 1000, 1800, 2800, 4200, 6000, 8500, 12000, 16000, 20000];
  const power_db: number[][] = [];

  // Compose highly distinct power spectra signatures per species
  for (let fIdx = 0; fIdx < frequencies.length; fIdx++) {
    const freq = frequencies[fIdx];
    const row: number[] = [];

    for (let tIdx = 0; tIdx < steps; tIdx++) {
      const t = times[tIdx];
      let power = -95; // Noise floor dB

      if (blob.id.includes("humpback")) {
        // Humpback low frequencies
        if (freq < 1000) {
          const sweepingPeak = 280 + 130 * Math.sin(2 * Math.PI * 0.3 * t);
          const distToPeak = Math.abs(freq - sweepingPeak);
          power = distToPeak < 150 ? -25 : -65;
        } else {
          power = -85;
        }
      } else if (blob.id.includes("canis")) {
        // Canine bark
        const isBark = (t % 1.2) < 0.25 || ((t % 1.2) > 0.45 && (t % 1.2) < 0.7);
        if (isBark) {
          power = freq < 2000 ? -30 + 10 * Math.cos(t) : -65;
        }
      } else if (blob.id.includes("uav")) {
        // Drone harmonic spikes
        const droneFund = 120 + 2 * Math.sin(2 * Math.PI * 1 * t);
        const harmonics = [droneFund, droneFund * 2, droneFund * 3, droneFund * 4];
        let isHarmonic = false;
        for (const harm of harmonics) {
          if (Math.abs(freq - harm) < 60) {
            isHarmonic = true;
          }
        }
        power = isHarmonic ? -18 : -80;
      } else if (blob.id.includes("turdus")) {
        // High warbling bird song
        const active = (t % 1.5) < 1.0;
        if (active && freq > 1000 && freq < 4500) {
          const fmFreq = Math.sin(2 * Math.PI * 14 * t);
          const birdPeak = 2200 + 900 * fmFreq;
          power = Math.abs(freq - birdPeak) < 250 ? -20 : -85;
        }
      } else if (blob.id.includes("odontocete")) {
        // Odontocete whistle + cavitation click-spurts
        const whistlePeak = 8000 + 2400 * Math.cos(2 * Math.PI * 0.8 * t);
        if (Math.abs(freq - whistlePeak) < 800) {
          power = -35;
        } else if (freq > 12000) {
          power = -45 + 10 * Math.sin(20 * t);
        }
      } else if (blob.id.includes("container")) {
        // Ship mechanical rumble & heavy propeller wake Modulations
        if (freq < 400) {
          power = -22 + 8 * Math.sin(2 * Math.PI * 5.8 * t);
        } else {
          power = -55 - 15 * Math.sin(2 * Math.PI * 5.8 * t);
        }
      } else if (blob.id.includes("explosion")) {
        // Broadband initial shockwave going down
        if (t > 1.2) {
          const elapsed = t - 1.2;
          const energy = -10 - 25 * elapsed;
          power = Math.max(-95, energy);
        }
      }

      row.push(power);
    }
    power_db.push(row);
  }

  res.json({
    ok: true,
    duration_sec: durationSec,
    waveform: {
      times,
      amplitudes,
    },
    spectrogram: {
      times,
      frequencies,
      power_db,
    },
  });
});

// Analysis caching layer
const analysisCache: { [blobId: string]: any } = {};

// SINE Core Classification Engine (Gemini Server-Side integration)
async function performAcousticAnalysis(blob: (typeof mockAcousticBlobs)[0]): Promise<any> {
  const cached = analysisCache[blob.id];
  if (cached) return cached;

  if (ai) {
    try {
      // Query server-side Gemini classifier for deep signal representation
      const promptText = `
        You are SINE-Embed-v1, the proprietary neural acoustic intelligence engine built for NatureOS and Mycosoft.
        Analyze the following metadata for a recording from our databank and compute a high-end multi-head classifier payload.

        File Properties:
        - Filename: ${blob.filename}
        - Title: ${blob.title}
        - Category: ${blob.category}
        - Acoustic Environment: ${blob.acoustic_environment} (air/water classification boundary)
        - Duration: ${blob.duration_sec} seconds
        - Sample Rate: ${blob.sample_rate_hz} Hz
        - Labelled species/source: ${blob.label_primary} (${blob.label_secondary})

        Please construct highly realistic, mathematically consistent detection events representing physical events in a 4.0 second crop window analysis:
        1. "identification_summary": primary species, class type (eg. "whistle", "bark", "rotor"), confidence (0.8 - 0.99) and out-of-domain score (0.01 - 0.15).
        2. "frequency_detections": peaks, frequencies, band durations inside the file duration.
        3. "activity_segments": timestamps of voices/noises.
        4. "bird_detections": (if avian environment) list bird calls with species label.
        5. "uav_detections": (if drone environment) drone classes, rotor rpm counts.
        6. "nps_detections": (NPS style eco logs matching profile codes).
        7. "deep_signal_matches": neural embedding similarity distance nodes from the MINDEX prototype bank.
        8. "sound_transcripts": chronological sequence of real-time sound event descriptions showing what specific things they are sounds of (e.g., specific vocalization sweeps, blade pass clicks, engine rumbles, impulse seismic booms), with times, labels, descriptions, sound_source, confidence, and frequency_range.

        Expose all coordinates correctly in the responses inside the file's duration.
      `;

      const response = await ai.models.generateContent({
        model: "gemini-3.5-flash",
        contents: promptText,
        config: {
          responseMimeType: "application/json",
          responseSchema: {
            type: Type.OBJECT,
            properties: {
              identification_summary: {
                type: Type.OBJECT,
                properties: {
                  top_label: { type: Type.STRING },
                  category: { type: Type.STRING },
                  type: { type: Type.STRING },
                  confidence: { type: Type.NUMBER },
                  ood_score: { type: Type.NUMBER },
                },
                required: ["top_label", "category", "type", "confidence", "ood_score"],
              },
              frequency_detections: {
                type: Type.ARRAY,
                items: {
                  type: Type.OBJECT,
                  properties: {
                    start_sec: { type: Type.NUMBER },
                    end_sec: { type: Type.NUMBER },
                    peak_sec: { type: Type.NUMBER },
                    freq_hz: { type: Type.NUMBER },
                    confidence: { type: Type.NUMBER },
                    type: { type: Type.STRING },
                  },
                  required: ["start_sec", "end_sec", "peak_sec", "freq_hz", "confidence", "type"],
                },
              },
              activity_segments: {
                type: Type.ARRAY,
                items: {
                  type: Type.OBJECT,
                  properties: {
                    start_sec: { type: Type.NUMBER },
                    end_sec: { type: Type.NUMBER },
                    label: { type: Type.STRING },
                    confidence: { type: Type.NUMBER },
                  },
                  required: ["start_sec", "end_sec", "label", "confidence"],
                },
              },
              bird_detections: {
                type: Type.ARRAY,
                items: {
                  type: Type.OBJECT,
                  properties: {
                    start_sec: { type: Type.NUMBER },
                    end_sec: { type: Type.NUMBER },
                    species: { type: Type.STRING },
                    confidence: { type: Type.NUMBER },
                    call_type: { type: Type.STRING },
                  },
                  required: ["start_sec", "end_sec", "species", "confidence", "call_type"],
                },
              },
              uav_detections: {
                type: Type.ARRAY,
                items: {
                  type: Type.OBJECT,
                  properties: {
                    start_sec: { type: Type.NUMBER },
                    end_sec: { type: Type.NUMBER },
                    drone_class: { type: Type.STRING },
                    confidence: { type: Type.NUMBER },
                    rpm_estimate: { type: Type.NUMBER },
                  },
                  required: ["start_sec", "end_sec", "drone_class", "confidence", "rpm_estimate"],
                },
              },
              nps_detections: {
                type: Type.ARRAY,
                items: {
                  type: Type.OBJECT,
                  properties: {
                    start_sec: { type: Type.NUMBER },
                    end_sec: { type: Type.NUMBER },
                    profile_code: { type: Type.STRING },
                    confidence: { type: Type.NUMBER },
                    label_primary: { type: Type.STRING },
                  },
                  required: ["start_sec", "end_sec", "profile_code", "confidence", "label_primary"],
                },
              },
              deep_signal_matches: {
                type: Type.ARRAY,
                items: {
                   type: Type.OBJECT,
                   properties: {
                     label: { type: Type.STRING },
                     score: { type: Type.NUMBER },
                     source: { type: Type.STRING },
                     segment_start: { type: Type.NUMBER },
                     segment_end: { type: Type.NUMBER },
                   },
                   required: ["label", "score", "source", "segment_start", "segment_end"],
                },
              },
              sound_transcripts: {
                type: Type.ARRAY,
                items: {
                  type: Type.OBJECT,
                  properties: {
                    start_sec: { type: Type.NUMBER },
                    end_sec: { type: Type.NUMBER },
                    label: { type: Type.STRING },
                    description: { type: Type.STRING },
                    sound_source: { type: Type.STRING },
                    confidence: { type: Type.NUMBER },
                    frequency_range: { type: Type.STRING },
                  },
                  required: ["start_sec", "end_sec", "label", "description", "sound_source", "confidence"],
                },
              },
            },
            required: [
              "identification_summary",
              "frequency_detections",
              "activity_segments",
              "bird_detections",
              "uav_detections",
              "nps_detections",
              "deep_signal_matches",
              "sound_transcripts",
            ],
          },
        },
      });

      const textOutput = response.text || "{}";
      const parsedRes = JSON.parse(textOutput);

      const filledPayload = {
        ok: true,
        blob_id: blob.id,
        model_version: "SINE-Embed-v1.0.0 (Neural Active-Core)",
        frontend_version: "SINE-Frontend-v1.0.0",
        ...parsedRes,
        diagnostics: {
          latency_ms: Math.floor(60 + Math.random() * 40),
          sample_rate_in: blob.sample_rate_hz,
          sample_rate_model: 32000,
          quantization: "FP16 (ONNX Runtime / TensorRT EP)",
        },
      };

      analysisCache[blob.id] = filledPayload;
      return filledPayload;
    } catch (err) {
      console.warn("AI Classification failed, running high-end deterministic DSP fallback: ", err);
    }
  }

  // Pure Deterministic DSP / Physics Heuristic Fallback
  const dspPayload = generateDspHeuristicPayload(blob);
  analysisCache[blob.id] = dspPayload;
  return dspPayload;
}

function generateDspHeuristicPayload(blob: (typeof mockAcousticBlobs)[0]): any {
  // Generates sophisticated local physics-gated results matching the species structure
  const dur = blob.duration_sec;
  const isMarine = blob.acoustic_environment === "water";

  const identification_summary = {
    top_label: blob.label_primary,
    category: isMarine ? "marine_mammal" : "terrestrial_fauna",
    type: blob.id.includes("uav") ? "quadcopter_rotor_harmonics" : "vocalization_calls",
    confidence: 0.92,
    ood_score: 0.04,
  };

  const frequency_detections = [
    { start_sec: 0.5, end_sec: dur - 0.5, peak_sec: dur / 2, freq_hz: blob.id.includes("humpback") ? 410 : blob.id.includes("canis") ? 250 : blob.id.includes("uav") ? 120 : 1800, confidence: 0.89, type: "dominant_spectral_centroid" }
  ];

  const activity_segments = [
    { start_sec: 0.2, end_sec: Math.min(dur, 4.0), label: "active_acoustic_burst", confidence: 0.95 },
    { start_sec: Math.max(0.1, dur - 3.0), end_sec: dur - 0.1, label: "secondary_acoustic_trail", confidence: 0.88 }
  ];

  const bird_detections = !isMarine && blob.id.includes("bird") ? [
    { start_sec: 0.5, end_sec: 3.2, species: "Turdus migratorius", confidence: 0.91, call_type: "warble_song" }
  ] : [];

  const uav_detections = blob.id.includes("uav") ? [
    { start_sec: 0.1, end_sec: dur - 0.1, drone_class: "DJI Phantom Series", confidence: 0.96, rpm_estimate: 4800 }
  ] : [];

  const nps_detections = [
    { start_sec: 0.5, end_sec: dur - 0.5, profile_code: isMarine ? "NPS-MAR-HYDRO-01" : "NPS-AER-MEMS-02", confidence: 0.90, label_primary: blob.label_secondary }
  ];

  const deep_signal_matches = [
    { label: blob.label_primary, score: 0.94, source: "mindex_prototype_centroid", segment_start: 0.0, segment_end: 4.0 },
    { label: "Background Environmental Ambient", score: 0.62, source: "mindex_prototype_centroid", segment_start: 0.0, segment_end: dur }
  ];

  // Real-Time Sound Transcripts correlating sounds to specific real physical events
  let sound_transcripts = [];
  if (blob.id.includes("humpback")) {
    sound_transcripts = [
      { start_sec: 0.0, end_sec: 3.5, label: "Low-pitch Whale Vocal", description: "Deep resonating low-frequency song introduction featuring descending whistles.", sound_source: "Humpback Whale (Megaptera novaeangliae)", confidence: 0.94, frequency_range: "120 Hz - 400 Hz" },
      { start_sec: 3.5, end_sec: 7.0, label: "Complex Sweep Vocal", description: "Complex modulated frequency sweep rising dramatically to 540 Hz centroid peak.", sound_source: "Humpback Whale (Megaptera novaeangliae)", confidence: 0.96, frequency_range: "250 Hz - 750 Hz" },
      { start_sec: 7.0, end_sec: 11.5, label: "Rhythmic Ocean Clicks", description: "Rhythmic oceanic clicks interleaved with continuous high-pitch whistles.", sound_source: "Humpback Whale (Megaptera novaeangliae)", confidence: 0.91, frequency_range: "800 Hz - 1800 Hz" },
      { start_sec: 11.5, end_sec: dur, label: "Reverberation Decays", description: "Echo reverberation fading into deep sea ambient hydrophone noise floor.", sound_source: "Pacific Basin Ambient Water Column", confidence: 0.88, frequency_range: "Faint background ambient" }
    ];
  } else if (blob.id.includes("canis")) {
    sound_transcripts = [
      { start_sec: 0.0, end_sec: 1.2, label: "Canine Bark Alert", description: "Primary canine bark alert segment producing rapid broadband sound pulses.", sound_source: "Domestic Dog (Canis familiaris)", confidence: 0.95, frequency_range: "150 Hz - 1200 Hz" },
      { start_sec: 1.2, end_sec: 2.4, label: "Echo Decays", description: "Secondary air pressure echo decaying across the open yard acoustic boundary.", sound_source: "Suburban Yard Ambient", confidence: 0.89, frequency_range: "Broadband decay" },
      { start_sec: 2.4, end_sec: 3.8, label: "Subsequent Bark Burst", description: "Vocalization bark sequence containing high-amplitude low-frequency formants.", sound_source: "Domestic Dog (Canis familiaris)", confidence: 0.92, frequency_range: "180 Hz - 950 Hz" },
      { start_sec: 3.8, end_sec: dur, label: "Wind & Silence Epoch", description: "Silent epoch interspersed with subtle low-frequency suburban wind rumbles.", sound_source: "Atmospheric Boundary Layer", confidence: 0.85, frequency_range: "Under 100 Hz" }
    ];
  } else if (blob.id.includes("uav") || blob.id.includes("phantom")) {
    sound_transcripts = [
      { start_sec: 0.0, end_sec: 2.5, label: "Rotor Throttle Lift", description: "Stabilized quadcopter rotor throttle lift hum with fundamental frequency 120 Hz.", sound_source: "DJI Phantom 4 Quadcopter", confidence: 0.97, frequency_range: "120 Hz - 350 Hz" },
      { start_sec: 2.5, end_sec: 5.5, label: "Harmonic Passage Peak", description: "Propeller blade passage frequency harmonics peaking steadily in the 480 Hz range.", sound_source: "DJI Phantom 4 Quadcopter", confidence: 0.94, frequency_range: "240 Hz - 960 Hz" },
      { start_sec: 5.5, end_sec: dur, label: "Hover GPS Lock Lock", description: "Modulated rotor speed adjustments maintaining continuous steady state GPS position hover.", sound_source: "DJI Phantom 4 Quadcopter", confidence: 0.93, frequency_range: "110 Hz - 400 Hz" }
    ];
  } else if (blob.id.includes("turdus") || blob.id.includes("robin")) {
    sound_transcripts = [
      { start_sec: 0.0, end_sec: 2.0, label: "Warbled Song Opener", description: "Melodious song opening with rapid FM bird pitch oscillations around 3.1 kHz.", sound_source: "American Robin (Turdus migratorius)", confidence: 0.93, frequency_range: "2000 Hz - 3500 Hz" },
      { start_sec: 2.0, end_sec: 4.5, label: "Territorial Call Sequence", description: "Oscine passerine warbled notes indicating territorial morning call sequence.", sound_source: "American Robin (Turdus migratorius)", confidence: 0.95, frequency_range: "1800 Hz - 4200 Hz" },
      { start_sec: 4.5, end_sec: dur, label: "Trill Chirp & Wind Rustle", description: "High-frequency bird trill chirp ending with subtle forest leaves wind rustles.", sound_source: "American Robin & Foliage Wind", confidence: 0.89, frequency_range: "2500 Hz - 6000 Hz" }
    ];
  } else if (blob.id.includes("odontocete") || blob.id.includes("clicks")) {
    sound_transcripts = [
      { start_sec: 0.0, end_sec: 3.5, label: "Echolocation Click Train", description: "High-density sonar click trains (18 kHz bursts) for navigation route mapping.", sound_source: "Pacific White-Sided Dolphin (Lagenorhynchus obliquidens)", confidence: 0.95, frequency_range: "12 kHz - 24 kHz" },
      { start_sec: 3.5, end_sec: 7.5, label: "Social Whistle Sweep", description: "Short social communication whistle sweep with high dolphin signature.", sound_source: "Pacific White-Sided Dolphin (Lagenorhynchus obliquidens)", confidence: 0.92, frequency_range: "6000 Hz - 14000 Hz" },
      { start_sec: 7.5, end_sec: dur, label: "Contour Navigation Burst", description: "Rapid acoustic squeaks and high-pitch cracking click bursts mapping seafloor contours.", sound_source: "Pacific White-Sided Dolphin (Lagenorhynchus obliquidens)", confidence: 0.96, frequency_range: "10 kHz - 30 kHz" }
    ];
  } else if (blob.id.includes("ship") || blob.id.includes("vessel") || blob.id.includes("propeller")) {
    sound_transcripts = [
      { start_sec: 0.0, end_sec: 5.0, label: "Diesel Propulsion Rumble", description: "Heavy marine diesel engine low cylinder rumble (68 Hz) and prop cavitation.", sound_source: "Container Ship Propeller", confidence: 0.93, frequency_range: "40 Hz - 180 Hz" },
      { start_sec: 5.0, end_sec: 12.0, label: "Rhythmic Blade Swish", description: "Continuous slow blade passage swish (5.8 Hz cycle) showing container transit speed.", sound_source: "Container Ship Propeller", confidence: 0.94, frequency_range: "50 Hz - 3000 Hz" },
      { start_sec: 12.0, end_sec: dur, label: "Turbulent Bubble Wake", description: "White hydrophone hiss background representing high-turbulence bubbles behind hull.", sound_source: "Propeller Turbulence Boundary", confidence: 0.90, frequency_range: "Broadband white" }
    ];
  } else if (blob.id.includes("explosion") || blob.id.includes("impulse")) {
    sound_transcripts = [
      { start_sec: 0.0, end_sec: 1.2, label: "Pre-Impulse Baseline Ambient", description: "Deep water hydrophone silence with faint ocean floor thermal hum.", sound_source: "Seafloor Thermal Basal", confidence: 0.85, frequency_range: "Below 60 Hz" },
      { start_sec: 1.2, end_sec: 2.5, label: "Undersea Blast Shockwave", description: "Instant high-amplitude explosive seismic impulse with rapid boiling water bubble expansion.", sound_source: "Subsurface Explosive Charge", confidence: 0.98, frequency_range: "10 Hz - 20000 Hz" },
      { start_sec: 2.5, end_sec: 4.5, label: "Acoustic Column Reverb", description: "Deep seismic sound ring reverberating across the massive MBARI water column.", sound_source: "Sea Column Acoustic Volume", confidence: 0.95, frequency_range: "20 Hz - 800 Hz" },
      { start_sec: 4.5, end_sec: dur, label: "Post-Blast Signal Decay", description: "Extreme blast pressure tail decaying back into baseline deep-sea ambient soundscapes.", sound_source: "Ocean Ambient Reversion", confidence: 0.90, frequency_range: "Fading low resonance" }
    ];
  } else {
    sound_transcripts = [
      { start_sec: 0.0, end_sec: dur, label: "General Acoustic Hum", description: "SINE standard baseline acoustic hum with continuous physical formants.", sound_source: "SINE Test Environment", confidence: 0.92, frequency_range: "440 Hz standard" }
    ];
  }

  return {
    ok: true,
    blob_id: blob.id,
    model_version: "SINE-Embed-v1.0.0-DSP (Deterministic)",
    frontend_version: "SINE-Frontend-v1.0.0",
    identification_summary,
    frequency_detections,
    activity_segments,
    bird_detections,
    uav_detections,
    nps_detections,
    deep_signal_matches,
    sound_transcripts,
    diagnostics: {
      latency_ms: 12,
      sample_rate_in: blob.sample_rate_hz,
      sample_rate_model: 32000,
      quantization: "INT8 (Deterministic FFT Core)",
    },
  };
}

// REST endpoints mapped exactly to specs
app.get("/api/mindex/sine/blobs/:id/analysis", async (req, res) => {
  const blob = mockAcousticBlobs.find((b) => b.id === req.params.id);
  if (!blob) {
    return res.status(404).json({ ok: false, error: "Acoustic blob not found" });
  }
  const payload = await performAcousticAnalysis(blob);
  res.json(payload);
});

app.post("/api/mindex/sine/blobs/:id/analyze", async (req, res) => {
  const blob = mockAcousticBlobs.find((b) => b.id === req.params.id);
  if (!blob) {
    return res.status(404).json({ ok: false, error: "Acoustic item register missing" });
  }
  blob.analysis_status = "ready";
  const payload = await performAcousticAnalysis(blob);
  res.json(payload);
});

app.post("/api/mindex/library/blobs/:id/classify", async (req, res) => {
  const blob = mockAcousticBlobs.find((b) => b.id === req.params.id);
  if (!blob) {
    return res.status(404).json({ ok: false, error: "Acoustic catalog reference missing" });
  }
  blob.analysis_status = "ready";
  const payload = await performAcousticAnalysis(blob);
  res.json(payload);
});

app.post("/api/natureos/mindex/library/classify", async (req, res) => {
  const blobId = req.query.id as string;
  const blob = mockAcousticBlobs.find((b) => b.id === blobId);
  if (!blob) {
    return res.status(404).json({ ok: false, error: "Missing blob ID" });
  }
  blob.analysis_status = "ready";
  const payload = await performAcousticAnalysis(blob);
  res.json(payload);
});

// Setup development or production environment
async function startServer() {
  if (process.env.NODE_ENV !== "production") {
    // Mount Vite middleware in development
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: "spa",
    });
    app.use(vite.middlewares);
  } else {
    // Serve production static assets compiled under /dist
    const distPath = path.join(process.cwd(), "dist");
    app.use(express.static(distPath));
    app.get("*", (req, res) => {
      res.sendFile(path.join(distPath, "index.html"));
    });
  }

  app.listen(PORT, "0.0.0.0", () => {
    console.log(`[SINE Backend v1] Live at http://localhost:${PORT}`);
  });
}

startServer();
