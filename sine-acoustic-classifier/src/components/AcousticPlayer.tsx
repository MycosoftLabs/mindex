/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useRef, useState, useEffect } from "react";
import {
  Play,
  Pause,
  AlertTriangle,
  Layers,
  Table,
  Fingerprint,
  RotateCcw,
  Volume2,
  Cpu,
  Bookmark,
  CheckCircle,
  HelpCircle,
} from "lucide-react";
import { AcousticBlob, AcousticAnalysis, AcousticVisualisation } from "../types";

interface AcousticPlayerProps {
  selectedBlob: AcousticBlob;
  onAnalysisUpdated?: (newAnalysis: AcousticAnalysis) => void;
}

export default function AcousticPlayer({ selectedBlob, onAnalysisUpdated }: AcousticPlayerProps) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [analysis, setAnalysis] = useState<AcousticAnalysis | null>(null);
  const [visualisation, setVisualisation] = useState<AcousticVisualisation | null>(null);
  const [loadingAnalysis, setLoadingAnalysis] = useState(false);
  const [loadingVisualisation, setLoadingVisualisation] = useState(false);
  const [hoverTime, setHoverTime] = useState<number | null>(null);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Reload visualization & analysis when selected file changes
  useEffect(() => {
    setIsPlaying(false);
    setCurrentTime(0);
    setAnalysis(null);
    setVisualisation(null);

    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.load();
    }

    // Load visual coordinates from MINDEX
    setLoadingVisualisation(true);
    fetch(`/api/mindex/sine/blobs/${selectedBlob.id}/visualisation`)
      .then((res) => {
        if (!res.ok) throw new Error("Unavailable visualization");
        return res.json();
      })
      .then((data) => {
        setVisualisation(data);
        setDuration(data.duration_sec || selectedBlob.duration_sec);
        setLoadingVisualisation(false);
      })
      .catch((err) => {
        console.warn("Spectrogram visual unavailable:", err);
        setLoadingVisualisation(false);
      });

    // Check if analysis is already cached or previously computed
    fetch(`/api/mindex/sine/blobs/${selectedBlob.id}/analysis`)
      .then((res) => {
        if (res.ok) return res.json();
        return null;
      })
      .then((data) => {
        if (data && data.identification_summary) {
          setAnalysis(data);
        }
      })
      .catch((err) => {
        console.log("No pre-existing analysis:", err);
      });
  }, [selectedBlob]);

  // Audio Playback Listeners
  const handleTimeUpdate = () => {
    if (audioRef.current) {
      setCurrentTime(audioRef.current.currentTime);
    }
  };

  const handleLoadedMetadata = () => {
    if (audioRef.current && !visualisation) {
      setDuration(audioRef.current.duration);
    }
  };

  const handleAudioEnded = () => {
    setIsPlaying(false);
  };

  const togglePlayback = () => {
    if (!audioRef.current) return;
    if (isPlaying) {
      audioRef.current.pause();
    } else {
      audioRef.current.play().catch((err) => console.log("Playback error:", err));
    }
    setIsPlaying(!isPlaying);
  };

  const handleReset = () => {
    if (audioRef.current) {
      audioRef.current.currentTime = 0;
      setCurrentTime(0);
    }
  };

  // Click on waveform to scrub
  const handleScrub = (clientX: number, width: number) => {
    if (!audioRef.current) return;
    const clickRatio = clientX / width;
    const targetTime = clickRatio * duration;
    audioRef.current.currentTime = targetTime;
    setCurrentTime(targetTime);
  };

  const drawVisualisation = () => {
    const canvas = canvasRef.current;
    if (!canvas || !visualisation) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    const { waveform, spectrogram } = visualisation;

    // Draw Spectrogram Grid Blocks (Background Spectrograph Layer)
    if (spectrogram && spectrogram.power_db && spectrogram.frequencies) {
      const specRows = spectrogram.power_db; // Bins over time
      const numFreqs = specRows.length;
      const numTimeFrames = specRows[0].length;

      const blockWidth = width / numTimeFrames;
      const blockHeight = height / numFreqs;

      for (let f = 0; f < numFreqs; f++) {
        for (let t = 0; t < numTimeFrames; t++) {
          const power = specRows[f][t];
          // Map power DB to a gorgeous high-contrast marine/neon scale
          // Range expected is -95 dB (black/purple) to -10 dB (bright orange/emerald)
          const normPower = Math.max(0, Math.min(1, (power + 95) / 85));

          let r = 8;
          let g = 10;
          let b = 24;

          if (normPower < 0.3) {
            // Purple scale
            r = Math.floor(normPower * 3 * 64);
            b = Math.floor(24 + normPower * 3 * 180);
          } else if (normPower < 0.7) {
            // Blue to Emerald shift
            const mix = (normPower - 0.3) / 0.4;
            r = Math.floor(0);
            g = Math.floor(mix * 200);
            b = Math.floor(204 - mix * 100);
          } else {
            // Emerald to Gold / Orange peak
            const mix = (normPower - 0.7) / 0.3;
            r = Math.floor(mix * 245);
            g = Math.floor(200 + mix * 50);
            b = Math.floor(50 - mix * 50);
          }

          ctx.fillStyle = `rgb(${r}, ${g}, ${b})`;
          // Invert y axis so low frequencies are at bottom
          const xPos = t * blockWidth;
          const yPos = height - (f + 1) * blockHeight;
          ctx.fillRect(xPos, yPos - 1, blockWidth + 1, blockHeight + 1);
        }
      }
    }

    // Draw Dual Waveform Amplitude Envelope overlay
    if (waveform && waveform.amplitudes) {
      const numPoints = waveform.amplitudes.length;
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = "rgba(6, 182, 212, 0.85)"; // Cyan/Teal overlay
      ctx.beginPath();

      const sliceWidth = width / numPoints;
      for (let i = 0; i < numPoints; i++) {
        const amp = waveform.amplitudes[i];
        const x = i * sliceWidth;
        // Vertically centered dual envelope mirroring
        const halfHeight = height / 2;
        const mappedAmp = amp * (height / 2.3);

        if (i === 0) {
          ctx.moveTo(x, halfHeight - mappedAmp);
        } else {
          ctx.lineTo(x, halfHeight - mappedAmp);
        }
      }

      for (let i = numPoints - 1; i >= 0; i--) {
        const amp = waveform.amplitudes[i];
        const x = i * sliceWidth;
        const halfHeight = height / 2;
        const mappedAmp = amp * (height / 2.3);
        ctx.lineTo(x, halfHeight + mappedAmp);
      }

      ctx.closePath();
      ctx.fillStyle = "rgba(6, 182, 212, 0.08)";
      ctx.fill();
      ctx.stroke();
    }

    // Render Hover vertical cursor markers
    if (hoverTime !== null && duration > 0) {
      const hoverX = (hoverTime / duration) * width;
      ctx.strokeStyle = "rgba(239, 68, 68, 0.4)"; // light transparent red
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(hoverX, 0);
      ctx.lineTo(hoverX, height);
      ctx.stroke();
    }

    // Render Active Playhead overlay line
    if (duration > 0) {
      const playheadX = (currentTime / duration) * width;
      ctx.strokeStyle = "#22d3ee"; // Bright cyan playhead
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(playheadX, 0);
      ctx.lineTo(playheadX, height);
      ctx.stroke();

      // Small anchor head circle
      ctx.fillStyle = "#22d3ee";
      ctx.beginPath();
      ctx.arc(playheadX, height - 4, 3, 0, 2 * Math.PI);
      ctx.fill();
    }
  };

  // Re-draw visualization when model variables or times update
  useEffect(() => {
    drawVisualisation();
  }, [visualisation, currentTime, duration, hoverTime]);

  // Request high-fidelity server-side AI evaluation
  const runActiveClassification = () => {
    setLoadingAnalysis(true);
    fetch(`/api/mindex/sine/blobs/${selectedBlob.id}/analyze`, {
      method: "POST",
    })
      .then((res) => res.json())
      .then((data) => {
        setAnalysis(data);
        if (onAnalysisUpdated) {
          onAnalysisUpdated(data);
        }
        setLoadingAnalysis(false);
      })
      .catch((err) => {
        console.error("Analysis invocation aborted:", err);
        setLoadingAnalysis(false);
      });
  };

  // Direct scrubbing coordinates
  const triggerScrubClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    handleScrub(clickX, rect.width);
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const hoverRatio = Math.max(0, Math.min(1, clickX / rect.width));
    setHoverTime(hoverRatio * duration);
  };

  const handleMouseLeave = () => {
    setHoverTime(null);
  };

  const isDetectionsAvailable =
    analysis &&
    (analysis.frequency_detections.length > 0 ||
      analysis.activity_segments.length > 0 ||
      analysis.bird_detections.length > 0 ||
      analysis.uav_detections.length > 0 ||
      analysis.nps_detections.length > 0);

  return (
    <div className="bg-[#0A0B0E]/60 border border-white/10 rounded-xl overflow-hidden shadow-xl flex flex-col">
      {/* SINE Streaming hidden hook */}
      <audio
        ref={audioRef}
        src={selectedBlob.stream_url}
        onTimeUpdate={handleTimeUpdate}
        onLoadedMetadata={handleLoadedMetadata}
        onEnded={handleAudioEnded}
      />

      {/* Header Info Panel */}
      <div className="bg-white/5 px-6 py-4 border-b border-white/10 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <span className="text-slate-500 font-mono text-[10px] uppercase tracking-widest block">
            Spectral Analyzer Target File
          </span>
          <h3 className="text-sm font-sans font-semibold text-white flex items-center gap-1.5 mt-0.5">
            <Bookmark className="h-4 w-4 text-cyan-400 shrink-0" />
            {selectedBlob.filename} 
            <span className="text-slate-500 font-mono text-xs font-normal">({selectedBlob.codec})</span>
          </h3>
          <p className="text-xs text-slate-400 mt-1">{selectedBlob.title}</p>
        </div>

        <div className="flex items-center space-x-3 shrink-0">
          <button
            onClick={runActiveClassification}
            disabled={loadingAnalysis}
            className="flex items-center space-x-2 px-4 py-2 bg-cyan-500 hover:bg-cyan-600 disabled:bg-white/5 text-black disabled:text-slate-500 text-xs font-sans font-bold rounded-lg cursor-pointer transition-all shadow-[0_0_12px_rgba(34,211,238,0.2)] hover:shadow-[0_0_16px_rgba(34,211,238,0.4)]"
          >
            <Cpu className="h-4 w-4 shrink-0" />
            <span>{loadingAnalysis ? "Evaluating Features..." : analysis ? "Re-Run Classifier" : "Run SINE Classify"}</span>
          </button>
        </div>
      </div>

      {/* Sonic Visualiser Timeline Workbench */}
      <div className="p-6 space-y-6">
        <div className="space-y-2">
          <div className="flex justify-between items-center text-xs font-mono text-slate-400">
            <div className="flex items-center space-x-2">
              <Volume2 className="h-3.5 w-3.5 text-cyan-400" />
              <span>
                {currentTime.toFixed(2)}s / <span className="text-slate-500">{duration.toFixed(2)}s</span>
              </span>
              {hoverTime !== null && (
                <span className="text-slate-600 text-xs pl-2 border-l border-white/10">
                  Cursor: {hoverTime.toFixed(2)}s
                </span>
              )}
            </div>
            <div className="text-slate-550 uppercase text-[10px] tracking-wide">
              Waveform &amp; Mel-Envelope Colormap Heatmap
            </div>
          </div>

          {/* Dynamic playhead render viewport */}
          <div className="relative bg-[#0A0B0E] border border-white/10 rounded-lg overflow-hidden h-48 cursor-crosshair">
            {loadingVisualisation && (
              <div className="absolute inset-0 bg-[#0A0B0E]/80 flex items-center justify-center space-x-2 z-10">
                <div className="h-2 w-2 rounded-full bg-cyan-405 animate-pulse shadow-[0_0_8px_rgba(34,211,238,0.8)]"></div>
                <span className="text-slate-400 font-sans text-xs">Decoding audio frames...</span>
              </div>
            )}
            <canvas
              ref={canvasRef}
              width={800}
              height={192}
              onClick={triggerScrubClick}
              onMouseMove={handleMouseMove}
              onMouseLeave={handleMouseLeave}
              className="w-full h-full block"
            />
          </div>

          {/* Transport Controls */}
          <div className="flex items-center justify-between bg-white/5 border border-white/10 rounded-lg p-3">
            <div className="flex items-center space-x-3">
              <button
                onClick={togglePlayback}
                className="p-2 bg-cyan-500/10 hover:bg-cyan-500/20 border border-cyan-500/30 hover:border-cyan-500/50 text-cyan-400 rounded-md cursor-pointer transition-colors"
                title={isPlaying ? "Pause" : "Play"}
              >
                {isPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4 fill-cyan-400 text-cyan-400" />}
              </button>
              <button
                onClick={handleReset}
                className="p-2 bg-[#0A0B0E] border border-white/10 hover:bg-white/5 text-slate-400 hover:text-white rounded-md transition-colors"
                title="Rewind Timeline"
              >
                <RotateCcw className="h-4 w-4" />
              </button>
            </div>

            <div className="flex items-center space-x-4 font-mono text-xs text-slate-500">
              <div className="flex items-center space-x-2">
                <span className="h-1.5 w-1.5 rounded-full" style={{backgroundColor: selectedBlob.acoustic_environment === "water" ? "#22d3ee" : "#d97706", boxShadow: selectedBlob.acoustic_environment === "water" ? "0 0 6px rgba(34,211,238,0.8)" : "none"}}></span>
                <span className="capitalize">{selectedBlob.acoustic_environment} medium</span>
              </div>
              <span>{selectedBlob.sample_rate_hz} Hz</span>
              <span>{selectedBlob.channels} Ch</span>
            </div>
          </div>
        </div>

        {/* Real-time Sound Subtitle Caption Overlay */}
        <div className="bg-black/40 border border-white/10 rounded-xl p-4 space-y-3 relative overflow-hidden">
          <div className="absolute right-0 top-0 h-full w-24 bg-gradient-to-l from-cyan-500/5 to-transparent pointer-events-none" />
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-mono text-cyan-400 font-bold uppercase tracking-wider flex items-center gap-1.5">
              <span className="flex h-2 w-2 relative">
                {isPlaying && (
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75"></span>
                )}
                <span className="relative inline-flex rounded-full h-2 w-2 bg-cyan-500"></span>
              </span>
              SINE Live Sound Transcript Narrator
            </span>
            <span className="text-[9px] font-mono text-slate-500">
              Active Head Correlator v1.2
            </span>
          </div>

          {!analysis ? (
            <div className="py-2 text-slate-500 font-sans text-xs italic flex items-center justify-center gap-2">
              <span className="animate-pulse">●</span> Please click "Run SINE Classify" to initialize Live Acoustic Subtitles and event correlation.
            </div>
          ) : (
            <div>
              {(() => {
                const currentSec = currentTime;
                const active = analysis.sound_transcripts?.find(
                  (t) => currentSec >= t.start_sec && currentSec <= t.end_sec
                );

                if (!active) {
                  return (
                    <div className="text-slate-400 font-sans text-xs italic py-1 text-center">
                      🔊 [Ambient Noise Floor] Standard marine/forest baseline noise tracking
                    </div>
                  );
                }

                return (
                  <div className="space-y-2">
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1.5">
                      <div className="text-xs font-sans font-bold text-white flex items-center gap-2">
                        <span className="px-1.5 py-0.5 bg-cyan-400/10 text-cyan-400 border border-cyan-400/20 rounded font-mono text-[9px] uppercase">
                          {active.label}
                        </span>
                        <span className="text-slate-500 font-normal">from</span>
                        <span className="text-cyan-400">{active.sound_source}</span>
                      </div>
                      <div className="flex items-center gap-3 text-[10px] font-mono text-slate-500">
                        <span>Range: {active.start_sec.toFixed(1)}s - {active.end_sec.toFixed(1)}s</span>
                        {active.frequency_range && (
                          <span className="text-slate-400/80">({active.frequency_range})</span>
                        )}
                        <span className="text-cyan-400 font-bold bg-cyan-950/40 px-1 py-0.1 border border-cyan-900/30 rounded">
                          {(active.confidence * 100).toFixed(0)}% Match
                        </span>
                      </div>
                    </div>
                    <p className="text-sm font-sans text-white/95 leading-relaxed font-semibold pl-1 border-l-2 border-cyan-450 animate-pulse bg-cyan-500/5 py-1.5 pr-2 rounded">
                      "{active.description}"
                    </p>
                  </div>
                );
              })()}
            </div>
          )}
        </div>

        {/* Detailed Timeline Transcripts (Interactive Chronology) */}
        {analysis && analysis.sound_transcripts && (
          <div className="space-y-2 bg-black/30 border border-white/10 rounded-xl p-4">
            <div className="flex items-center justify-between pb-2 border-b border-white/5">
              <h5 className="text-[10px] uppercase font-mono text-slate-400 font-bold tracking-wider">
                Full Chronological Acoustic Script ({analysis.sound_transcripts.length} Event Epochs)
              </h5>
              <div className="text-[9px] font-mono text-slate-500">
                Click any timestamp row to warp playback coordinates
              </div>
            </div>

            <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1">
              {analysis.sound_transcripts.map((entry, idx) => {
                const isActive = currentTime >= entry.start_sec && currentTime <= entry.end_sec;
                return (
                  <div
                    key={idx}
                    onClick={() => {
                      if (audioRef.current) {
                        audioRef.current.currentTime = entry.start_sec;
                        setCurrentTime(entry.start_sec);
                        if (!isPlaying) {
                          audioRef.current.play().catch(e => console.log(e));
                          setIsPlaying(true);
                        }
                      }
                    }}
                    className={`p-2 rounded-lg cursor-pointer transition-all border text-left flex flex-col md:flex-row md:items-center justify-between gap-3 ${
                      isActive
                        ? "bg-cyan-500/10 border-cyan-500/35 shadow-[0_0_8px_rgba(6,182,212,0.15)]"
                        : "bg-white/5 border-white/5 hover:bg-white/10 hover:border-white/10"
                    }`}
                  >
                    <div className="space-y-1 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-[9px] px-1.5 py-0.5 bg-black/40 border border-white/10 rounded text-slate-400 font-bold">
                          {entry.start_sec.toFixed(1)}s - {entry.end_sec.toFixed(1)}s
                        </span>
                        <span className={`text-xs font-semibold ${isActive ? "text-cyan-400" : "text-white"}`}>{entry.label}</span>
                        <span className="text-[10px] text-slate-500 font-normal">({entry.sound_source})</span>
                      </div>
                      <p className={`text-xs pl-1 font-sans ${isActive ? "text-cyan-100" : "text-slate-400"}`}>
                        {entry.description}
                      </p>
                    </div>

                    <div className="flex items-center gap-3 shrink-0 font-mono text-[10px] self-end md:self-center">
                      {entry.frequency_range && (
                        <span className="text-slate-500 text-[9px]">{entry.frequency_range}</span>
                      )}
                      <span className={`text-[9px] font-bold ${isActive ? "text-cyan-405 shadow-[0_0_6px_rgba(34,211,238,0.4)]" : "text-slate-500"}`}>
                        {(entry.confidence * 100).toFixed(0)}% Match
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Multi-head Detection Lanes (Sonic Visualiser annotations) */}
        <div className="space-y-3">
          <h4 className="text-slate-400 font-sans text-xs font-semibold uppercase tracking-wider flex items-center gap-1.5">
            <Layers className="h-3.5 w-3.5 text-cyan-400" />
            Telemetry Detection Lanes
          </h4>

          <div className="bg-white/5 border border-white/10 rounded-lg p-4 space-y-3">
            {!analysis ? (
              <div className="py-6 text-center text-slate-500 space-y-2">
                <AlertTriangle className="h-7 w-7 mx-auto text-amber-500/80 animate-bounce" />
                <p className="text-slate-400 font-sans text-xs font-medium">SINE Deep Classifier Not Yet Executed</p>
                <p className="text-slate-500 text-[11px]">
                  Click the "Run SINE Classify" button to evaluate log-mel and PCEN spectral features
                </p>
              </div>
            ) : (
              <div className="space-y-4">
                {/* Visual Time Graph Overlay */}
                <div className="relative h-28 bg-black/40 border border-white/10 rounded p-2 flex flex-col justify-between">
                  <div className="text-[9px] font-mono text-slate-550 absolute right-2 top-1.5 uppercase">
                    Acoustic Channel Timelines
                  </div>

                  {/* Lane 1: Active Segments */}
                  <div className="relative h-3 w-full bg-white/5 rounded flex items-center">
                    <span className="text-[8px] font-mono text-slate-400 w-16 absolute left-1">Activity</span>
                    <div className="h-full bg-white/5 absolute left-16 right-0 rounded overflow-hidden">
                      {analysis.activity_segments.map((seg, idx) => (
                        <div
                          key={idx}
                          className="h-full bg-cyan-400/40 border-l border-r border-cyan-400/70 absolute cursor-pointer"
                          style={{
                            left: `${(seg.start_sec / duration) * 100}%`,
                            width: `${((seg.end_sec - seg.start_sec) / duration) * 100}%`,
                          }}
                          title={`Activity [${seg.start_sec}s - ${seg.end_sec}s]`}
                          onClick={() => {
                            if (audioRef.current) audioRef.current.currentTime = seg.start_sec;
                          }}
                        />
                      ))}
                    </div>
                  </div>

                  {/* Lane 2: Frequencies */}
                  <div className="relative h-3 w-full bg-white/5 rounded flex items-center">
                    <span className="text-[8px] font-mono text-slate-400 w-16 absolute left-1">FFT Peaks</span>
                    <div className="h-full bg-white/5 absolute left-16 right-0 rounded overflow-hidden">
                      {analysis.frequency_detections.map((freq, idx) => (
                        <div
                          key={idx}
                          className="h-full bg-cyan-400/30 border-l border-r border-cyan-400/60 absolute cursor-pointer"
                          style={{
                            left: `${(freq.start_sec / duration) * 100}%`,
                            width: `${((freq.end_sec - freq.start_sec) / duration) * 100}%`,
                          }}
                          title={`FFT: ${freq.freq_hz}Hz`}
                          onClick={() => {
                            if (audioRef.current) audioRef.current.currentTime = freq.start_sec;
                          }}
                        />
                      ))}
                    </div>
                  </div>

                  {/* Lane 3: Birds (In-Air bioacoustics) */}
                  <div className="relative h-3 w-full bg-white/5 rounded flex items-center">
                    <span className="text-[8px] font-mono text-slate-400 w-16 absolute left-1">Bird Call</span>
                    <div className="h-full bg-white/5 absolute left-16 right-0 rounded overflow-hidden">
                      {selectedBlob.acoustic_environment === "air" && analysis.bird_detections ? (
                        analysis.bird_detections.map((bio, idx) => (
                          <div
                            key={idx}
                            className="h-full bg-sky-400/40 border-l border-r border-sky-400/60 absolute cursor-pointer"
                            style={{
                              left: `${(bio.start_sec / duration) * 100}%`,
                              width: `${((bio.end_sec - bio.start_sec) / duration) * 100}%`,
                            }}
                            title={`Avian Call: ${bio.species}`}
                            onClick={() => {
                              if (audioRef.current) audioRef.current.currentTime = bio.start_sec;
                            }}
                          />
                        ))
                      ) : (
                        <div className="text-[8px] font-mono text-slate-600 italic absolute left-[30%]">
                          Not applicable in {selectedBlob.acoustic_environment} Medium
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Lane 4: UAV Drone */}
                  <div className="relative h-3 w-full bg-white/5 rounded flex items-center">
                    <span className="text-[8px] font-mono text-slate-400 w-16 absolute left-1">Rotor UAV</span>
                    <div className="h-full bg-white/5 absolute left-16 right-0 rounded overflow-hidden">
                      {selectedBlob.acoustic_environment === "air" && analysis.uav_detections ? (
                        analysis.uav_detections.map((uav, idx) => (
                          <div
                            key={idx}
                            className="h-full bg-amber-400/40 border-l border-r border-amber-400/60 absolute cursor-pointer"
                            style={{
                              left: `${(uav.start_sec / duration) * 100}%`,
                              width: `${((uav.end_sec - uav.start_sec) / duration) * 100}%`,
                            }}
                            title={`Rotor hum: ${uav.drone_class}`}
                            onClick={() => {
                              if (audioRef.current) audioRef.current.currentTime = uav.start_sec;
                            }}
                          />
                        ))
                      ) : (
                        <div className="text-[8px] font-mono text-slate-600 italic absolute left-[30%]">
                          No rotor harmonics detected
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Lane 5: NPS Discovery */}
                  <div className="relative h-3 w-full bg-white/5 rounded flex items-center">
                    <span className="text-[8px] font-mono text-slate-400 w-16 absolute left-1">NPS Profile</span>
                    <div className="h-full bg-white/5 absolute left-16 right-0 rounded overflow-hidden">
                      {analysis.nps_detections.map((nps, idx) => (
                        <div
                          key={idx}
                          className="h-full bg-cyan-500/30 border-l border-r border-cyan-500/50 absolute cursor-pointer"
                          style={{
                            left: `${(nps.start_sec / duration) * 100}%`,
                            width: `${((nps.end_sec - nps.start_sec) / duration) * 100}%`,
                          }}
                          title={`NPS Match: ${nps.profile_code}`}
                          onClick={() => {
                            if (audioRef.current) audioRef.current.currentTime = nps.start_sec;
                          }}
                        />
                      ))}
                    </div>
                  </div>
                </div>

                {/* SINE Classifier Summary Strip */}
                <div className="bg-black/40 p-4 rounded-lg border border-white/10 grid grid-cols-2 lg:grid-cols-5 gap-4">
                  <div>
                    <span className="text-slate-550 font-mono text-[9px] block uppercase">Identity Inference</span>
                    <span className="text-cyan-400 font-sans text-sm font-semibold block uppercase">
                      {analysis.identification_summary.top_label}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-550 font-mono text-[9px] block uppercase">Signal Category</span>
                    <span className="text-slate-300 font-sans text-xs font-medium block capitalize">
                      {analysis.identification_summary.category.replace("_", " ")}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-550 font-mono text-[9px] block uppercase">Target Modality</span>
                    <span className="text-slate-300 font-sans text-xs font-medium block capitalize">
                      {analysis.identification_summary.type.replace("_", " ")}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-550 font-mono text-[9px] block uppercase">Classifier Confidence</span>
                    <span className="text-cyan-400 font-mono text-xs font-bold block">
                      {(analysis.identification_summary.confidence * 100).toFixed(1)}%
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-550 font-mono text-[9px] block uppercase">Abstention Hazard</span>
                    <span className="text-rose-400 font-mono text-xs font-bold block">
                      {(analysis.identification_summary.ood_score * 100).toFixed(1)}% (OOD)
                    </span>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Multi-Head tabular breakdown index */}
        {analysis && isDetectionsAvailable && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Index Detections Table */}
            <div className="space-y-3">
              <h4 className="text-slate-400 font-sans text-xs font-semibold uppercase tracking-wider flex items-center gap-1.5">
                <Table className="h-3.5 w-3.5 text-cyan-400" />
                Physical Detection Events Registry
              </h4>

              <div className="bg-white/5 border border-white/10 rounded-lg overflow-x-auto">
                <table className="w-full text-left border-collapse font-sans text-xs">
                  <thead>
                     <tr className="bg-black/40 text-slate-405 border-b border-white/10 uppercase font-mono text-[9px]">
                      <th className="p-3">Event/Type</th>
                      <th className="p-3">Time Span</th>
                      <th className="p-3 text-right">Magnitude / Pitch</th>
                      <th className="p-3 text-right">Confidence</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5 text-slate-300">
                    {analysis.frequency_detections.map((f, idx) => (
                      <tr
                        key={`freq-${idx}`}
                        onClick={() => {
                          if (audioRef.current) audioRef.current.currentTime = f.start_sec;
                        }}
                        className="hover:bg-white/5 cursor-pointer"
                      >
                        <td className="p-3 font-semibold text-cyan-400 flex items-center gap-1.5">
                          <CheckCircle className="h-3 w-3" />
                          <span>FFT Centroid</span>
                        </td>
                        <td className="p-3 font-mono text-slate-450 text-[11px]">
                          {f.start_sec.toFixed(1)}s - {f.end_sec.toFixed(1)}s
                        </td>
                        <td className="p-3 text-right font-mono text-slate-300">{f.freq_hz} Hz</td>
                        <td className="p-3 text-right font-mono font-bold text-cyan-400">
                          {(f.confidence * 100).toFixed(0)}%
                        </td>
                      </tr>
                    ))}

                    {analysis.activity_segments.map((a, idx) => (
                      <tr
                        key={`act-${idx}`}
                        onClick={() => {
                          if (audioRef.current) audioRef.current.currentTime = a.start_sec;
                        }}
                        className="hover:bg-white/5 cursor-pointer"
                      >
                        <td className="p-3 font-semibold text-slate-200 flex items-center gap-1.5">
                          <Bookmark className="h-3 w-3 text-cyan-400 animate-pulse" />
                          <span>Voice Segment</span>
                        </td>
                        <td className="p-3 font-mono text-slate-450 text-[11px]">
                          {a.start_sec.toFixed(1)}s - {a.end_sec.toFixed(1)}s
                        </td>
                        <td className="p-3 text-right text-slate-500 italic">Auditok Segment</td>
                        <td className="p-3 text-right font-mono font-bold text-cyan-400">
                          {(a.confidence * 100).toFixed(0)}%
                        </td>
                      </tr>
                    ))}

                    {selectedBlob.acoustic_environment === "air" &&
                      analysis.bird_detections?.map((b, idx) => (
                        <tr
                          key={`bird-${idx}`}
                          onClick={() => {
                            if (audioRef.current) audioRef.current.currentTime = b.start_sec;
                          }}
                          className="hover:bg-white/5 cursor-pointer"
                        >
                          <td className="p-3 font-semibold text-sky-400 flex items-center gap-1.5">
                            <Layers className="h-3 w-3 text-sky-450" />
                            <span>{b.species}</span>
                          </td>
                          <td className="p-3 font-mono text-slate-450 text-[11px]">
                            {b.start_sec.toFixed(1)}s - {b.end_sec.toFixed(1)}s
                          </td>
                          <td className="p-3 text-right text-slate-300 font-mono text-[11px] uppercase">
                            {b.call_type}
                          </td>
                          <td className="p-3 text-right font-mono font-bold text-sky-405">
                            {(b.confidence * 100).toFixed(0)}%
                          </td>
                        </tr>
                      ))}

                    {selectedBlob.acoustic_environment === "air" &&
                      analysis.uav_detections?.map((u, idx) => (
                        <tr
                          key={`uav-${idx}`}
                          onClick={() => {
                            if (audioRef.current) audioRef.current.currentTime = u.start_sec;
                          }}
                          className="hover:bg-white/5 cursor-pointer"
                        >
                          <td className="p-3 font-semibold text-amber-400 flex items-center gap-1.5">
                            <span className="h-1.5 w-1.5 rounded-full bg-amber-400 shrink-0"></span>
                            <span>{u.drone_class}</span>
                          </td>
                          <td className="p-3 font-mono text-slate-450 text-[11px]">
                            {u.start_sec.toFixed(1)}s - {u.end_sec.toFixed(1)}s
                          </td>
                          <td className="p-3 text-right text-slate-300 font-mono">{u.rpm_estimate} RPM</td>
                          <td className="p-3 text-right font-mono font-bold text-amber-400">
                            {(u.confidence * 100).toFixed(0)}%
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* SINE-Embed Similarity Matches Panel */}
            <div className="space-y-3">
              <h4 className="text-slate-400 font-sans text-xs font-semibold uppercase tracking-wider flex items-center gap-1.5">
                <Fingerprint className="h-3.5 w-3.5 text-cyan-400" />
                MINDEX Cluster Neighbors (512-d Prototype Bank)
              </h4>

              <div className="bg-white/5 border border-white/10 rounded-lg p-4 space-y-4">
                <div className="flex items-center justify-between text-xs font-mono font-semibold text-slate-450 border-b border-white/10 pb-2">
                  <span>Matched Centroid</span>
                  <span>Cosine Similarity</span>
                </div>

                <div className="space-y-3">
                  {analysis.deep_signal_matches.map((item, idx) => (
                    <div key={idx} className="space-y-1">
                      <div className="flex justify-between items-center text-xs font-sans">
                        <span className="text-slate-205 font-sans font-medium flex items-center gap-1.5">
                          <span
                            className={`h-1.5 w-1.5 rounded-full ${idx === 0 ? "bg-cyan-400 shadow-[0_0_6px_rgba(34,211,238,0.8)]" : "bg-slate-600"}`}
                          ></span>
                          {item.label}
                        </span>
                        <span className="font-mono font-bold text-cyan-400">
                          {(item.score * 100).toFixed(2)}%
                        </span>
                      </div>
                      <div className="w-full h-1 bg-black/40 rounded overflow-hidden">
                        <div
                          className={`h-full ${idx === 0 ? "bg-cyan-500 shadow-[0_0_8px_rgba(34,211,238,0.4)]" : "bg-slate-700"}`}
                          style={{ width: `${item.score * 100}%` }}
                        />
                      </div>
                      <span className="text-[10px] font-mono text-slate-500 block">
                        Source: {item.source} (Frame: {item.segment_start}s - {item.segment_end}s)
                      </span>
                    </div>
                  ))}
                </div>

                <div className="bg-black/40 border border-white/10 rounded p-3 text-[11px] font-sans text-slate-400 space-y-1 mt-4">
                  <span className="font-semibold text-slate-300">Neural Similarity Notes:</span>
                  <p>
                    Matching metric derived from L2 normalized Euclidean distance inside a 512-dimensional manifold,
                    aligned with SINE-Embed-v1 contrastive fine-tuning and the Oceanship/BirdSet centroids library.
                  </p>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
