import httpx
from dotenv import load_dotenv
import os

load_dotenv()
api_key = os.getenv("OPENAQ_API_KEY")

with httpx.Client(base_url="https://api.openaq.org", headers={"X-API-Key": api_key}) as client:
    # Query locations in UK with parameter pm25, ordered or filtered
    resp = client.get("/v3/locations", params={"countries_id": 41, "parameters_id": 2, "limit": 25})
    results = resp.json().get("results", [])
    print(f"Found {len(results)} locations in UK with PM2.5:")
    active_count = 0
    for loc in results:
        loc_id = loc.get("id")
        name = loc.get("name")
        sensors = loc.get("sensors", [])
        pm25_sensors = [s for s in sensors if s.get("parameter", {}).get("name") == "pm25"]
        # check datetimeLast
        for s in pm25_sensors:
            sid = s.get("id")
            s_resp = client.get(f"/v3/sensors/{sid}")
            s_data = s_resp.json().get("results", [{}])[0]
            first_dt = s_data.get("datetimeFirst", {}).get("utc")
            last_dt = s_data.get("datetimeLast", {}).get("utc")
            count = s_data.get("coverage", {}).get("observedCount", 0)
            print(f"Station {loc_id} ({name}) - Sensor {sid}: {first_dt} -> {last_dt} (count={count})")
