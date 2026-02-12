#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Restart MINDEX services on VM 192.168.0.189

.DESCRIPTION
    Connects via SSH and restarts PostgreSQL, Redis, Qdrant, and MINDEX API containers.
    Fixes common database connection issues.

.PARAMETER CheckOnly
    Only check status, don't restart anything

.EXAMPLE
    .\restart-mindex-vm.ps1
    Restart all MINDEX services

.EXAMPLE
    .\restart-mindex-vm.ps1 -CheckOnly
    Check status only
#>

param(
    [switch]$CheckOnly
)

$VM_HOST = "192.168.0.189"
$VM_USER = "mycosoft"
$MINDEX_DIR = "/home/mycosoft/mindex"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "MINDEX VM Restart Script" -ForegroundColor Cyan
Write-Host "Target: $VM_HOST" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# Check connectivity
Write-Host "[1/7] Testing VM connectivity..." -ForegroundColor Yellow
$canConnect = Test-NetConnection -ComputerName $VM_HOST -Port 22 -InformationLevel Quiet -WarningAction SilentlyContinue
if (-not $canConnect) {
    Write-Host "❌ Cannot reach VM $VM_HOST on port 22" -ForegroundColor Red
    Write-Host "   Check: ping $VM_HOST" -ForegroundColor Gray
    exit 1
}
Write-Host "✅ VM is reachable`n" -ForegroundColor Green

# Check MINDEX API
Write-Host "[2/7] Checking MINDEX API health..." -ForegroundColor Yellow
try {
    $health = Invoke-RestMethod -Uri "http://${VM_HOST}:8000/api/mindex/health" -TimeoutSec 5 -ErrorAction Stop
    Write-Host "✅ API Status: $($health.status)" -ForegroundColor Green
    Write-Host "   DB Status: $($health.db)" -ForegroundColor $(if ($health.db -eq "ok") { "Green" } else { "Red" })
    Write-Host "   Version: $($health.version)`n" -ForegroundColor Gray
    
    if ($health.db -eq "ok" -and -not $CheckOnly) {
        Write-Host "✅ Database is already connected! No restart needed." -ForegroundColor Green
        Write-Host "`nTesting data endpoint..." -ForegroundColor Yellow
        try {
            $obs = Invoke-RestMethod -Uri "http://${VM_HOST}:8000/api/mindex/observations?limit=3" -TimeoutSec 5
            Write-Host "✅ Observations endpoint working: $($obs.total) total records" -ForegroundColor Green
        } catch {
            Write-Host "⚠️  Observations endpoint error: $($_.Exception.Message)" -ForegroundColor Yellow
        }
        exit 0
    }
    
    if ($health.db -ne "ok") {
        Write-Host "❌ Database connection is failing - restart required`n" -ForegroundColor Red
    }
} catch {
    Write-Host "❌ MINDEX API unreachable: $($_.Exception.Message)`n" -ForegroundColor Red
}

if ($CheckOnly) {
    Write-Host "`nCheck complete. Use without -CheckOnly to restart services." -ForegroundColor Cyan
    exit 0
}

# SSH and restart
Write-Host "[3/7] SSH into VM and checking containers..." -ForegroundColor Yellow
Write-Host "⚠️  You will be prompted for SSH password" -ForegroundColor Yellow
Write-Host "   (Or set VM_PASSWORD environment variable)`n" -ForegroundColor Gray

$commands = @(
    "echo '=== Docker Containers ==='",
    "docker ps -a --filter name=mindex --format '{{.Names}}: {{.Status}}'",
    "",
    "echo '=== Restarting PostgreSQL ==='",
    "cd $MINDEX_DIR && docker compose restart mindex-postgres",
    "sleep 5",
    "",
    "echo '=== Restarting Redis ==='",
    "cd $MINDEX_DIR && docker compose restart mindex-redis",
    "sleep 2",
    "",
    "echo '=== Restarting Qdrant ==='",
    "cd $MINDEX_DIR && docker compose restart mindex-qdrant",
    "sleep 2",
    "",
    "echo '=== Restarting MINDEX API ==='",
    "cd $MINDEX_DIR && docker compose restart mindex-api",
    "sleep 10",
    "",
    "echo '=== Container Status After Restart ==='",
    "docker ps --filter name=mindex --format '{{.Names}}: {{.Status}}'",
    "",
    "echo '=== Health Check ==='",
    "curl -s http://localhost:8000/api/mindex/health",
    "",
    "echo ''",
    "echo '=== Test Observations Endpoint ==='",
    "curl -s 'http://localhost:8000/api/mindex/observations?limit=3' | head -100"
)

$sshCommand = $commands -join " && "

Write-Host "Executing SSH commands...`n" -ForegroundColor Cyan
ssh "${VM_USER}@${VM_HOST}" $sshCommand

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "[4/7] Verifying from local machine..." -ForegroundColor Yellow
Start-Sleep -Seconds 2

try {
    $health = Invoke-RestMethod -Uri "http://${VM_HOST}:8000/api/mindex/health" -TimeoutSec 10
    Write-Host "✅ API Status: $($health.status)" -ForegroundColor Green
    Write-Host "   DB Status: $($health.db)" -ForegroundColor $(if ($health.db -eq "ok") { "Green" } else { "Red" })
    
    if ($health.db -eq "ok") {
        Write-Host "`n[5/7] Testing data endpoints..." -ForegroundColor Yellow
        
        $obs = Invoke-RestMethod -Uri "http://${VM_HOST}:8000/api/mindex/observations?limit=3" -TimeoutSec 10
        Write-Host "✅ Observations: $($obs.total) total" -ForegroundColor Green
        
        $stats = Invoke-RestMethod -Uri "http://${VM_HOST}:8000/api/mindex/stats" -TimeoutSec 10
        Write-Host "✅ Taxa: $($stats.total_taxa)" -ForegroundColor Green
        Write-Host "✅ Observations: $($stats.total_observations)`n" -ForegroundColor Green
        
        Write-Host "[6/7] Testing website integration..." -ForegroundColor Yellow
        $websiteHealth = Invoke-RestMethod -Uri "http://localhost:3010/api/natureos/mindex/health" -TimeoutSec 10
        Write-Host "✅ Website → MINDEX: $($websiteHealth.status)" -ForegroundColor Green
        Write-Host "   API: $($websiteHealth.api), DB: $($websiteHealth.database)`n" -ForegroundColor Gray
        
        Write-Host "[7/7] MINDEX is now FULLY OPERATIONAL! 🚀" -ForegroundColor Green
        Write-Host "`nPages to test:" -ForegroundColor Cyan
        Write-Host "  • http://localhost:3010/natureos/mindex" -ForegroundColor Gray
        Write-Host "  • http://localhost:3010/natureos/mindex/explorer" -ForegroundColor Gray
        Write-Host "  • http://localhost:3010/mindex" -ForegroundColor Gray
    } else {
        Write-Host "`n❌ Database still not connected after restart" -ForegroundColor Red
        Write-Host "   Check logs: ssh ${VM_USER}@${VM_HOST} 'docker logs mindex-api --tail 50'" -ForegroundColor Yellow
    }
} catch {
    Write-Host "`n❌ MINDEX API still unreachable: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "   Manual fix required - see docs/FIX_MINDEX_DB_CONNECTION_FEB11_2026.md" -ForegroundColor Yellow
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Script complete" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
