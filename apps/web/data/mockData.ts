export interface StationItem {
  id: number;
  name: string;
  latitude: number;
  longitude: number;
  status: "HEALTHY" | "LATE" | "NO_REPORT";
  latest_pm25: number;
  age_hours: number;
  is_archived_pilot: boolean;
}

export interface BaselineMetric {
  name: string;
  calibrator: string;
  mae: number;
  rmse: number;
  coverage: string;
  width: string;
  winkler: string;
  worst_gap: string;
  abstention: string;
  advantage: string;
}

export const PILOT_STATIONS: StationItem[] = [
  {
    id: 1,
    name: "London Camden",
    latitude: 51.543,
    longitude: -0.175,
    status: "HEALTHY",
    latest_pm25: 14.2,
    age_hours: 0.8,
    is_archived_pilot: true,
  },
  {
    id: 2,
    name: "London Bloomsbury",
    latitude: 51.522,
    longitude: -0.125,
    status: "HEALTHY",
    latest_pm25: 12.8,
    age_hours: 1.0,
    is_archived_pilot: true,
  },
  {
    id: 3,
    name: "City of London",
    latitude: 51.513,
    longitude: -0.093,
    status: "HEALTHY",
    latest_pm25: 16.5,
    age_hours: 1.2,
    is_archived_pilot: true,
  },
  {
    id: 4,
    name: "London Marylebone Road",
    latitude: 51.522,
    longitude: -0.155,
    status: "HEALTHY",
    latest_pm25: 19.8,
    age_hours: 0.5,
    is_archived_pilot: true,
  },
  {
    id: 5,
    name: "London Westminster",
    latitude: 51.494,
    longitude: -0.131,
    status: "HEALTHY",
    latest_pm25: 13.1,
    age_hours: 1.5,
    is_archived_pilot: true,
  },
  {
    id: 6,
    name: "Southwark A2 Old Kent Rd",
    latitude: 51.501,
    longitude: -0.100,
    status: "HEALTHY",
    latest_pm25: 15.4,
    age_hours: 1.1,
    is_archived_pilot: true,
  },
  {
    id: 7,
    name: "London N. Kensington",
    latitude: 51.521,
    longitude: -0.213,
    status: "HEALTHY",
    latest_pm25: 11.7,
    age_hours: 0.9,
    is_archived_pilot: true,
  },
  {
    id: 8,
    name: "London Harlington",
    latitude: 51.488,
    longitude: -0.441,
    status: "HEALTHY",
    latest_pm25: 10.9,
    age_hours: 2.1,
    is_archived_pilot: true,
  },
];

export const BASELINE_BENCHMARKS: BaselineMetric[] = [
  {
    name: "Persistence (B0a)",
    calibrator: "Split Conformal",
    mae: 0.24,
    rmse: 0.70,
    coverage: "96.9%",
    width: "2.00 µg/m³",
    winkler: "3.16",
    worst_gap: "23.3%",
    abstention: "0.4%",
    advantage: "Fastest trivial baseline, but static window",
  },
  {
    name: "Ridge Regression (B2a)",
    calibrator: "Split Conformal",
    mae: 0.30,
    rmse: 0.69,
    coverage: "96.0%",
    width: "2.23 µg/m³",
    winkler: "3.26",
    worst_gap: "23.3%",
    abstention: "0.4%",
    advantage: "Linear spatiotemporal interpolation",
  },
  {
    name: "LightGBM + CQR",
    calibrator: "Conformalized Quantile Regression",
    mae: 0.29,
    rmse: 0.83,
    coverage: "99.9%",
    width: "11.35 µg/m³",
    winkler: "11.45",
    worst_gap: "10.0%",
    abstention: "0.4%",
    advantage: "Sharpest intervals under calm exchangeability",
  },
  {
    name: "Rolling Conformal",
    calibrator: "168h Trailing Residuals",
    mae: 0.28,
    rmse: 0.80,
    coverage: "90.8%",
    width: "3.06 µg/m³",
    winkler: "4.13",
    worst_gap: "31.7%",
    abstention: "0.4%",
    advantage: "Adaptive to local variance, tight intervals",
  },
  {
    name: "SAGE Full Framework",
    calibrator: "Sensitivity Envelope + Policy",
    mae: 0.28,
    rmse: 0.80,
    coverage: "99.9%",
    width: "17.01 µg/m³",
    winkler: "17.06",
    worst_gap: "10.0%",
    abstention: "12.5%",
    advantage: "Guaranteed robustness & abstention under MNAR",
  },
  {
    name: "SAGE (No Abstention)",
    calibrator: "Sensitivity Envelope (All Pub)",
    mae: 0.28,
    rmse: 0.80,
    coverage: "99.9%",
    width: "17.01 µg/m³",
    winkler: "17.06",
    worst_gap: "10.0%",
    abstention: "0.0%",
    advantage: "Ablation control verifying unified mask invariant",
  },
];

export const HOURLY_TIME_SERIES = [
  { hour: "00:00", observed: 12.1, point_pred: 12.3, lower: 8.5, upper: 16.1, status: "publish" },
  { hour: "01:00", observed: 11.8, point_pred: 12.0, lower: 8.2, upper: 15.8, status: "publish" },
  { hour: "02:00", observed: 11.2, point_pred: 11.5, lower: 7.8, upper: 15.2, status: "publish" },
  { hour: "03:00", observed: 10.9, point_pred: 11.1, lower: 7.4, upper: 14.8, status: "publish" },
  { hour: "04:00", observed: 11.5, point_pred: 11.3, lower: 7.6, upper: 15.0, status: "publish" },
  { hour: "05:00", observed: 12.8, point_pred: 12.2, lower: 8.4, upper: 16.0, status: "publish" },
  { hour: "06:00", observed: 14.6, point_pred: 13.9, lower: 9.8, upper: 18.0, status: "publish" },
  { hour: "07:00", observed: 17.2, point_pred: 16.1, lower: 11.5, upper: 20.7, status: "publish" },
  { hour: "08:00", observed: 19.5, point_pred: 18.2, lower: 13.0, upper: 23.4, status: "publish" },
  { hour: "09:00", observed: 18.8, point_pred: 18.5, lower: 13.2, upper: 23.8, status: "publish" },
  { hour: "10:00", observed: 16.9, point_pred: 17.2, lower: 12.0, upper: 22.4, status: "publish" },
  { hour: "11:00", observed: 15.4, point_pred: 15.8, lower: 10.9, upper: 20.7, status: "publish" },
  { hour: "12:00", observed: 14.8, point_pred: 15.0, lower: 10.2, upper: 19.8, status: "publish" },
  { hour: "13:00", observed: 14.2, point_pred: 14.5, lower: 9.8, upper: 19.2, status: "publish" },
  { hour: "14:00", observed: 13.9, point_pred: 14.1, lower: 9.5, upper: 18.7, status: "publish" },
  { hour: "15:00", observed: 14.7, point_pred: 14.3, lower: 9.7, upper: 18.9, status: "publish" },
  { hour: "16:00", observed: 16.2, point_pred: 15.6, lower: 10.8, upper: 20.4, status: "publish" },
  { hour: "17:00", observed: 18.5, point_pred: 17.4, lower: 12.2, upper: 22.6, status: "publish" },
  { hour: "18:00", observed: 20.1, point_pred: 19.0, lower: 13.5, upper: 24.5, status: "caution" },
  { hour: "19:00", observed: 19.4, point_pred: 18.8, lower: 13.1, upper: 24.5, status: "caution" },
  { hour: "20:00", observed: 17.6, point_pred: 17.9, lower: 12.4, upper: 23.4, status: "publish" },
  { hour: "21:00", observed: 16.1, point_pred: 16.5, lower: 11.2, upper: 21.8, status: "publish" },
  { hour: "22:00", observed: 14.8, point_pred: 15.2, lower: 10.1, upper: 20.3, status: "publish" },
  { hour: "23:00", observed: 13.9, point_pred: 14.2, lower: 9.4, upper: 19.0, status: "publish" },
];
