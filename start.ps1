$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$calendarPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $calendarPython)) {
    Write-Host 'Создайте окружение: py -3 -m venv .venv'
    exit 1
}
& $calendarPython main.py
exit $LASTEXITCODE
