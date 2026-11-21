import json
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from sensorguard.audit import audit_candidate_location
from sensorguard.providers import OpenAQClient

client = OpenAQClient()
candidate_ids = [137, 141, 147, 151, 153, 154, 157, 223687]
reports = []

print("Auditing candidate locations...")
for loc_id in candidate_ids:
    try:
        rep = audit_candidate_location(
            location_id=loc_id,
            client=client,
            datetime_from="2024-01-01T00:00:00Z",
            datetime_to="2024-03-31T23:59:59Z",
            max_pages=5,
            page_size=100
        )
        reports.append(rep)
        print(f"Station {loc_id} ({rep.station_name}): PM2.5 sensors={rep.pm25_sensor_count}, sufficient={rep.has_sufficient_pm25}")
        for s in rep.sensors:
            print(f"  Sensor {s.sensor_id}: total={s.total_records}, gap_rate={s.missing_gap_rate:.2%}, usable={s.is_usable_history}")
    except Exception as e:
        print(f"Station {loc_id} error: {e}")

out_path = Path("artifacts/data_audit/report.json")
out_path.parent.mkdir(parents=True, exist_ok=True)
audit_summary = {
    "region": "Greater London, United Kingdom",
    "bounding_box": [-0.51, 51.28, 0.33, 51.69],
    "audited_stations": len(reports),
    "stations_meeting_criteria": sum(1 for r in reports if r.has_sufficient_pm25),
    "criterion": ">=8 active PM2.5 monitoring locations with multi-month coverage and <40% gap rate",
    "reports": [
        {
            "location_id": r.location_id,
            "station_name": r.station_name,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "country": r.country,
            "pm25_sensor_count": r.pm25_sensor_count,
            "has_sufficient_pm25": r.has_sufficient_pm25,
            "sensors": [
                {
                    "sensor_id": s.sensor_id,
                    "parameter": s.parameter,
                    "unit": s.unit,
                    "total_records": s.total_records,
                    "first_observed_at": s.first_observed_at,
                    "last_observed_at": s.last_observed_at,
                    "hours_spanned": s.hours_spanned,
                    "missing_gap_rate": s.missing_gap_rate,
                    "cadence_hours": s.cadence_hours,
                    "is_usable_history": s.is_usable_history,
                }
                for s in r.sensors
            ],
            "summary": r.summary,
        }
        for r in reports
    ]
}

out_path.write_text(json.dumps(audit_summary, indent=2), encoding="utf-8")
print(f"Audit report saved to {out_path}")
