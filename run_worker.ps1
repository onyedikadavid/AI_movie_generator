# Keeps the Celery worker running through network drops: if it exits (e.g. Upstash unreachable), wait and start it again.
# Usage (PowerShell, in this folder):   .\run_worker.ps1
while ($true) {
    python -m celery -A app.core.celery_app.celery_app worker --loglevel=info --pool=solo
    Write-Host "Worker stopped (network down?). Restarting in 15 s - press Ctrl+C twice to quit." -ForegroundColor Yellow
    Start-Sleep -Seconds 15
}
