/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  FolderOpen,
  Cpu,
  Tv,
  Database,
  Radio,
  Activity,
  User,
  Settings,
  Flame,
  Gauge,
  HelpCircle,
} from "lucide-react";
import LibraryTab from "./components/LibraryTab";
import SINEStatus from "./components/SINEStatus";
import ModelExplorer from "./components/ModelExplorer";

export default function App() {
  const [activeTab, setActiveTab] = useState<"library" | "architecture">("library");
  const [showOperatorLogs, setShowOperatorLogs] = useState(false);
  const [logs, setLogs] = useState<string[]>([
    "SINE-Embed-v1 Engine instantiated successfully.",
    "Database Catalog synced - Loaded 7 biological and mechanical recordings.",
    "Probing remote NAS Storage: OK (mounted at /mnt/nas/mindex/Library/acoustic)",
    "FUSARIUM bridge active - Waiting for field device heartbeats...",
  ]);

  const addLog = (message: string) => {
    setLogs((prev) => [`[${new Date().toLocaleTimeString()}] ${message}`, ...prev.slice(0, 19)]);
  };

  return (
    <div className="min-h-screen bg-[#0A0B0E] text-[#E0E2E7] flex flex-col font-sans antialiased">
      {/* Upper Universal Navigation Header - Sleek Theme */}
      <header className="border-b border-white/10 px-6 py-5 flex flex-col lg:flex-row items-start lg:items-end justify-between gap-6 bg-black/40 backdrop-blur-md">
        <div className="flex items-center space-x-3.5">
          <span className="w-2.5 h-8 bg-cyan-500 rounded-sm inline-block shrink-0 shadow-[0_0_12px_rgba(6,182,212,0.5)]"></span>
          <div>
            <div className="flex items-center space-x-2">
              <h1 className="text-xl font-display font-bold tracking-tight text-white flex items-center gap-1.5">
                SINE
              </h1>
              <span className="px-1.5 py-0.5 bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 rounded text-[9px] font-mono font-bold uppercase tracking-widest">
                Unified Acoustic Intelligence
              </span>
            </div>
            <p className="text-[10px] uppercase tracking-[0.2em] text-slate-500 mt-1 font-semibold">
              NLM INTEGRATED CORE // MINDEX LIBRARY ACCESS
            </p>
          </div>
        </div>

        {/* Unified Edge Status and Performance Indicators */}
        <div className="flex flex-wrap items-center gap-6 text-xs text-slate-400 shrink-0">
          <div className="text-left py-0.5">
            <p className="text-[9px] text-slate-500 uppercase font-bold tracking-wider">System Load</p>
            <p className="text-xs font-mono text-cyan-400 font-semibold">4.2ms / 38.4 TOPS</p>
          </div>
          <div className="h-8 w-px bg-white/10 hidden sm:block"></div>
          
          <div className="flex gap-2">
            <div className="px-3 py-1 bg-white/5 border border-white/10 rounded text-[11px] font-bold text-slate-300 font-mono">
              EDGE_NODE_01
            </div>
            <div className="px-3 py-1 bg-cyan-500/10 border border-cyan-500/30 text-cyan-400 rounded text-[11px] font-bold font-mono flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-cyan-400 animate-pulse shadow-[0_0_8px_rgba(34,211,238,0.8)]"></span>
              HYDROPHONE_STREAM
            </div>
          </div>
        </div>
      </header>

      {/* Main Command Center Dashboard */}
      <main className="flex-1 p-6 space-y-6 max-w-7xl mx-auto w-full">
        {/* Dynamic Compact SINE Status Header */}
        <SINEStatus />

        {/* Console view switching tabs */}
        <div className="flex justify-between items-center bg-white/5 border border-white/10 p-1.5 rounded-xl text-xs font-semibold">
          <div className="flex space-x-1.5">
            <button
              onClick={() => setActiveTab("library")}
              className={`flex items-center space-x-2 px-4 py-2.5 rounded-lg cursor-pointer transition-all ${
                activeTab === "library"
                  ? "bg-cyan-500/15 text-cyan-400 border border-cyan-500/25 shadow-inner font-bold"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <FolderOpen className="h-4 w-4" />
              <span>MINDEX Library Databank</span>
            </button>

            <button
              onClick={() => setActiveTab("architecture")}
              className={`flex items-center space-x-2 px-4 py-2.5 rounded-lg cursor-pointer transition-all ${
                activeTab === "architecture"
                  ? "bg-cyan-500/15 text-cyan-400 border border-cyan-500/25 shadow-inner font-bold"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <Cpu className="h-4 w-4" />
              <span>SINE-Embed Neural Engine (CRNN)</span>
            </button>
          </div>

          <button
            onClick={() => {
              setShowOperatorLogs(!showOperatorLogs);
              addLog("Scanned local telemetry registers.");
            }}
            className="flex items-center space-x-1.5 px-3 py-1.5 hover:bg-white/5 border border-transparent hover:border-white/10 text-slate-400 hover:text-white rounded-lg text-xs"
          >
            <Gauge className="h-3.5 w-3.5" />
            <span>Telemetry Logs ({showOperatorLogs ? "Hide" : "Show"})</span>
          </button>
        </div>

        {/* Dynamic Display Panels with transitions */}
        <div className="relative">
          <AnimatePresence mode="wait">
            {activeTab === "library" && (
              <motion.div
                key="library"
                initial={{ opacity: 0, y: 15 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -15 }}
                transition={{ duration: 0.25, ease: "easeOut" }}
              >
                <LibraryTab
                  onBlobSelected={(blob) =>
                    addLog(`Active file changed: ${blob.filename || blob.id}`)
                  }
                />
              </motion.div>
            )}

            {activeTab === "architecture" && (
              <motion.div
                key="architecture"
                initial={{ opacity: 0, y: 15 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -15 }}
                transition={{ duration: 0.25, ease: "easeOut" }}
              >
                <ModelExplorer />
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Telemetry Console drawer overlay block */}
        <AnimatePresence>
          {showOperatorLogs && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="bg-black/60 border border-white/10 rounded-xl overflow-hidden shadow-2xl mt-6 animate-fade-in"
            >
              <div className="bg-white/5 border-b border-white/10 px-4 py-3 flex items-center justify-between text-xs">
                <span className="font-mono text-slate-300 flex items-center gap-1.5">
                  <Activity className="h-3.5 w-3.5 text-cyan-400 animate-pulse" />
                  Operator Telemetry Console
                </span>
                <span className="text-[10px] font-mono text-slate-500">
                  FUSARIUM MDP V2 Active Gating Link
                </span>
              </div>
              <div className="p-4 bg-black/40 text-[11px] font-mono text-slate-400 space-y-1 max-h-48 overflow-y-auto pr-3">
                {logs.map((log, idx) => (
                  <div key={idx} className="flex space-x-2 truncate">
                    <span className="text-slate-600">[{idx}]</span>
                    <span>{log}</span>
                  </div>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </main>

      <footer className="bg-black/40 border-t border-white/10 px-6 py-5 mt-12 text-center text-xs text-slate-500 transition-colors">
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row justify-between items-center gap-3 font-mono text-[10px] uppercase tracking-wider">
          <div className="flex flex-wrap justify-center gap-6">
            <span>FUSARIUM FUSION: ENABLED</span>
            <span className="text-cyan-400">NLM SYNC: ACTIVE (STABLE)</span>
            <span>BUFFER: 4096KB CLEAR</span>
          </div>
          <p>© 2026 Mycosoft Acoustic Intelligence, SINE Research Division. Licensed CC-BY-NC-4.0.</p>
        </div>
      </footer>
    </div>
  );
}
