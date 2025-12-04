# Comprehensive script to ensure all code is pushed to GitHub
$ErrorActionPreference = "Stop"
$outputFile = "push_output.txt"

function Write-Output-And-File {
    param($message)
    Write-Host $message
    Add-Content -Path $outputFile -Value $message
}

Write-Output-And-File "=== Starting GitHub push process ==="
Write-Output-And-File ""

# Check current directory
Write-Output-And-File "Current directory: $(Get-Location)"
Write-Output-And-File ""

# Check git status
Write-Output-And-File "=== Git Status ==="
$status = git status 2>&1 | Out-String
Write-Output-And-File $status
Write-Output-And-File ""

# Check current branch
Write-Output-And-File "=== Current Branch ==="
$branch = git branch --show-current 2>&1 | Out-String
Write-Output-And-File "Branch: $branch"
Write-Output-And-File ""

# Check remote
Write-Output-And-File "=== Remote Configuration ==="
$remote = git remote -v 2>&1 | Out-String
Write-Output-And-File $remote
Write-Output-And-File ""

# Count files in directory
Write-Output-And-File "=== File Counts ==="
$allFiles = (Get-ChildItem -Recurse -File | Where-Object { $_.FullName -notlike '*\.git\*' }).Count
$trackedFiles = (git ls-files 2>&1 | Measure-Object -Line).Lines
Write-Output-And-File "Files in directory: $allFiles"
Write-Output-And-File "Files tracked by git: $trackedFiles"
Write-Output-And-File ""

# Add all files
Write-Output-And-File "=== Adding all files ==="
git add -A 2>&1 | Out-String | ForEach-Object { Write-Output-And-File $_ }
Write-Output-And-File ""

# Show what's staged
Write-Output-And-File "=== Staged files ==="
$staged = git diff --cached --name-only 2>&1 | Out-String
Write-Output-And-File $staged
Write-Output-And-File ""

# Commit if there are changes
Write-Output-And-File "=== Committing changes ==="
$commitOutput = git commit -m "Add all MINDEX project files - API, ETL, migrations, tests, and documentation" 2>&1 | Out-String
Write-Output-And-File $commitOutput
Write-Output-And-File ""

# Push to GitHub
Write-Output-And-File "=== Pushing to GitHub ==="
$pushOutput = git push -u origin main 2>&1 | Out-String
Write-Output-And-File $pushOutput
Write-Output-And-File ""

# Final status
Write-Output-And-File "=== Final Git Status ==="
$finalStatus = git status 2>&1 | Out-String
Write-Output-And-File $finalStatus
Write-Output-And-File ""

Write-Output-And-File "=== Process Complete ==="
