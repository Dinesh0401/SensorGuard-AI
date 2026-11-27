"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  FlaskConical,
  Info,
  Layers,
  MapPin,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import {
  api,
  type ExperimentDetailResponse,
  type ExperimentSummary,
  type ForecastRecord,
  type ModelStatusResponse,
  type ObservationRecord,
  type PilotReadinessResponse,
  type StationReliabilityResponse,
  type StationSummary,
} from "../lib/api";
import {
  BASELINE_BENCHMARKS,
  HOURLY_TIME_SERIES,
  PILOT_STATIONS,
} from "../data/mockData";

type ScenarioKey = "natural" | "mcar_20" | "mnar_gamma_1.5" | "burst_24h";

const IS_STATIC_PORTFOLIO = process.env.NEXT_PUBLIC_STATIC_PORTFOLIO === "true";

const SCENARIO_LABELS: Record<
  ScenarioKey,
  { title: string; badge: string; isSimulated: boolean; description: string }
> = {
  natural: {
    title: "Natural Observed Gaps",
    badge: "NATURAL OBSERVED",
    isSimulated: false,
    description:
      "Evaluated only on naturally visible target records; unobserved gaps reflect upstream selection bias.",
  },
  mcar_20: {
    title: "MCAR (20% Independent Dropout)",
    badge: "SIMULATED OUTAGE",
    isSimulated: true,
    description:
      "Synthetic independent masking (20%). Evaluator-only ground truth retained separately for unbiased coverage measurement.",
  },
  "mnar_gamma_1.5": {
    title: "MNAR (γ = 1.5 High-PM2.5 Dropout)",
    badge: "SIMULATED OUTAGE",
    isSimulated: true,
    description:
      "Target-dependent nonrandom blackout parameterized by logit sensitivity γ = 1.5. Features rebuilt strictly from visible history.",
  },
  burst_24h: {
    title: "Contiguous 24-Hour Outage Burst",
    badge: "SIMULATED OUTAGE",
    isSimulated: true,
    description:
      "24-hour continuous blackout testing cold-start priors, neighbor spatial fallback, and staleness abstention.",
  },
};

function computeScenarioDisagreement(f: ForecastRecord): number {
  if (!f.scenario_bounds || f.scenario_bounds.length === 0) return 0;
  const uppers = f.scenario_bounds.map((b) => b.upper);
  return Math.max(...uppers) - Math.min(...uppers);
}

function toHourKey(isoString: string): string {
  const d = new Date(isoString);
  if (Number.isNaN(d.getTime())) return isoString;
  return d.toISOString().slice(0, 13);
}

export default function StagingDashboard() {
  const [stations, setStations] = useState<StationSummary[]>([]);
  const [selectedStationId, setSelectedStationId] = useState<number | null>(null);
  const [observations, setObservations] = useState<ObservationRecord[]>([]);
  const [forecasts, setForecasts] = useState<ForecastRecord[]>([]);
  const [reliability, setReliability] = useState<StationReliabilityResponse | null>(
    null
  );
  const [experiments, setExperiments] = useState<ExperimentSummary[]>([]);
  const [benchmarkDetail, setBenchmarkDetail] =
    useState<ExperimentDetailResponse | null>(null);
  const [ablationDetail, setAblationDetail] =
    useState<ExperimentDetailResponse | null>(null);
  const [modelStatus, setModelStatus] = useState<ModelStatusResponse | null>(null);
  const [pilotReadiness, setPilotReadiness] = useState<PilotReadinessResponse | null>(null);

  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isStationLoading, setIsStationLoading] = useState<boolean>(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [isDemoMode, setIsDemoMode] = useState<boolean>(false);
  const [forceDemoMode, setForceDemoMode] = useState<boolean>(false);

  const [activeTab, setActiveTab] = useState<
    "forecast" | "benchmarks" | "methodology"
  >("forecast");
  const [gammaFilter, setGammaFilter] = useState<string>("envelope");
  const [selectedScenario, setSelectedScenario] =
    useState<ScenarioKey>("natural");

  const activateDemoFallback = useCallback((reason: string | null) => {
    setApiError(reason);
    setIsDemoMode(true);
    const mockAsSummaries: StationSummary[] = PILOT_STATIONS.map((s) => ({
      id: s.id,
      provider: "UK AURN",
      provider_location_id: s.provider_location_id,
      name: s.name,
      latitude: s.latitude,
      longitude: s.longitude,
      country: "GB",
      timezone_name: "Europe/London",
      status: s.status,
      is_stale: s.status !== "HEALTHY",
      is_archived_pilot: true,
      latest_observation_time: "2024-03-31T23:00:00Z",
      latest_pm25_value: s.latest_pm25,
      age_hours: s.age_hours,
    }));
    setStations(mockAsSummaries);
    setSelectedStationId((prev) => prev ?? mockAsSummaries[0].id);
    setBenchmarkDetail(null);
    setAblationDetail(null);
  }, []);

  const loadInitialData = useCallback(async () => {
    setIsLoading(true);
    setApiError(null);

    if (forceDemoMode || IS_STATIC_PORTFOLIO) {
      activateDemoFallback(
        forceDemoMode ? "Manual Demonstration Mode enabled by operator." : null
      );
      setIsLoading(false);
      return;
    }

    try {
      const [stRes, expRes, mStatus, pReadiness] = await Promise.all([
        api.getStations(),
        api.getExperiments().catch(() => null),
        api.getModelStatus(),
        api.getPilotReadiness(),
      ]);

      setStations(stRes.stations);
      if (stRes.stations.length > 0) {
        setSelectedStationId((prev) => prev ?? stRes.stations[0].id);
      }

      if (expRes) {
        setExperiments(expRes.experiments);
        const benchExp = expRes.experiments.find(
          (e) =>
            e.experiment_id.startsWith("SG-BENCH") ||
            e.models_evaluated.includes("persistence")
        );
        const ablExp = expRes.experiments.find(
          (e) =>
            e.experiment_id.startsWith("SG-SAGE") ||
            e.models_evaluated.includes("full_sage")
        );

        const [bDetail, aDetail] = await Promise.all([
          benchExp ? api.getExperimentDetail(benchExp.experiment_id).catch(() => null) : Promise.resolve(null),
          ablExp ? api.getExperimentDetail(ablExp.experiment_id).catch(() => null) : Promise.resolve(null),
        ]);
        setBenchmarkDetail(bDetail);
        setAblationDetail(aDetail);
      }

      if (mStatus) setModelStatus(mStatus);
      if (pReadiness) setPilotReadiness(pReadiness);
      setIsDemoMode(false);
    } catch (err: unknown) {
      const msg =
        err instanceof Error
          ? err.message
          : "Failed connecting to FastAPI backend at http://localhost:8000";
      activateDemoFallback(msg);
    } finally {
      setIsLoading(false);
    }
  }, [forceDemoMode, activateDemoFallback]);

  useEffect(() => {
    loadInitialData();
  }, [loadInitialData]);

  useEffect(() => {
    if (!selectedStationId) return;

    if (isDemoMode) {
      const mockObs: ObservationRecord[] = HOURLY_TIME_SERIES.map((h) => ({
        observed_at: `2024-03-31T${h.hour}:00Z`,
        received_at: `2024-03-31T${h.hour}:05Z`,
        fetched_at: `2024-03-31T${h.hour}:10Z`,
        value: h.observed,
        unit: "µg/m³",
        quality_flag: "VALID",
        source_status: "HEALTHY",
        version: 1,
      }));
      const mockFcast: ForecastRecord[] = HOURLY_TIME_SERIES.map((h) => ({
        station_id: selectedStationId,
        decision_cutoff: `2024-03-31T${h.hour}:00Z`,
        target_time: `2024-03-31T${h.hour}:00Z`,
        point_forecast: h.point_pred,
        lower_bound: h.lower,
        upper_bound: h.upper,
        nominal_coverage: 0.9,
        model_version: "SAGE-LIGHTGBM-v1",
        status: h.status as "publish" | "caution" | "abstain",
        abstain_reason:
          h.status === "abstain"
            ? "stale_sensor_history"
            : h.status === "caution"
            ? "moderate_scenario_disagreement"
            : "sufficient_evidence",
        is_stale: h.status === "abstain",
        scenario_bounds: [
          { gamma: 0.0, lower: h.point_pred - 6.6, upper: h.point_pred + 6.6 },
          { gamma: 0.5, lower: h.lower + 1.5, upper: h.upper - 1.5 },
          { gamma: 1.0, lower: h.lower + 1.0, upper: h.upper - 1.0 },
          { gamma: 1.5, lower: h.lower + 0.5, upper: h.upper - 0.5 },
          { gamma: 2.0, lower: h.lower, upper: h.upper },
        ],
      }));
      setObservations(mockObs);
      setForecasts(mockFcast);
      setReliability(null);
      return;
    }

    const fetchDetails = async () => {
      setIsStationLoading(true);
      try {
        const [obsRes, fcastRes, relRes] = await Promise.allSettled([
          api.getObservations(selectedStationId, 72),
          api.getForecasts(selectedStationId, 24),
          api.getReliability(selectedStationId),
        ]);

        if (obsRes.status === "fulfilled") {
          setObservations(obsRes.value.observations);
        }
        if (fcastRes.status === "fulfilled") {
          setForecasts(fcastRes.value.forecasts);
        }
        if (relRes.status === "fulfilled") {
          setReliability(relRes.value);
        }
      } finally {
        setIsStationLoading(false);
      }
    };

    fetchDetails();
  }, [selectedStationId, isDemoMode]);

  const currentStation =
    stations.find((s) => s.id === selectedStationId) || stations[0];

  // Strict timestamp map of observations by UTC hour key (no fabrication on gaps)
  const observedByHour = useMemo(() => {
    const map = new Map<string, number>();
    for (const obs of observations) {
      if (obs.value !== null && Number.isFinite(obs.value)) {
        map.set(toHourKey(obs.observed_at), obs.value);
      }
    }
    return map;
  }, [observations]);

  // Dynamic chart scale (fits observed & interval envelope bounds cleanly)
  const chartMaxY = useMemo(() => {
    let maxVal = 30.0;
    for (const f of forecasts) {
      if (f.upper_bound > maxVal) maxVal = f.upper_bound;
      const obsVal = observedByHour.get(toHourKey(f.target_time));
      if (obsVal !== undefined && obsVal > maxVal) maxVal = obsVal;
    }
    return Math.ceil(maxVal / 10) * 10 + 5;
  }, [forecasts, observedByHour]);

  // UK AURN bounding box covering actual 8 audited stations (Southampton 50.91N to Manchester 53.48N)
  const getMapCoords = (lat: number, lon: number) => {
    const minLat = 50.6;
    const maxLat = 53.8;
    const minLon = -2.6;
    const maxLon = 1.4;

    const x = ((lon - minLon) / (maxLon - minLon)) * 100;
    const y = 100 - ((lat - minLat) / (maxLat - minLat)) * 100;
    return { x: Math.max(6, Math.min(94, x)), y: Math.max(8, Math.min(92, y)) };
  };

  // Extract live scenario metrics when backend experiment detail is available
  const liveScenarioBenchRows = useMemo(() => {
    const scenData =
      benchmarkDetail?.details?.scenarios?.[selectedScenario] || null;
    if (!scenData) return null;

    const orderedKeys = [
      "persistence",
      "seasonal_naive",
      "spatial_neighbor",
      "ridge",
      "lightgbm_point",
      "lightgbm_quantile",
      "lightgbm_rolling",
      "lightgbm_aci",
      "sage_gamma_0",
      "sage_no_abstain",
      "sage_full",
    ];
    return orderedKeys
      .filter((k) => Boolean(scenData[k]))
      .map((k) => {
        const entry = scenData[k];
        const m = entry.metrics || {};
        return {
          key: k,
          name: k,
          calibrator: entry.calibrator || "Conformal",
          mae: typeof m.mae === "number" ? m.mae : null,
          rmse: typeof m.rmse === "number" ? m.rmse : null,
          picp: typeof m.picp === "number" ? m.picp : null,
          published_picp:
            typeof m.published_picp === "number" ? m.published_picp : null,
          mpiw: typeof m.mpiw === "number" ? m.mpiw : null,
          winkler:
            typeof m.winkler_score === "number" ? m.winkler_score : null,
          worst_gap:
            typeof m.worst_stratum_coverage_gap === "number"
              ? m.worst_stratum_coverage_gap
              : null,
          abstention:
            typeof m.abstention_rate === "number" ? m.abstention_rate : 0.0,
        };
      });
  }, [benchmarkDetail, selectedScenario]);

  const liveAblationRows = useMemo(() => {
    const agg = ablationDetail?.details?.aggregates?.[selectedScenario] || null;
    if (!agg) return null;
    return Object.entries(agg).map(([cond, stats]) => ({
      condition: cond,
      picp_mean: stats.picp_mean,
      published_picp_mean: stats.published_picp_mean,
      mpiw_mean: stats.mpiw_mean,
      winkler_score_mean: stats.winkler_score_mean,
      abstention_rate_mean: stats.abstention_rate_mean,
      mean_disagreement_mean: stats.mean_disagreement_mean,
    }));
  }, [ablationDetail, selectedScenario]);

  const selectedGammaNum =
    gammaFilter === "envelope" ? null : Number(gammaFilter);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      {/* Top Banner - Strict Archived Pilot Attribution */}
      <div className="bg-amber-950/80 border-b border-amber-500/40 px-4 py-2 text-xs flex flex-wrap items-center justify-between gap-2 text-amber-200">
        <div className="flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
          <span className="font-semibold tracking-wide uppercase">
            Strict Pilot Mode: Archived Q1 2024 UK AURN Network
          </span>
          <span className="hidden md:inline text-amber-300/80">
            — Zero live telemetry or medical health claims. Point-in-time historical validation only.
          </span>
        </div>
        <div className="flex items-center gap-2.5 flex-wrap">
          <span className="bg-amber-500/20 px-2 py-0.5 rounded border border-amber-500/40 text-[11px] font-mono">
            is_archived_pilot=True
          </span>
          <span className="bg-sky-500/20 px-2 py-0.5 rounded border border-sky-500/40 text-[11px] font-mono text-sky-200">
            mode={pilotReadiness?.operational_data_mode ?? modelStatus?.operational_data_mode ?? "archived_historical"} • Active: {modelStatus?.model_class ?? "SAGEForecaster"}
          </span>
          <span
            className={`px-2 py-0.5 rounded border text-[11px] font-mono flex items-center gap-1.5 ${
              isDemoMode
                ? "bg-indigo-500/20 text-indigo-200 border-indigo-500/40"
                : "bg-emerald-500/20 text-emerald-300 border-emerald-500/40"
            }`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                isDemoMode
                  ? "bg-indigo-400"
                  : "bg-emerald-400 animate-pulse"
              }`}
            ></span>
            {isDemoMode ? "Demo Mode (Offline Fixtures)" : "FastAPI Connected"}
          </span>
          <button
            type="button"
            aria-label={
              forceDemoMode
                ? "Switch back to live API mode"
                : "Enable demonstration fallback mode"
            }
            onClick={() => setForceDemoMode((prev) => !prev)}
            className="bg-slate-900/70 hover:bg-slate-800 border border-slate-700 px-2 py-0.5 rounded text-[11px] font-mono text-slate-200 transition"
          >
            {forceDemoMode ? "Exit Demo Mode" : "Toggle Demo Mode"}
          </button>
        </div>
      </div>

      {/* Demo Mode / API Fallback Banner */}
      {isDemoMode && (
        <div className="bg-indigo-950/90 border-b border-indigo-500/40 px-4 py-2 text-xs flex flex-wrap items-center justify-between gap-2 text-indigo-200">
          <div className="flex items-center gap-2">
            <Info className="w-4 h-4 text-indigo-400 shrink-0" />
            <span>
              <strong>Demonstration Mode Active:</strong> Showing audited Q1 2024 fallback fixtures
              {apiError ? ` (${apiError})` : "."}
            </span>
          </div>
          <button
            type="button"
            onClick={() => {
              setForceDemoMode(false);
              loadInitialData();
            }}
            className="flex items-center gap-1 bg-indigo-600/50 hover:bg-indigo-600/70 px-2.5 py-1 rounded text-[11px] transition"
          >
            <RefreshCw className="w-3 h-3" /> Retry Live API
          </button>
        </div>
      )}

      {/* Main Header */}
      <header className="border-b border-slate-800 bg-slate-900/60 backdrop-blur px-6 py-4 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-indigo-600/20 border border-indigo-500/40 text-indigo-400">
            <ShieldCheck className="w-6 h-6" />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
              SensorGuard AI
              <span className="text-xs font-normal px-2 py-0.5 bg-slate-800 text-slate-300 rounded-full border border-slate-700">
                Private Staging v0.5.3
              </span>
            </h1>
            <p className="text-xs text-slate-400">
              Sensitivity-Aware Guarded Estimation (SAGE) for Environmental Sensor Outage Resilience
            </p>
          </div>
        </div>

        {/* Navigation Tabs */}
        <nav
          aria-label="Primary sections"
          className="flex items-center gap-1 bg-slate-900 border border-slate-800 p-1 rounded-lg text-sm"
        >
          <button
            type="button"
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
            type="button"
            onClick={() => setActiveTab("benchmarks")}
            className={`px-3 py-1.5 rounded-md transition font-medium text-xs flex items-center gap-1.5 ${
              activeTab === "benchmarks"
                ? "bg-indigo-600 text-white shadow"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <FlaskConical className="w-3.5 h-3.5" /> Simulator Lab & Ablations
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("methodology")}
            className={`px-3 py-1.5 rounded-md transition font-medium text-xs flex items-center gap-1.5 ${
              activeTab === "methodology"
                ? "bg-indigo-600 text-white shadow"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Info className="w-3.5 h-3.5" /> SAGE Methodology
          </button>
        </nav>
      </header>

      {/* Main Content Area */}
      <main className="flex-1 p-6 space-y-6">
        {isLoading ? (
          <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-10 text-center text-xs text-slate-400">
            Loading archived Q1 2024 UK AURN station catalog and verified model manifest...
          </div>
        ) : (
          <>
            {activeTab === "forecast" && (
              <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
                {/* Left Column: Interactive UK Network Map & Station Selector */}
                <div className="lg:col-span-4 space-y-4">
                  <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4">
                    <div className="flex items-center justify-between mb-3">
                      <h2 className="text-sm font-semibold text-slate-200 flex items-center gap-1.5">
                        <MapPin className="w-4 h-4 text-indigo-400" />
                        UK AURN 8-Station Network (Q1 2024)
                      </h2>
                      <span className="text-xs text-slate-400 font-mono">
                        {stations.length} Stations
                      </span>
                    </div>

                    <div
                      role="region"
                      aria-label="Geographic projection map of UK AURN stations"
                      className="relative w-full h-64 bg-slate-950 border border-slate-800 rounded-lg overflow-hidden flex items-center justify-center p-2"
                    >
                      <div className="absolute inset-0 bg-[linear-gradient(to_right,#1e293b15_1px,transparent_1px),linear-gradient(to_bottom,#1e293b15_1px,transparent_1px)] bg-[size:20px_20px]" />
                      <div className="absolute top-2 left-2 text-[10px] text-slate-400 font-mono">
                        53.80°N / -2.60°W (Manchester NW)
                      </div>
                      <div className="absolute bottom-2 right-2 text-[10px] text-slate-400 font-mono">
                        50.60°N / 1.40°E (Channel / East)
                      </div>

                      {stations.map((st) => {
                        const coords = getMapCoords(st.latitude, st.longitude);
                        const isSelected = st.id === selectedStationId;
                        const statusColor =
                          st.status === "HEALTHY"
                            ? "bg-emerald-500"
                            : st.status === "LATE"
                            ? "bg-amber-500"
                            : "bg-rose-500";

                        return (
                          <button
                            key={st.id}
                            type="button"
                            aria-label={`Select ${st.name}, status ${st.status}`}
                            onClick={() => setSelectedStationId(st.id)}
                            style={{ left: `${coords.x}%`, top: `${coords.y}%` }}
                            className={`absolute -translate-x-1/2 -translate-y-1/2 group transition-transform ${
                              isSelected ? "scale-125 z-20" : "hover:scale-110 z-10"
                            }`}
                            title={`${st.name} (${st.status}) - Lat: ${st.latitude.toFixed(3)}, Lon: ${st.longitude.toFixed(3)}`}
                          >
                            <div className="relative flex items-center justify-center">
                              {isSelected && (
                                <span
                                  className={`absolute w-6 h-6 rounded-full animate-ping opacity-75 ${statusColor}`}
                                />
                              )}
                              <div
                                className={`w-3.5 h-3.5 rounded-full border-2 border-slate-950 ${statusColor} shadow-md`}
                              />
                            </div>
                            <span className="absolute top-4 left-1/2 -translate-x-1/2 bg-slate-900/95 text-slate-200 border border-slate-700 px-1.5 py-0.5 rounded text-[10px] whitespace-nowrap opacity-0 group-hover:opacity-100 pointer-events-none transition z-30 shadow">
                              {st.name}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  {/* Station Catalog List */}
                  <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4">
                    <div className="flex items-center justify-between mb-3">
                      <h3 className="text-sm font-semibold text-slate-200">
                        Select Station
                      </h3>
                      <span className="text-[11px] text-slate-400">
                        Authentic AURN Locations
                      </span>
                    </div>
                    <div className="space-y-2 max-h-60 overflow-y-auto pr-1">
                      {stations.map((st) => {
                        const isSelected = st.id === selectedStationId;
                        return (
                          <button
                            key={st.id}
                            type="button"
                            onClick={() => setSelectedStationId(st.id)}
                            className={`w-full text-left px-3 py-2.5 rounded-lg border transition flex items-center justify-between ${
                              isSelected
                                ? "bg-indigo-950/60 border-indigo-500/60 text-white"
                                : "bg-slate-900/40 border-slate-800/80 text-slate-300 hover:bg-slate-800/50"
                            }`}
                          >
                            <div>
                              <div className="text-xs font-semibold flex items-center gap-1.5">
                                {st.name}
                                <span className="text-[10px] font-mono text-slate-400">
                                  (#{st.provider_location_id})
                                </span>
                              </div>
                              <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                                {st.latest_pm25_value !== null
                                  ? `${st.latest_pm25_value.toFixed(1)} µg/m³`
                                  : "No recent measurement"}{" "}
                                •{" "}
                                {st.age_hours !== null
                                  ? `${st.age_hours.toFixed(1)}h age`
                                  : "N/A"}
                              </div>
                            </div>
                            <span
                              className={`text-[10px] font-mono uppercase px-2 py-0.5 rounded border ${
                                st.status === "HEALTHY"
                                  ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400"
                                  : st.status === "LATE"
                                  ? "bg-amber-500/10 border-amber-500/30 text-amber-400"
                                  : "bg-rose-500/10 border-rose-500/30 text-rose-400"
                              }`}
                            >
                              {st.status}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  {/* Station Reliability Diagnostics */}
                  {reliability && (
                    <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 text-xs space-y-2">
                      <h4 className="font-semibold text-slate-200">
                        Historical Reliability (Q1 2024)
                      </h4>
                      <div className="grid grid-cols-2 gap-2 text-slate-300">
                        <div className="bg-slate-950 p-2 rounded border border-slate-800">
                          <div className="text-slate-400 text-[10px]">
                            Total Observations
                          </div>
                          <div className="text-sm font-semibold">
                            {reliability.total_observations.toLocaleString()}
                          </div>
                        </div>
                        <div className="bg-slate-950 p-2 rounded border border-slate-800">
                          <div className="text-slate-400 text-[10px]">
                            Missingness / Gap Rate
                          </div>
                          <div className="text-sm font-semibold">
                            {reliability.gap_rate_pct.toFixed(1)}%
                          </div>
                        </div>
                        <div className="bg-slate-950 p-2 rounded border border-slate-800 col-span-2 flex items-center justify-between">
                          <span className="text-slate-400 text-[10px]">
                            Max Consecutive Outage
                          </span>
                          <span className="font-mono font-semibold">
                            {reliability.longest_consecutive_outage_hours.toFixed(1)}h
                          </span>
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                {/* Right Column: Time Series & SAGE Forecast Canvas */}
                <div className="lg:col-span-8 space-y-4">
                  {currentStation && (
                    <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 flex flex-wrap items-center justify-between gap-4">
                      <div>
                        <div className="flex items-center gap-2 flex-wrap">
                          <h2 className="text-base font-bold text-white">
                            {currentStation.name}
                          </h2>
                          <span className="text-xs font-mono text-slate-400">
                            Lat: {currentStation.latitude.toFixed(4)}, Lon:{" "}
                            {currentStation.longitude.toFixed(4)}
                          </span>
                        </div>
                        <p className="text-xs text-slate-400 mt-1">
                          Timestamp-aligned observed PM2.5 vs. SAGE +1h forecast and 90% sensitivity envelope
                        </p>
                      </div>

                      <div className="flex items-center gap-2">
                        <label
                          htmlFor="gamma-selector"
                          className="text-xs text-slate-400"
                        >
                          Sensitivity Slice:
                        </label>
                        <select
                          id="gamma-selector"
                          value={gammaFilter}
                          onChange={(e) => setGammaFilter(e.target.value)}
                          className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono"
                        >
                          <option value="envelope">
                            Conservative Union Envelope [All γ]
                          </option>
                          <option value="0">Highlight γ = 0.0 (MAR)</option>
                          <option value="0.5">Highlight γ = 0.5</option>
                          <option value="1">Highlight γ = 1.0</option>
                          <option value="1.5">Highlight γ = 1.5</option>
                          <option value="2">Highlight γ = 2.0 (Severe MNAR)</option>
                        </select>
                      </div>
                    </div>
                  )}

                  {/* Time Series Canvas Graph */}
                  <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 space-y-4">
                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400 border-b border-slate-800 pb-2">
                      <div className="flex flex-wrap items-center gap-4">
                        <span className="flex items-center gap-1.5">
                          <span className="w-3 h-0.5 bg-sky-400 inline-block"></span>
                          Measured PM2.5 (Actuals)
                        </span>
                        <span className="flex items-center gap-1.5">
                          <span className="w-3 h-0.5 bg-indigo-300 inline-block border-dashed"></span>
                          SAGE +1h Estimate
                        </span>
                        <span className="flex items-center gap-1.5">
                          <span className="w-3 h-2 bg-indigo-500/20 border border-indigo-500/40 inline-block rounded-sm"></span>
                          90% Conservative Envelope
                        </span>
                        {selectedGammaNum !== null && (
                          <span className="flex items-center gap-1.5 text-amber-300">
                            <span className="w-3 h-2 bg-amber-400/25 border border-amber-400 inline-block rounded-sm"></span>
                            γ={selectedGammaNum.toFixed(1)} Interval
                          </span>
                        )}
                      </div>
                      <span className="font-mono text-[11px] text-slate-400">
                        {isStationLoading
                          ? "Refreshing station..."
                          : `${forecasts.length} timesteps (gaps not filled)`}
                      </span>
                    </div>

                    <div className="relative w-full h-72 bg-slate-950/80 border border-slate-800 rounded-lg p-2">
                      {forecasts.length === 0 ? (
                        <div className="w-full h-full flex items-center justify-center text-xs text-slate-500">
                          No forecast records available for this station.
                        </div>
                      ) : (
                        <svg
                          role="img"
                          aria-label="Observed PM2.5 and SAGE prediction interval envelope chart"
                          className="w-full h-full overflow-visible"
                          viewBox="0 0 800 240"
                        >
                          <line
                            x1="0"
                            y1="40"
                            x2="800"
                            y2="40"
                            stroke="#334155"
                            strokeDasharray="3 3"
                          />
                          <line
                            x1="0"
                            y1="100"
                            x2="800"
                            y2="100"
                            stroke="#334155"
                            strokeDasharray="3 3"
                          />
                          <line
                            x1="0"
                            y1="160"
                            x2="800"
                            y2="160"
                            stroke="#334155"
                            strokeDasharray="3 3"
                          />
                          <line
                            x1="0"
                            y1="220"
                            x2="800"
                            y2="220"
                            stroke="#475569"
                          />

                          <text x="5" y="45" fill="#64748b" fontSize="10">
                            {chartMaxY} µg/m³
                          </text>
                          <text x="5" y="105" fill="#64748b" fontSize="10">
                            {Math.round(chartMaxY * 0.66)} µg/m³
                          </text>
                          <text x="5" y="165" fill="#64748b" fontSize="10">
                            {Math.round(chartMaxY * 0.33)} µg/m³
                          </text>
                          <text x="5" y="225" fill="#64748b" fontSize="10">
                            0 µg/m³
                          </text>

                          {(() => {
                            const getY = (v: number) =>
                              220 -
                              (Math.min(chartMaxY, Math.max(0, v)) / chartMaxY) *
                                180;
                            const stepX = 800 / Math.max(1, forecasts.length - 1);

                            // 1. Full Conservative Envelope
                            const upperEnv = forecasts
                              .map((f, i) => `${i * stepX},${getY(f.upper_bound)}`)
                              .join(" ");
                            const lowerEnv = [...forecasts]
                              .reverse()
                              .map(
                                (f, i) =>
                                  `${(forecasts.length - 1 - i) * stepX},${getY(
                                    f.lower_bound
                                  )}`
                              )
                              .join(" ");

                            // 2. Selected Gamma Scenario Slice (if selected)
                            let gammaPolygon: React.ReactNode = null;
                            if (selectedGammaNum !== null) {
                              const sliceBounds = forecasts.map((f) => {
                                const match =
                                  f.scenario_bounds.find(
                                    (b) => Math.abs(b.gamma - selectedGammaNum) < 0.05
                                  ) || f.scenario_bounds[0];
                                return {
                                  lower: match ? match.lower : f.lower_bound,
                                  upper: match ? match.upper : f.upper_bound,
                                };
                              });
                              const uSlice = sliceBounds
                                .map((b, i) => `${i * stepX},${getY(b.upper)}`)
                                .join(" ");
                              const lSlice = [...sliceBounds]
                                .reverse()
                                .map(
                                  (b, i) =>
                                    `${(sliceBounds.length - 1 - i) * stepX},${getY(
                                      b.lower
                                    )}`
                                )
                                .join(" ");
                              gammaPolygon = (
                                <polygon
                                  points={`${uSlice} ${lSlice}`}
                                  fill="rgba(251, 191, 36, 0.22)"
                                  stroke="rgba(251, 191, 36, 0.75)"
                                  strokeWidth="1.2"
                                />
                              );
                            }

                            // 3. Timestamp-aligned observed segments (break line on missing hours)
                            const obsSegments: string[] = [];
                            const gapMarkers: { x: number }[] = [];
                            let currentSeg: string[] = [];
                            forecasts.forEach((f, i) => {
                              const val = observedByHour.get(
                                toHourKey(f.target_time)
                              );
                              if (val !== undefined) {
                                currentSeg.push(`${i * stepX},${getY(val)}`);
                              } else {
                                if (currentSeg.length > 0) {
                                  obsSegments.push(currentSeg.join(" "));
                                  currentSeg = [];
                                }
                                gapMarkers.push({ x: i * stepX });
                              }
                            });
                            if (currentSeg.length > 0) {
                              obsSegments.push(currentSeg.join(" "));
                            }

                            // 4. SAGE Point Forecast
                            const pointPts = forecasts
                              .map((f, i) => `${i * stepX},${getY(f.point_forecast)}`)
                              .join(" ");

                            return (
                              <>
                                <polygon
                                  points={`${upperEnv} ${lowerEnv}`}
                                  fill="rgba(99, 102, 241, 0.16)"
                                  stroke="rgba(99, 102, 241, 0.4)"
                                  strokeWidth="1"
                                />
                                {gammaPolygon}
                                {obsSegments.map((seg, idx) => (
                                  <polyline
                                    key={`obs-${idx}`}
                                    points={seg}
                                    fill="none"
                                    stroke="#38bdf8"
                                    strokeWidth="2"
                                  />
                                ))}
                                {gapMarkers.map((g, idx) => (
                                  <line
                                    key={`gap-${idx}`}
                                    x1={g.x}
                                    y1="40"
                                    x2={g.x}
                                    y2="220"
                                    stroke="#f43f5e"
                                    strokeOpacity="0.35"
                                    strokeDasharray="2 4"
                                  />
                                ))}
                                <polyline
                                  points={pointPts}
                                  fill="none"
                                  stroke="#a5b4fc"
                                  strokeWidth="1.5"
                                  strokeDasharray="4 4"
                                />
                              </>
                            );
                          })()}

                          {forecasts.map((f, i) => {
                            const stepX = 800 / Math.max(1, forecasts.length - 1);
                            const cx = i * stepX;
                            const color =
                              f.status === "publish"
                                ? "#10b981"
                                : f.status === "caution"
                                ? "#f59e0b"
                                : "#f43f5e";
                            return <circle key={i} cx={cx} cy="232" r="3.5" fill={color} />;
                          })}
                        </svg>
                      )}
                    </div>

                    {/* Table of Forecast Decisions */}
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-xs border-collapse">
                        <thead>
                          <tr className="border-b border-slate-800 text-slate-400 font-mono">
                            <th className="py-2 px-3">Target UTC</th>
                            <th className="py-2 px-3">Measured</th>
                            <th className="py-2 px-3">Point Est.</th>
                            <th className="py-2 px-3">90% Envelope</th>
                            <th className="py-2 px-3">D_sens</th>
                            <th className="py-2 px-3">Policy</th>
                            <th className="py-2 px-3">Reason</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800/60 font-mono text-[11px]">
                          {forecasts.slice(-6).map((f, idx) => {
                            const measured = observedByHour.get(
                              toHourKey(f.target_time)
                            );
                            const disag = computeScenarioDisagreement(f);
                            return (
                              <tr key={idx} className="hover:bg-slate-800/30">
                                <td className="py-2 px-3 text-slate-300">
                                  {new Date(f.target_time).toISOString().slice(11, 16)}Z
                                </td>
                                <td className="py-2 px-3 text-sky-300">
                                  {measured !== undefined
                                    ? `${measured.toFixed(1)} µg/m³`
                                    : "MISSING"}
                                </td>
                                <td className="py-2 px-3 text-white font-semibold">
                                  {f.point_forecast.toFixed(1)} µg/m³
                                </td>
                                <td className="py-2 px-3 text-indigo-300">
                                  [{f.lower_bound.toFixed(1)}, {f.upper_bound.toFixed(1)}]
                                </td>
                                <td className="py-2 px-3 text-amber-300">
                                  {disag.toFixed(2)}
                                </td>
                                <td className="py-2 px-3">
                                  <span
                                    className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase border ${
                                      f.status === "publish"
                                        ? "bg-emerald-500/10 border-emerald-500/40 text-emerald-400"
                                        : f.status === "caution"
                                        ? "bg-amber-500/10 border-amber-500/40 text-amber-400"
                                        : "bg-rose-500/10 border-rose-500/40 text-rose-400"
                                    }`}
                                  >
                                    {f.status}
                                  </span>
                                </td>
                                <td className="py-2 px-3 text-slate-400 font-sans text-xs">
                                  {f.abstain_reason}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Simulator Research Lab & Ablations Tab */}
            {activeTab === "benchmarks" && (
              <div className="space-y-6">
                {/* Scenario Selector Banner */}
                <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 space-y-4">
                  <div className="flex flex-wrap items-center justify-between gap-4">
                    <div>
                      <div className="flex items-center gap-2">
                        <h2 className="text-base font-bold text-white">
                          Reliability Simulator Lab & Benchmark Audits
                        </h2>
                        <span
                          className={`text-[10px] font-mono px-2 py-0.5 rounded border ${
                            SCENARIO_LABELS[selectedScenario].isSimulated
                              ? "bg-amber-500/15 border-amber-500/40 text-amber-300"
                              : "bg-sky-500/15 border-sky-500/40 text-sky-300"
                          }`}
                        >
                          {SCENARIO_LABELS[selectedScenario].badge}
                        </span>
                      </div>
                      <p className="text-xs text-slate-400 mt-1">
                        {SCENARIO_LABELS[selectedScenario].description}
                      </p>
                    </div>

                    <div className="flex flex-wrap gap-1.5">
                      {(Object.keys(SCENARIO_LABELS) as ScenarioKey[]).map(
                        (sKey) => (
                          <button
                            key={sKey}
                            type="button"
                            onClick={() => setSelectedScenario(sKey)}
                            className={`px-3 py-1.5 rounded-lg text-xs font-mono border transition ${
                              selectedScenario === sKey
                                ? "bg-indigo-600 text-white border-indigo-400"
                                : "bg-slate-950 text-slate-300 border-slate-800 hover:bg-slate-800"
                            }`}
                          >
                            {sKey}
                          </button>
                        )
                      )}
                    </div>
                  </div>

                  {/* Experiment Manifest Badges */}
                  {experiments.length > 0 && (
                    <div className="flex flex-wrap gap-2 text-[11px] font-mono text-slate-300">
                      {experiments.map((exp) => (
                        <div
                          key={exp.experiment_id}
                          className="bg-slate-950 border border-slate-800 px-2.5 py-1 rounded flex items-center gap-2"
                        >
                          <span className="text-indigo-400 font-semibold">
                            {exp.experiment_id}
                          </span>
                          <span className="text-slate-500">|</span>
                          <span>hash: {exp.dataset_hash}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Baseline Table */}
                  <div className="overflow-x-auto pt-2">
                    <table className="w-full text-left text-xs border-collapse">
                      <thead>
                        <tr className="border-b border-slate-800 text-slate-400 font-mono">
                          <th className="py-2.5 px-3">Method</th>
                          <th className="py-2.5 px-3">Calibrator / Policy</th>
                          <th className="py-2.5 px-3">Overall PICP</th>
                          <th className="py-2.5 px-3">Published PICP</th>
                          <th className="py-2.5 px-3">MPIW (Width)</th>
                          <th className="py-2.5 px-3">Winkler</th>
                          <th className="py-2.5 px-3">Abstention</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800/60 font-mono text-[11px]">
                        {liveScenarioBenchRows ? (
                          liveScenarioBenchRows.map((row) => (
                            <tr
                              key={row.key}
                              className={`hover:bg-slate-800/30 ${
                                row.key.startsWith("sage_full")
                                  ? "bg-indigo-950/30 border-t border-indigo-500/40"
                                  : row.key === "lightgbm_quantile"
                                  ? "bg-emerald-950/15"
                                  : ""
                              }`}
                            >
                              <td className="py-2.5 px-3 font-semibold text-white">
                                {row.name}
                              </td>
                              <td className="py-2.5 px-3 text-slate-300 font-sans text-xs">
                                {row.calibrator}
                              </td>
                              <td className="py-2.5 px-3 text-emerald-400 font-bold">
                                {row.picp !== null
                                  ? `${(row.picp * 100).toFixed(1)}%`
                                  : "N/A"}
                              </td>
                              <td className="py-2.5 px-3 text-indigo-300">
                                {row.published_picp !== null
                                  ? `${(row.published_picp * 100).toFixed(2)}%`
                                  : "N/A"}
                              </td>
                              <td className="py-2.5 px-3 text-slate-200">
                                {row.mpiw !== null
                                  ? `${row.mpiw.toFixed(2)} µg/m³`
                                  : "N/A"}
                              </td>
                              <td className="py-2.5 px-3 text-slate-300">
                                {row.winkler !== null
                                  ? row.winkler.toFixed(2)
                                  : "N/A"}
                              </td>
                              <td className="py-2.5 px-3 text-amber-300">
                                {(row.abstention * 100).toFixed(2)}%
                              </td>
                            </tr>
                          ))
                        ) : isDemoMode ? (
                          BASELINE_BENCHMARKS.map((b) => (
                            <tr key={b.key} className="hover:bg-slate-800/30">
                              <td className="py-2.5 px-3 font-semibold text-white">
                                {b.name}
                              </td>
                              <td className="py-2.5 px-3 text-slate-300 font-sans text-xs">
                                {b.calibrator}
                              </td>
                              <td className="py-2.5 px-3 text-emerald-400 font-bold">
                                {b.coverage}
                              </td>
                              <td className="py-2.5 px-3 text-indigo-300">
                                {b.published_coverage}
                              </td>
                              <td className="py-2.5 px-3 text-slate-200">
                                {b.width}
                              </td>
                              <td className="py-2.5 px-3 text-slate-300">
                                {b.winkler}
                              </td>
                              <td className="py-2.5 px-3 text-amber-300">
                                {b.abstention}
                              </td>
                            </tr>
                          ))
                        ) : (
                          <tr>
                            <td
                              colSpan={7}
                              className="py-6 px-3 text-center text-slate-400 font-sans"
                            >
                              Live experiment detail unavailable from backend API (no static fixture substitution in live mode).
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* Live SAGE Ablation Table */}
                {liveAblationRows && (
                  <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 space-y-3">
                    <h3 className="text-sm font-bold text-white flex items-center gap-2">
                      <Layers className="w-4 h-4 text-indigo-400" />
                      SAGE Architectural Ablations ({selectedScenario}, 3-Seed Mean)
                    </h3>
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-xs border-collapse">
                        <thead>
                          <tr className="border-b border-slate-800 text-slate-400 font-mono">
                            <th className="py-2 px-3">Ablation Condition</th>
                            <th className="py-2 px-3">Overall PICP</th>
                            <th className="py-2 px-3">Published PICP</th>
                            <th className="py-2 px-3">Mean Width</th>
                            <th className="py-2 px-3">Mean D_sens</th>
                            <th className="py-2 px-3">Abstention</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800/60 font-mono text-[11px]">
                          {liveAblationRows.map((a) => (
                            <tr key={a.condition} className="hover:bg-slate-800/30">
                              <td className="py-2 px-3 font-semibold text-white">
                                {a.condition}
                              </td>
                              <td className="py-2 px-3 text-emerald-400">
                                {(a.picp_mean * 100).toFixed(2)}%
                              </td>
                              <td className="py-2 px-3 text-indigo-300">
                                {(a.published_picp_mean * 100).toFixed(2)}%
                              </td>
                              <td className="py-2 px-3 text-slate-200">
                                {a.mpiw_mean.toFixed(2)} µg/m³
                              </td>
                              <td className="py-2 px-3 text-amber-300">
                                {a.mean_disagreement_mean.toFixed(2)} µg/m³
                              </td>
                              <td className="py-2 px-3 text-slate-300">
                                {(a.abstention_rate_mean * 100).toFixed(2)}%
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Scientific Caveats Notice */}
                <div className="bg-slate-900/50 border border-slate-800 rounded-xl p-4 text-xs text-slate-300 flex items-start gap-2.5">
                  <ShieldAlert className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                  <div className="space-y-1">
                    <div className="font-semibold text-slate-200">
                      Honest Scientific Interpretation & Calibration Limits
                    </div>
                    <p className="text-slate-400">
                      Under natural Q1 2024 conditions, Persistence (2.00 µg/m³) and LightGBM + CQR (11.35 µg/m³) produce sharper intervals and better Winkler scores than SAGE (17.01 µg/m³ overall). No formal MNAR coverage guarantee is claimed; stale-history intervals (age &gt; 12h) are explicitly withheld by the safety guard rather than delivered as published forecasts.
                    </p>
                  </div>
                </div>
              </div>
            )}

            {/* Methodology Tab */}
            {activeTab === "methodology" && (
              <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-6 space-y-4 max-w-4xl text-xs leading-relaxed text-slate-300">
                <h2 className="text-base font-bold text-white">
                  SAGE Scientific Methodology
                </h2>
                <p>
                  Sensitivity-Aware Guarded Estimation (SAGE) bounds empirical uncertainty for environmental sensor networks when missingness may depend on unobserved PM2.5 concentrations (MNAR). No unconditional or distribution-free coverage guarantee is claimed under unrestricted MNAR; bounds are heuristic sensitivity envelopes parameterized by an analyst-imposed logit sensitivity coefficient γ in{" "}
                  <code className="text-indigo-300 font-mono">
                    logit P(R=1 | X,Y) = g(X) + γ(Y - μ)/σ
                  </code>{" "}
                  (distinct from Rosenbaum&apos;s odds-ratio Γ).
                </p>
                <div className="space-y-2">
                  <h3 className="font-semibold text-white text-sm">
                    Core Mathematical Formulations:
                  </h3>
                  <ul className="list-disc pl-5 space-y-1.5">
                    <li>
                      <strong>Observed Propensity Model:</strong> Estimates reporting probability{" "}
                      <code className="text-indigo-300 font-mono">
                        P(R = 1 | X_≤t)
                      </code>{" "}
                      from issue-time covariates without future lookahead.
                    </li>
                    <li>
                      <strong>Sensitivity Disagreement (D_sensitivity):</strong>{" "}
                      Spread across upper bounds under hypothesized logit sensitivity shifts:{" "}
                      <code className="text-indigo-300 font-mono">
                        max_γ U_γ − min_γ U_γ
                      </code>{" "}
                      (harmonized abstention threshold τ_disagree = 8.0 µg/m³).
                    </li>
                    <li>
                      <strong>Conservative Envelope Width (W_envelope):</strong>{" "}
                      Total interval width across the scenario union:{" "}
                      <code className="text-indigo-300 font-mono">
                        U_max − L_min
                      </code>{" "}
                      (ceiling τ_envelope = 50.0 µg/m³).
                    </li>
                    <li>
                      <strong>Guarded Abstention:</strong> Explicitly withholds forecast publication if scenario disagreement exceeds 8.0 µg/m³, envelope width exceeds 50.0 µg/m³, Kish ESS drops below 15, sensor observation age exceeds 12 hours, or cold-start history is absent. Note: in archived Q1 2024 UK AURN tests with D_sensitivity ≈ 3.32 µg/m³, active abstentions were triggered by stale sensor age (&gt;12h) rather than disagreement.
                    </li>
                  </ul>
                </div>
              </div>
            )}
          </>
        )}

        {/* Phase 8 Governance, Serving Versioning & Live Pilot Readiness Panel */}
        <section
          aria-label="Milestone 8 Governance and Pilot Readiness"
          className="mt-6 rounded-xl border border-slate-800 bg-slate-900/70 p-4 text-xs text-slate-300 space-y-3"
        >
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-2.5">
            <div className="flex items-center gap-2 font-semibold text-slate-100">
              <ShieldCheck className="w-4 h-4 text-emerald-400 shrink-0" />
              <span>Model Serving Governance, Data Operating Modes &amp; Live Pilot Readiness</span>
            </div>
            <span className="font-mono text-[11px] px-2 py-0.5 rounded border border-rose-500/40 bg-rose-950/50 text-rose-200">
              Live Polling: {pilotReadiness?.kill_switch_active !== false ? "LOCKED (Fail-Closed Kill Switch)" : "Shadow Opt-In Active"}
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 space-y-1.5">
              <div className="font-semibold text-slate-100">Active Serving vs. Shadow Research</div>
              <div className="text-slate-300">
                <span className="text-emerald-300 font-medium">Primary API Model:</span>{" "}
                <span className="font-mono">{modelStatus?.model_class ?? "SAGEForecaster"}</span>{" "}
                ({modelStatus?.model_id ?? "sage_pilot_q1_2024"} v{modelStatus?.version ?? "1.0.0"}, Static SAGE).
                Sensitivity offset is constant across stations/times; intervals expand cautiously up to 12h staleness before abstaining.
              </div>
              <div className="text-slate-400">
                <span className="text-amber-300 font-medium">Shadow Research (Not Served):</span>{" "}
                <span className="font-mono">AdaptiveSAGEForecaster</span> remains offline/shadow only (unpromoted; M7.7 causal audit did not beat locally weighted MAR).
              </div>
            </div>

            <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 space-y-1.5">
              <div className="font-semibold text-slate-100">Four Explicit Data Modes &amp; Freshness</div>
              <div className="flex flex-wrap gap-1.5 font-mono text-[10px]">
                {(pilotReadiness?.allowed_data_modes ?? [
                  "archived_historical",
                  "simulated_outage",
                  "shadow_live",
                  "validated_live",
                ]).map((modeName) => {
                  const activeMode =
                    pilotReadiness?.operational_data_mode ??
                    modelStatus?.operational_data_mode ??
                    "archived_historical";
                  const isActive = modeName === activeMode;
                  return (
                    <span
                      key={modeName}
                      className={`px-1.5 py-0.5 rounded border ${
                        isActive
                          ? "border-sky-400/60 bg-sky-500/20 text-sky-200 font-semibold"
                          : "border-slate-700 bg-slate-900 text-slate-400"
                      }`}
                    >
                      {modeName}
                    </span>
                  );
                })}
              </div>
              <div className="text-slate-400">
                Abstention (&gt;12h stale) indicates missing upstream telemetry—not confirmed hardware failure. Prediction intervals require MAR assumptions and are uncalibrated under unrestricted MNAR missingness.
              </div>
            </div>

            <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 space-y-1.5">
              <div className="font-semibold text-slate-100">Offline Pilot Quota &amp; Feed Snapshot</div>
              <div className="text-slate-300">
                Daily OpenAQ Cap:{" "}
                <span className="font-mono">
                  {pilotReadiness ? `${pilotReadiness.requests_used_today} / ${pilotReadiness.max_requests_per_day}` : "0 / 48"}
                </span>{" "}
                requests used • Opt-In:{" "}
                <span className="font-mono">{pilotReadiness?.opt_in_enabled ? "enabled" : "disabled"}</span>
              </div>
              <div className="text-slate-400">
                Stored 2026-11-22 snapshot: {pilotReadiness?.station_metadata_audit?.recently_active_at_snapshot ?? 2}/
                {pilotReadiness?.station_metadata_audit?.total_stations ?? 8} UK stations (LAQN OGL) recent; 6/8 EEA (ODC-BY) feeds aged May 2026. Live state unverified in offline mode.
              </div>
              <div className="text-slate-400">
                Shadow calibration gate:{" "}
                <span className="font-mono">
                  {pilotReadiness?.shadow_evaluation_summary?.status ?? "INSUFFICIENT_OBSERVED_OUTCOMES"}
                </span>{" "}
                (requires &ge;{pilotReadiness?.shadow_evaluation_summary?.min_matched_outcomes_required ?? 100} matched targets, &ge;{pilotReadiness?.shadow_evaluation_summary?.min_unique_hours_required ?? 24} independent hours, &ge;{Math.round((pilotReadiness?.shadow_evaluation_summary?.min_lead_seconds_required ?? 300) / 60)}m lead, verified JSON SHA-256 artifacts).
              </div>
            </div>
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-800 bg-slate-950 px-6 py-3 text-xs text-slate-400 flex flex-wrap items-center justify-between gap-2">
        <div>
          Data Source: UK Department for Environment, Food & Rural Affairs (DEFRA) AURN Q1 2024 (OGL v3).
        </div>
        <div className="font-mono text-[11px] text-slate-400">
          Model:{" "}
          {modelStatus
            ? `${modelStatus.model_class ?? "SAGEForecaster"} (${modelStatus.algorithm} v${modelStatus.version}) • SHA-256 ${
                modelStatus.verified ? "Verified" : "UNVERIFIED"
              } (${modelStatus.sha256.slice(0, 12)}…)`
            : isDemoMode
            ? "Offline Demo Fixtures (API Unreachable)"
            : "Checking Model Governance…"}
        </div>
      </footer>
    </div>
  );
}
