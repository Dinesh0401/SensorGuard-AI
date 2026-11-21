import os
from dotenv import load_dotenv
load_dotenv()

from sensorguard.providers import OpenAQClient

client = OpenAQClient()
# Query Greater London bbox: min_lon, min_lat, max_lon, max_lat
resp = client.get_locations(bbox=(-0.51, 51.28, 0.33, 51.69), limit=15)
results = resp.payload.get("results", [])
print(f"Found {len(results)} locations in London bbox")
for r in results:
    sensors = r.get("sensors", [])
    pm25_s = [s for s in sensors if (s.get("parameter", {}).get("name") if isinstance(s.get("parameter"), dict) else s.get("parameter")) == "pm25"]
    print(f"ID: {r.get('id')}, Name: {r.get('name')}, PM2.5 sensors: {len(pm25_s)}")
