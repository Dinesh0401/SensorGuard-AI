import urllib.request
import json

def check_run(run_id):
    req = urllib.request.Request(
        f'https://api.github.com/repos/Dinesh0401/SensorGuard-AI/actions/runs/{run_id}/jobs',
        headers={'User-Agent': 'CI'}
    )
    with urllib.request.urlopen(req) as res:
        data = json.loads(res.read().decode('utf-8'))
        for j in data.get('jobs', []):
            print(f"Job: {j.get('name')} -> {j.get('conclusion')}")
            for s in j.get('steps', []):
                print(f"  Step: {s.get('name')} -> {s.get('conclusion')}")

check_run(45428314670)
