"""Cross-platform runner for Playwright E2E verification against live Uvicorn + built web export."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "apps" / "web"

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _wait_ready(url: str, timeout_s: float = 45.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=2.0)
            if resp.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        time.sleep(0.5)
    raise TimeoutError(f"Service did not become ready at {url} within {timeout_s}s: {last_err}")


def main() -> int:
    api_host = "127.0.0.1"
    api_port = int(os.getenv("E2E_API_PORT", "8000"))
    web_port = int(os.getenv("E2E_WEB_PORT", "3000"))
    api_url = f"http://{api_host}:{api_port}"
    web_url = f"http://{api_host}:{web_port}"

    # Auto-seed SQLite pilot database if not populated
    from scripts.seed_pilot_db import seed_sqlite_pilot_db

    db_path = ROOT_DIR / "data" / "sensorguard.db"
    seed_sqlite_pilot_db(db_path=db_path)

    env_api = os.environ.copy()
    env_api.setdefault("CORS_ORIGINS", f"http://localhost:{web_port},{web_url}")

    api_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "sensorguard.api:app",
            "--host",
            api_host,
            "--port",
            str(api_port),
        ],
        cwd=str(ROOT_DIR),
        env=env_api,
    )
    web_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "http.server",
            str(web_port),
            "--bind",
            api_host,
            "--directory",
            str(WEB_DIR / "out"),
        ],
        cwd=str(ROOT_DIR),
    )

    try:
        _wait_ready(f"{api_url}/health/ready", timeout_s=60.0)
        _wait_ready(web_url, timeout_s=20.0)

        env_e2e = os.environ.copy()
        env_e2e["PLAYWRIGHT_BASE_URL"] = web_url
        env_e2e["PLAYWRIGHT_API_URL"] = api_url

        npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
        res = subprocess.run(
            [npm_cmd, "--prefix", str(WEB_DIR), "run", "test:e2e"],
            cwd=str(ROOT_DIR),
            env=env_e2e,
            check=False,
        )
        return int(res.returncode)
    finally:
        for proc in (web_proc, api_proc):
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
