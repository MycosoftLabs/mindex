#!/usr/bin/env pwsh
# Script to diagnose and push code to GitHub

Write-Host "=== Checking Git Status ===" -ForegroundColor Cyan
git status

Write-Host "`n=== Current Branch ===" -ForegroundColor Cyan
git branch

Write-Host "`n=== Files in working directory ===" -ForegroundColor Cyan
Get-ChildItem -Recurse -File | Where-Object { $_.FullName -notlike '*\.git\*' } | Select-Object -First 10 | ForEach-Object { Write-Host $_.FullName.Replace((Get-Location).Path + '\', '') }

Write-Host "`n=== Files tracked by git ===" -ForegroundColor Cyan
git ls-files | Select-Object -First 10

Write-Host "`n=== Recent commits ===" -ForegroundColor Cyan
git log --oneline -5

Write-Host "`n=== Remote status ===" -ForegroundColor Cyan
git remote -v

Write-Host "`n=== Adding all files ===" -ForegroundColor Cyan
git add -A
git status --short

Write-Host "`n=== Committing changes ===" -ForegroundColor Cyan
git commit -m "Add all MINDEX project files" 2>&1

Write-Host "`n=== Pushing to GitHub ===" -ForegroundColor Cyan
git push -u origin main 2>&1

Write-Host "`n=== Final status ===" -ForegroundColor Cyan
git status
