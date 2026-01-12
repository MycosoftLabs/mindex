# MINDEX ETL Sync Monitor
# Shows real-time progress of the data sync

Write-Host "`n=== MINDEX Full Fungi Data Sync Monitor ===" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop monitoring`n" -ForegroundColor Yellow

while ($true) {
    Clear-Host
    Write-Host "=== MINDEX Database Status ===" -ForegroundColor Cyan
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')]`n" -ForegroundColor Gray
    
    # Get database counts
    $taxaQuery = "SELECT source, count(*) as count FROM core.taxon GROUP BY source ORDER BY count DESC;"
    $obsQuery = "SELECT source, count(*) as count FROM obs.observation GROUP BY source ORDER BY count DESC;"
    
    Write-Host "Taxa by Source:" -ForegroundColor Yellow
    docker exec mindex-postgres psql -U mindex -d mindex -c $taxaQuery 2>&1 | Select-Object -Skip 2 -First 10
    
    Write-Host "`nObservations by Source:" -ForegroundColor Yellow
    docker exec mindex-postgres psql -U mindex -d mindex -c $obsQuery 2>&1 | Select-Object -Skip 2 -First 10
    
    Write-Host "`n=== Sync Logs (last 10 lines) ===" -ForegroundColor Cyan
    docker logs mindex-full-sync 2>&1 | Select-Object -Last 10
    
    Write-Host "`n=== Container Status ===" -ForegroundColor Cyan
    docker ps --filter "name=mindex" --format "table {{.Names}}\t{{.Status}}\t{{.CreatedAt}}"
    
    Write-Host "`nRefreshing in 30 seconds... (Ctrl+C to exit)" -ForegroundColor Gray
    Start-Sleep -Seconds 30
}
