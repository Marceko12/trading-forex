$ErrorActionPreference = 'Stop'
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'No se encontró py launcher. Instale Python 3.12.' }
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Push-Location frontend
npm install
Pop-Location
Write-Host 'Instalación completada. Use scripts/start-backend.ps1 y scripts/start-frontend.ps1 en terminales separadas.'
