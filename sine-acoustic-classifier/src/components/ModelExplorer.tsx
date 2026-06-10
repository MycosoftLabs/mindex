/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import {
  Cpu,
  Layers,
  Activity,
  Award,
  Globe2,
  Table,
  Sliders,
  Play,
  FileText,
  Workflow,
  Zap,
} from "lucide-react";

export default function ModelExplorer() {
  const [activeLayer, setActiveLayer] = useState<"layer1" | "layer2" | "layer3">("layer2");
  const [selectedSubBlock, setSelectedSubBlock] = useState<string>("resnet_trunk");

  const architectureBlocks = [
    {
      id: "input_wave",
      name: "Input Audio WAVE (float32)",
      desc: "32,000 Hz canonical sample rate, cropped to 4.0 second window.",
      shape: "[B, 1, 128000] samples",
      details: "Normalized amplitude clamped to [-1, 1] bounds.",
    },
    {
      id: "feature_extractor",
      name: "SINEFrontendV1: STFT & PCEN Branch",
      desc: "Dual feature channels mapping: raw mel-magnitude and adaptive Per-Channel Energy Normalization (PCEN).",
      shape: "[B, 2, 128, 401] spectrogram frames",
      details: "PCEN acts as dynamic bandpass compensation, reducing marine background noise and wind bursts.",
    },
    {
      id: "resnet_trunk",
      name: "Residual Convolution trunk (Stem + Stage A-E)",
      desc: "Five hierarchical residual block levels using 2D convolutions, batch normalization, and SiLU activations.",
      shape: "[B, 256, 4, 51] feature map",
      details: "Discovers local spectro-temporal textures and structures across multiple scales.",
    },
    {
      id: "temporal_block",
      name: "Bi-directional GRU Temporal Sequence Block",
      desc: "Sequence-pooled time axis model utilizing a 2-layer Bidirectional recurrent network.",
      shape: "[B, 51, 512] outputs",
      details: "Tracks dynamic harmonic contours, whale whistles, dolphin clicking trains, and rotor propeller modulations.",
    },
    {
      id: "attention_pooling",
      name: "Linear Attention Pooling Matrix",
      desc: "Softmax attention mapping across timeframes, concentrating on active signals and ignoring environmental noise.",
      shape: "[B, 512] pooled vector",
      details: "Compresses variable duration signals into single semantic landmarks.",
    },
    {
      id: "projection_mlp",
      name: "L2 Normalized Projection MLP",
      desc: "Full projection layer emitting our canonical 512D SINE-Embed acoustic vector.",
      shape: "[B, 512] canonical embedding",
      details: "Trained contrastively to position ecologically similar calls together in hyperspace.",
    },
  ];

  const selectedBlockDetails = architectureBlocks.find((b) => b.id === selectedSubBlock) || architectureBlocks[2];

  return (
    <div className="bg-[#0A0B0E]/60 border border-white/10 rounded-xl overflow-hidden shadow-xl p-6 space-y-6">
      <div className="border-b border-white/10 pb-4 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center space-x-2">
            <Workflow className="h-5 w-5 text-cyan-400 animate-pulse" />
            <h2 className="text-lg font-sans font-semibold tracking-tight text-white">
              SINE-Embed-v1 Acoustic Stack Explorer
            </h2>
          </div>
          <p className="text-slate-400 text-xs mt-1 font-sans">
            Hybrid Deterministic DSP &amp; Multi-Head Deep Representation Pipeline Spec
          </p>
        </div>

        <div className="flex bg-white/5 p-1 rounded-lg border border-white/10 shrink-0 text-xs font-sans">
          <button
            onClick={() => setActiveLayer("layer1")}
            className={`px-3 py-1.5 rounded transition-all cursor-pointer ${
              activeLayer === "layer1"
                ? "bg-cyan-500 text-black font-bold shadow-[0_0_12px_rgba(34,211,238,0.4)]"
                : "text-slate-400 hover:text-white"
            }`}
          >
            L1: Physics DSP
          </button>
          <button
            onClick={() => setActiveLayer("layer2")}
            className={`px-3 py-1.5 rounded transition-all cursor-pointer ${
              activeLayer === "layer2"
                ? "bg-cyan-500 text-black font-bold shadow-[0_0_12px_rgba(34,211,238,0.4)]"
                : "text-slate-400 hover:text-white"
            }`}
          >
            L2: Deep Embed
          </button>
          <button
            onClick={() => setActiveLayer("layer3")}
            className={`px-3 py-1.5 rounded transition-all cursor-pointer ${
              activeLayer === "layer3"
                ? "bg-cyan-500 text-black font-bold shadow-[0_0_12px_rgba(34,211,238,0.4)]"
                : "text-slate-400 hover:text-white"
            }`}
          >
            L3: Semantic Heads
          </button>
        </div>
      </div>

      {activeLayer === "layer1" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
          <div className="space-y-4">
            <h3 className="text-white font-sans text-sm font-semibold uppercase tracking-wider flex items-center gap-1.5">
              <Zap className="h-4 w-4 text-cyan-400" />
              Layer 1: Deterministic Signal Physics
            </h3>
            <p className="text-slate-400 text-xs leading-relaxed font-sans">
              Primary deterministic signal processing block. This handles fast, light, non-neural calculations on the raw
              stream, acting as an active gating prior before powering up high-TOPS GPU inference cores.
            </p>

            <div className="space-y-3">
              <div className="bg-white/5 border border-white/10 p-4 rounded-lg space-y-1.5">
                <span className="text-cyan-400 font-mono text-xs font-bold block">frequency_fft</span>
                <p className="text-slate-200 text-xs">
                  Extracts raw Short-Time Fourier Transform peak points, zero crossing rate variations, and dominant centroids.
                </p>
                <span className="font-mono text-slate-500 text-[10px] block">Reference: Arduino-Audio-Tools Simple Frequency</span>
              </div>

              <div className="bg-white/5 border border-white/10 p-4 rounded-lg space-y-1.5">
                <span className="text-cyan-400 font-mono text-xs font-bold block">activity_auditok</span>
                <p className="text-slate-200 text-xs">
                  Performs energy-gated acoustic activity extraction, splitting silent epochs from vocalizations.
                </p>
                <span className="font-mono text-slate-500 text-[10px] block">Reference: Amsehili/Auditok Segmentation Core</span>
              </div>

              <div className="bg-white/5 border border-white/10 p-4 rounded-lg space-y-1.5">
                <span className="text-cyan-400 font-mono text-xs font-bold block">uav_rotor / propeller harmonics</span>
                <p className="text-slate-200 text-xs">
                  Runs narrow-band harmonic extraction to detect rotor blade rotation pass speeds or cavitation shaft rumbles.
                </p>
                <span className="font-mono text-slate-500 text-[10px] block">Reference: Pcasabianca/Acoustic-UAV harmonic stack</span>
              </div>
            </div>
          </div>

          <div className="bg-white/5 border border-white/10 rounded-lg p-5 space-y-4">
            <h4 className="text-slate-300 font-sans text-xs font-semibold uppercase">DSP Prior Math Extraction</h4>
            <div className="bg-black/40 p-4 rounded border border-white/10 font-mono text-xs text-cyan-455 space-y-2 overflow-x-auto">
              <div className="text-cyan-400 opacity-80"># Centroid Extraction Prior</div>
              <div className="text-cyan-300">win_sz = 1024; hop = 320</div>
              <div className="text-cyan-300">X = stft(waveform, win_sz, hop)</div>
              <div className="text-cyan-400">centroid_hz = sum(freq[f] * abs(X[f, t])) / sum(abs(X[f, t]))</div>
              <div className="text-cyan-300">zcr_rate = zero_crossing_rate(waveform)</div>
              <div className="text-cyan-400 opacity-80"># DSP Prior Feedback Vector</div>
              <div className="text-cyan-400">prior_matrix = concat([centroid_hz, zcr_rate, envelope_decays])</div>
            </div>
            <div className="text-slate-400 text-xs font-sans leading-relaxed">
              These low-compute priors are concatenated with deep spatial embeddings to form our final high-end SINE classification ensemble.
            </div>
          </div>
        </div>
      )}

      {activeLayer === "layer2" && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          {/* Active graph sidebar */}
          <div className="lg:col-span-5 space-y-3">
            <div className="flex items-center space-x-1.5 border-b border-white/10 pb-2">
              <Layers className="h-4 w-4 text-cyan-400" />
              <h4 className="text-slate-300 font-sans text-xs font-semibold uppercase">Module Tensor Diagram</h4>
            </div>

            <div className="flex flex-col space-y-2">
              {architectureBlocks.map((block) => (
                <div
                  key={block.id}
                  onClick={() => setSelectedSubBlock(block.id)}
                  className={`p-3 border rounded-lg text-left cursor-pointer transition-all ${
                    selectedSubBlock === block.id
                      ? "bg-cyan-500/10 border-cyan-500/30 text-cyan-400 shadow-[0_0_8px_rgba(6,182,212,0.05)]"
                      : "bg-black/20 border-white/5 hover:border-white/10 text-slate-400"
                  }`}
                >
                  <div className="flex justify-between items-center text-[10px] font-mono">
                    <span className="font-semibold">{block.id.toUpperCase()}</span>
                    <span className="text-slate-500">{block.shape}</span>
                  </div>
                  <span className="text-xs font-sans font-semibold text-white mt-1 block">
                    {block.name}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Block details view */}
          <div className="lg:col-span-7 bg-white/5 border border-white/10 rounded-xl p-5 space-y-4">
            <div className="flex items-center justify-between">
              <span className="px-2 py-0.5 bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 text-[9px] font-mono rounded uppercase tracking-wider">
                Active Block Profile
              </span>
            </div>
            <h3 className="text-base font-sans font-semibold text-white">
              {selectedBlockDetails.name}
            </h3>

            <div className="grid grid-cols-2 gap-4 text-xs">
              <div className="bg-black/40 p-3 rounded border border-white/10">
                <span className="text-slate-550 font-mono text-[9px] uppercase">Tensor Dimensions</span>
                <span className="text-slate-205 font-mono block mt-0.5">{selectedBlockDetails.shape}</span>
              </div>
              <div className="bg-black/40 p-3 rounded border border-white/10">
                <span className="text-slate-550 font-mono text-[9px] uppercase">Operational Scope</span>
                <span className="text-slate-205 font-sans font-medium block mt-0.5">SINE-Embed-v1 Stack Core</span>
              </div>
            </div>

            <p className="text-slate-400 text-xs font-sans leading-relaxed">
              {selectedBlockDetails.desc}
            </p>

            <div className="bg-black/40 p-4 border border-white/10 rounded">
              <span className="text-[10px] font-mono text-slate-500 block uppercase mb-1">Architecture details &amp; constraints</span>
              <p className="text-xs text-slate-300 leading-relaxed font-sans">
                {selectedBlockDetails.details}
              </p>
            </div>

            <div className="flex items-center justify-between border-t border-white/10 pt-3 text-xs font-mono text-slate-500">
              <span>Total Network Params: 7,358,593</span>
              <span>Model Volume: ~14.7 MB (FP16 ONNX)</span>
            </div>
          </div>
        </div>
      )}

      {activeLayer === "layer3" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
          <div className="space-y-4">
            <h3 className="text-white font-sans text-sm font-semibold uppercase tracking-wider flex items-center gap-1.5">
              <Zap className="h-4 w-4 text-cyan-400" />
              Layer 3: Target Classification Heads &amp; MINDEX Clusters
            </h3>
            <p className="text-slate-400 text-xs leading-relaxed font-sans">
              To guarantee that SINE can scale to support new species without requiring massive retraining overheads, species-level classifications are routed as nearest-neighbor retrieval queries inside a 512D prototype manifold, while target heads classify four main coarse domains.
            </p>

            <div className="grid grid-cols-2 gap-3">
              <div className="bg-white/5 border border-white/10 p-3 rounded-lg">
                <span className="text-[10px] font-mono text-cyan-400 block uppercase font-bold">Head 1: Marine Animals</span>
                <span className="text-slate-300 text-xs block mt-1">Whale species index, dolphin click, seal barks</span>
              </div>
              <div className="bg-white/5 border border-white/10 p-3 rounded-lg">
                <span className="text-[10px] font-mono text-cyan-400 block uppercase font-bold">Head 2: Marine Metals</span>
                <span className="text-slate-300 text-xs block mt-1">Container propellers, hull pumps, active sonar</span>
              </div>
              <div className="bg-white/5 border border-white/10 p-3 rounded-lg">
                <span className="text-[10px] font-mono text-cyan-400 block uppercase font-bold">Head 3: Terrestrial Bio</span>
                <span className="text-slate-300 text-xs block mt-1">Bird vocal types, insects, amphibians</span>
              </div>
              <div className="bg-white/5 border border-white/10 p-3 rounded-lg">
                <span className="text-[10px] font-mono text-cyan-400 block uppercase font-bold">Head 4: UAV / Rotors</span>
                <span className="text-slate-300 text-xs block mt-1">Quadcopters blade count harmonics, hover speeds</span>
              </div>
            </div>
          </div>

          <div className="bg-white/5 border border-white/10 rounded-xl p-5 space-y-4">
            <h3 className="text-slate-300 font-sans text-xs font-semibold uppercase">Prototype bank Retrieval</h3>
            <div className="bg-black/40 p-4 rounded border border-white/10 font-mono text-xs text-cyan-455 space-y-3 overflow-x-auto">
              <div className="text-cyan-400 opacity-80"># Prototype cosine similarity mapping</div>
              <div className="text-cyan-305"># t = target input embedding vector</div>
              <div className="text-cyan-305"># c = candidate prototype centroid inside MINDEX database library</div>
              <div className="text-cyan-400">t_normalized = norm(input_embed)</div>
              <div className="text-cyan-300">scores = dot_product(t_normalized, prototype_centroids)</div>
              <div className="text-cyan-400 opacity-80"># Get nearest taxonomic match</div>
              <div className="text-cyan-400">top_taxon = labels[argmax(scores)]</div>
            </div>
            <div className="text-slate-400 text-xs font-sans leading-relaxed">
              Dynamic matching ensures the system remains robust during deep field explorations in newly tapped eco-systems.
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
