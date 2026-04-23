# Start both backend and frontend in development mode on Windows.
# Backend: uvicorn on port 8000 (with hot reload)
# Frontend: Vite dev server on port 3000 (proxies /api and /ws to backend)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Add uv to PATH if installed via official installer
$uvBin = "$env:USERPROFILE\.local\bin"
if ((Test-Path "$uvBin\uvx.exe") -and -not $env:PATH.Contains($uvBin)) {
    $env:PATH = "$uvBin;$env:PATH"
}

# Load environment variables from .env
if (Test-Path ".env") {
    Get-Content ".env" | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
        $name, $value = $_ -split '=', 2
        [System.Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
    }
}

# Activate virtual environment
if (Test-Path ".venv\Scripts\Activate.ps1") {
    . ".venv\Scripts\Activate.ps1"
}

# Ensure frontend dependencies are installed
if (-not (Test-Path "frontend\node_modules")) {
    Write-Host "Installing frontend dependencies..."
    Push-Location frontend
    npm install
    Pop-Location
}

Write-Host "Starting backend (uvicorn :8000) + frontend (vite :3000)..."

# --- Cleanup existing processes ---
Write-Host "Checking for existing processes..."

# Kill existing backend (uvicorn main_server:app)
$backendPids = Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -match "uvicorn main_server:app"
}
if ($backendPids) {
    Write-Host "  Killing backend processes: $($backendPids.Id -join ', ')"
    $backendPids | Stop-Process -Force
    Start-Sleep -Seconds 1
    Write-Host "  Backend stopped."
} else {
    Write-Host "  No existing backend process found."
}

# Kill existing frontend (Node process on port 3000)
$frontendPids = Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    Where-Object { $_ -gt 4 } |
    ForEach-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue } |
    Where-Object { $_.Name -match 'node' }
if ($frontendPids) {
    Write-Host "  Killing frontend processes on port 3000: $($frontendPids.Id -join ', ')"
    $frontendPids | Stop-Process -Force
    Start-Sleep -Seconds 1
    Write-Host "  Frontend stopped."
} else {
    Write-Host "  No existing frontend process found."
}

# Use concurrently to run both processes
# --kill-others-on-fail: if one fails, kill the other
# --handle-input: allow sending input to processes

# Resolve uvicorn path from virtual environment
$uvicorn = if (Test-Path ".venv\Scripts\uvicorn.exe") {
    ".\.venv\Scripts\uvicorn.exe"
} else {
    "uvicorn"
}

npx concurrently `
    --kill-others-on-fail `
    --handle-input `
    --names "API,WEB" `
    --prefix-colors "blue,green" `
    "$uvicorn main_server:app --host 0.0.0.0 --port 8000 --log-level info" `
    "cd frontend && npm run dev"
