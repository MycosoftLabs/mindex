[CmdletBinding()]
param(
    [switch]$Run,
    [switch]$TestSourceGuard
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
$requiredWebsiteSourceCommit = 'b90a9d2299a610413c83360d59257c1f23348566'
$websiteWorkerSourcePins = @{
    'scripts/formspace-worker.ts' = 'be77c43e4cfa52401747254a36f8f7348671b81aca5eb1c125e6b3978c09e976'
    'lib/formspace/durable-worker.ts' = 'f3b564baadab9f27ae0c25472b8b89550f91430eb66bbe4b02cd7fb98d29c545'
    'lib/formspace/durable-domain.ts' = '3b1793bbb655a8c39af281d81b16e086cfde1c05dce68ee212e67d8221cd09df'
    'lib/formspace/native-ssm.ts' = '4c04deeb5ab1dfbeb819840464767bcc628682111b4926b10cd3fe930bdc78dd'
}
$pytestTimeoutSeconds = 240
$nodeHeapLimitMiB = 384

function Assert-WebsiteWorkerSourcePins([string]$Root) {
    foreach ($relativePath in $websiteWorkerSourcePins.Keys) {
        $sourcePath = Join-Path $Root ($relativePath.Replace('/', [IO.Path]::DirectorySeparatorChar))
        if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
            throw "Reviewed worker source file is missing: $relativePath"
        }
        $actualHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -cne $websiteWorkerSourcePins[$relativePath]) {
            throw "Reviewed worker source hash mismatch for $relativePath ($actualHash)."
        }
    }
}

function Test-WebsiteWorkerSourceGuard {
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $scratch = [IO.Path]::GetFullPath((Join-Path $tempRoot ('formspace-source-guard-' + [Guid]::NewGuid().ToString('N'))))
    if (-not $scratch.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $scratch)) {
        throw 'Refusing an invalid or pre-existing source-guard test path.'
    }
    New-Item -ItemType Directory -Path $scratch | Out-Null
    try {
        foreach ($relativePath in $websiteWorkerSourcePins.Keys) {
            $copyPath = Join-Path $scratch ($relativePath.Replace('/', [IO.Path]::DirectorySeparatorChar))
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $copyPath) | Out-Null
            Copy-Item -LiteralPath (Join-Path $website ($relativePath.Replace('/', [IO.Path]::DirectorySeparatorChar))) -Destination $copyPath
        }
        Assert-WebsiteWorkerSourcePins $scratch
        $mutatedPath = Join-Path $scratch 'scripts/formspace-worker.ts'
        $bytes = [IO.File]::ReadAllBytes($mutatedPath)
        if ($bytes.Length -eq 0) { throw 'Worker entry source is unexpectedly empty.' }
        $bytes[0] = [byte]($bytes[0] -bxor 1)
        [IO.File]::WriteAllBytes($mutatedPath, $bytes)
        $rejected = $false
        try { Assert-WebsiteWorkerSourcePins $scratch } catch { $rejected = $true }
        if (-not $rejected) { throw 'The source guard accepted an edited worker entry.' }
        Write-Output 'Source guard self-test passed: matching worker closure accepted; one-byte executable-source edit rejected.'
    }
    finally {
        if ((Test-Path -LiteralPath $scratch) -and $scratch.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $scratch -Recurse -Force
        }
    }
}

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
if ($LASTEXITCODE -ne 0 -or $websiteBranch -ne $expectedWebsiteBranch) {
    throw "Unexpected website worker branch: $websiteBranch"
}
& git -C $website merge-base --is-ancestor $requiredWebsiteSourceCommit HEAD
if ($LASTEXITCODE -ne 0) { throw "Website checkout is not based on reviewed source $requiredWebsiteSourceCommit" }
Assert-WebsiteWorkerSourcePins $website
if ($TestSourceGuard) { Test-WebsiteWorkerSourceGuard }
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
Write-Output "Website source: branch $websiteBranch at $websiteHead; reviewed worker closure: $requiredWebsiteSourceCommit"
Write-Output "PostgreSQL: $pgVersion; Node: $nodeVersion; tsx: $tsxVersion; proposed loopback port: $port"
Write-Output "New fixture only: $dataDir; database: $dbName"
Write-Output "Bounded run limits: one pytest process, one PostgreSQL server (max_connections=12, shared_buffers=64MB, work_mem=4MB), one TS worker at a time (Node heap $nodeHeapLimitMiB MiB), pytest timeout $pytestTimeoutSeconds seconds, per-worker timeout 30 seconds."
Write-Output 'This preflight did not create a directory, database, or service.'

if (-not $Run) { return }
if ($env:MYCOSOFT_RESOURCE_SLOT_CONFIRMED -ne 'true' -or [string]::IsNullOrWhiteSpace($env:MYCOSOFT_RESOURCE_SLOT_ID)) {
    throw 'Refusing runtime startup without the coordinator-granted slot marker and slot ID.'
}
if (Test-Path -LiteralPath $taskRoot) {
    throw "Refusing to reuse an existing fixture path; preserve it and choose a new task-owned directory: $taskRoot"
}

$envNames = @('RETENTION_TEST_ALLOW_DISPOSABLE','RETENTION_TEST_DSN','FORMSPACE_WEBSITE_CHECKOUT','FORMSPACE_TSX_CLI','NODE_OPTIONS')
$previousEnv = @{}
foreach ($name in $envNames) { $previousEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$serverStarted = $false
$exitCode = 0
try {
    New-Item -ItemType Directory -Path $taskRoot | Out-Null
    & $initdb -D $dataDir -U postgres --auth-local=trust --auth-host=trust --encoding=UTF8 --locale=C --no-instructions
    if ($LASTEXITCODE -ne 0) { throw "initdb failed with exit code $LASTEXITCODE" }

    $serverOptions = "-h 127.0.0.1 -p $port -c listen_addresses=127.0.0.1 -c max_connections=12 -c shared_buffers=64MB -c work_mem=4MB"
    $serverStarted = $true
    & $pgCtl -D $dataDir -l $logFile -w -o $serverOptions start
    if ($LASTEXITCODE -ne 0) { throw "pg_ctl start failed with exit code $LASTEXITCODE" }

    & $createdb --host=127.0.0.1 --port=$port --username=postgres $dbName
    if ($LASTEXITCODE -ne 0) { throw "createdb failed with exit code $LASTEXITCODE" }

    $env:RETENTION_TEST_ALLOW_DISPOSABLE = '1'
    $env:RETENTION_TEST_DSN = "postgresql://postgres@127.0.0.1:$port/$dbName"
    $env:FORMSPACE_WEBSITE_CHECKOUT = $website
    $env:FORMSPACE_TSX_CLI = $tsx
    $env:NODE_OPTIONS = "--max-old-space-size=$nodeHeapLimitMiB"
    Push-Location $repoRoot
    try {
        $stdoutLog = Join-Path $taskRoot 'pytest.stdout.log'
        $stderrLog = Join-Path $taskRoot 'pytest.stderr.log'
        $testProcess = Start-Process -FilePath $python -ArgumentList @('-m', 'pytest', '-q', 'tests/test_formspace_durable_api_postgres.py') `
            -WorkingDirectory $repoRoot -PassThru -NoNewWindow -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
        if (-not $testProcess.WaitForExit($pytestTimeoutSeconds * 1000)) {
            & "$env:SystemRoot\System32\taskkill.exe" /PID $testProcess.Id /T /F | Out-Null
            if ($LASTEXITCODE -ne 0) { Write-Warning 'Timed out pytest process tree could not be fully terminated.' }
            $testProcess.WaitForExit()
            throw "Signed-JWT/API/worker test exceeded $pytestTimeoutSeconds seconds; only its pytest process tree was terminated."
        }
        $exitCode = $testProcess.ExitCode
        if (Test-Path -LiteralPath $stdoutLog) { Get-Content -LiteralPath $stdoutLog }
        if (Test-Path -LiteralPath $stderrLog) { Get-Content -LiteralPath $stderrLog }
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
