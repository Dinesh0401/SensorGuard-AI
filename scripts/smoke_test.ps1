#!/usr/bin/env pwsh
# SensorGuard AI — smoke test script
# Usage: .\scripts\smoke_test.ps1 [-BaseUrl http://localhost:8000]
# Requires: curl or Invoke-WebRequest, jq (optional)

param(
    [string]$BaseUrl = "http://localhost:8000"
)

$ErrorActionPreference = "Stop"
$failures = 0

function Check-Endpoint {
    param([string]$Name, [string]$Url, [int]$ExpectedStatus = 200)
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
        if ($resp.StatusCode -eq $ExpectedStatus) {
            Write-Host "  [PASS] $Name ($($resp.StatusCode))" -ForegroundColor Green
        } else {
            Write-Host "  [FAIL] $Name — expected $ExpectedStatus got $($resp.StatusCode)" -ForegroundColor Red
            $script:failures++
        }
    } catch {
        Write-Host "  [FAIL] $Name — $($_.Exception.Message)" -ForegroundColor Red
        $script:failures++
    }
}

Write-Host ""
Write-Host "SensorGuard AI — Smoke Test" -ForegroundColor Cyan
Write-Host "Base URL: $BaseUrl"
Write-Host "─────────────────────────────────────────"

# Health
Check-Endpoint "GET /health"               "$BaseUrl/health"

# OpenAPI docs (confirms routes registered)
Check-Endpoint "GET /docs"                 "$BaseUrl/docs"

# Stations
Check-Endpoint "GET /api/v1/stations"      "$BaseUrl/api/v1/stations"

# Latest observations
Check-Endpoint "GET /api/v1/observations/latest" "$BaseUrl/api/v1/observations/latest"

# Model status
Check-Endpoint "GET /api/v1/model/status"  "$BaseUrl/api/v1/model/status"

# Experiment list
Check-Endpoint "GET /api/v1/research/experiments" "$BaseUrl/api/v1/research/experiments"

# Forecasts (station 1, 1-hour horizon)
Check-Endpoint "GET /api/v1/forecast/1"    "$BaseUrl/api/v1/forecast/1?horizon_hours=1"

Write-Host "─────────────────────────────────────────"
if ($failures -eq 0) {
    Write-Host "All checks passed." -ForegroundColor Green
    exit 0
} else {
    Write-Host "$failures check(s) FAILED." -ForegroundColor Red
    exit 1
}
