import httpx
from dotenv import load_dotenv
import os

load_dotenv()
api_key = os.getenv("OPENAQ_API_KEY")

with httpx.Client(base_url="https://api.openaq.org", headers={"X-API-Key": api_key}) as client:
    resp = client.get("/v3/locations", params={"countries_id": 79, "parameters_id": 2, "limit": 30})
    locations = resp.json().get("results", [])
    print(f"Found {len(locations)} locations in GB with PM2.5:")
    valid_candidates = []
    for loc in locations:
        loc_id = loc.get("id")
        name = loc.get("name")
        sensors = loc.get("sensors", [])
        pm25_s = [s for s in sensors if s.get("parameter", {}).get("name") == "pm25"]
        for s in pm25_s:
            sid = s.get("id")
            s_resp = client.get(f"/v3/sensors/{sid}")
            s_data = s_resp.json().get("results", [{}])[0]
            first_dt = s_data.get("datetimeFirst", {}).get("utc")
            last_dt = s_data.get("datetimeLast", {}).get("utc")
            count = s_data.get("coverage", {}).get("observedCount", 0)
            print(f"Loc {loc_id} ({name[:30]}) | Sensor {sid} | {first_dt} -> {last_dt} | count={count}")
            if count and count >= 500:
                valid_candidates.append((loc_id, name, sid, first_dt, last_dt, count))

    print(f"\nCandidates with >= 500 records: {len(valid_candidates)}")
    for c in valid_candidates:
        print(" ", c)
