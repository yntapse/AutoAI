$ErrorActionPreference = "Continue"
$log = Join-Path $PSScriptRoot "logs\kill_orphan_8000.log"
"==== kill_orphan_8000 $(Get-Date -Format s) ====" | Out-File $log -Encoding utf8

function Listeners { Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue }

"--- listeners on 8000 BEFORE ---" | Add-Content $log
Listeners | ForEach-Object {
  $alive = [bool](Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue)
  "  $($_.LocalAddress):$($_.LocalPort) owner=$($_.OwningProcess) owner_alive=$alive" | Add-Content $log
}

$procs = Get-CimInstance Win32_Process
"--- every python process ---" | Add-Content $log
$procs | Where-Object { $_.Name -like "python*" } | ForEach-Object {
  "  pid=$($_.ProcessId) parent=$($_.ParentProcessId) started=$($_.CreationDate) cmd=$($_.CommandLine)" | Add-Content $log
}

# An orphan: its parent is the DEAD pid netstat still reports as the socket owner.
foreach ($c in Listeners) {
  $owner = $c.OwningProcess
  if (-not (Get-Process -Id $owner -ErrorAction SilentlyContinue)) {
    "--- dead owner $owner : finding the process that inherited its socket ---" | Add-Content $log
    $orphans = $procs | Where-Object {
      $_.ParentProcessId -eq $owner -or ($_.CommandLine -and $_.CommandLine -match "parent_pid=$owner\b")
    }
    foreach ($o in $orphans) {
      "  KILL orphan pid=$($o.ProcessId) parent=$($o.ParentProcessId) cmd=$($o.CommandLine)" | Add-Content $log
      Stop-Process -Id $o.ProcessId -Force -ErrorAction Continue
    }
    if (-not $orphans) { "  no child found by parent pid" | Add-Content $log }
  }
}

Start-Sleep -Seconds 3
"--- listeners on 8000 AFTER ---" | Add-Content $log
Listeners | ForEach-Object {
  $alive = [bool](Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue)
  "  $($_.LocalAddress):$($_.LocalPort) owner=$($_.OwningProcess) owner_alive=$alive" | Add-Content $log
}
"--- GET /__build ---" | Add-Content $log
try { (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/__build -TimeoutSec 10).Content | Add-Content $log }
catch { "  $($_.Exception.Message)" | Add-Content $log }
"==== done ====" | Add-Content $log
