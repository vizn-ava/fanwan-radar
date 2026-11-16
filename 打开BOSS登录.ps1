$ErrorActionPreference = 'Stop'
$candidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
)
$chrome = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) { throw 'Install Google Chrome or Microsoft Edge first.' }
$profileDir = Join-Path ([IO.Path]::GetTempPath()) 'fanwan-radar-chrome'
$port = if ($env:JOB_CDP_PORT) { [int]$env:JOB_CDP_PORT } else { 9222 }
$existing = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Port $port is already in use. Check that it is the intended dedicated browser."
    exit 0
}
# Chrome manages its own temporary profile. No main-profile or Cookie copying.
Start-Process -FilePath $chrome -ArgumentList @(
    "--remote-debugging-port=$port",
    '--remote-debugging-address=127.0.0.1',
    "--remote-allow-origins=http://localhost:$port,http://127.0.0.1:$port",
    "--user-data-dir=`"$profileDir`"",
    '--no-first-run',
    'https://www.zhipin.com/'
)
Write-Host 'Log in personally in this dedicated browser, then use BOSS collection in the demo.'
Write-Host 'No cookies are exported. Your normal browser profile is not accessed.'
Write-Host "Temporary browser profile: $profileDir"
