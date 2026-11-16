param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) { & py -3 -m venv .venv } else { & python -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
$url = 'http://127.0.0.1:8765'
$expectedInfoJson = & $python -c "import hashlib,json,pathlib; from app.main import VERSION,BUILD_ID; print(json.dumps({'version':VERSION,'build':BUILD_ID,'identity':hashlib.sha256(str(pathlib.Path.cwd().resolve()).lower().encode()).hexdigest()[:12]}))"
if ($LASTEXITCODE -ne 0) { throw 'Application dependencies could not load. Reinstall requirements.txt.' }
$expectedInfo = $expectedInfoJson | ConvertFrom-Json
$expectedVersion = $expectedInfo.version
$expectedIdentity = $expectedInfo.identity
$expectedBuild = $expectedInfo.build
$ready = $false
$health = $null
try { $health = Invoke-RestMethod "$url/api/health" -TimeoutSec 2 } catch {}
if ($health -and $health.status -eq 'ok') {
    if ($health.version -ne $expectedVersion -or $health.instance_id -ne $expectedIdentity -or $health.build_id -ne $expectedBuild) {
        throw 'Port 8765 is running a different or older application build. Close that server window and run this launcher again; do not mix new pages with an old server. Saved data will not be deleted.'
    }
    $ready = $true
}
if (-not $ready) {
    Start-Process -FilePath $python -WorkingDirectory $PSScriptRoot -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8765')
    for ($attempt = 0; $attempt -lt 25; $attempt++) {
        try {
            $health = Invoke-RestMethod "$url/api/health" -TimeoutSec 2
            $ready = $health.status -eq 'ok' -and $health.version -eq $expectedVersion -and $health.instance_id -eq $expectedIdentity -and $health.build_id -eq $expectedBuild
        } catch {}
        if ($ready) { break }
        Start-Sleep -Milliseconds 400
    }
}
if (-not $ready) { throw 'The app did not start. Check the server window or port 8765.' }
if (-not $NoBrowser) { Start-Process $url }
Write-Host "Fanwan Radar is running at $url"
