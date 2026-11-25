#!/usr/bin/env pwsh
# SensorGuard AI — API & Frontend smoke test script
# Usage: .\scripts\smoke_test.ps1 [-BaseUrl http://localhost:8000]

param(
    [string]$BaseUrl = "http://localhost:8000",
    [int]$StationId = 1
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

Check-Endpoint "GET /health"                                      "$BaseUrl/health"
Check-Endpoint "GET /health/ready"                                "$BaseUrl/health/ready"
Check-Endpoint "GET /docs"                                        "$BaseUrl/docs"
Check-Endpoint "GET /api/v1/model/status"                         "$BaseUrl/api/v1/model/status"
Check-Endpoint "GET /api/v1/stations"                             "$BaseUrl/api/v1/stations"
Check-Endpoint "GET /api/v1/stations/$StationId/observations"     "$BaseUrl/api/v1/stations/$StationId/observations?limit=24"
Check-Endpoint "GET /api/v1/stations/$StationId/forecasts"        "$BaseUrl/api/v1/stations/$StationId/forecasts?limit=24"
Check-Endpoint "GET /api/v1/stations/$StationId/reliability"      "$BaseUrl/api/v1/stations/$StationId/reliability"
Check-Endpoint "GET /api/v1/research/experiments"                 "$BaseUrl/api/v1/research/experiments"

# Verify fail-closed / auth rejection on protected admin endpoint without valid key
try {
    $res = Invoke-WebRequest -Uri "$BaseUrl/api/v1/admin/ingest" -Method Post -ContentType "application/json" -Body '{"days":1}' -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
    Write-Host "  [FAIL] POST /api/v1/admin/ingest unauthenticated returned $($res.StatusCode)" -ForegroundColor Red
    $failures++
} catch {
    $code = [int]$_.Exception.Response.StatusCode
    if ($code -in @(401, 422, 503)) {
        Write-Host "  [PASS] POST /api/v1/admin/ingest rejected unauthenticated ($code)" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] POST /api/v1/admin/ingest unexpected status $code" -ForegroundColor Red
        $failures++
    }
}

Write-Host "─────────────────────────────────────────"
if ($failures -eq 0) {
    Write-Host "All checks passed." -ForegroundColor Green
    exit 0
} else {
    Write-Host "$failures check(s) FAILED." -ForegroundColor Red
    exit 1
}
