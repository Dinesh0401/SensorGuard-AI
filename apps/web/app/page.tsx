"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  ChevronDown,
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
import { UkStationMap } from "../components/UkStationMap";

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
      "Evaluated only on naturally visible Q1 2024 target records; unobserved hours reflect upstream reporting gaps.",
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

function formatHistoricalStatus(status: StationSummary["status"]) {
  if (status === "HEALTHY") {
    return {
      label: "Healthy at historical evaluation time",
      pill: "bg-emerald-500/15 border-emerald-500/35 text-emerald-300",
    };
  }
  if (status === "LATE") {
    return {
      label: "Delayed at historical evaluation time",
      pill: "bg-amber-500/15 border-amber-500/35 text-amber-300",
    };
  }
  return {
    label: "Withheld (>12h historical gap)",
    pill: "bg-rose-500/15 border-rose-500/35 text-rose-300",
  };
}

function describeDecision(forecast: ForecastRecord | null, station: StationSummary | undefined) {
  const status = forecast?.status ?? (station?.status === "NO_REPORT" ? "abstain" : "publish");
  const rawReason = (forecast?.abstain_reason || "").toLowerCase();

  if (status === "abstain") {
    return {
      headline: "Abstain (Withheld)",
      sublabel: "Insufficient recent historical evidence",
      heading: "Why was this forecast withheld?",
      body: rawReason.includes("stale")
        ? "Upstream reports had been absent for over 12 hours at this Q1 2024 cutoff. SensorGuard withholds intervals during long gaps instead of showing uncertain estimates—without claiming the physical sensor failed."
        : "Sensitivity spread or effective sample size exceeded safe publication limits at this cutoff, so the model withheld the forecast.",
      cardStyle: "border-rose-500/40 bg-rose-950/25",
      badgeStyle: "bg-rose-500/20 text-rose-200 border-rose-500/40",
    };
  }

  if (status === "caution") {
    return {
      headline: "Caution",
      sublabel: "Published with widened bounds",
      heading: "Why was this forecast flagged for caution?",
      body: "Available Q1 2024 observations passed freshness thresholds, but sensitivity scenarios showed moderate uncertainty spread across missingness assumptions.",
      cardStyle: "border-amber-500/40 bg-amber-950/20",
      badgeStyle: "bg-amber-500/20 text-amber-200 border-amber-500/40",
    };
  }

  return {
    headline: "Publish",
    sublabel: "Sufficient historical evidence",
    heading: "Why was this forecast allowed?",
    body: "The available historical observations passed the model's freshness and reliability checks. This does not guarantee the forecast was accurate or that the sensor is currently reporting.",
    cardStyle: "border-emerald-500/35 bg-emerald-950/20",
    badgeStyle: "bg-emerald-500/20 text-emerald-200 border-emerald-500/40",
  };
}

function formatPlainReason(reason: string): string {
  const clean = reason.toLowerCase();
  if (clean.includes("stale")) return "Stale history (>12h gap at cutoff)";
  if (clean.includes("disagree")) return "Moderate sensitivity spread";
  if (clean.includes("sufficient") || clean.includes("publish")) {
    return "Passed freshness & spread checks";
  }
  return reason;
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
    setSelectedStationId((prev) => {
      if (prev !== null) return prev;
      if (IS_STATIC_PORTFOLIO) {
        const marylebone = mockAsSummaries.find((st) => st.provider_location_id === 154);
        if (marylebone) return marylebone.id;
      }
      return mockAsSummaries[0].id;
    });
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

  const latestForecast =
    forecasts.length > 0 ? forecasts[forecasts.length - 1] : null;
  const heroDecision = describeDecision(latestForecast, currentStation);
  const heroStationStatus = currentStation
    ? formatHistoricalStatus(currentStation.status)
    : null;

  const observedByHour = useMemo(() => {
    const map = new Map<string, number>();
    for (const obs of observations) {
      if (obs.value !== null && Number.isFinite(obs.value)) {
        map.set(toHourKey(obs.observed_at), obs.value);
      }
    }
    return map;
  }, [observations]);

  const chartMaxY = useMemo(() => {
    let maxVal = 30.0;
    for (const f of forecasts) {
      if (f.upper_bound > maxVal) maxVal = f.upper_bound;
      const obsVal = observedByHour.get(toHourKey(f.target_time));
      if (obsVal !== undefined && obsVal > maxVal) maxVal = obsVal;
    }
    return Math.ceil(maxVal / 10) * 10 + 5;
  }, [forecasts, observedByHour]);

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

  const heroMeasuredValue =
    currentStation?.latest_pm25_value !== null &&
    currentStation?.latest_pm25_value !== undefined
      ? currentStation.latest_pm25_value
      : latestForecast?.point_forecast ?? null;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      {/* Compact Top Context Notice (Single Bar) */}
      <div className="bg-slate-900/95 border-b border-slate-800 px-4 sm:px-6 py-2 text-xs sm:text-sm flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-amber-200 flex-wrap">
          <span className="inline-flex items-center gap-1.5 font-semibold bg-amber-500/15 border border-amber-500/35 text-amber-200 px-2.5 py-0.5 rounded-full text-xs">
            Historical Research Demo — Not Live
          </span>
          <span className="text-slate-300 text-xs sm:text-sm">
            STRICT PILOT MODE: ARCHIVED Q1 2024 UK AURN NETWORK • Zero live telemetry or health advisories
          </span>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <span
            className={`px-2.5 py-0.5 rounded-full border text-xs font-medium flex items-center gap-1.5 ${
              isDemoMode
                ? "bg-indigo-500/15 text-indigo-200 border-indigo-500/40"
                : "bg-emerald-500/15 text-emerald-300 border-emerald-500/40"
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                isDemoMode ? "bg-indigo-400" : "bg-emerald-400 animate-pulse"
              }`}
            ></span>
            {IS_STATIC_PORTFOLIO && !forceDemoMode
              ? "Portfolio Demo (Q1 2024 Fixtures)"
              : isDemoMode
              ? "Demo Mode (Offline Fixtures)"
              : "FastAPI Connected"}
          </span>

          {isDemoMode && !IS_STATIC_PORTFOLIO && (
            <button
              type="button"
              onClick={() => {
                setForceDemoMode(false);
                loadInitialData();
              }}
              className="flex items-center gap-1 bg-indigo-600/40 hover:bg-indigo-600/60 border border-indigo-500/40 px-2.5 py-0.5 rounded-full text-xs text-indigo-100 transition"
            >
              <RefreshCw className="w-3 h-3" /> Retry Live API
            </button>
          )}

          {!IS_STATIC_PORTFOLIO && (
            <button
              type="button"
              aria-label={
                forceDemoMode
                  ? "Switch back to live API mode"
                  : "Enable demonstration fallback mode"
              }
              onClick={() => setForceDemoMode((prev) => !prev)}
              className="bg-slate-800 hover:bg-slate-700 border border-slate-700 px-2.5 py-0.5 rounded-full text-xs text-slate-200 transition"
            >
              {forceDemoMode ? "Exit Demo Mode" : "Toggle Demo Mode"}
            </button>
          )}
        </div>
      </div>

      {/* Product Header & View Switcher */}
      <header className="border-b border-slate-800/80 bg-slate-900/40 px-4 sm:px-6 py-5">
        <div className="max-w-7xl mx-auto flex flex-col lg:flex-row lg:items-center lg:justify-between gap-5">
          <div className="space-y-1.5 max-w-2xl">
            <div className="flex items-center gap-2.5">
              <div className="p-2 rounded-xl bg-indigo-600/20 border border-indigo-500/40 text-indigo-400">
                <ShieldCheck className="w-6 h-6" />
              </div>
              <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">
                SensorGuard AI
              </h1>
              <span className="text-xs font-medium px-2.5 py-0.5 bg-slate-800 text-slate-300 rounded-full border border-slate-700">
                UK AURN Replay · Q1 2024
              </span>
            </div>
            <p className="text-sm sm:text-base text-slate-300 leading-relaxed">
              SensorGuard shows when environmental sensor forecasts have enough evidence to be displayed—and when missing data makes them unreliable.
            </p>
          </div>

          {/* Primary Navigation (User View vs Research Views) */}
          <nav
            aria-label="Primary sections"
            className="flex flex-wrap items-center gap-1.5 bg-slate-900 border border-slate-800 p-1.5 rounded-xl self-start lg:self-center"
          >
            <button
              type="button"
              onClick={() => setActiveTab("forecast")}
              className={`px-3.5 py-2 rounded-lg transition font-medium text-sm flex items-center gap-2 ${
                activeTab === "forecast"
                  ? "bg-indigo-600 text-white shadow"
                  : "text-slate-300 hover:text-white hover:bg-slate-800/60"
              }`}
            >
              <Activity className="w-4 h-4" /> Station Forecasts &amp; Map
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("benchmarks")}
              className={`px-3.5 py-2 rounded-lg transition font-medium text-sm flex items-center gap-2 ${
                activeTab === "benchmarks"
                  ? "bg-indigo-600 text-white shadow"
                  : "text-slate-300 hover:text-white hover:bg-slate-800/60"
              }`}
            >
              <FlaskConical className="w-4 h-4" /> Simulator Lab &amp; Ablations
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("methodology")}
              className={`px-3.5 py-2 rounded-lg transition font-medium text-sm flex items-center gap-2 ${
                activeTab === "methodology"
                  ? "bg-indigo-600 text-white shadow"
                  : "text-slate-300 hover:text-white hover:bg-slate-800/60"
              }`}
            >
              <Info className="w-4 h-4" /> SAGE Methodology
            </button>
          </nav>
        </div>

        {isDemoMode && apiError && (
          <div className="max-w-7xl mx-auto mt-3 text-xs sm:text-sm text-indigo-200 bg-indigo-950/60 border border-indigo-500/30 rounded-lg px-3.5 py-2 flex items-center gap-2">
            <Info className="w-4 h-4 text-indigo-400 shrink-0" />
            <span>
              <strong>DEMO</strong> fallback active — showing audited Q1 2024 fixtures ({apiError})
            </span>
          </div>
        )}
      </header>

      {/* Main Content */}
      <main className="flex-1 px-4 sm:px-6 py-6 max-w-7xl w-full mx-auto space-y-6">
        {isLoading ? (
          <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-12 text-center text-base text-slate-300">
            Loading archived Q1 2024 UK AURN station catalog and verified model manifest...
          </div>
        ) : (
          <>
            {activeTab === "forecast" && (
              <div className="space-y-6">
                {/* Product-First Station Decision Hero Card */}
                {currentStation && (
                  <section
                    aria-label="Selected station forecast decision summary"
                    className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5 sm:p-6 space-y-5"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-800 pb-4">
                      <div className="space-y-1">
                        <div className="flex items-center gap-2.5 flex-wrap">
                          <h2 className="text-xl sm:text-2xl font-bold text-white">
                            {currentStation.name}
                          </h2>
                          <span className="px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-800 border border-slate-700 text-slate-200">
                            Archived
                          </span>
                          {heroStationStatus && (
                            <span
                              className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${heroStationStatus.pill}`}
                            >
                              {heroStationStatus.label}
                            </span>
                          )}
                        </div>
                        <p className="text-sm text-slate-400">
                          Historical research replay · Q1 2024 •{" "}
                          <span className="font-mono text-slate-300">
                            Lat: {currentStation.latitude.toFixed(4)}, Lon:{" "}
                            {currentStation.longitude.toFixed(4)}
                          </span>
                        </p>
                      </div>

                      <span className="text-xs font-mono px-2.5 py-1 rounded-lg bg-slate-950 border border-slate-800 text-slate-300">
                        {isDemoMode
                          ? "Historical demo fixture"
                          : `Eval cutoff: ${
                              currentStation.latest_observation_time
                                ? new Date(currentStation.latest_observation_time)
                                    .toISOString()
                                    .slice(0, 16)
                                    .replace("T", " ") + " UTC"
                                : "Q1 2024"
                            }`}
                      </span>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-12 gap-4 items-stretch">
                      {/* Measured PM2.5 KPI */}
                      <div className="md:col-span-3 rounded-xl border border-slate-800 bg-slate-950/70 p-4 flex flex-col justify-between">
                        <div className="text-sm font-medium text-slate-400">
                          Measured PM2.5
                        </div>
                        <div className="mt-2">
                          <div className="text-3xl sm:text-4xl font-bold tracking-tight text-white tabular-nums">
                            {heroMeasuredValue !== null
                              ? heroMeasuredValue.toFixed(1)
                              : "N/A"}
                          </div>
                          <div className="text-sm text-slate-400 mt-1">
                            µg/m³ · {isDemoMode ? "historical fixture" : "Q1 2024 archive"}
                          </div>
                        </div>
                      </div>

                      {/* Forecast Decision KPI */}
                      <div
                        className={`md:col-span-4 rounded-xl border p-4 flex flex-col justify-between ${heroDecision.cardStyle}`}
                      >
                        <div className="text-sm font-medium text-slate-300">
                          Forecast decision
                        </div>
                        <div className="mt-2 space-y-1.5">
                          <div className="flex items-center gap-2.5 flex-wrap">
                            <span className="text-2xl sm:text-3xl font-bold text-white">
                              {heroDecision.headline}
                            </span>
                            {latestForecast && (
                              <span
                                className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${heroDecision.badgeStyle}`}
                              >
                                90% band: [{latestForecast.lower_bound.toFixed(1)},{" "}
                                {latestForecast.upper_bound.toFixed(1)}] µg/m³
                              </span>
                            )}
                          </div>
                          <div className="text-sm text-slate-200">
                            {heroDecision.sublabel}
                          </div>
                        </div>
                      </div>

                      {/* Plain-English Decision Rationale */}
                      <div className="md:col-span-5 rounded-xl border border-slate-800 bg-slate-950/70 p-4 space-y-1.5">
                        <div className="text-sm font-semibold text-white">
                          {heroDecision.heading}
                        </div>
                        <p className="text-sm text-slate-300 leading-relaxed">
                          {heroDecision.body}
                        </p>
                      </div>
                    </div>

                    <p className="text-xs text-slate-400">
                      {isDemoMode
                        ? "Illustrative UI layout using archived Q1 2024 demonstration fixtures—not a live forecast or verified current reading for this station."
                        : "Point-in-time historical replay from archived Q1 2024 UK AURN data—not a live forecast or current sensor status."}
                    </p>
                  </section>
                )}

                {/* Map + Station Selection & Forecast Chart Grid */}
                <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
                  {/* Left Rail: Geographic UK Map, Station Picker & Historical Reliability */}
                  <div className="lg:col-span-5 space-y-5">
                    <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-4 sm:p-5 space-y-3">
                      <div className="flex items-center justify-between gap-2">
                        <h3 className="text-base font-semibold text-white flex items-center gap-2">
                          <MapPin className="w-4 h-4 text-indigo-400 shrink-0" />
                          UK AURN 8-Station Network (Q1 2024)
                        </h3>
                        <span className="text-sm text-slate-300 font-medium">
                          {stations.length} Stations
                        </span>
                      </div>

                      <UkStationMap
                        stations={stations}
                        selectedStationId={selectedStationId}
                        onSelectStation={setSelectedStationId}
                      />

                      <div className="pt-1 space-y-2 max-h-72 overflow-y-auto pr-1">
                        {stations.map((st) => {
                          const isSelected = st.id === selectedStationId;
                          const hStat = formatHistoricalStatus(st.status);
                          return (
                            <button
                              key={st.id}
                              type="button"
                              onClick={() => setSelectedStationId(st.id)}
                              className={`w-full text-left px-3.5 py-3 rounded-xl border transition flex items-center justify-between gap-3 ${
                                isSelected
                                  ? "bg-indigo-950/60 border-indigo-500/60 text-white"
                                  : "bg-slate-950/50 border-slate-800 text-slate-200 hover:bg-slate-800/50"
                              }`}
                            >
                              <div className="min-w-0">
                                <div className="text-sm font-semibold truncate">
                                  {st.name}{" "}
                                  <span className="text-xs font-mono text-slate-400">
                                    (#{st.provider_location_id})
                                  </span>
                                </div>
                                <div className="text-xs text-slate-400 mt-0.5">
                                  {st.latest_pm25_value !== null
                                    ? `${st.latest_pm25_value.toFixed(1)} µg/m³`
                                    : "No recent value"}{" "}
                                  •{" "}
                                  {st.age_hours !== null
                                    ? `${st.age_hours.toFixed(1)}h lag at Q1 2024 cutoff`
                                    : "Lag N/A"}
                                </div>
                              </div>
                              <span
                                className={`shrink-0 text-xs font-medium px-2.5 py-0.5 rounded-full border ${hStat.pill}`}
                              >
                                {st.status === "HEALTHY"
                                  ? "Healthy"
                                  : st.status === "LATE"
                                  ? "Delayed"
                                  : "Gap"}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    {/* Station Reliability Diagnostics (Live / Migrated DB) */}
                    {reliability && (
                      <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-5 space-y-3">
                        <h4 className="text-base font-semibold text-white">
                          Historical Reliability (Q1 2024)
                        </h4>
                        <div className="grid grid-cols-2 gap-3 text-slate-200">
                          <div className="bg-slate-950/80 p-3 rounded-xl border border-slate-800">
                            <div className="text-xs text-slate-400">
                              Total Observations
                            </div>
                            <div className="text-lg font-bold mt-0.5 tabular-nums">
                              {reliability.total_observations.toLocaleString()}
                            </div>
                          </div>
                          <div className="bg-slate-950/80 p-3 rounded-xl border border-slate-800">
                            <div className="text-xs text-slate-400">
                              Missingness / Gap Rate
                            </div>
                            <div className="text-lg font-bold mt-0.5 tabular-nums">
                              {reliability.gap_rate_pct.toFixed(1)}%
                            </div>
                          </div>
                          <div className="bg-slate-950/80 p-3 rounded-xl border border-slate-800 col-span-2 flex items-center justify-between">
                            <span className="text-sm text-slate-400">
                              Max Consecutive Outage
                            </span>
                            <span className="text-base font-mono font-semibold text-white">
                              {reliability.longest_consecutive_outage_hours.toFixed(1)}h
                            </span>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Right Column: Trajectory Chart & Recent Forecast Checks */}
                  <div className="lg:col-span-7 space-y-5">
                    <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-5 space-y-4">
                      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3">
                        <div>
                          <h3 className="text-base font-semibold text-white">
                            Hourly Measured vs. Estimated PM2.5 Range
                          </h3>
                          <p className="text-sm text-slate-400">
                            Timestamp-aligned Q1 2024 actuals and 90% sensitivity interval (missing hours stay uninterpolated)
                          </p>
                        </div>

                        <div className="flex items-center gap-2">
                          <label
                            htmlFor="gamma-selector"
                            className="text-sm text-slate-300"
                          >
                            Sensitivity Slice:
                          </label>
                          <select
                            id="gamma-selector"
                            value={gammaFilter}
                            onChange={(e) => setGammaFilter(e.target.value)}
                            className="bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1.5 text-sm text-slate-100 font-mono"
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

                      <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-slate-300">
                        <div className="flex flex-wrap items-center gap-4">
                          <span className="flex items-center gap-1.5">
                            <span className="w-3.5 h-0.5 bg-sky-400 inline-block"></span>
                            Measured PM2.5
                          </span>
                          <span className="flex items-center gap-1.5">
                            <span className="w-3.5 h-0.5 bg-indigo-300 inline-block border-dashed"></span>
                            SAGE +1h Estimate
                          </span>
                          <span className="flex items-center gap-1.5">
                            <span className="w-3.5 h-2.5 bg-indigo-500/25 border border-indigo-500/45 inline-block rounded-sm"></span>
                            90% Envelope
                          </span>
                          {selectedGammaNum !== null && (
                            <span className="flex items-center gap-1.5 text-amber-300">
                              <span className="w-3.5 h-2.5 bg-amber-400/25 border border-amber-400 inline-block rounded-sm"></span>
                              γ={selectedGammaNum.toFixed(1)} Interval
                            </span>
                          )}
                        </div>
                        <span className="font-mono text-xs sm:text-sm text-slate-400">
                          {isStationLoading
                            ? "Refreshing station..."
                            : `${forecasts.length} timesteps (gaps not filled)`}
                        </span>
                      </div>

                      <div className="relative w-full h-72 bg-slate-950/80 border border-slate-800 rounded-xl p-2">
                        {forecasts.length === 0 ? (
                          <div className="w-full h-full flex items-center justify-center text-sm text-slate-400">
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

                            <text x="6" y="46" fill="#94a3b8" fontSize="11">
                              {chartMaxY} µg/m³
                            </text>
                            <text x="6" y="106" fill="#94a3b8" fontSize="11">
                              {Math.round(chartMaxY * 0.66)} µg/m³
                            </text>
                            <text x="6" y="166" fill="#94a3b8" fontSize="11">
                              {Math.round(chartMaxY * 0.33)} µg/m³
                            </text>
                            <text x="6" y="216" fill="#94a3b8" fontSize="11">
                              0 µg/m³
                            </text>

                            {(() => {
                              const getY = (v: number) =>
                                220 -
                                (Math.min(chartMaxY, Math.max(0, v)) / chartMaxY) *
                                  180;
                              const stepX = 800 / Math.max(1, forecasts.length - 1);

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
                                      strokeWidth="2.2"
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
                                    strokeWidth="1.6"
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

                      {/* Readable Hourly Forecast Decisions Table */}
                      <div className="space-y-2 pt-2">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <h4 className="text-sm font-semibold text-white">
                            Recent Forecast Policy Decisions (Historical Replay)
                          </h4>
                          <span className="text-xs text-slate-400">
                            Showing latest 6 hourly steps
                          </span>
                        </div>
                        <div className="overflow-x-auto rounded-xl border border-slate-800/80">
                          <table className="w-full text-left text-sm border-collapse">
                            <thead>
                              <tr className="border-b border-slate-800 bg-slate-950/80 text-slate-300 font-medium text-xs sm:text-sm">
                                <th className="py-2.5 px-3">Target UTC</th>
                                <th className="py-2.5 px-3">Measured</th>
                                <th className="py-2.5 px-3">Point Est.</th>
                                <th className="py-2.5 px-3">90% Envelope</th>
                                <th className="py-2.5 px-3">D_sens</th>
                                <th className="py-2.5 px-3">Policy</th>
                                <th className="py-2.5 px-3">Explanation</th>
                              </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-800/60 text-xs sm:text-sm">
                              {forecasts.slice(-6).map((f, idx) => {
                                const measured = observedByHour.get(
                                  toHourKey(f.target_time)
                                );
                                const disag = computeScenarioDisagreement(f);
                                return (
                                  <tr key={idx} className="hover:bg-slate-800/30">
                                    <td className="py-2.5 px-3 font-mono text-slate-200 whitespace-nowrap">
                                      {new Date(f.target_time).toISOString().slice(11, 16)}Z
                                    </td>
                                    <td className="py-2.5 px-3 font-mono text-sky-300 whitespace-nowrap">
                                      {measured !== undefined
                                        ? `${measured.toFixed(1)} µg/m³`
                                        : "MISSING"}
                                    </td>
                                    <td className="py-2.5 px-3 font-mono text-white font-semibold whitespace-nowrap">
                                      {f.point_forecast.toFixed(1)} µg/m³
                                    </td>
                                    <td className="py-2.5 px-3 font-mono text-indigo-300 whitespace-nowrap">
                                      [{f.lower_bound.toFixed(1)}, {f.upper_bound.toFixed(1)}]
                                    </td>
                                    <td className="py-2.5 px-3 font-mono text-amber-300">
                                      {disag.toFixed(2)}
                                    </td>
                                    <td className="py-2.5 px-3">
                                      <span
                                        className={`px-2.5 py-0.5 rounded-full text-xs font-semibold uppercase border ${
                                          f.status === "publish"
                                            ? "bg-emerald-500/10 border-emerald-500/40 text-emerald-300"
                                            : f.status === "caution"
                                            ? "bg-amber-500/10 border-amber-500/40 text-amber-300"
                                            : "bg-rose-500/10 border-rose-500/40 text-rose-300"
                                        }`}
                                      >
                                        {f.status}
                                      </span>
                                    </td>
                                    <td className="py-2.5 px-3 text-slate-300">
                                      {formatPlainReason(f.abstain_reason)}
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
                </div>
              </div>
            )}

            {/* Research View: Plain-English Takeaways + Detailed Simulator Lab & Ablations */}
            {activeTab === "benchmarks" && (
              <div className="space-y-6">
                {/* Understandable Plain-English Research Summary */}
                <section
                  aria-label="Key research findings in plain language"
                  className="grid grid-cols-1 md:grid-cols-3 gap-4"
                >
                  <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5 space-y-2">
                    <h3 className="text-base font-semibold text-white">
                      1. Calm Weather Winner: Simpler Baselines
                    </h3>
                    <p className="text-sm text-slate-300 leading-relaxed">
                      Under normal Q1 2024 conditions, Persistence (<span className="font-mono text-white">2.00 µg/m³</span> wide) and LightGBM + CQR (<span className="font-mono text-white">11.35 µg/m³</span>) give much tighter forecast intervals than SAGE (<span className="font-mono text-white">17.01 µg/m³</span>).
                    </p>
                  </div>
                  <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5 space-y-2">
                    <h3 className="text-base font-semibold text-white">
                      2. Where SAGE Protects Operators
                    </h3>
                    <p className="text-sm text-slate-300 leading-relaxed">
                      During extended sensor blackouts or target-dependent missingness, SAGE detects stale history (&gt;12h) or sensitivity spread and withholds unsafe predictions rather than guessing.
                    </p>
                  </div>
                  <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5 space-y-2">
                    <h3 className="text-base font-semibold text-white">
                      3. Reading Research Metrics Below
                    </h3>
                    <p className="text-sm text-slate-300 leading-relaxed">
                      <strong>PICP</strong> measures how often truth fell inside the band (target 90%). <strong>MPIW</strong> is average width (smaller is sharper). <strong>Winkler</strong> scores width plus miss penalties (lower is better).
                    </p>
                  </div>
                </section>

                {/* Detailed Scenario Simulator & Benchmarks */}
                <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-5 sm:p-6 space-y-4">
                  <div className="flex flex-wrap items-center justify-between gap-4">
                    <div>
                      <div className="flex items-center gap-2.5 flex-wrap">
                        <h2 className="text-lg sm:text-xl font-bold text-white">
                          Reliability Simulator Lab &amp; Benchmark Audits
                        </h2>
                        <span
                          className={`text-xs font-mono px-2.5 py-0.5 rounded-full border ${
                            SCENARIO_LABELS[selectedScenario].isSimulated
                              ? "bg-amber-500/15 border-amber-500/40 text-amber-300"
                              : "bg-sky-500/15 border-sky-500/40 text-sky-300"
                          }`}
                        >
                          {SCENARIO_LABELS[selectedScenario].badge}
                        </span>
                      </div>
                      <p className="text-sm text-slate-300 mt-1">
                        {SCENARIO_LABELS[selectedScenario].description}
                      </p>
                    </div>

                    <div className="flex flex-wrap gap-2">
                      {(Object.keys(SCENARIO_LABELS) as ScenarioKey[]).map(
                        (sKey) => (
                          <button
                            key={sKey}
                            type="button"
                            onClick={() => setSelectedScenario(sKey)}
                            className={`px-3.5 py-2 rounded-xl text-sm font-mono border transition ${
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

                  {experiments.length > 0 && (
                    <div className="flex flex-wrap gap-2 text-xs sm:text-sm font-mono text-slate-300">
                      {experiments.map((exp) => (
                        <div
                          key={exp.experiment_id}
                          className="bg-slate-950 border border-slate-800 px-3 py-1 rounded-lg flex items-center gap-2"
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

                  <div className="overflow-x-auto pt-2">
                    <table className="w-full text-left text-sm border-collapse">
                      <thead>
                        <tr className="border-b border-slate-800 text-slate-300 font-mono text-xs sm:text-sm">
                          <th className="py-3 px-3">Method</th>
                          <th className="py-3 px-3">Calibrator / Policy</th>
                          <th className="py-3 px-3">Overall PICP</th>
                          <th className="py-3 px-3">Published PICP</th>
                          <th className="py-3 px-3">MPIW (Width)</th>
                          <th className="py-3 px-3">Winkler</th>
                          <th className="py-3 px-3">Abstention</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800/60 font-mono text-xs sm:text-sm">
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
                              <td className="py-3 px-3 font-semibold text-white">
                                {row.name}
                              </td>
                              <td className="py-3 px-3 text-slate-300 font-sans text-sm">
                                {row.calibrator}
                              </td>
                              <td className="py-3 px-3 text-emerald-400 font-bold">
                                {row.picp !== null
                                  ? `${(row.picp * 100).toFixed(1)}%`
                                  : "N/A"}
                              </td>
                              <td className="py-3 px-3 text-indigo-300">
                                {row.published_picp !== null
                                  ? `${(row.published_picp * 100).toFixed(2)}%`
                                  : "N/A"}
                              </td>
                              <td className="py-3 px-3 text-slate-200">
                                {row.mpiw !== null
                                  ? `${row.mpiw.toFixed(2)} µg/m³`
                                  : "N/A"}
                              </td>
                              <td className="py-3 px-3 text-slate-300">
                                {row.winkler !== null
                                  ? row.winkler.toFixed(2)
                                  : "N/A"}
                              </td>
                              <td className="py-3 px-3 text-amber-300">
                                {(row.abstention * 100).toFixed(2)}%
                              </td>
                            </tr>
                          ))
                        ) : isDemoMode ? (
                          BASELINE_BENCHMARKS.map((b) => (
                            <tr key={b.key} className="hover:bg-slate-800/30">
                              <td className="py-3 px-3 font-semibold text-white">
                                {b.name}
                              </td>
                              <td className="py-3 px-3 text-slate-300 font-sans text-sm">
                                {b.calibrator}
                              </td>
                              <td className="py-3 px-3 text-emerald-400 font-bold">
                                {b.coverage}
                              </td>
                              <td className="py-3 px-3 text-indigo-300">
                                {b.published_coverage}
                              </td>
                              <td className="py-3 px-3 text-slate-200">
                                {b.width}
                              </td>
                              <td className="py-3 px-3 text-slate-300">
                                {b.winkler}
                              </td>
                              <td className="py-3 px-3 text-amber-300">
                                {b.abstention}
                              </td>
                            </tr>
                          ))
                        ) : (
                          <tr>
                            <td
                              colSpan={7}
                              className="py-6 px-3 text-center text-slate-400 font-sans text-sm"
                            >
                              Live experiment detail unavailable from backend API (no static fixture substitution in live mode).
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>

                {liveAblationRows && (
                  <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-5 sm:p-6 space-y-3">
                    <h3 className="text-base font-bold text-white flex items-center gap-2">
                      <Layers className="w-4 h-4 text-indigo-400" />
                      SAGE Architectural Ablations ({selectedScenario}, 3-Seed Mean)
                    </h3>
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-sm border-collapse">
                        <thead>
                          <tr className="border-b border-slate-800 text-slate-300 font-mono text-xs sm:text-sm">
                            <th className="py-2.5 px-3">Ablation Condition</th>
                            <th className="py-2.5 px-3">Overall PICP</th>
                            <th className="py-2.5 px-3">Published PICP</th>
                            <th className="py-2.5 px-3">Mean Width</th>
                            <th className="py-2.5 px-3">Mean D_sens</th>
                            <th className="py-2.5 px-3">Abstention</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800/60 font-mono text-xs sm:text-sm">
                          {liveAblationRows.map((a) => (
                            <tr key={a.condition} className="hover:bg-slate-800/30">
                              <td className="py-2.5 px-3 font-semibold text-white">
                                {a.condition}
                              </td>
                              <td className="py-2.5 px-3 text-emerald-400">
                                {(a.picp_mean * 100).toFixed(2)}%
                              </td>
                              <td className="py-2.5 px-3 text-indigo-300">
                                {(a.published_picp_mean * 100).toFixed(2)}%
                              </td>
                              <td className="py-2.5 px-3 text-slate-200">
                                {a.mpiw_mean.toFixed(2)} µg/m³
                              </td>
                              <td className="py-2.5 px-3 text-amber-300">
                                {a.mean_disagreement_mean.toFixed(2)} µg/m³
                              </td>
                              <td className="py-2.5 px-3 text-slate-300">
                                {(a.abstention_rate_mean * 100).toFixed(2)}%
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                <div className="bg-slate-900/50 border border-slate-800 rounded-2xl p-5 text-sm text-slate-300 flex items-start gap-3">
                  <ShieldAlert className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
                  <div className="space-y-1">
                    <div className="font-semibold text-white">
                      Honest Scientific Interpretation &amp; Calibration Limits
                    </div>
                    <p className="text-slate-300 leading-relaxed">
                      Under natural Q1 2024 conditions, Persistence (2.00 µg/m³) and LightGBM + CQR (11.35 µg/m³) produce sharper intervals and better Winkler scores than SAGE (17.01 µg/m³ overall). No formal MNAR coverage guarantee is claimed; stale-history intervals (age &gt; 12h) are explicitly withheld by the safety guard rather than delivered as published forecasts.
                    </p>
                  </div>
                </div>
              </div>
            )}

            {/* Methodology Tab */}
            {activeTab === "methodology" && (
              <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-6 sm:p-8 space-y-5 max-w-4xl text-sm sm:text-base leading-relaxed text-slate-300">
                <h2 className="text-xl font-bold text-white">
                  SAGE Scientific Methodology
                </h2>
                <p>
                  Sensitivity-Aware Guarded Estimation (SAGE) bounds empirical uncertainty for environmental sensor networks when missingness may depend on unobserved PM2.5 concentrations (MNAR). No unconditional or distribution-free coverage guarantee is claimed under unrestricted MNAR; bounds are heuristic sensitivity envelopes parameterized by an analyst-imposed logit sensitivity coefficient γ in{" "}
                  <code className="text-indigo-300 font-mono text-sm">
                    logit P(R=1 | X,Y) = g(X) + γ(Y - μ)/σ
                  </code>{" "}
                  (distinct from Rosenbaum&apos;s odds-ratio Γ).
                </p>
                <div className="space-y-3">
                  <h3 className="font-semibold text-white text-base sm:text-lg">
                    Core Mathematical Formulations
                  </h3>
                  <ul className="list-disc pl-5 space-y-2.5">
                    <li>
                      <strong>Observed Propensity Model:</strong> Estimates reporting probability{" "}
                      <code className="text-indigo-300 font-mono text-sm">
                        P(R = 1 | X_≤t)
                      </code>{" "}
                      from issue-time covariates without future lookahead.
                    </li>
                    <li>
                      <strong>Sensitivity Disagreement (D_sensitivity):</strong>{" "}
                      Spread across upper bounds under hypothesized logit sensitivity shifts:{" "}
                      <code className="text-indigo-300 font-mono text-sm">
                        max_γ U_γ − min_γ U_γ
                      </code>{" "}
                      (harmonized abstention threshold τ_disagree = 8.0 µg/m³).
                    </li>
                    <li>
                      <strong>Conservative Envelope Width (W_envelope):</strong>{" "}
                      Total interval width across the scenario union:{" "}
                      <code className="text-indigo-300 font-mono text-sm">
                        U_max − L_min
                      </code>{" "}
                      (ceiling τ_envelope = 50.0 µg/m³).
                    </li>
                    <li>
                      <strong>Guarded Abstention:</strong> Explicitly withholds forecast publication if scenario disagreement exceeds 8.0 µg/m³, envelope width exceeds 50.0 µg/m³, Kish ESS drops below 15, sensor observation age exceeds 12 hours, or cold-start history is absent. In archived Q1 2024 UK AURN tests with D_sensitivity ≈ 3.32 µg/m³, active abstentions were triggered by stale sensor age (&gt;12h) rather than disagreement.
                    </li>
                  </ul>
                </div>
              </div>
            )}
          </>
        )}

        {/* Expandable Technical Research & Operations Governance Section */}
        <details className="rounded-2xl border border-slate-800 bg-slate-900/50 text-sm text-slate-300 group">
          <summary className="cursor-pointer list-none px-5 py-4 flex flex-wrap items-center justify-between gap-3 hover:bg-slate-900/80 rounded-2xl transition">
            <div className="flex items-center gap-2.5 font-semibold text-white">
              <ShieldCheck className="w-4 h-4 text-emerald-400 shrink-0" />
              <span>Research &amp; Operations Governance Details (Model Integrity, Data Modes &amp; Stage 2 Lock)</span>
            </div>
            <div className="flex items-center gap-2.5">
              <span className="font-mono text-xs px-2.5 py-0.5 rounded-full border border-rose-500/40 bg-rose-950/50 text-rose-200">
                Live Polling: {pilotReadiness?.kill_switch_active !== false ? "LOCKED (Fail-Closed Kill Switch)" : "Shadow Opt-In Active"}
              </span>
              <ChevronDown className="w-4 h-4 text-slate-400 transition-transform group-open:rotate-180" />
            </div>
          </summary>

          <div className="px-5 pb-5 pt-2 border-t border-slate-800/80 grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
            <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4 space-y-1.5">
              <div className="font-semibold text-white">Active Serving vs. Shadow Research</div>
              <div className="text-slate-300">
                Served Model:{" "}
                <span className="font-mono text-emerald-300">
                  {modelStatus?.model_class ?? "SAGEForecaster"}
                </span>{" "}
                ({modelStatus?.algorithm ?? "SAGE-LIGHTGBM"} v{modelStatus?.version ?? "1.0.0"})
              </div>
              <div className="text-slate-400 text-xs">
                Shadow Research Candidate: AdaptiveSAGEForecaster (promoted:{" "}
                <span className="font-mono text-rose-300">
                  {pilotReadiness?.adaptive_sage_promoted ? "true" : "false"}
                </span>
                )
              </div>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4 space-y-2">
              <div className="font-semibold text-white">Four Explicit Data Modes</div>
              <div className="flex flex-wrap gap-1.5 font-mono text-xs">
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
                      className={`px-2 py-0.5 rounded border ${
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
              <div className="text-slate-400 text-xs">
                Abstention (&gt;12h stale) indicates missing upstream telemetry—not confirmed physical hardware failure.
              </div>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4 space-y-1.5">
              <div className="font-semibold text-white">Offline Pilot Quota &amp; Readiness Gate</div>
              <div className="text-slate-300 text-xs sm:text-sm">
                Daily OpenAQ Cap:{" "}
                <span className="font-mono">
                  {pilotReadiness ? `${pilotReadiness.requests_used_today} / ${pilotReadiness.max_requests_per_day}` : "0 / 48"}
                </span>{" "}
                • Opt-In:{" "}
                <span className="font-mono">{pilotReadiness?.opt_in_enabled ? "enabled" : "disabled"}</span>
              </div>
              <div className="text-slate-400 text-xs">
                Shadow calibration gate:{" "}
                <span className="font-mono">
                  {pilotReadiness?.shadow_evaluation_summary?.status ?? "INSUFFICIENT_OBSERVED_OUTCOMES"}
                </span>{" "}
                (&ge;{pilotReadiness?.shadow_evaluation_summary?.min_matched_outcomes_required ?? 100} matched targets, &ge;{pilotReadiness?.shadow_evaluation_summary?.min_unique_hours_required ?? 24}h, &ge;{Math.round((pilotReadiness?.shadow_evaluation_summary?.min_lead_seconds_required ?? 300) / 60)}m lead, verified JSON artifacts).
              </div>
            </div>
          </div>
        </details>
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-800 bg-slate-950 px-6 py-4 text-xs sm:text-sm text-slate-400">
        <div className="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-2">
          <div>
            Data Source: UK Department for Environment, Food &amp; Rural Affairs (DEFRA) AURN Q1 2024 (OGL v3).
          </div>
          <div className="font-mono text-xs text-slate-400">
            Model:{" "}
            {modelStatus
              ? `${modelStatus.model_class ?? "SAGEForecaster"} (${modelStatus.algorithm} v${modelStatus.version}) • SHA-256 ${
                  modelStatus.verified ? "Verified" : "UNVERIFIED"
                } (${modelStatus.sha256.slice(0, 12)}…)`
              : isDemoMode
              ? IS_STATIC_PORTFOLIO
                ? "Portfolio Static Showcase (Audited Q1 2024 Fixtures)"
                : "Offline Demo Fixtures (API Unreachable)"
              : "Checking Model Governance…"}
          </div>
        </div>
      </footer>
    </div>
  );
}
