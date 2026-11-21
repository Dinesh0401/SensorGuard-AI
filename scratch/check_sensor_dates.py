import os
from dotenv import load_dotenv
load_dotenv()
from sensorguard.providers import OpenAQClient

client = OpenAQClient()
resp = client.get_sensor_hours_raw(sensor_id=211, limit=5)
print("Sensor 211 results count:", len(resp.payload.get("results", [])))
print("First 2 results:", resp.payload.get("results", [])[:2])
