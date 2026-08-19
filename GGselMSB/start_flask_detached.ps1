$ErrorActionPreference = 'Stop'
$projectDir = 'C:\Users\Atreum\Desktop\MSBWorkshop\GGselMSB'
$logFile = Join-Path $projectDir 'logs\app.log'

# Убедимся, что logs\ существует
if (-not (Test-Path (Join-Path $projectDir 'logs'))) {
    New-Item -ItemType Directory -Path (Join-Path $projectDir 'logs') | Out-Null
}

# Start-Process с -WindowStyle Hidden отвязывает процесс от родителя.
# -RedirectStandardOutput / -RedirectStandardError отвязывают потоки.
# После этого bash-tool wrapper может умереть, Flask продолжит жить.
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = (Get-Command python).Source
$psi.Arguments = 'app.py'
$psi.WorkingDirectory = $projectDir
$psi.UseShellExecute = $true
$psi.WindowStyle = 'Hidden'

$proc = [System.Diagnostics.Process]::Start($psi)
Write-Host "Flask started as PID $($proc.Id)"

# Ждём 4 секунды и проверяем, что отвечает
Start-Sleep -Seconds 4
try {
    $resp = Invoke-WebRequest -Uri 'http://127.0.0.1:5000/' -TimeoutSec 5 -UseBasicParsing
    Write-Host "Flask responded: $($resp.StatusCode)"
} catch {
    Write-Host "Flask not up yet: $($_.Exception.Message)"
    Get-Content $logFile -Tail 20 -ErrorAction SilentlyContinue
}
