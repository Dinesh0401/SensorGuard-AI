import os
from dotenv import load_dotenv
load_dotenv()
from sensorguard.providers import OpenAQClient

client = OpenAQClient()
# Try different date filter parameter names
for p_from, p_to in [
    ("datetime_from", "datetime_to"),
    ("date_from", "date_to"),
]:
    resp = client._execute_request(
        "GET",
        "/v3/sensors/211/hours",
        params={"limit": 5, p_from: "2024-01-01T00:00:00Z", p_to: "2024-01-02T00:00:00Z"}
    )
    results = resp.payload.get("results", [])
    print(f"Params ({p_from}, {p_to}): count={len(results)}")
    if results:
        print("  First result datetime:", results[0].get("period", {}).get("datetimeTo"))

# Also check location detail for sensor 211 to see its datetimeFirst and datetimeLast!
loc_resp = client.get_location(137)
results = loc_resp.payload.get("results", [])
if results:
    sensors = results[0].get("sensors", [])
    for s in sensors:
        if s.get("id") == 211:
            print("Sensor 211 metadata:", s.get("datetimeFirst"), s.get("datetimeLast"))
