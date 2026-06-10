/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { Activity, Cpu, Database, Disc, Layers, RefreshCw, Radio } from "lucide-react";

interface ServiceStatus {
  ok: boolean;
  model_version: string;
  engine: string;
  quantization: string;
  detectors: string[];
  last_calibration_time: string;
}

export default function SINEStatus() {
  const [status, setStatus] = useState<ServiceStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [calibrating, setCalibrating] = useState(false);

  useEffect(() => {
    fetchStatus();
  }, []);

  const fetchStatus = () => {
    setLoading(true);
    fetch("/api/mindex/sine/status")
      .then((res) => res.json())
      .then((data) => {
        setStatus(data);
        setLoading(false);
      })
      .catch((err) => {
        console.error("Failed to load SINE status:", err);
        setLoading(false);
      });
  };

  const handleRecalibrate = () => {
    setCalibrating(true);
    setTimeout(() => {
      setCalibrating(false);
      fetchStatus();
    }, 2000);
  };

  if (loading) {
    return (
      <div className="bg-white/5 border border-white/10 rounded-lg p-6 flex items-center justify-center space-x-3 text-slate-400">
        <RefreshCw className="h-5 w-5 animate-spin text-cyan-400" />
        <span className="font-sans text-sm font-medium">Interrogating SINE Acoustic Stack...</span>
      </div>
    );
  }

  return (
    <div className="bg-[#0A0B0E]/60 border border-white/10 rounded-xl overflow-hidden p-6 space-y-4 shadow-xl">
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center space-y-4 md:space-y-0 border-b border-white/10 pb-4">
        <div>
          <div className="flex items-center space-x-2">
            <Radio className="h-5 w-5 text-cyan-400 animate-pulse" />
            <h2 className="text-lg font-sans font-semibold tracking-tight text-white">
              SINE Spectral Intelligence Network
            </h2>
          </div>
          <p className="text-slate-400 text-xs mt-1">
            Active acoustic nervous system for NatureOS &amp; MINDEX Library Nodes
          </p>
        </div>

        <button
          onClick={handleRecalibrate}
          disabled={calibrating}
          className="flex items-center space-x-2 px-3 py-1.5 bg-cyan-500/10 hover:bg-cyan-500/20 border border-cyan-500/30 disabled:opacity-50 text-cyan-400 text-xs font-mono rounded mt-2 md:mt-0 transition-all cursor-pointer shadow-[0_0_10px_rgba(6,182,212,0.1)]"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${calibrating ? "animate-spin text-cyan-400" : ""}`} />
          <span>{calibrating ? "Aligning Spectrograms..." : "Calibrate Embeddings"}</span>
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-white/5 p-4 rounded-lg border border-white/10 flex items-start space-x-3">
          <Cpu className="h-5 w-5 text-cyan-400 mt-1 shrink-0" />
          <div className="space-y-1">
            <span className="text-slate-500 text-[10px] uppercase font-mono tracking-wider block">Neural Backbone</span>
            <span className="text-white text-sm font-medium font-sans block">{status?.model_version}</span>
            <span className="text-slate-400 text-xs block">{status?.engine}</span>
          </div>
        </div>

        <div className="bg-white/5 p-4 rounded-lg border border-white/10 flex items-start space-x-3">
          <Layers className="h-5 w-5 text-cyan-400 mt-1 shrink-0" />
          <div className="space-y-1">
            <span className="text-slate-500 text-[10px] uppercase font-mono tracking-wider block">Quantization Matrix</span>
            <span className="text-white text-sm font-medium font-mono block">{status?.quantization}</span>
            <span className="text-slate-400 text-xs block font-sans">Unified Marine/Air Core Model</span>
          </div>
        </div>

        <div className="bg-white/5 p-4 rounded-lg border border-white/10 flex items-start space-x-3">
          <Database className="h-5 w-5 text-cyan-400 mt-1 shrink-0" />
          <div className="space-y-1">
            <span className="text-slate-500 text-[10px] uppercase font-mono tracking-wider block">Ensemble Drivers</span>
            <span className="text-white text-sm font-medium font-mono block">
              {status?.detectors.length} Active Pipelines
            </span>
            <span className="text-slate-400 text-xs block font-sans">Physics + Deep Learning Fusion</span>
          </div>
        </div>

        <div className="bg-white/5 p-4 rounded-lg border border-white/10 flex items-start space-x-3">
          <Activity className="h-5 w-5 text-cyan-400 mt-1 shrink-0" />
          <div className="space-y-1">
            <span className="text-slate-500 text-[10px] uppercase font-mono tracking-wider block">Array Calibration</span>
            <span className="text-white text-sm font-medium font-mono block">Delta-Offset Locked</span>
            <span className="text-slate-400 text-xs block font-sans">
              {status?.last_calibration_time ? new Date(status.last_calibration_time).toLocaleTimeString() : ""}
            </span>
          </div>
        </div>
      </div>

      <div className="bg-black/40 p-4 rounded-lg border border-white/10 space-y-2">
        <h4 className="text-slate-350 font-sans text-xs font-semibold uppercase tracking-wider">
          Loaded Physics &amp; Stochastic Detectors
        </h4>
        <div className="flex flex-wrap gap-2">
          {status?.detectors.map((detector) => (
            <div
              key={detector}
              className="flex items-center space-x-1.5 px-2.5 py-1 bg-white/5 border border-white/10 rounded text-slate-300 font-mono text-[11px]"
            >
              <span className="h-1.5 w-1.5 rounded-full bg-cyan-400 animate-pulse shadow-[0_0_6px_rgba(34,211,238,0.8)]"></span>
              <span>{detector}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
