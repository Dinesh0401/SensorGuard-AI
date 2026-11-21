import httpx
from dotenv import load_dotenv
import os

load_dotenv()
api_key = os.getenv("OPENAQ_API_KEY")

with httpx.Client(base_url="https://api.openaq.org", headers={"X-API-Key": api_key}) as client:
    resp = client.get("/v3/countries", params={"limit": 100})
    countries = resp.json().get("results", [])
    for c in countries:
        if c.get("code") in ["GB", "US", "IN"]:
            print(f"Country {c.get('code')}: ID={c.get('id')}, name={c.get('name')}")
