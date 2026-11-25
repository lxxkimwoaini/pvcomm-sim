param(
    [Parameter(Mandatory=$true)][int]$TaskRootPid,
    [Parameter(Mandatory=$true)][string]$Output,
    [Parameter(Mandatory=$true)][string]$StopFile
)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $Output) { throw 'measurement output already exists' }
$previousCpu = $null
$previousTime = $null
while (-not (Test-Path -LiteralPath $StopFile)) {
    $processTable = @(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId)
    $owned = [System.Collections.Generic.HashSet[int]]::new()
    [void]$owned.Add($TaskRootPid)
    do {
        $added = $false
        foreach ($item in $processTable) {
            if ($owned.Contains([int]$item.ParentProcessId)) {
                $added = $owned.Add([int]$item.ProcessId) -or $added
            }
        }
    } while ($added)
    $active = @($owned | ForEach-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
    $cpu = ($active | Measure-Object -Property CPU -Sum).Sum
    $rss = ($active | Measure-Object -Property WorkingSet64 -Sum).Sum
    $now = [DateTimeOffset]::UtcNow
    $cpuPercent = if ($null -eq $previousCpu) { $null } else {
        [Math]::Max(0, ($cpu - $previousCpu) / ($now - $previousTime).TotalSeconds * 100)
    }
    [ordered]@{
        ts_utc_ms = $now.ToUnixTimeMilliseconds()
        cpu_percent_one_core = $cpuPercent
        working_set_bytes_sum = $rss
        owned_pids = @($active.Id)
        scope = 'Windows harness browser Vite and WSL wrappers; excludes Linux API; shared pages may double count'
    } | ConvertTo-Json -Compress | Add-Content -LiteralPath $Output -Encoding utf8
    $previousCpu = $cpu
    $previousTime = $now
    Start-Sleep -Seconds 5
}
