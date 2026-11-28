export interface StationItem {
  id: number;
  provider_location_id: number;
  name: string;
  latitude: number;
  longitude: number;
  status: "HEALTHY" | "LATE" | "NO_REPORT";
  latest_pm25: number;
  age_hours: number;
  is_archived_pilot: boolean;
}

export interface BaselineMetric {
  key: string;
  name: string;
  calibrator: string;
  mae: number;
  rmse: number;
  coverage: string;
  published_coverage: string;
  width: string;
  winkler: string;
  worst_gap: string;
  abstention: string;
  advantage: string;
}

/**
 * Fallback demonstration stations matching the exact 8 audited UK AURN Q1 2024
 * pilot stations in data/sensorguard.db (shown only in labeled DEMO MODE).
 */
export const PILOT_STATIONS: StationItem[] = [
  {
    id: 1,
    provider_location_id: 141,
    name: "London Harlington - UKA00472",
    latitude: 51.4888,
    longitude: -0.4416,
    status: "HEALTHY",
    latest_pm25: 10.9,
    age_hours: 0.8,
    is_archived_pilot: true,
  },
  {
    id: 2,
    provider_location_id: 153,
    name: "London Hillingdon - UKA00266",
    latitude: 51.4963,
    longitude: -0.4609,
    status: "HEALTHY",
    latest_pm25: 11.7,
    age_hours: 1.0,
    is_archived_pilot: true,
  },
  {
    id: 3,
    provider_location_id: 154,
    name: "Westminster - Marylebone Road",
    latitude: 51.5225,
    longitude: -0.1546,
    status: "HEALTHY",
    latest_pm25: 19.8,
    age_hours: 0.5,
    is_archived_pilot: true,
  },
  {
    id: 4,
    provider_location_id: 157,
    name: "Bexley - Slade Green",
    latitude: 51.466,
    longitude: 0.1849,
    status: "HEALTHY",
    latest_pm25: 15.4,
    age_hours: 1.1,
    is_archived_pilot: true,
  },
  {
    id: 5,
    provider_location_id: 698,
    name: "Southampton Centre - UKA00235",
    latitude: 50.9081,
    longitude: -1.3958,
    status: "HEALTHY",
    latest_pm25: 13.1,
    age_hours: 1.5,
    is_archived_pilot: true,
  },
  {
    id: 6,
    provider_location_id: 804,
    name: "Wicken Fen - UKA00362",
    latitude: 52.2985,
    longitude: 0.2909,
    status: "HEALTHY",
    latest_pm25: 9.8,
    age_hours: 1.2,
    is_archived_pilot: true,
  },
  {
    id: 7,
    provider_location_id: 1795,
    name: "St Osyth - UKA00445",
    latitude: 51.778,
    longitude: 1.049,
    status: "LATE",
    latest_pm25: 12.6,
    age_hours: 4.2,
    is_archived_pilot: true,
  },
  {
    id: 8,
    provider_location_id: 2312,
    name: "Manchester Piccadilly - UKA00248",
    latitude: 53.4815,
    longitude: -2.2379,
    status: "HEALTHY",
    latest_pm25: 14.2,
    age_hours: 0.9,
    is_archived_pilot: true,
  },
];

export const BASELINE_BENCHMARKS: BaselineMetric[] = [
  {
    key: "persistence",
    name: "Persistence (B0a)",
    calibrator: "Split Conformal",
    mae: 0.24,
    rmse: 0.7,
    coverage: "96.9%",
    published_coverage: "97.0%",
    width: "2.00 µg/m³",
    winkler: "3.16",
    worst_gap: "23.3%",
    abstention: "0.4%",
    advantage: "Sharpest trivial baseline under stationary hourly telemetry",
  },
  {
    key: "ridge",
    name: "Ridge Regression (B2a)",
    calibrator: "Split Conformal",
    mae: 0.3,
    rmse: 0.69,
    coverage: "96.0%",
    published_coverage: "96.2%",
    width: "2.23 µg/m³",
    winkler: "3.26",
    worst_gap: "23.3%",
    abstention: "0.4%",
    advantage: "Tight linear spatiotemporal regression intervals",
  },
  {
    key: "lightgbm_quantile",
    name: "LightGBM + CQR",
    calibrator: "CQR (Romano et al. 2019)",
    mae: 0.29,
    rmse: 0.83,
    coverage: "99.9%",
    published_coverage: "99.9%",
    width: "11.35 µg/m³",
    winkler: "11.45",
    worst_gap: "10.0%",
    abstention: "0.4%",
    advantage: "Sharper than SAGE under calm exchangeable natural regimes",
  },
  {
    key: "lightgbm_rolling",
    name: "LightGBM Rolling",
    calibrator: "Rolling Conformal (168h)",
    mae: 0.28,
    rmse: 0.8,
    coverage: "90.8%",
    published_coverage: "90.9%",
    width: "3.06 µg/m³",
    winkler: "4.13",
    worst_gap: "31.7%",
    abstention: "0.4%",
    advantage: "Tight time-adaptive residuals near 90% nominal target",
  },
  {
    key: "sage_full",
    name: "SAGE Full (Guarded)",
    calibrator: "Sensitivity Envelope + Policy (tau=8.0)",
    mae: 0.28,
    rmse: 0.8,
    coverage: "99.9%",
    published_coverage: "99.9%",
    width: "17.01 µg/m³",
    winkler: "17.06",
    worst_gap: "10.0%",
    abstention: "0.4%",
    advantage: "Bounds MNAR sensitivity shifts; selectively abstains up to 0.95% in outages",
  },
  {
    key: "sage_no_abstain",
    name: "SAGE (No Abstention)",
    calibrator: "Sensitivity Envelope (All Pub)",
    mae: 0.28,
    rmse: 0.8,
    coverage: "99.9%",
    published_coverage: "99.9%",
    width: "17.01 µg/m³",
    winkler: "17.06",
    worst_gap: "10.0%",
    abstention: "0.0%",
    advantage: "Ablation control verifying overall PICP == published PICP",
  },
];

export const HOURLY_TIME_SERIES = [
  { hour: "00:00", observed: 12.1, point_pred: 12.3, lower: 4.1, upper: 20.5, status: "publish" },
  { hour: "01:00", observed: 11.8, point_pred: 12.0, lower: 3.8, upper: 20.2, status: "publish" },
  { hour: "02:00", observed: 11.2, point_pred: 11.5, lower: 3.3, upper: 19.7, status: "publish" },
  { hour: "03:00", observed: 10.9, point_pred: 11.1, lower: 2.9, upper: 19.3, status: "publish" },
  { hour: "04:00", observed: 11.5, point_pred: 11.3, lower: 3.1, upper: 19.5, status: "publish" },
  { hour: "05:00", observed: 12.8, point_pred: 12.2, lower: 4.0, upper: 20.4, status: "publish" },
  { hour: "06:00", observed: 14.6, point_pred: 13.9, lower: 5.5, upper: 22.3, status: "publish" },
  { hour: "07:00", observed: 17.2, point_pred: 16.1, lower: 7.6, upper: 24.6, status: "publish" },
  { hour: "08:00", observed: 19.5, point_pred: 18.2, lower: 9.7, upper: 26.7, status: "publish" },
  { hour: "09:00", observed: 18.8, point_pred: 18.5, lower: 10.0, upper: 27.0, status: "publish" },
  { hour: "10:00", observed: 16.9, point_pred: 17.2, lower: 8.7, upper: 25.7, status: "publish" },
  { hour: "11:00", observed: 15.4, point_pred: 15.8, lower: 7.3, upper: 24.3, status: "publish" },
  { hour: "12:00", observed: 14.8, point_pred: 15.0, lower: 6.5, upper: 23.5, status: "publish" },
  { hour: "13:00", observed: 14.2, point_pred: 14.5, lower: 6.0, upper: 23.0, status: "publish" },
  { hour: "14:00", observed: 13.9, point_pred: 14.1, lower: 5.6, upper: 22.6, status: "publish" },
  { hour: "15:00", observed: 14.7, point_pred: 14.3, lower: 5.8, upper: 22.8, status: "publish" },
  { hour: "16:00", observed: 16.2, point_pred: 15.6, lower: 7.1, upper: 24.1, status: "publish" },
  { hour: "17:00", observed: 18.5, point_pred: 17.4, lower: 8.9, upper: 25.9, status: "publish" },
  { hour: "18:00", observed: 20.1, point_pred: 19.0, lower: 10.5, upper: 27.5, status: "caution" },
  { hour: "19:00", observed: 19.4, point_pred: 18.8, lower: 10.3, upper: 27.3, status: "caution" },
  { hour: "20:00", observed: 17.6, point_pred: 17.9, lower: 9.4, upper: 26.4, status: "publish" },
  { hour: "21:00", observed: 16.1, point_pred: 16.5, lower: 8.0, upper: 25.0, status: "publish" },
  { hour: "22:00", observed: 14.8, point_pred: 15.2, lower: 6.7, upper: 23.7, status: "publish" },
  { hour: "23:00", observed: 13.9, point_pred: 14.2, lower: 5.7, upper: 22.7, status: "publish" },
];
