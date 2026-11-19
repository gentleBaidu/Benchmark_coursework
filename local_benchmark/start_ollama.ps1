# Starts Ollama using this project's model directory. Does not change user-wide settings.
$ErrorActionPreference = 'Stop'
$env:OLLAMA_MODELS = Join-Path $PSScriptRoot 'models'
$env:OLLAMA_HOST = '127.0.0.1:11434'
New-Item -ItemType Directory -Force -Path $env:OLLAMA_MODELS | Out-Null
Write-Host "Model directory: $env:OLLAMA_MODELS"
Write-Host 'Keep this window open. Run download and evaluation commands in another terminal.'
Write-Host 'If port 11434 is in use, quit the existing Ollama app/server and run this script again.'
& ollama serve
exit $LASTEXITCODE
