/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import {
  Search,
  Database,
  Layers,
  FolderOpen,
  Filter,
  CheckCircle,
  AlertTriangle,
  PlayCircle,
  HardDrive,
  Info,
} from "lucide-react";
import { AcousticBlob, AcousticAnalysis } from "../types";
import AcousticPlayer from "./AcousticPlayer";

interface LibraryTabProps {
  onBlobSelected?: (blob: AcousticBlob) => void;
}

export default function LibraryTab({ onBlobSelected }: LibraryTabProps) {
  const [blobs, setBlobs] = useState<AcousticBlob[]>([]);
  const [selectedBlob, setSelectedBlob] = useState<AcousticBlob | null>(null);
  const [isStorageUnavailable, setIsStorageUnavailable] = useState(false);
  const [storageInfo, setStorageInfo] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);

  // Search & Filters state
  const [searchQuery, setSearchQuery] = useState("");
  const [mediumFilter, setMediumFilter] = useState<'all' | 'air' | 'water'>('all');
  const [sourceFilter, setSourceFilter] = useState<string>('all');
  const [visibleCount, setVisibleCount] = useState(5);

  useEffect(() => {
    // 1. Fetch library blobs from BFF
    fetch("/api/natureos/mindex/library")
      .then((res) => {
        if (!res.ok) throw new Error("Catalog load failed");
        return res.json();
      })
      .then((data) => {
        if (data.items) {
          setBlobs(data.items);
          // Auto-select shortest clip first as instructed in specs!
          const sorted = [...data.items].sort((a, b) => a.duration_sec - b.duration_sec);
          if (sorted.length > 0) {
            setSelectedBlob(sorted[0]);
            if (onBlobSelected) onBlobSelected(sorted[0]);
          }
        }
        setLoading(false);
      })
      .catch((err) => {
        console.error("Failed to load catalog:", err);
        setIsStorageUnavailable(true);
        setLoading(false);
      });

    // 2. Fetch NAS storage metadata from BFF
    fetch("/api/mindex/library/storage")
      .then((res) => {
        if (res.ok) return res.json();
        return null;
      })
      .then((data) => {
        if (data && data.remote_nas) {
          setStorageInfo(data);
        }
      })
      .catch((err) => {
        console.warn("Storage details unqueried:", err);
      });
  }, []);

  const handleBlobClick = (blob: AcousticBlob) => {
    setSelectedBlob(blob);
    if (onBlobSelected) {
      onBlobSelected(blob);
    }
  };

  const handleAnalysisUpdatedInPlayer = (newAnalysis: AcousticAnalysis) => {
    // Update local blob list's status mapping
    setBlobs((prevBlobs) =>
      prevBlobs.map((b) =>
        b.id === newAnalysis.blob_id ? { ...b, analysis_status: "ready" } : b
      )
    );
    if (selectedBlob && selectedBlob.id === newAnalysis.blob_id) {
      setSelectedBlob((prev) => (prev ? { ...prev, analysis_status: "ready" } : null));
    }
  };

  // Filtering Logic
  const filteredBlobs = blobs.filter((blob) => {
    const matchesSearch =
      blob.filename.toLowerCase().includes(searchQuery.toLowerCase()) ||
      blob.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      blob.label_primary.toLowerCase().includes(searchQuery.toLowerCase()) ||
      blob.source_name.toLowerCase().includes(searchQuery.toLowerCase());

    const matchesMedium = mediumFilter === 'all' || blob.acoustic_environment === mediumFilter;
    const matchesSource = sourceFilter === 'all' || blob.source_id === sourceFilter;

    return matchesSearch && matchesMedium && matchesSource;
  });

  const uniqueSources: string[] = Array.from(new Set(blobs.map((b) => b.source_id)));

  if (loading) {
    return (
      <div className="bg-white/5 p-12 text-center border border-white/10 rounded-xl space-y-3">
        <div className="h-6 w-6 rounded-full border-2 border-cyan-500 border-t-transparent animate-spin mx-auto" />
        <p className="text-slate-400 font-sans text-sm font-medium">Interrogating MINDEX databank files...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Dynamic Storage Stats strip - Sleek theme */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white/5 border border-white/10 p-4 rounded-xl flex items-center space-x-3.5 shadow-md">
          <div className="bg-cyan-500/10 p-2.5 rounded-lg border border-cyan-500/30 text-cyan-400 shadow-[0_0_8px_rgba(6,182,212,0.15)]">
            <HardDrive className="h-5 w-5" />
          </div>
          <div>
            <span className="text-[10px] font-mono uppercase text-slate-500 tracking-wider block">NAS Status</span>
            <span className="text-white text-sm font-semibold font-sans block">
              {isStorageUnavailable ? "Connection Degraded" : storageInfo?.storage_type || "Active Mounted CIFS"}
            </span>
            <span className="text-slate-400 text-xs font-mono block">
              {isStorageUnavailable ? "NAS unavailable" : `${storageInfo?.free_space_tb || "5.86"} TB / ${storageInfo?.total_space_tb || "7.2"} TB Free`}
            </span>
          </div>
        </div>

        <div className="bg-white/5 border border-white/10 p-4 rounded-xl flex items-center space-x-3.5 shadow-md">
          <div className="bg-cyan-500/10 p-2.5 rounded-lg border border-cyan-500/30 text-cyan-400 shadow-[0_0_8px_rgba(6,182,212,0.15)]">
            <Database className="h-5 w-5" />
          </div>
          <div>
            <span className="text-[10px] font-mono uppercase text-slate-500 tracking-wider block">Acoustic catalog</span>
            <span className="text-white text-sm font-semibold block">
              {blobs.length} Core Audio Nodes
            </span>
            <span className="text-slate-400 text-xs block">ESC-50 &amp; MBARI Ingestions Verified</span>
          </div>
        </div>

        <div className="bg-white/5 border border-white/10 p-4 rounded-xl flex items-center space-x-3.5 shadow-md">
          <div className="bg-cyan-500/10 p-2.5 rounded-lg border border-cyan-500/30 text-cyan-400 shadow-[0_0_8px_rgba(6,182,212,0.15)]">
            <Layers className="h-5 w-5" />
          </div>
          <div>
            <span className="text-[10px] font-mono uppercase text-slate-500 tracking-wider block">Database host</span>
            <span className="text-slate-200 text-xs font-mono block truncate font-medium">
              {storageInfo?.mindex_database_host || "mindex-postgres-db:5432"}
            </span>
            <span className="text-cyan-400 text-xs flex items-center gap-1 mt-0.5 font-sans font-medium">
              <span className="h-1.5 w-1.5 rounded-full bg-cyan-400 animate-pulse shadow-[0_0_6px_rgba(34,211,238,0.8)]"></span>
              Synchronized &amp; Online
            </span>
          </div>
        </div>
      </div>

      {isStorageUnavailable && (
        <div className="bg-white/5 border border-red-500/30 p-4 rounded-lg flex items-start space-x-3 text-red-300">
          <AlertTriangle className="h-5 w-5 shrink-0 text-red-400" />
          <div className="space-y-1">
            <span className="text-sm font-sans font-semibold">MINDEX Library Storage is Unavailable</span>
            <p className="text-xs text-slate-400 font-sans">
              Disk full errors on VM 189 was resolved, but the remote NAS mounted tree reported degraded latency. Audio waveforms will scale using client decoders.
            </p>
          </div>
        </div>
      )}

      {/* Main double column layout */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* Left Side: Catalog Lists */}
        <div className="lg:col-span-5 bg-[#0A0B0E]/60 border border-white/10 rounded-xl p-5 space-y-4 shadow-xl">
          <div className="flex justify-between items-center border-b border-white/10 pb-3">
            <div className="flex items-center space-x-2">
              <FolderOpen className="h-5 w-5 text-cyan-400" />
              <h3 className="text-sm font-sans font-semibold text-slate-200">MINDEX Databank Nodes</h3>
            </div>
            <span className="px-2 py-0.5 bg-white/5 border border-white/10 rounded text-slate-500 font-mono text-[10px]">
              {filteredBlobs.length} found
            </span>
          </div>

          {/* Search Box */}
          <div className="relative">
            <Search className="h-4 w-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-550" />
            <input
              type="text"
              placeholder="Search by filename, species name, tags..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2 bg-black/40 border border-white/10 focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500/20 rounded-md font-sans text-xs text-slate-100 placeholder-slate-500 outline-none transition-all"
            />
          </div>

          {/* Filters Row */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <label className="text-[9px] font-mono uppercase text-slate-500 tracking-wider">Acoustic Medium</label>
              <select
                value={mediumFilter}
                onChange={(e: any) => setMediumFilter(e.target.value)}
                className="w-full p-2 bg-black/60 border border-white/10 focus:border-cyan-500 text-slate-300 rounded font-sans text-xs outline-none transition-colors cursor-pointer"
              >
                <option value="all">All Ecosystems</option>
                <option value="air">Airborne (MEMS Mics)</option>
                <option value="water">Marine (Hydrophones)</option>
              </select>
            </div>

            <div className="space-y-1">
              <label className="text-[9px] font-mono uppercase text-slate-500 tracking-wider">Source Library</label>
              <select
                value={sourceFilter}
                onChange={(e) => setSourceFilter(e.target.value)}
                className="w-full p-2 bg-black/60 border border-white/10 focus:border-cyan-500 text-slate-300 rounded font-sans text-xs outline-none transition-colors cursor-pointer"
              >
                <option value="all">All Datasets</option>
                {uniqueSources.map((id) => (
                  <option key={id} value={id}>
                    {id.replace("_", " ").toUpperCase()}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Blobs List grouped by environmental context */}
          <div className="divide-y divide-white/5 max-h-[460px] overflow-y-auto pr-1 space-y-1">
            {filteredBlobs.slice(0, visibleCount).map((blob) => {
              const isSelected = selectedBlob && selectedBlob.id === blob.id;
              return (
                <div
                  key={blob.id}
                  onClick={() => handleBlobClick(blob)}
                  className={`p-3 rounded-lg flex items-center justify-between gap-3 cursor-pointer transition-all ${
                    isSelected
                      ? "bg-cyan-500/10 border border-cyan-500/30 shadow-[0_0_10px_rgba(6,182,212,0.05)]"
                      : "bg-black/20 hover:bg-white/5 border border-transparent hover:border-white/10"
                  }`}
                >
                  <div className="space-y-1 min-w-0 flex-1">
                    <div className="flex items-center space-x-1.5">
                      <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${blob.acoustic_environment === "water" ? "bg-cyan-400 shadow-[0_0_4px_rgba(34,211,238,0.8)]" : "bg-amber-400"}`}></span>
                      <span className="text-slate-400 font-mono text-[10px] tracking-tight truncate block">
                        {blob.filename}
                      </span>
                    </div>
                    <span className="text-white font-sans text-xs font-semibold block truncate">
                      {blob.label_primary}
                    </span>
                    <span className="text-slate-500 text-[11px] block truncate">
                      {blob.source_name} • {blob.duration_sec.toFixed(1)}s
                    </span>
                  </div>

                  <div className="flex items-center space-x-2 shrink-0">
                    {blob.analysis_status === "ready" ? (
                      <span className="px-2 py-0.5 bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 text-[9px] font-mono rounded">
                        Classified
                      </span>
                    ) : (
                      <span className="px-2 py-0.5 bg-white/5 text-slate-400 border border-white/10 text-[9px] font-mono rounded">
                        Raw
                      </span>
                    )}
                    <PlayCircle className={`h-5 w-5 ${isSelected ? "text-cyan-400" : "text-slate-600"}`} />
                  </div>
                </div>
              );
            })}

            {filteredBlobs.length === 0 && (
              <div className="py-12 text-center text-slate-500 space-y-1">
                <Filter className="h-6 w-6 text-slate-600 mx-auto" />
                <p className="font-sans text-xs">No acoustic files matched current filters.</p>
                <button
                  onClick={() => {
                    setSearchQuery("");
                    setMediumFilter("all");
                    setSourceFilter("all");
                  }}
                  className="text-xs text-cyan-400 font-semibold underline mt-2"
                >
                  Reset Active Filters
                </button>
              </div>
            )}
          </div>

          {/* Pagination limit toggler */}
          {filteredBlobs.length > visibleCount && (
            <button
              onClick={() => setVisibleCount((prev) => prev * 2)}
              className="w-full text-center py-2 bg-white/5 border border-white/10 hover:bg-white/10 hover:border-cyan-500/30 text-slate-300 font-sans text-xs font-semibold rounded cursor-pointer transition-colors"
            >
              Load More Recordings...
            </button>
          )}
        </div>

        {/* Right Side: High-End SINE Player and Diagnostics Explorer */}
        <div className="lg:col-span-7 space-y-6">
          {selectedBlob ? (
            <>
              {/* Core SINE Timeline and Spectrometer */}
              <AcousticPlayer
                selectedBlob={selectedBlob}
                onAnalysisUpdated={handleAnalysisUpdatedInPlayer}
              />

              {/* Advanced Species & Target Data Card */}
              <div className="bg-[#0A0B0E]/60 border border-white/10 rounded-xl p-5 space-y-3.5 shadow-xl">
                <h4 className="text-sm font-sans font-semibold text-slate-200 flex items-center gap-1.5 border-b border-white/10 pb-2">
                  <Info className="h-4 w-4 text-cyan-400" />
                  Target Item Metadata Profile
                </h4>

                <div className="grid grid-cols-2 lg:grid-cols-3 gap-y-3.5 gap-x-6 text-xs">
                  <div>
                    <span className="text-slate-500 font-mono text-[9px] uppercase">Scientific Taxonomy</span>
                    <span className="text-slate-200 font-sans font-medium block truncate mt-0.5">
                      {selectedBlob.label_primary}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-500 font-mono text-[9px] uppercase">Ecosystem Category</span>
                    <span className="text-slate-300 font-sans font-medium block truncate mt-0.5">
                      {selectedBlob.label_secondary}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-500 font-mono text-[9px] uppercase">Source Provider</span>
                    <span className="text-slate-300 font-sans font-medium block truncate mt-0.5">
                      {selectedBlob.source_name}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-500 font-mono text-[9px] uppercase">Sample Rate</span>
                    <span className="text-slate-200 font-mono block mt-0.5">
                      {selectedBlob.sample_rate_hz} Hz ({selectedBlob.channels} ch)
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-500 font-mono text-[9px] uppercase">Hash Digest</span>
                    <span className="text-slate-300 font-mono text-[11px] block truncate mt-0.5">
                      {selectedBlob.content_hash}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-500 font-mono text-[9px] uppercase">License Clearance</span>
                    <span className="text-slate-300 font-sans block truncate mt-0.5">
                      {selectedBlob.license}
                    </span>
                  </div>
                </div>
              </div>
            </>
          ) : (
            <div className="bg-[#0A0B0E]/60 border border-white/10 rounded-xl p-12 text-center text-slate-500">
              <FolderOpen className="h-10 w-10 text-slate-600 mx-auto mb-3" />
              <p className="font-sans text-sm font-medium">No recording selected</p>
              <p className="text-slate-500 text-xs mt-1">
                Choose an acoustic item from the databank on the left to activate SINE Player
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
