[CmdletBinding()]
param(
    [switch]$Run
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$repoPrefix = $repoRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$taskRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot '.formspace-chart-revision-qualification-20261001'))
$dataDir = Join-Path $taskRoot 'data'
$logFile = Join-Path $taskRoot 'postgres.log'
$python = 'C:\Users\Owner1\.codex\worktrees\brief09-shared-retention\runtime\Scripts\python.exe'
$pgBin = 'C:\Users\Owner1\.codex\worktrees\brief09-shared-retention\runtime\postgres\pgsql\bin'
$initdb = Join-Path $pgBin 'initdb.exe'
$pgCtl = Join-Path $pgBin 'pg_ctl.exe'
$createdb = Join-Path $pgBin 'createdb.exe'
$postgres = Join-Path $pgBin 'postgres.exe'
$website = 'C:\Users\Owner1\.codex\worktrees\formspace-recovered-oct03\website'
$tsx = 'D:\Users\admin2\Desktop\MYCOSOFT\CODE\WEBSITE\website\node_modules\tsx\dist\cli.mjs'
$dbName = 'retention_fixture_formspacechart_20261001'
$expectedMindexBranch = 'codex/formspace-durable-jobs'
$requiredMindexTestCommit = '3e4e83c90b7248358e959a7b651d5210565a4452'
$expectedWebsiteBranch = 'codex/formspace-durable-experiments'
$expectedWebsiteHead = 'b90a9d2299a610413c83360d59257c1f23348566'

if (-not $taskRoot.StartsWith($repoPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Refusing a fixture path outside this MINDEX worktree.'
}
foreach ($path in @($python, $initdb, $pgCtl, $createdb, $postgres, $website, $tsx)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Required local fixture dependency is missing: $path" }
}

$mindexBranch = (& git -C $repoRoot branch --show-current | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $mindexBranch -ne $expectedMindexBranch) { throw "Unexpected MINDEX branch: $mindexBranch" }
& git -C $repoRoot merge-base --is-ancestor $requiredMindexTestCommit HEAD
if ($LASTEXITCODE -ne 0) { throw "Required signed-JWT test commit is absent: $requiredMindexTestCommit" }
& git -C $repoRoot diff --quiet HEAD -- migrations/20261001_formspace_durable.sql mindex_api/formspace tests/test_formspace_durable_api_postgres.py tests/test_retention_api.py tests/test_retention_postgres.py
if ($LASTEXITCODE -ne 0) { throw 'FormSpace/retention code or signed-JWT tests have uncommitted changes; refusing to run a different candidate.' }
$websiteBranch = (& git -C $website branch --show-current | Out-String).Trim()
$websiteHead = (& git -C $website rev-parse HEAD | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $websiteBranch -ne $expectedWebsiteBranch -or $websiteHead -ne $expectedWebsiteHead) {
    throw "Unexpected website worker checkout: $websiteBranch at $websiteHead"
}
$node = (Get-Command node -ErrorAction Stop).Source
$nodeVersion = (& $node --version | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Node.js is unavailable for the bounded TypeScript worker subprocess.' }
$tsxVersion = (& $node $tsx --version | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $tsxVersion -notmatch '4\.22\.4') { throw "Unexpected tsx CLI version: $tsxVersion" }

$pythonVersions = & $python -c "import sys,jwt,pytest,pytest_asyncio,fastapi,uvicorn,sqlalchemy,asyncpg,httpx,cryptography; print('python='+sys.version.split()[0]+' PyJWT='+jwt.__version__+' pytest='+pytest.__version__+' FastAPI='+fastapi.__version__+' SQLAlchemy='+sqlalchemy.__version__+' asyncpg='+asyncpg.__version__)"
if ($LASTEXITCODE -ne 0) { throw 'The existing Brief 09 Python runtime failed its signed-JWT test dependency check.' }
$pgVersion = (& $postgres --version | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $pgVersion -notmatch 'PostgreSQL\) 17\.11$') {
    throw "Expected the existing PostgreSQL 17.11 binary, received: $pgVersion"
}

$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = ([Net.IPEndPoint]$listener.LocalEndpoint).Port
$listener.Stop()
$occupied = @(Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue)
if ($occupied.Count -gt 0) { throw "Selected PostgreSQL port $port became occupied; rerun preflight." }

Write-Output "Preflight passed: $($pythonVersions.Trim())"
Write-Output "PostgreSQL: $pgVersion; Node: $nodeVersion; tsx: $tsxVersion; proposed loopback port: $port"
Write-Output "New fixture only: $dataDir; database: $dbName"
Write-Output 'This preflight did not create a directory, database, or service.'

if (-not $Run) { return }
if ($env:MYCOSOFT_RESOURCE_SLOT_CONFIRMED -ne 'true' -or [string]::IsNullOrWhiteSpace($env:MYCOSOFT_RESOURCE_SLOT_ID)) {
    throw 'Refusing runtime startup without the coordinator-granted slot marker and slot ID.'
}
if (Test-Path -LiteralPath $taskRoot) {
    throw "Refusing to reuse an existing fixture path; preserve it and choose a new task-owned directory: $taskRoot"
}

$envNames = @('RETENTION_TEST_ALLOW_DISPOSABLE','RETENTION_TEST_DSN','FORMSPACE_WEBSITE_CHECKOUT','FORMSPACE_TSX_CLI')
$previousEnv = @{}
foreach ($name in $envNames) { $previousEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$serverStarted = $false
$exitCode = 0
try {
    New-Item -ItemType Directory -Path $taskRoot | Out-Null
    & $initdb -D $dataDir -U postgres --auth-local=trust --auth-host=trust --encoding=UTF8 --locale=C --no-instructions
    if ($LASTEXITCODE -ne 0) { throw "initdb failed with exit code $LASTEXITCODE" }

    $serverOptions = "-h 127.0.0.1 -p $port -c listen_addresses=127.0.0.1 -c max_connections=24 -c shared_buffers=128MB"
    $serverStarted = $true
    & $pgCtl -D $dataDir -l $logFile -w -o $serverOptions start
    if ($LASTEXITCODE -ne 0) { throw "pg_ctl start failed with exit code $LASTEXITCODE" }

    & $createdb --host=127.0.0.1 --port=$port --username=postgres $dbName
    if ($LASTEXITCODE -ne 0) { throw "createdb failed with exit code $LASTEXITCODE" }

    $env:RETENTION_TEST_ALLOW_DISPOSABLE = '1'
    $env:RETENTION_TEST_DSN = "postgresql://postgres@127.0.0.1:$port/$dbName"
    $env:FORMSPACE_WEBSITE_CHECKOUT = $website
    $env:FORMSPACE_TSX_CLI = $tsx
    Push-Location $repoRoot
    try {
        & $python -m pytest -q tests/test_formspace_durable_api_postgres.py
        $exitCode = $LASTEXITCODE
    }
    finally { Pop-Location }
}
finally {
    if ($serverStarted) {
        & $pgCtl -D $dataDir -m fast -w stop
        if ($LASTEXITCODE -ne 0) { Write-Warning 'The task-owned PostgreSQL stop command failed; inspect only this fixture log and PID.' }
    }
    foreach ($name in $envNames) { [Environment]::SetEnvironmentVariable($name, $previousEnv[$name], 'Process') }
    if (Test-Path -LiteralPath $logFile) { Write-Output "Preserved task-owned PostgreSQL log: $logFile" }
    Write-Output "Preserved task-owned fixture files: $taskRoot"
}
exit $exitCode
