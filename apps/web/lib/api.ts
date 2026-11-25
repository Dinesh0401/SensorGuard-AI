/**
 * Typed client for SensorGuard AI FastAPI Backend.
 *
 * Strictly aligned with Pydantic schemas in src/sensorguard/schemas.py:
 * - /health & /health/ready
 * - /api/v1/stations
 * - /api/v1/stations/{id}
 * - /api/v1/stations/{id}/observations
 * - /api/v1/stations/{id}/forecasts
 * - /api/v1/stations/{id}/reliability
 * - /api/v1/research/experiments
 * - /api/v1/research/experiments/{id}
 * - /api/v1/model/status
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface StationSummary {
  id: number;
  provider: string;
  provider_location_id: number;
  name: string;
  latitude: number;
  longitude: number;
  country: string | null;
  timezone_name: string | null;
  status: "HEALTHY" | "LATE" | "NO_REPORT" | "OFFLINE_CONFIRMED";
  is_stale: boolean;
  is_archived_pilot: boolean;
  latest_observation_time: string | null;
  latest_pm25_value: number | null;
  age_hours: number | null;
}

export interface StationListResponse {
  stations: StationSummary[];
  total_count: number;
  provider_citation: string;
}

export interface ObservationRecord {
  observed_at: string;
  received_at: string | null;
  fetched_at: string;
  value: number | null;
  unit: string;
  quality_flag: string;
  source_status: string;
  version: number;
}

export interface StationObservationsResponse {
  station_id: number;
  station_name: string;
  observations: ObservationRecord[];
  total_returned: number;
  latest_observed_at: string | null;
  age_hours: number | null;
  freshness_status: "fresh" | "moderate_delay" | "stale";
  provider_citation: string;
}

export interface ForecastScenarioBounds {
  gamma: number;
  lower: number;
  upper: number;
}

export interface ForecastRecord {
  station_id: number;
  decision_cutoff: string;
  target_time: string;
  point_forecast: number;
  lower_bound: number;
  upper_bound: number;
  nominal_coverage: number;
  model_version: string;
  status: "publish" | "caution" | "abstain";
  abstain_reason: string;
  is_stale: boolean;
  scenario_bounds: ForecastScenarioBounds[];
  feature_hash?: string | null;
}

export interface StationForecastsResponse {
  station_id: number;
  station_name: string;
  current_status: "publish" | "caution" | "abstain";
  latest_forecast: ForecastRecord | null;
  forecasts: ForecastRecord[];
  provider_citation: string;
}

export interface StationReliabilityResponse {
  station_id: number;
  station_name: string;
  total_observations: number;
  first_observed_at: string | null;
  last_observed_at: string | null;
  gap_rate_pct: number;
  longest_consecutive_outage_hours: number;
  status_distribution: Record<string, number>;
  benchmark_metrics?: {
    mae: number | null;
    rmse: number | null;
    picp: number | null;
    mpiw: number | null;
    winkler_score: number | null;
  } | null;
}

export interface ExperimentSummary {
  experiment_id: string;
  title: string;
  dataset_hash: string;
  nominal_coverage: number;
  models_evaluated: string[];
  scenarios_evaluated: string[];
  timestamp?: string | null;
}

export interface ExperimentListResponse {
  experiments: ExperimentSummary[];
  total_count: number;
}

export interface ScenarioMethodMetrics {
  mae?: number | null;
  rmse?: number | null;
  picp?: number | null;
  published_picp?: number | null;
  mpiw?: number | null;
  winkler_score?: number | null;
  worst_stratum_coverage_gap?: number | null;
  abstention_rate?: number | null;
}

export interface ScenarioMethodEntry {
  calibrator?: string;
  metrics?: ScenarioMethodMetrics;
}

export interface AblationAggregateStats {
  picp_mean: number;
  published_picp_mean: number;
  mpiw_mean: number;
  winkler_score_mean: number;
  abstention_rate_mean: number;
  mean_disagreement_mean: number;
}

export interface ExperimentDetailPayload {
  scenarios?: Record<string, Record<string, ScenarioMethodEntry>>;
  aggregates?: Record<string, Record<string, AblationAggregateStats>>;
  [key: string]: unknown;
}

export interface ExperimentDetailResponse {
  experiment_id: string;
  title: string;
  details: ExperimentDetailPayload;
  research_caveats: string[];
}

export interface ModelStatusResponse {
  model_id: string;
  version: string;
  algorithm: string;
  created_at: string;
  dataset_hash: string;
  nominal_coverage: number;
  sha256: string;
  verified: boolean;
  model_path: string;
  artifact_exists: boolean;
  extra?: Record<string, unknown> | null;
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function fetchJson<T>(path: string): Promise<T> {
  const url = `${API_BASE_URL}${path}`;
  const res = await fetch(url, {
    headers: {
      Accept: "application/json",
    },
    cache: "no-store",
  });

  if (!res.ok) {
    let errDetail = res.statusText;
    try {
      const data = await res.json();
      if (data?.detail) errDetail = data.detail;
    } catch {
      // ignore non-JSON body
    }
    throw new ApiError(
      `Request to ${path} failed (${res.status}): ${errDetail}`,
      res.status
    );
  }

  return res.json() as Promise<T>;
}

export const api = {
  getStations: () => fetchJson<StationListResponse>("/api/v1/stations"),
  getStation: (id: number) =>
    fetchJson<StationSummary>(`/api/v1/stations/${id}`),
  getObservations: (id: number, limit = 48) =>
    fetchJson<StationObservationsResponse>(
      `/api/v1/stations/${id}/observations?limit=${limit}`
    ),
  getForecasts: (id: number, limit = 24) =>
    fetchJson<StationForecastsResponse>(
      `/api/v1/stations/${id}/forecasts?limit=${limit}`
    ),
  getReliability: (id: number) =>
    fetchJson<StationReliabilityResponse>(
      `/api/v1/stations/${id}/reliability`
    ),
  getExperiments: () =>
    fetchJson<ExperimentListResponse>("/api/v1/research/experiments"),
  getExperimentDetail: (experimentId: string) =>
    fetchJson<ExperimentDetailResponse>(
      `/api/v1/research/experiments/${encodeURIComponent(experimentId)}`
    ),
  getModelStatus: () =>
    fetchJson<ModelStatusResponse>("/api/v1/model/status").catch(() => null),
  getHealth: () =>
    fetchJson<{ status: string; data_mode?: string }>("/health"),
};
