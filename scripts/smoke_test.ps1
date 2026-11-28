# SensorGuard AI - API Smoke Test Script
# Usage: .\scripts\smoke_test.ps1 [-BaseUrl http://127.0.0.1:8000]

param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [int]$StationId = 1
)

$ErrorActionPreference = "Stop"
$script:failures = 0

function Test-Endpoint {
    param([string]$Name, [string]$Url, [int]$ExpectedStatus = 200)
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
        if ($resp.StatusCode -eq $ExpectedStatus) {
            Write-Host "  [PASS] $Name ($($resp.StatusCode))" -ForegroundColor Green
        } else {
            Write-Host "  [FAIL] $Name - expected $ExpectedStatus got $($resp.StatusCode)" -ForegroundColor Red
            $script:failures++
        }
    } catch {
        Write-Host "  [FAIL] $Name - $($_.Exception.Message)" -ForegroundColor Red
        $script:failures++
    }
}

Write-Host ""
Write-Host "SensorGuard AI - Smoke Test" -ForegroundColor Cyan
Write-Host "Base URL: $BaseUrl"
Write-Host "-----------------------------------------"

Test-Endpoint "GET /health"                                  "$BaseUrl/health"
Test-Endpoint "GET /health/ready"                            "$BaseUrl/health/ready"
Test-Endpoint "GET /docs"                                    "$BaseUrl/docs"
Test-Endpoint "GET /api/v1/model/status"                     "$BaseUrl/api/v1/model/status"
Test-Endpoint "GET /api/v1/stations"                         "$BaseUrl/api/v1/stations"
Test-Endpoint "GET /api/v1/stations/$StationId/observations" "$BaseUrl/api/v1/stations/$StationId/observations?limit=24"
Test-Endpoint "GET /api/v1/stations/$StationId/forecasts"    "$BaseUrl/api/v1/stations/$StationId/forecasts?limit=24"
Test-Endpoint "GET /api/v1/stations/$StationId/reliability"  "$BaseUrl/api/v1/stations/$StationId/reliability"
Test-Endpoint "GET /api/v1/research/experiments"             "$BaseUrl/api/v1/research/experiments"

try {
    $res = Invoke-WebRequest -Uri "$BaseUrl/api/v1/admin/ingest" -Method Post -ContentType "application/json" -Body '{"days":1}' -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
    Write-Host "  [FAIL] POST /api/v1/admin/ingest unauthenticated returned $($res.StatusCode)" -ForegroundColor Red
    $script:failures++
} catch {
    $code = [int]$_.Exception.Response.StatusCode
    if ($code -in @(401, 422, 503)) {
        Write-Host "  [PASS] POST /api/v1/admin/ingest rejected unauthenticated ($code)" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] POST /api/v1/admin/ingest unexpected status $code" -ForegroundColor Red
        $script:failures++
    }
}

Write-Host "-----------------------------------------"
if ($script:failures -eq 0) {
    Write-Host "All checks passed." -ForegroundColor Green
    exit 0
} else {
    Write-Host "$($script:failures) check(s) FAILED." -ForegroundColor Red
    exit 1
}
