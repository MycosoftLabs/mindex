[CmdletBinding()]
param(
    [switch]$Run,
    [switch]$TestSourceGuard,
    [switch]$TestProcessSupervisor,
    [switch]$InternalRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Remove ambient application/deployment credentials before any dependency imports
# or child process starts. Runtime-specific values are added explicitly below.
$qualificationEnvironmentAllowlist = @(
    'PATH','SystemRoot','WINDIR','SystemDrive','TEMP','TMP','USERPROFILE','HOMEDRIVE','HOMEPATH','APPDATA','LOCALAPPDATA',
    'ComSpec','PATHEXT','PSModulePath','PROCESSOR_ARCHITECTURE','PROCESSOR_LEVEL',
    'PROCESSOR_REVISION','NUMBER_OF_PROCESSORS','MYCOSOFT_RESOURCE_SLOT_CONFIRMED',
    'MYCOSOFT_RESOURCE_SLOT_ID','FORMSPACE_QUALIFICATION_DEADLINE_UTC',
    'RETENTION_TEST_ALLOW_DISPOSABLE','RETENTION_TEST_DSN','FORMSPACE_WEBSITE_CHECKOUT',
    'FORMSPACE_TSX_CLI'
)
$qualificationEnvironment = [Environment]::GetEnvironmentVariables('Process')
foreach ($environmentName in @($qualificationEnvironment.Keys)) {
    if ($environmentName -notin $qualificationEnvironmentAllowlist) {
        [Environment]::SetEnvironmentVariable([string]$environmentName, $null, 'Process')
    }
}
$nodeHeapLimitMiB = 384
$env:NODE_OPTIONS = "--max-old-space-size=$nodeHeapLimitMiB"
$qualificationInvocationStart = [DateTimeOffset]::UtcNow

if (-not ('FormSpaceQualificationJob' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
public sealed class FormSpaceCaptureResult {
    public int ExitCode; public bool TimedOut; public bool OutputLimitExceeded; public bool Reaped;
}
public static class FormSpaceQualificationJob {
    [StructLayout(LayoutKind.Sequential)] struct BASIC_LIMITS {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags; public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit; public UIntPtr Affinity; public uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)] struct IO_COUNTERS {
        public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
        public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
    }
    [StructLayout(LayoutKind.Sequential)] struct EXTENDED_LIMITS {
        public BASIC_LIMITS BasicLimitInformation; public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct STARTUPINFO {
        public uint cb; public string lpReserved, lpDesktop, lpTitle;
        public uint dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
        public ushort wShowWindow, cbReserved2; public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
    }
    [StructLayout(LayoutKind.Sequential)] struct PROCESS_INFORMATION {
        public IntPtr hProcess, hThread; public uint dwProcessId, dwThreadId;
    }
    [StructLayout(LayoutKind.Sequential)] struct BASIC_PROCESS_ID_LIST {
        public uint NumberOfAssignedProcesses, NumberOfProcessIdsInList; public UIntPtr FirstProcessId;
    }
    sealed class CAPTURE_STATE { public long Total; public volatile bool Exceeded; }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref EXTENDED_LIMITS info, uint length);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool QueryInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint length, out uint returned);
    [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool result);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool CreateProcess(string app, StringBuilder command, IntPtr procAttr, IntPtr threadAttr, bool inherit, uint flags, IntPtr env, string cwd, ref STARTUPINFO startup, out PROCESS_INFORMATION info);
    [DllImport("kernel32.dll", SetLastError=true)] static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll", SetLastError=true)] static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetExitCodeProcess(IntPtr process, out uint exitCode);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateProcess(IntPtr process, uint exitCode);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
    const uint PROCESS_TERMINATE=0x0001, SYNCHRONIZE=0x00100000, CREATE_SUSPENDED=0x00000004, CREATE_NO_WINDOW=0x08000000;
    public static IntPtr CreateBoundedJob(ulong memoryBytes, uint maxProcesses) {
        return CreateBoundedJob(memoryBytes,maxProcesses,true);
    }
    public static IntPtr CreateBoundedJob(ulong memoryBytes, uint maxProcesses, bool killOnClose) {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        EXTENDED_LIMITS limits = new EXTENDED_LIMITS();
        limits.BasicLimitInformation.LimitFlags = 0x00000200 | 0x00000008 | (killOnClose ? 0x00002000u : 0u);
        limits.BasicLimitInformation.ActiveProcessLimit = maxProcesses; limits.JobMemoryLimit=(UIntPtr)memoryBytes;
        if (!SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(typeof(EXTENDED_LIMITS)))) {
            int error=Marshal.GetLastWin32Error(); CloseHandle(job); throw new Win32Exception(error);
        }
        return job;
    }
    public static void AssignCurrentProcess(IntPtr job) {
        uint pid=(uint)Process.GetCurrentProcess().Id; IntPtr proc=OpenProcess(0x0001|0x0100,false,pid);
        if (proc==IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        try { if (!AssignProcessToJobObject(job,proc)) throw new Win32Exception(Marshal.GetLastWin32Error()); }
        finally { CloseHandle(proc); }
    }
    public static IntPtr StartSuspendedInJob(IntPtr job, string executable, string arguments, string cwd, out uint pid) {
        STARTUPINFO si=new STARTUPINFO(); si.cb=(uint)Marshal.SizeOf(typeof(STARTUPINFO));
        PROCESS_INFORMATION pi; StringBuilder command=new StringBuilder("\""+executable+"\" "+arguments);
        if (!CreateProcess(executable,command,IntPtr.Zero,IntPtr.Zero,true,CREATE_SUSPENDED|CREATE_NO_WINDOW,IntPtr.Zero,cwd,ref si,out pi))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        try {
            bool alreadyInJob;
            if (!IsProcessInJob(pi.hProcess,job,out alreadyInJob)) throw new Win32Exception(Marshal.GetLastWin32Error());
            if (!alreadyInJob && !AssignProcessToJobObject(job,pi.hProcess)) throw new Win32Exception(Marshal.GetLastWin32Error());
            uint resumed=ResumeThread(pi.hThread); if (resumed==0xFFFFFFFF) throw new Win32Exception(Marshal.GetLastWin32Error());
            pid=pi.dwProcessId; return pi.hProcess;
        } catch { TerminateProcess(pi.hProcess, 1); WaitForSingleObject(pi.hProcess, 3000); CloseHandle(pi.hProcess); throw; }
        finally { CloseHandle(pi.hThread); }
    }
    public static uint Wait(IntPtr process, uint ms) { return WaitForSingleObject(process,ms); }
    public static uint ExitCode(IntPtr process) { uint code; if(!GetExitCodeProcess(process,out code)) throw new Win32Exception(Marshal.GetLastWin32Error()); return code; }
    public static uint CurrentPid() { return (uint)Process.GetCurrentProcess().Id; }
    public static uint[] JobPids(IntPtr job) {
        int cap=16, offset=8; IntPtr buffer=Marshal.AllocHGlobal(offset+cap*IntPtr.Size);
        try {
            uint returned;
            if (!QueryInformationJobObject(job,3,buffer,(uint)(offset+cap*IntPtr.Size),out returned)) throw new Win32Exception(Marshal.GetLastWin32Error());
            BASIC_PROCESS_ID_LIST list=(BASIC_PROCESS_ID_LIST)Marshal.PtrToStructure(buffer,typeof(BASIC_PROCESS_ID_LIST));
            if (list.NumberOfProcessIdsInList>cap) throw new InvalidOperationException("Job process list exceeded its configured capacity.");
            uint[] pids=new uint[list.NumberOfProcessIdsInList];
            for(int i=0;i<pids.Length;i++) pids[i]=(uint)Marshal.ReadIntPtr(buffer,offset+i*IntPtr.Size).ToInt64();
            return pids;
        } finally { Marshal.FreeHGlobal(buffer); }
    }
    public static void TerminateJobMembersExcept(IntPtr job, uint keepPid) {
        foreach(uint pid in JobPids(job)) {
            if(pid==keepPid) continue;
            IntPtr proc=OpenProcess(PROCESS_TERMINATE|SYNCHRONIZE,false,pid);
            if(proc==IntPtr.Zero) continue;
            try { if(!TerminateProcess(proc,137) && Marshal.GetLastWin32Error()!=5) throw new Win32Exception(Marshal.GetLastWin32Error()); }
            finally { CloseHandle(proc); }
        }
    }
    static void CopyBounded(Stream input, string path, CAPTURE_STATE state, long byteLimit) {
        byte[] buffer=new byte[8192];
        using(FileStream output=new FileStream(path,FileMode.Create,FileAccess.Write,FileShare.Read)) {
            int count;
            while((count=input.Read(buffer,0,buffer.Length))>0) {
                long previous=Interlocked.Add(ref state.Total,count)-count;
                int allowed=(int)Math.Max(0,Math.Min(count,byteLimit-previous));
                if(allowed>0) output.Write(buffer,0,allowed);
                if(allowed<count) state.Exceeded=true;
            }
            output.Flush(true);
        }
    }
    static bool KillProcessTree(Process process) {
        Process killer=null;
        try {
            ProcessStartInfo psi=new ProcessStartInfo(); psi.FileName=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"taskkill.exe");
            psi.Arguments="/PID "+process.Id+" /T /F"; psi.CreateNoWindow=true; psi.UseShellExecute=false;
            killer=Process.Start(psi);
            if(killer!=null && !killer.WaitForExit(3000)) { try{killer.Kill();}catch{}; killer.WaitForExit(1000); }
        } catch {}
        try {
            if(!process.WaitForExit(3000)) { process.Kill(); if(!process.WaitForExit(3000)) return false; }
            return true;
        } catch { return false; }
    }
    public static FormSpaceCaptureResult RunBoundedCapture(string executable, string arguments, string cwd, string stdoutPath, string stderrPath, int timeoutMs, long byteLimit) {
        FormSpaceCaptureResult result=new FormSpaceCaptureResult(); result.Reaped=true;
        ProcessStartInfo psi=new ProcessStartInfo(); psi.FileName=executable; psi.Arguments=arguments; psi.WorkingDirectory=cwd;
        psi.UseShellExecute=false; psi.CreateNoWindow=true; psi.RedirectStandardOutput=true; psi.RedirectStandardError=true;
        using(Process process=new Process()) {
            process.StartInfo=psi; if(!process.Start()) throw new InvalidOperationException("Could not start bounded pytest process.");
            CAPTURE_STATE state=new CAPTURE_STATE();
            Task stdout=Task.Run(()=>CopyBounded(process.StandardOutput.BaseStream,stdoutPath,state,byteLimit));
            Task stderr=Task.Run(()=>CopyBounded(process.StandardError.BaseStream,stderrPath,state,byteLimit));
            Stopwatch timer=Stopwatch.StartNew();
            while(true) {
                if(state.Exceeded) { result.OutputLimitExceeded=true; break; }
                int remaining=timeoutMs-(int)timer.ElapsedMilliseconds;
                if(remaining<=0) { result.TimedOut=true; break; }
                if(process.WaitForExit(Math.Min(50,remaining))) break;
            }
            if(result.OutputLimitExceeded || result.TimedOut) result.Reaped=KillProcessTree(process);
            else { result.ExitCode=process.ExitCode; }
            try { if(!Task.WaitAll(new Task[]{stdout,stderr},3000)) result.Reaped=false; }
            catch { result.Reaped=false; }
            if((result.OutputLimitExceeded || result.TimedOut) && !result.Reaped) return result;
            if(!result.OutputLimitExceeded && !result.TimedOut) result.ExitCode=process.ExitCode;
        }
        return result;
    }
    public static void CloseHandleSafe(IntPtr handle) { if(handle!=IntPtr.Zero) CloseHandle(handle); }
}
'@
}

function Test-QualificationProcessSupervisor {
    $hostExe = (Get-Process -Id $PID).Path
    $cwd = [IO.Path]::GetDirectoryName($hostExe)
    $job = [FormSpaceQualificationJob]::CreateBoundedJob(256MB, 4)
    $ok = $false
    try {
        [uint32]$dummyPid = 0
        $dummy = [FormSpaceQualificationJob]::StartSuspendedInJob($job, $hostExe, '-NoProfile -Command "Start-Sleep -Milliseconds 250"', $cwd, [ref]$dummyPid)
        if ([FormSpaceQualificationJob]::Wait($dummy, 5000) -ne 0) { throw 'Dummy supervised child did not exit within five seconds.' }
        if ([FormSpaceQualificationJob]::ExitCode($dummy) -ne 0) { throw 'Dummy supervised child returned a nonzero exit code.' }
        [FormSpaceQualificationJob]::CloseHandleSafe($dummy)

        $timed = [FormSpaceQualificationJob]::CreateBoundedJob(256MB, 4)
        try {
            [uint32]$timedPid = 0
            $slow = [FormSpaceQualificationJob]::StartSuspendedInJob($timed, $hostExe, '-NoProfile -Command "Start-Sleep -Seconds 30"', $cwd, [ref]$timedPid)
            if ([FormSpaceQualificationJob]::Wait($slow, 100) -eq 0) { throw 'Timeout control child unexpectedly exited before its deadline.' }
            [FormSpaceQualificationJob]::TerminateJobMembersExcept($timed, [FormSpaceQualificationJob]::CurrentPid())
            if ([FormSpaceQualificationJob]::Wait($slow, 5000) -ne 0) { throw 'Timed-out dummy child was not reaped within five seconds.' }
            [FormSpaceQualificationJob]::CloseHandleSafe($slow)
        } finally { [FormSpaceQualificationJob]::CloseHandleSafe($timed) }
        $ok = $true
    } finally { [FormSpaceQualificationJob]::CloseHandleSafe($job) }
    if (-not $ok) { throw 'Process supervisor controls failed.' }
    Write-Output 'Process supervisor controls passed: suspended assignment, hard-job setup, natural exit, timeout termination, and bounded reap.'
    $forcedRecovery = Get-PostgresCleanupOutcome -GracefulStopSucceeded $false -ForcedStopSucceeded $true -PostmasterStillAlive $false
    if (-not $forcedRecovery.Cleaned -or $forcedRecovery.QualificationSucceeded) {
        throw 'Cleanup policy accepted forced recovery as a successful qualification after graceful stop failure.'
    }
    $gracefulPass = Get-PostgresCleanupOutcome -GracefulStopSucceeded $true -ForcedStopSucceeded $false -PostmasterStillAlive $false
    if (-not $gracefulPass.Cleaned -or -not $gracefulPass.QualificationSucceeded) {
        throw 'Cleanup policy rejected a successful graceful stop.'
    }
    $unreaped = Get-PostgresCleanupOutcome -GracefulStopSucceeded $false -ForcedStopSucceeded $false -PostmasterStillAlive $true
    if ($unreaped.Cleaned -or $unreaped.QualificationSucceeded) { throw 'Cleanup policy accepted an unreaped PostgreSQL process.' }
    Write-Output 'Cleanup policy controls passed: forced recovery after graceful-stop failure fails qualification; graceful stop passes; unreaped process fails.'

    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $captureScratch = [IO.Path]::GetFullPath((Join-Path $tempRoot ('formspace-capture-guard-' + [Guid]::NewGuid().ToString('N'))))
    if (-not $captureScratch.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $captureScratch)) {
        throw 'Refusing an invalid or pre-existing capture-guard test path.'
    }
    New-Item -ItemType Directory -Path $captureScratch | Out-Null
    try {
        $captureA = Join-Path $captureScratch 'stdout.bin'
        $captureB = Join-Path $captureScratch 'stderr.bin'
        [IO.File]::WriteAllBytes($captureA, [byte[]](1..32))
        [IO.File]::WriteAllBytes($captureB, [byte[]](1..32))
        if ((Get-CapturedOutputBytes @($captureA, $captureB)) -ne 64) { throw 'Combined output byte counter mismeasured a within-limit capture.' }
        [IO.File]::AppendAllText($captureB, 'x')
        if ((Get-CapturedOutputBytes @($captureA, $captureB)) -le 64) { throw 'Combined output byte counter accepted an over-limit capture.' }
        $boundedOut = Join-Path $captureScratch 'bounded.stdout'
        $boundedErr = Join-Path $captureScratch 'bounded.stderr'
        $bounded = [FormSpaceQualificationJob]::RunBoundedCapture($hostExe,
            '-NoProfile -Command "[Console]::Write(''x'' * 4096); Start-Sleep -Seconds 30"',
            $cwd, $boundedOut, $boundedErr, 5000, 64)
        if (-not $bounded.OutputLimitExceeded -or -not $bounded.Reaped) { throw 'Bounded capture control did not stop and reap an over-limit dummy process.' }
        if ((Get-CapturedOutputBytes @($boundedOut, $boundedErr)) -gt 64) { throw 'Bounded capture control wrote more bytes than its hard cap.' }
    } finally {
        if ((Test-Path -LiteralPath $captureScratch) -and $captureScratch.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $captureScratch -Recurse -Force
        }
    }
    $selfJob = [FormSpaceQualificationJob]::CreateBoundedJob(1152MB, 12, $false)
    try { [FormSpaceQualificationJob]::AssignCurrentProcess($selfJob) }
    finally { [FormSpaceQualificationJob]::CloseHandleSafe($selfJob) }
    Write-Output 'Capture guard passed: combined stdout/stderr bytes measured and over-limit content detected.'
}

if ($Run -and -not $InternalRun) {
    if ($env:MYCOSOFT_RESOURCE_SLOT_CONFIRMED -ne 'true' -or [string]::IsNullOrWhiteSpace($env:MYCOSOFT_RESOURCE_SLOT_ID)) {
        throw 'Refusing runtime startup without the coordinator-granted slot marker and slot ID.'
    }
    $deadlineUnix = $qualificationInvocationStart.AddSeconds(240).ToUnixTimeSeconds()
    $env:FORMSPACE_QUALIFICATION_DEADLINE_UTC = [string]$deadlineUnix
    $outerJob = [FormSpaceQualificationJob]::CreateBoundedJob(1152MB, 12)
    try { [FormSpaceQualificationJob]::AssignCurrentProcess($outerJob) }
    catch { throw 'Windows Job Object could not attach the supervisor; refusing runtime startup.' }
    $repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    $hostExe = (Get-Process -Id $PID).Path
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Run -InternalRun'
    if ($TestSourceGuard) { $arguments += ' -TestSourceGuard' }
    if ($TestProcessSupervisor) { $arguments += ' -TestProcessSupervisor' }
    [uint32]$runnerPid = 0
    $runner = [FormSpaceQualificationJob]::StartSuspendedInJob($outerJob, $hostExe, $arguments, $repoRoot, [ref]$runnerPid)
    $hardDeadlineMs = [int64]$deadlineUnix * 1000
    $runnerWaitMs = [int][Math]::Max(0, [Math]::Min(230000, $hardDeadlineMs - [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - 10000))
    $waitResult = [FormSpaceQualificationJob]::Wait($runner, [uint32]$runnerWaitMs)
    if ($waitResult -ne 0) {
        Write-Warning 'The bounded setup/test phase expired; terminating only this runner Job Object process tree.'
        [FormSpaceQualificationJob]::TerminateJobMembersExcept($outerJob, [FormSpaceQualificationJob]::CurrentPid())
        if ([FormSpaceQualificationJob]::Wait($runner, 10000) -ne 0) {
            Write-Warning 'The runner process handle did not signal within the ten-second reap bound; closing the runner job will kill remaining task-owned processes.'
        }
        [FormSpaceQualificationJob]::CloseHandleSafe($runner)
        exit 124
    }
    $runnerExit = [FormSpaceQualificationJob]::ExitCode($runner)
    [FormSpaceQualificationJob]::CloseHandleSafe($runner)
    $leftovers = @([FormSpaceQualificationJob]::JobPids($outerJob) | Where-Object { $_ -ne [FormSpaceQualificationJob]::CurrentPid() })
    if ($leftovers.Count -gt 0) {
        [FormSpaceQualificationJob]::TerminateJobMembersExcept($outerJob, [FormSpaceQualificationJob]::CurrentPid())
        if ($runnerExit -eq 0) { $runnerExit = 1 }
        Write-Warning 'Stopped leftover processes that remained in this qualification Job Object after runner exit.'
    }
    exit [int]$runnerExit
}

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
$qualificationDeadlineUnix = if ($env:FORMSPACE_QUALIFICATION_DEADLINE_UTC) { [long]$env:FORMSPACE_QUALIFICATION_DEADLINE_UTC } else { $null }
$captureLimitBytes = 4MB
$cleanupReserveSeconds = 15

function Get-RemainingQualificationMilliseconds([switch]$AllowZero) {
    if ($null -eq $qualificationDeadlineUnix) { return 240000 }
    $remaining = [int64]$qualificationDeadlineUnix * 1000 - [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    if ($remaining -le 0 -and -not $AllowZero) { throw 'The total 240-second qualification deadline expired.' }
    return [int][Math]::Max(0, [Math]::Min([int]::MaxValue, $remaining))
}

function Get-CapturedOutputBytes([string[]]$Paths) {
    [int64]$total = 0
    foreach ($path in $Paths) {
        if (Test-Path -LiteralPath $path -PathType Leaf) { $total += (Get-Item -LiteralPath $path).Length }
    }
    return $total
}

function Get-PostgresCleanupOutcome([bool]$GracefulStopSucceeded, [bool]$ForcedStopSucceeded, [bool]$PostmasterStillAlive) {
    $cleaned = ($GracefulStopSucceeded -or $ForcedStopSucceeded) -and -not $PostmasterStillAlive
    $qualified = $GracefulStopSucceeded -and -not $PostmasterStillAlive
    $message = if ($qualified) { $null } elseif (-not $cleaned) {
        'Task-owned PostgreSQL cleanup failed; graceful and forced stop both failed or the process remains alive.'
    } else {
        'Task-owned PostgreSQL required forced cleanup after graceful pg_ctl stop failed; qualification fails closed.'
    }
    return [pscustomobject]@{ Cleaned = $cleaned; QualificationSucceeded = $qualified; Failure = $message }
}

function Stop-TaskOwnedProcessTree([Diagnostics.Process]$Process, [int]$ReapMilliseconds = 3000) {
    if ($null -eq $Process) { return $true }
    try { $Process.Refresh(); if ($Process.HasExited) { return $true } } catch { return $true }
    $killer = $null
    try {
        $killer = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\taskkill.exe') `
            -ArgumentList @('/PID', [string]$Process.Id, '/T', '/F') -PassThru -WindowStyle Hidden
        if (-not $killer.WaitForExit(3000)) {
            try { $killer.Kill() } catch { }
            [void]$killer.WaitForExit(1000)
        }
    } catch { Write-Warning "Bounded taskkill attempt failed for owned PID $($Process.Id): $($_.Exception.Message)" }
    try {
        if (-not $Process.WaitForExit($ReapMilliseconds)) {
            $Process.Kill()
            if (-not $Process.WaitForExit($ReapMilliseconds)) { return $false }
        }
    } catch { return $false }
    return $true
}

function Stop-TaskOwnedPostgres {
    $pidFile = Join-Path $dataDir 'postmaster.pid'
    if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) { return $true }
    $pidText = (Get-Content -LiteralPath $pidFile -TotalCount 1).Trim()
    [int]$postgresPid = 0
    if (-not [int]::TryParse($pidText, [ref]$postgresPid) -or $postgresPid -le 0) {
        throw "Refusing to stop PostgreSQL with an invalid task-owned postmaster PID file: $pidFile"
    }
    $process = Get-Process -Id $postgresPid -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $true }
    $actualPath = [IO.Path]::GetFullPath($process.Path)
    $expectedPath = [IO.Path]::GetFullPath($postgres)
    if (-not $actualPath.Equals($expectedPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to kill PID $postgresPid because its executable is not the pinned task PostgreSQL binary."
    }
    if (-not (Stop-TaskOwnedProcessTree -Process $process -ReapMilliseconds 3000)) {
        throw "Task-owned PostgreSQL process tree rooted at PID $postgresPid could not be reaped within the bounded cleanup window."
    }
    return $true
}

function Test-TaskOwnedPostgresAlive {
    $pidFile = Join-Path $dataDir 'postmaster.pid'
    if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) { return $false }
    $pidText = (Get-Content -LiteralPath $pidFile -TotalCount 1).Trim()
    [int]$postgresPid = 0
    if (-not [int]::TryParse($pidText, [ref]$postgresPid) -or $postgresPid -le 0) {
        throw "Refusing to inspect PostgreSQL with an invalid task-owned postmaster PID file: $pidFile"
    }
    $process = Get-Process -Id $postgresPid -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $false }
    $actualPath = [IO.Path]::GetFullPath($process.Path)
    $expectedPath = [IO.Path]::GetFullPath($postgres)
    if (-not $actualPath.Equals($expectedPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to treat PID $postgresPid as task-owned PostgreSQL because its executable path differs."
    }
    return $true
}

$jobMemoryLimitMiB = 1152

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
$expectedNodeOptions = "--max-old-space-size=$nodeHeapLimitMiB"
if ($env:NODE_OPTIONS -cne $expectedNodeOptions) { throw 'NODE_OPTIONS is not the explicit pinned runner value before Node.js startup.' }
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

$jobProbe = [FormSpaceQualificationJob]::CreateBoundedJob([UInt64]$jobMemoryLimitMiB * 1MB, 12)
[FormSpaceQualificationJob]::CloseHandleSafe($jobProbe)
if ($TestProcessSupervisor) { Test-QualificationProcessSupervisor }

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
Write-Output "Bounded run limits: Windows Job Object caps the supervisor and complete setup/test/cleanup process tree at $jobMemoryLimitMiB MiB committed job memory and 12 active processes; total wall-clock deadline $pytestTimeoutSeconds seconds including preflight, fixture setup, tests, and cleanup; PostgreSQL max_connections=12/shared_buffers=64MB/work_mem=4MB; Node heap $nodeHeapLimitMiB MiB; 30-second worker and 15-second post-commit child timeouts; captured pytest stdout+stderr limit $captureLimitBytes bytes."
Write-Output 'This preflight did not create a directory, database, or service.'

if (-not $Run) { return }
if ($env:MYCOSOFT_RESOURCE_SLOT_CONFIRMED -ne 'true' -or [string]::IsNullOrWhiteSpace($env:MYCOSOFT_RESOURCE_SLOT_ID)) {
    throw 'Refusing runtime startup without the coordinator-granted slot marker and slot ID.'
}
if ($null -eq $qualificationDeadlineUnix) { throw 'The supervisor did not provide a total qualification deadline.' }
if (Test-Path -LiteralPath $taskRoot) {
    throw "Refusing to reuse an existing fixture path; preserve it and choose a new task-owned directory: $taskRoot"
}

$envNames = @('RETENTION_TEST_ALLOW_DISPOSABLE','RETENTION_TEST_DSN','FORMSPACE_WEBSITE_CHECKOUT','FORMSPACE_TSX_CLI','NODE_OPTIONS')
$previousEnv = @{}
foreach ($name in $envNames) { $previousEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$serverStarted = $false
$exitCode = 0
$cleanupFailure = $null
try {
    New-Item -ItemType Directory -Path $taskRoot | Out-Null
    [void](Get-RemainingQualificationMilliseconds)
    & $initdb -D $dataDir -U postgres --auth-local=trust --auth-host=trust --encoding=UTF8 --locale=C --no-instructions
    if ($LASTEXITCODE -ne 0) { throw "initdb failed with exit code $LASTEXITCODE" }

    $serverOptions = "-h 127.0.0.1 -p $port -c listen_addresses=127.0.0.1 -c max_connections=12 -c shared_buffers=64MB -c work_mem=4MB"
    $serverWaitSeconds = [Math]::Max(1, [int][Math]::Floor((Get-RemainingQualificationMilliseconds) / 1000) - $cleanupReserveSeconds)
    $serverStarted = $true
    & $pgCtl -D $dataDir -l $logFile -w -t $serverWaitSeconds -o $serverOptions start
    if ($LASTEXITCODE -ne 0) { throw "pg_ctl start failed with exit code $LASTEXITCODE" }

    [void](Get-RemainingQualificationMilliseconds)
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
        $testBudgetMs = [Math]::Max(1, (Get-RemainingQualificationMilliseconds) - ($cleanupReserveSeconds * 1000))
        $testResult = [FormSpaceQualificationJob]::RunBoundedCapture($python, '-m pytest -q tests/test_formspace_durable_api_postgres.py',
            $repoRoot, $stdoutLog, $stderrLog, [int]$testBudgetMs, [int64]$captureLimitBytes)
        if (-not $testResult.Reaped) { throw 'Owned pytest process tree or its output pumps did not reap within the bounded three-second window.' }
        if ($testResult.OutputLimitExceeded) { throw "Pytest output exceeded the combined $captureLimitBytes-byte capture limit; its owned process tree was terminated." }
        if ($testResult.TimedOut) { throw 'Signed-JWT/API/worker test exhausted its remaining total-deadline budget; its owned process tree was terminated and reaped.' }
        $exitCode = $testResult.ExitCode
        if (Test-Path -LiteralPath $stdoutLog) { Get-Content -LiteralPath $stdoutLog }
        if (Test-Path -LiteralPath $stderrLog) { Get-Content -LiteralPath $stderrLog }
    }
    finally { Pop-Location }
}
finally {
    if ($serverStarted) {
        $gracefulStopSucceeded = $false
        $forcedStopSucceeded = $false
        $postmasterStillAlive = $false
        $forcedStopFailure = $null
        try {
            $remainingCleanupMs = Get-RemainingQualificationMilliseconds -AllowZero
            if ($remainingCleanupMs -gt 0) {
                $stopSeconds = [Math]::Max(1, [int][Math]::Floor($remainingCleanupMs / 1000))
                & $pgCtl -D $dataDir -m fast -w -t $stopSeconds stop
                $gracefulStopSucceeded = ($LASTEXITCODE -eq 0)
            }
        }
        catch {
            $cleanupFailure = "Bounded graceful PostgreSQL stop failed: $($_.Exception.Message)"
        }
        try { $postmasterStillAlive = Test-TaskOwnedPostgresAlive }
        catch {
            if (-not $cleanupFailure) { $cleanupFailure = "PostgreSQL ownership could not be verified after graceful stop: $($_.Exception.Message)" }
            $postmasterStillAlive = $true
        }
        if (-not $gracefulStopSucceeded -or $postmasterStillAlive) {
            try { $forcedStopSucceeded = Stop-TaskOwnedPostgres }
            catch { $forcedStopFailure = $_.Exception.Message }
        }
        try { $postmasterStillAlive = Test-TaskOwnedPostgresAlive }
        catch {
            if (-not $cleanupFailure) { $cleanupFailure = "PostgreSQL ownership could not be verified after forced cleanup: $($_.Exception.Message)" }
            $postmasterStillAlive = $true
        }
        $cleanupOutcome = Get-PostgresCleanupOutcome $gracefulStopSucceeded $forcedStopSucceeded $postmasterStillAlive
        if (-not $cleanupOutcome.QualificationSucceeded) {
            $cleanupFailure = $cleanupOutcome.Failure
            if ($forcedStopFailure) { $cleanupFailure += " Forced cleanup error: $forcedStopFailure" }
            if ($cleanupFailure -and $cleanupOutcome.Cleaned -and -not $forcedStopFailure) {
                $cleanupFailure = $cleanupOutcome.Failure
            }
        }
    }
    foreach ($name in $envNames) { [Environment]::SetEnvironmentVariable($name, $previousEnv[$name], 'Process') }
    if (Test-Path -LiteralPath $logFile) { Write-Output "Preserved task-owned PostgreSQL log: $logFile" }
    Write-Output "Preserved task-owned fixture files: $taskRoot"
    if ($cleanupFailure) { throw $cleanupFailure }
}
exit $exitCode
