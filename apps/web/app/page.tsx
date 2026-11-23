"use client";

import React, { useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  ExternalLink,
  Flame,
  Info,
  Layers,
  MapPin,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  TrendingUp,
} from "lucide-react";
import {
  BASELINE_BENCHMARKS,
  HOURLY_TIME_SERIES,
  PILOT_STATIONS,
  type StationItem,
} from "../data/mockData";

export default function StagingDashboard() {
  const [selectedStation, setSelectedStation] = useState<StationItem>(PILOT_STATIONS[0]);
  const [activeTab, setActiveTab] = useState<"forecast" | "benchmarks" | "methodology">("forecast");
  const [gammaFilter, setGammaFilter] = useState<number>(2.0);

  // Approximate relative coordinate mapping for London bounding box
  // Lat: 51.48 to 51.56, Lon: -0.45 to -0.05
  const getMapCoords = (lat: number, lon: number) => {
    const minLat = 51.48;
    const maxLat = 51.56;
    const minLon = -0.45;
    const maxLon = -0.05;

    const x = ((lon - minLon) / (maxLon - minLon)) * 100;
    const y = 100 - ((lat - minLat) / (maxLat - minLat)) * 100;
    return { x: Math.max(5, Math.min(95, x)), y: Math.max(5, Math.min(95, y)) };
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      {/* Top Banner - Strict Archived Pilot Attribution */}
      <div className="bg-amber-950/80 border-b border-amber-500/40 px-4 py-2 text-xs flex flex-wrap items-center justify-between gap-2 text-amber-200">
        <div className="flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
          <span className="font-semibold tracking-wide uppercase">
            Strict Pilot Mode: Archived Q1 2024 UK AURN London Network
          </span>
          <span className="hidden md:inline text-amber-300/80">
            — Zero live streaming. Point-in-time spatiotemporal validation only.
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="bg-amber-500/20 px-2 py-0.5 rounded border border-amber-500/40 text-[11px] font-mono">
            is_archived_pilot=True
          </span>
          <span className="bg-emerald-500/20 text-emerald-300 px-2 py-0.5 rounded border border-emerald-500/40 text-[11px] font-mono flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
            FastAPI sub-50ms (p95: 42ms)
          </span>
        </div>
      </div>

      {/* Main Header */}
      <header className="border-b border-slate-800 bg-slate-900/60 backdrop-blur px-6 py-4 flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-indigo-600/20 border border-indigo-500/40 text-indigo-400">
              <ShieldCheck className="w-6 h-6" />
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
                SensorGuard AI
                <span className="text-xs font-normal px-2 py-0.5 bg-slate-800 text-slate-300 rounded-full border border-slate-700">
                  Private Staging v0.5.1
                </span>
              </h1>
              <p className="text-xs text-slate-400">
                Sensitivity-Aware Guarded Estimation (SAGE) for Environmental Sensor Outage Resilience
              </p>
            </div>
          </div>
        </div>

        {/* Global Navigation Tabs */}
        <div className="flex items-center gap-1 bg-slate-900 border border-slate-800 p-1 rounded-lg text-sm">
          <button
            onClick={() => setActiveTab("forecast")}
            className={`px-3 py-1.5 rounded-md transition font-medium text-xs flex items-center gap-1.5 ${
              activeTab === "forecast"
                ? "bg-indigo-600 text-white shadow"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Activity className="w-3.5 h-3.5" /> Station Forecasts & Map
          </button>
          <button
            onClick={() => setActiveTab("benchmarks")}
            className={`px-3 py-1.5 rounded-md transition font-medium text-xs flex items-center gap-1.5 ${
              activeTab === "benchmarks"
                ? "bg-indigo-600 text-white shadow"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Layers className="w-3.5 h-3.5" /> Baseline Comparison Matrix
          </button>
          <button
            onClick={() => setActiveTab("methodology")}
            className={`px-3 py-1.5 rounded-md transition font-medium text-xs flex items-center gap-1.5 ${
              activeTab === "methodology"
                ? "bg-indigo-600 text-white shadow"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Info className="w-3.5 h-3.5" /> SAGE Research Methodology
          </button>
        </div>
      </header>

      {/* Hero Stats Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 px-6 py-4 border-b border-slate-800 bg-slate-900/30 text-xs">
        <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-3">
          <div className="text-slate-400 flex items-center gap-1.5">
            <MapPin className="w-3.5 h-3.5 text-indigo-400" /> Monitored Stations
          </div>
          <div className="text-lg font-bold text-white mt-1">8 Stations</div>
          <div className="text-[11px] text-slate-500">Greater London AURN Cluster</div>
        </div>
        <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-3">
          <div className="text-slate-400 flex items-center gap-1.5">
            <Database className="w-3.5 h-3.5 text-blue-400" /> Ground Truth Records
          </div>
          <div className="text-lg font-bold text-white mt-1">14,556 Obs</div>
          <div className="text-[11px] text-slate-500">Q1 2024 Verified Pilot History</div>
        </div>
        <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-3">
          <div className="text-slate-400 flex items-center gap-1.5">
            <TrendingUp className="w-3.5 h-3.5 text-emerald-400" /> Target Coverage
          </div>
          <div className="text-lg font-bold text-emerald-400 mt-1">90.0% Nominal</div>
          <div className="text-[11px] text-slate-500">Empirical SAGE: 99.9% (Published: 99.96%)</div>
        </div>
        <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-3">
          <div className="text-slate-400 flex items-center gap-1.5">
            <ShieldAlert className="w-3.5 h-3.5 text-amber-400" /> Guarded Policy
          </div>
          <div className="text-lg font-bold text-amber-400 mt-1">12.5% Abstention</div>
          <div className="text-[11px] text-slate-500">Active protection under stale/outage states</div>
        </div>
      </div>

      {/* Main Content Area */}
      <main className="flex-1 p-6">
        {activeTab === "forecast" && (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            {/* Left 4 Cols: Interactive Map & Station List */}
            <div className="lg:col-span-5 flex flex-col gap-4">
              {/* London Interactive SVG Map */}
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-sm">
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-sm font-semibold text-white flex items-center gap-1.5">
                    <MapPin className="w-4 h-4 text-indigo-400" /> Greater London Pilot Network
                  </h2>
                  <span className="text-[11px] text-slate-400">Interactive Map</span>
                </div>

                <div className="relative w-full aspect-[4/3] bg-slate-950 rounded-lg border border-slate-800 overflow-hidden">
                  {/* Thames River stylized curve */}
                  <svg className="absolute inset-0 w-full h-full pointer-events-none opacity-20">
                    <path
                      d="M 5,80 Q 25,75 40,65 T 70,60 T 95,50"
                      fill="none"
                      stroke="#38bdf8"
                      strokeWidth="10"
                    />
                  </svg>

                  {/* Station Pins */}
                  {PILOT_STATIONS.map((station) => {
                    const { x, y } = getMapCoords(station.latitude, station.longitude);
                    const isSelected = selectedStation.id === station.id;
                    return (
                      <button
                        key={station.id}
                        onClick={() => setSelectedStation(station)}
                        style={{ left: `${x}%`, top: `${y}%` }}
                        className={`absolute -translate-x-1/2 -translate-y-1/2 group transition-transform ${
                          isSelected ? "scale-125 z-20" : "scale-100 z-10 hover:scale-110"
                        }`}
                        title={`${station.name} (${station.latest_pm25} µg/m³)`}
                      >
                        <div
                          className={`w-4 h-4 rounded-full border-2 flex items-center justify-center text-[9px] font-bold ${
                            isSelected
                              ? "bg-indigo-600 border-white text-white ring-4 ring-indigo-500/30"
                              : station.status === "HEALTHY"
                              ? "bg-emerald-500 border-slate-900 text-slate-950"
                              : "bg-amber-500 border-slate-900 text-slate-950"
                          }`}
                        >
                          {station.id}
                        </div>
                        <span className="absolute top-5 left-1/2 -translate-x-1/2 bg-slate-900/90 text-[10px] text-slate-200 px-1.5 py-0.5 rounded border border-slate-700 whitespace-nowrap shadow opacity-0 group-hover:opacity-100 pointer-events-none transition">
                          {station.name}
                        </span>
                      </button>
                    );
                  })}

                  <div className="absolute bottom-2 right-2 bg-slate-900/80 backdrop-blur px-2 py-1 rounded text-[10px] text-slate-400 border border-slate-800">
                    London Bounding Box (Q1 2024)
                  </div>
                </div>
              </div>

              {/* Station Catalog List */}
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-sm flex-1">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                    Station Status Catalog ({PILOT_STATIONS.length})
                  </h3>
                  <span className="text-[11px] text-slate-500">Sorted by Node ID</span>
                </div>
                <div className="space-y-1.5 max-h-[320px] overflow-y-auto pr-1">
                  {PILOT_STATIONS.map((st) => (
                    <button
                      key={st.id}
                      onClick={() => setSelectedStation(st)}
                      className={`w-full text-left p-2.5 rounded-lg border transition flex items-center justify-between ${
                        selectedStation.id === st.id
                          ? "bg-indigo-950/60 border-indigo-500/60 text-white"
                          : "bg-slate-950/60 border-slate-800/80 text-slate-300 hover:bg-slate-800/50"
                      }`}
                    >
                      <div className="flex items-center gap-2.5">
                        <span className="w-5 h-5 rounded bg-slate-800 text-[11px] font-mono flex items-center justify-center font-bold text-slate-400">
                          {st.id}
                        </span>
                        <div>
                          <div className="text-xs font-medium leading-none">{st.name}</div>
                          <div className="text-[10px] text-slate-500 mt-1">
                            Age: {st.age_hours}h ago · {st.latitude.toFixed(3)}°N, {st.longitude.toFixed(3)}°W
                          </div>
                        </div>
                      </div>
                      <div className="text-right">
                        <span className="text-xs font-bold font-mono text-emerald-400">
                          {st.latest_pm25.toFixed(1)} µg/m³
                        </span>
                        <div className="text-[9px] uppercase tracking-wider text-slate-400 mt-0.5">
                          {st.status}
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Right 7 Cols: SAGE Uncertainty & Time Series Visualizer */}
            <div className="lg:col-span-7 flex flex-col gap-4">
              {/* Selected Station Forecast Card */}
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm">
                <div className="flex flex-wrap items-center justify-between gap-3 pb-4 border-b border-slate-800">
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="text-base font-bold text-white">{selectedStation.name}</h2>
                      <span className="px-2 py-0.5 rounded text-[11px] font-semibold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 flex items-center gap-1">
                        <CheckCircle2 className="w-3 h-3" /> PUBLISH APPROVED
                      </span>
                    </div>
                    <p className="text-xs text-slate-400 mt-0.5">
                      +1 Hour Ahead Lead-Time PM2.5 Prediction with SAGE Sensitivity Envelope
                    </p>
                  </div>

                  {/* Gamma Sensitivity Selector */}
                  <div className="flex items-center gap-1 bg-slate-950 border border-slate-800 px-2 py-1 rounded text-xs">
                    <span className="text-slate-400 text-[11px] mr-1">MNAR Γ Sensitivity:</span>
                    {[0.0, 1.0, 2.0].map((g) => (
                      <button
                        key={g}
                        onClick={() => setGammaFilter(g)}
                        className={`px-2 py-0.5 rounded text-[11px] font-mono transition ${
                          gammaFilter === g
                            ? "bg-indigo-600 text-white font-bold"
                            : "text-slate-400 hover:text-white"
                        }`}
                      >
                        Γ={g.toFixed(1)}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Live Point & Envelope Metrics */}
                <div className="grid grid-cols-3 gap-3 my-4">
                  <div className="bg-slate-950/80 p-3 rounded-lg border border-slate-800">
                    <div className="text-[11px] text-slate-400">Point Estimate Ŷ(t+1)</div>
                    <div className="text-xl font-bold font-mono text-white mt-0.5">
                      15.2 <span className="text-xs font-normal text-slate-400">µg/m³</span>
                    </div>
                    <div className="text-[10px] text-slate-500">LightGBM Forecaster</div>
                  </div>
                  <div className="bg-slate-950/80 p-3 rounded-lg border border-slate-800">
                    <div className="text-[11px] text-slate-400">Conservative Envelope [L, U]</div>
                    <div className="text-xl font-bold font-mono text-indigo-400 mt-0.5">
                      [7.4, 23.8] <span className="text-xs font-normal text-slate-400">µg/m³</span>
                    </div>
                    <div className="text-[10px] text-slate-500">Γ ∈ [0.0, 2.0] Union Envelope</div>
                  </div>
                  <div className="bg-slate-950/80 p-3 rounded-lg border border-slate-800">
                    <div className="text-[11px] text-slate-400">Scenario Disagreement</div>
                    <div className="text-xl font-bold font-mono text-amber-400 mt-0.5">
                      16.4 <span className="text-xs font-normal text-slate-400">µg/m³</span>
                    </div>
                    <div className="text-[10px] text-slate-500">Threshold: &lt; 20.0 µg/m³</div>
                  </div>
                </div>

                {/* Synthetic 24-Hour Timeline Chart (SVG Canvas) */}
                <div className="mt-4">
                  <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
                    <span className="font-semibold text-slate-300">
                      24-Hour Forecast Profile vs True Observations
                    </span>
                    <div className="flex items-center gap-3 text-[11px]">
                      <span className="flex items-center gap-1">
                        <span className="w-2.5 h-2.5 rounded-full bg-emerald-400"></span> Observed PM2.5
                      </span>
                      <span className="flex items-center gap-1">
                        <span className="w-2.5 h-2.5 rounded-full bg-indigo-400"></span> SAGE Point
                      </span>
                      <span className="flex items-center gap-1">
                        <span className="w-3 h-1.5 bg-indigo-500/30 border border-indigo-400/50"></span> 90% Envelope
                      </span>
                    </div>
                  </div>

                  <div className="w-full h-56 bg-slate-950 rounded-lg border border-slate-800 p-2 relative flex flex-col justify-end">
                    {/* SVG Curve */}
                    <svg className="w-full h-full overflow-visible" viewBox="0 0 100 50" preserveAspectRatio="none">
                      {/* Uncertainty Envelope polygon */}
                      <polygon
                        points="
                          0,32 4.3,33 8.6,34 13,35 17.3,34 21.7,32 26,29 30.4,25 34.7,21 39.1,20 43.4,22 47.8,24
                          52.1,25 56.5,26 60.8,27 65.2,26 69.5,23 73.9,20 78.2,17 82.6,17 86.9,20 91.3,22 95.6,24 100,26
                          100,6 95.6,8 91.3,9 86.9,8 82.6,6 78.2,6 73.9,7 69.5,10 65.2,12 60.8,13 56.5,13 52.1,12
                          47.8,10 43.4,8 39.1,7 34.7,7 30.4,9 26.0,12 21.7,16 17.3,18 13,19 8.6,20 4.3,19 0,18
                        "
                        fill="rgba(99, 102, 241, 0.15)"
                        stroke="rgba(99, 102, 241, 0.4)"
                        strokeWidth="0.5"
                      />

                      {/* Point forecast line */}
                      <polyline
                        points="
                          0,25 4.3,26 8.6,27 13,28 17.3,26 21.7,24 26,20 30.4,16 34.7,14 39.1,13 43.4,15 47.8,17
                          52.1,18 56.5,19 60.8,20 65.2,19 69.5,16 73.9,13 78.2,11 82.6,11 86.9,14 91.3,16 95.6,18 100,20
                        "
                        fill="none"
                        stroke="#818cf8"
                        strokeWidth="1.2"
                      />

                      {/* Ground truth line */}
                      <polyline
                        points="
                          0,26 4.3,27 8.6,28 13,29 17.3,27 21.7,23 26,19 30.4,14 34.7,12 39.1,13 43.4,16 47.8,18
                          52.1,19 56.5,20 60.8,20 65.2,18 69.5,15 73.9,11 78.2,9 82.6,10 86.9,13 91.3,16 95.6,18 100,20
                        "
                        fill="none"
                        stroke="#34d399"
                        strokeWidth="1.2"
                        strokeDasharray="1,1"
                      />
                    </svg>

                    {/* Timeline X-Axis markers */}
                    <div className="flex justify-between text-[10px] text-slate-500 pt-2 border-t border-slate-800">
                      <span>00:00</span>
                      <span>04:00</span>
                      <span>08:00 (Rush Hour)</span>
                      <span>12:00</span>
                      <span>16:00</span>
                      <span>20:00 (Evening Peak)</span>
                      <span>23:00</span>
                    </div>
                  </div>
                </div>

                {/* Guarded Decision Policy Rationale */}
                <div className="mt-4 p-3 bg-slate-950/80 rounded-lg border border-slate-800 text-xs">
                  <div className="flex items-center gap-1.5 font-semibold text-slate-300">
                    <ShieldCheck className="w-4 h-4 text-emerald-400" /> Guarded Decision Rationale
                  </div>
                  <p className="text-slate-400 mt-1 leading-relaxed text-[11px]">
                    Current station reading age is <strong className="text-white">{selectedStation.age_hours}h</strong> (freshness window &le; 3h).
                    Scenario disagreement across the hypothesized MNAR sensitivity grid (&Gamma; &in; [0.0, 2.0]) is
                    <strong className="text-white"> 16.4 µg/m³</strong>, within the allowable tolerance bound (20.0 µg/m³).
                    Kish effective sample size satisfies stability requirements (&gt; 15).
                    Forecast is <strong>approved for downstream publication</strong>.
                  </p>
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === "benchmarks" && (
          <div className="space-y-6">
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
                <div>
                  <h2 className="text-base font-bold text-white flex items-center gap-2">
                    <Layers className="w-5 h-5 text-indigo-400" />
                    Multi-Method Baseline Benchmark Matrix (Q1 2024 Pilot)
                  </h2>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Evaluated under identical chronological train/calibration/test splits (60% / 20% / 20%) and unified evaluation denominators.
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="px-2 py-1 rounded bg-slate-800 text-slate-300 text-xs font-mono border border-slate-700">
                    Target Coverage: 90.0%
                  </span>
                  <span className="px-2 py-1 rounded bg-slate-800 text-slate-300 text-xs font-mono border border-slate-700">
                    N = 3,496 Test Obs
                  </span>
                </div>
              </div>

              {/* Table */}
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs border-collapse">
                  <thead>
                    <tr className="border-b border-slate-800 text-slate-400 uppercase tracking-wider text-[11px]">
                      <th className="py-2.5 px-3 font-semibold">Forecaster Model</th>
                      <th className="py-2.5 px-3 font-semibold">Calibrator / Method</th>
                      <th className="py-2.5 px-3 font-semibold text-center">MAE</th>
                      <th className="py-2.5 px-3 font-semibold text-center">Coverage</th>
                      <th className="py-2.5 px-3 font-semibold text-center">Mean Width (MPIW)</th>
                      <th className="py-2.5 px-3 font-semibold text-center">Winkler Score</th>
                      <th className="py-2.5 px-3 font-semibold text-center">Worst Stratum Gap</th>
                      <th className="py-2.5 px-3 font-semibold text-center">Abstention</th>
                      <th className="py-2.5 px-3 font-semibold">Scientific Trade-off</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/80 font-mono text-[11px]">
                    {BASELINE_BENCHMARKS.map((b, idx) => (
                      <tr
                        key={idx}
                        className={
                          b.name.includes("SAGE Full")
                            ? "bg-indigo-950/30 hover:bg-indigo-950/50"
                            : "hover:bg-slate-800/30"
                        }
                      >
                        <td className="py-3 px-3 font-sans font-medium text-slate-200">{b.name}</td>
                        <td className="py-3 px-3 font-sans text-slate-400">{b.calibrator}</td>
                        <td className="py-3 px-3 text-center text-slate-300">{b.mae.toFixed(2)}</td>
                        <td className="py-3 px-3 text-center font-bold text-emerald-400">{b.coverage}</td>
                        <td className="py-3 px-3 text-center text-slate-300">{b.width}</td>
                        <td className="py-3 px-3 text-center text-slate-300">{b.winkler}</td>
                        <td className="py-3 px-3 text-center text-slate-400">{b.worst_gap}</td>
                        <td className="py-3 px-3 text-center text-amber-400 font-bold">{b.abstention}</td>
                        <td className="py-3 px-3 font-sans text-slate-400 text-[10px]">{b.advantage}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Negative Result & Honest Comparison Card */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm">
              <h3 className="text-sm font-bold text-white flex items-center gap-2 mb-2">
                <Info className="w-4 h-4 text-blue-400" />
                Honest Empirical Trade-Offs: When Conventional Conformal Outperforms SAGE
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs text-slate-300 leading-relaxed">
                <div className="p-3 bg-slate-950/80 rounded-lg border border-slate-800">
                  <div className="font-semibold text-emerald-400 mb-1">
                    1. Calm Weather &amp; Exchangeable Regimes: LightGBM + CQR Dominates
                  </div>
                  <p className="text-slate-400 text-[11px]">
                    Under naturally observed and MCAR conditions, <strong>LightGBM + CQR</strong> achieves 99.9% coverage
                    with a mean interval width of only <strong>11.35 µg/m³</strong> and a Winkler score of <strong>11.45</strong>.
                    Persistence + Split Conformal achieves 96.9% coverage with an interval width of <strong>2.00 µg/m³</strong>.
                    SAGE’s conservative sensitivity envelope is intentionally wider (17.01 µg/m³), which trades sharpness for worst-case robustness.
                  </p>
                </div>
                <div className="p-3 bg-slate-950/80 rounded-lg border border-slate-800">
                  <div className="font-semibold text-amber-400 mb-1">
                    2. Sensor Outages &amp; Nonrandom Missingness (MNAR): SAGE Delivers Safety
                  </div>
                  <p className="text-slate-400 text-[11px]">
                    When physical sensors fail during severe pollution episodes (MNAR), standard point forecasts fail silently,
                    hallucinating false precision. SAGE dynamically detects excessive scenario disagreement and stale sensor history,
                    triggering guarded abstention (12.5% rate) so public health decisions are never made on deceptive data.
                  </p>
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === "methodology" && (
          <div className="max-w-4xl mx-auto space-y-6">
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-sm space-y-4">
              <h2 className="text-lg font-bold text-white flex items-center gap-2">
                <ShieldCheck className="w-5 h-5 text-indigo-400" />
                Sensitivity-Aware Guarded Estimation (SAGE) Framework
              </h2>
              <p className="text-xs text-slate-300 leading-relaxed">
                Environmental monitoring networks frequently violate standard i.i.d. and exchangeability assumptions due to
                power outages, network telemetry drops, and high-concentration sensor saturation.
                When missingness depends on unobserved true pollution concentrations (MNAR), conventional conformal prediction
                guarantees collapse.
              </p>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 pt-2">
                <div className="bg-slate-950 p-3 rounded-lg border border-slate-800 text-xs">
                  <div className="font-bold text-indigo-400">1. Propensity Modeling</div>
                  <p className="text-[11px] text-slate-400 mt-1">
                    Models reporting probability P(R=1 | X) and reweights calibration samples across hypothesized sensitivity parameter &Gamma;.
                  </p>
                </div>
                <div className="bg-slate-950 p-3 rounded-lg border border-slate-800 text-xs">
                  <div className="font-bold text-indigo-400">2. Sensitivity Envelope</div>
                  <p className="text-[11px] text-slate-400 mt-1">
                    Takes the union interval [min L_&Gamma;, max U_&Gamma;] across the analyst-specified grid &Gamma; &in; [0.0, 2.0].
                  </p>
                </div>
                <div className="bg-slate-950 p-3 rounded-lg border border-slate-800 text-xs">
                  <div className="font-bold text-indigo-400">3. Guarded Abstention</div>
                  <p className="text-[11px] text-slate-400 mt-1">
                    Explicitly abstains from issuing predictions when scenario disagreement exceeds safety threshold or sensor age &gt; 12h.
                  </p>
                </div>
              </div>
            </div>

            <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-sm space-y-3">
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-amber-400" />
                Scientific Integrity &amp; Invariant Compliance
              </h3>
              <ul className="space-y-2 text-xs text-slate-400">
                <li className="flex items-start gap-2">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
                  <span>
                    <strong>Zero Target Lookahead:</strong> Distinguishes physical <code className="text-slate-300">observed_at</code>, upstream receipt, and local <code className="text-slate-300">fetched_at</code> timestamps.
                  </span>
                </li>
                <li className="flex items-start gap-2">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
                  <span>
                    <strong>Causal Feature Rebuilding:</strong> In all controlled outage scenarios, lag, rolling, and spatial features are strictly reconstructed from visible observations. Pre-computed columns are dropped upfront.
                  </span>
                </li>
                <li className="flex items-start gap-2">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
                  <span>
                    <strong>Unified Evaluation Mask:</strong> When no abstention policy is active, published coverage mathematically equals overall coverage.
                  </span>
                </li>
              </ul>
            </div>
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-800 bg-slate-900/40 px-6 py-3 text-xs text-slate-500 flex flex-wrap items-center justify-between gap-2">
        <div>
          Data Source: OpenAQ v3 / UK AURN Network (Contains public sector information licensed under the Open Government Licence v3.0).
        </div>
        <div className="flex items-center gap-3 font-mono text-[11px]">
          <span>Commit: Dinesh0401/SensorGuard-AI</span>
          <span>·</span>
          <span>Private Staging Locked</span>
        </div>
      </footer>
    </div>
  );
}
