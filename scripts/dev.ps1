# scripts/dev.ps1 — run backend (FastAPI) + frontend (Vite) in parallel.
# Stop with Ctrl+C.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$env:AOAI_DEV = '1'

Write-Host ""
Write-Host "  Starting AOAI Chat dev servers..." -ForegroundColor Cyan
Write-Host ""

$backend = Start-Job -Name 'aoai-backend' -ScriptBlock {
    Set-Location "$using:root\backend"
    $env:AOAI_DEV = '1'
    uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8765
}

$frontend = Start-Job -Name 'aoai-frontend' -ScriptBlock {
    Set-Location "$using:root\frontend"
    npm run dev
}

Write-Host "  Backend:  http://127.0.0.1:8765" -ForegroundColor Cyan
Write-Host "  Frontend: http://127.0.0.1:5173" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Press Ctrl+C to stop both." -ForegroundColor DarkGray
Write-Host ""

try {
    while ($backend.State -eq 'Running' -or $frontend.State -eq 'Running') {
        Receive-Job -Job $backend, $frontend
        Start-Sleep -Milliseconds 300
    }
} finally {
    Write-Host ""
    Write-Host "  Stopping dev servers..." -ForegroundColor Yellow
    Stop-Job -Job $backend, $frontend -ErrorAction SilentlyContinue
    Receive-Job -Job $backend, $frontend
    Remove-Job -Job $backend, $frontend -Force -ErrorAction SilentlyContinue
}
