import httpx
from dotenv import load_dotenv
import os

load_dotenv()
api_key = os.getenv("OPENAQ_API_KEY")

# Query OpenAPI schema or test parameters
with httpx.Client(base_url="https://api.openaq.org", headers={"X-API-Key": api_key}) as client:
    resp = client.get("/openapi.json")
    if resp.status_code == 200:
        spec = resp.json()
        hours_endpoint = spec.get("paths", {}).get("/v3/sensors/{sensors_id}/hours", {})
        print("Hours endpoint parameters:")
        for p in hours_endpoint.get("get", {}).get("parameters", []):
            print(" ", p.get("name"), ":", p.get("in"), p.get("description", ""))
    else:
        print("OpenAPI status:", resp.status_code)
