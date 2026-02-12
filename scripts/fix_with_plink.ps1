#!/usr/bin/env pwsh
# Fix MINDEX using plink (PuTTY) with password

param(
    [string]$Password = "Mycosoft2024!"
)

$VM_HOST = "192.168.0.189"
$VM_USER = "mycosoft"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "MINDEX Database Fix via Plink/PuTTY" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# Check if plink is available
$plinkPath = Get-Command plink -ErrorAction SilentlyContinue
if (-not $plinkPath) {
    Write-Host "[WARNING] plink not found - trying alternative method`n" -ForegroundColor Yellow
    
    # Try using SSH with password from env
    $env:SSHPASS = $Password
    
    Write-Host "[1] Checking containers..." -ForegroundColor Yellow
    $cmd1 = "cd /home/mycosoft/mindex && docker compose ps"
    echo "y" | ssh -o StrictHostKeyChecking=no $VM_USER@$VM_HOST $cmd1
    
    Write-Host "`n[2] Checking database tables..." -ForegroundColor Yellow
    $cmd2 = "docker exec mindex-postgres psql -U mindex -d mindex -c 'SELECT COUNT(*) FROM core.taxon;'"
    echo "y" | ssh $VM_USER@$VM_HOST $cmd2
    
    Write-Host "`n[3] Restarting containers..." -ForegroundColor Yellow
    $cmd3 = "cd /home/mycosoft/mindex && docker compose restart && sleep 10 && curl http://localhost:8000/api/mindex/health"
    echo "y" | ssh $VM_USER@$VM_HOST $cmd3
    
} else {
    Write-Host "[INFO] Using plink for password authentication`n" -ForegroundColor Green
    
    # Use plink with password
    Write-Host "[1] Checking containers..." -ForegroundColor Yellow
    echo $Password | plink -batch -pw $Password $VM_USER@$VM_HOST "cd /home/mycosoft/mindex && docker compose ps"
    
    Write-Host "`n[2] Checking database..." -ForegroundColor Yellow
    echo $Password | plink -batch -pw $Password $VM_USER@$VM_HOST "docker exec mindex-postgres psql -U mindex -d mindex -c 'SELECT COUNT(*) FROM core.taxon;'"
    
    Write-Host "`n[3] Restarting..." -ForegroundColor Yellow
    echo $Password | plink -batch -pw $Password $VM_USER@$VM_HOST "cd /home/mycosoft/mindex && docker compose restart"
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Done - Check results above" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
