import httpx
from dotenv import load_dotenv
import os

load_dotenv()
api_key = os.getenv("OPENAQ_API_KEY")

with httpx.Client(base_url="https://api.openaq.org", headers={"X-API-Key": api_key}) as client:
    # Sensor 211
    resp = client.get("/v3/sensors/211")
    print("Sensor 211 info:", resp.json())
