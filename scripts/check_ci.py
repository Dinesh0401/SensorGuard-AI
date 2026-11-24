import urllib.request
import json

req = urllib.request.Request(
    'https://api.github.com/repos/Dinesh0401/SensorGuard-AI/actions/runs/45428314670/jobs',
    headers={'User-Agent': 'CI'}
)
with urllib.request.urlopen(req) as res:
    data = json.loads(res.read().decode('utf-8'))
    for j in data.get('jobs', []):
        job_id = j.get('id')
        print(f"Job ID: {job_id}, Name: {j.get('name')}")
        # Fetch log
        log_req = urllib.request.Request(
            f'https://api.github.com/repos/Dinesh0401/SensorGuard-AI/actions/jobs/{job_id}/logs',
            headers={'User-Agent': 'CI'}
        )
        try:
            with urllib.request.urlopen(log_req) as log_res:
                lines = log_res.read().decode('utf-8', errors='replace').splitlines()
                # find failures in pytest
                failed_lines = []
                capture = False
                for line in lines:
                    if "FAILED " in line or "FAILURES" in line or "ERRORS" in line:
                        capture = True
                    if capture:
                        failed_lines.append(line)
                        if len(failed_lines) > 60:
                            break
                print("\n".join(failed_lines if failed_lines else lines[-50:]))
        except Exception as e:
            print(f"Error fetching logs: {e}")
