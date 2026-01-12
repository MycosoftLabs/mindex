# MINDEX Data Volume Query Script (PowerShell)
# Quick way to check database statistics

Write-Host "`n=== MINDEX Database Statistics ===" -ForegroundColor Cyan
Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')]`n" -ForegroundColor Gray

# Run the Python script
docker run --rm --network mindex_mindex-network `
  -e DATABASE_URL=postgresql://mindex:mindex@mindex-postgres:5432/mindex `
  mindex-api python /app/scripts/query_data_volume.py

# Optionally get JSON output
if ($args -contains "--json") {
    Write-Host "`n=== JSON Output ===" -ForegroundColor Cyan
    docker run --rm --network mindex_mindex-network `
      -e DATABASE_URL=postgresql://mindex:mindex@mindex-postgres:5432/mindex `
      mindex-api python /app/scripts/query_data_volume.py --json
}
