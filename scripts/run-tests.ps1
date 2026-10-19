$ErrorActionPreference = 'Stop'
$env:PYTHONPATH = (Join-Path $PSScriptRoot '..\backend')
if (Test-Path '.\.venv\Scripts\python.exe') { & .\.venv\Scripts\python.exe -m pytest backend\tests -q }
else { python -m pytest backend\tests -q }
