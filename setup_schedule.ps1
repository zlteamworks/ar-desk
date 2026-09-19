# ============================================================
#  AR DESK  -  register the twice-daily refresh with Task Scheduler
# ============================================================
#  Run this ONCE, from a normal (non-admin) PowerShell window:
#
#      powershell -ExecutionPolicy Bypass -File "setup_schedule.ps1"
#
#  It creates a task called "AR Desk refresh" that runs
#  refresh_site.ps1 at 7am and 7pm daily.
#
#  Afterwards:
#      Start-ScheduledTask   -TaskName 'AR Desk refresh'   # run it now
#      Get-ScheduledTaskInfo -TaskName 'AR Desk refresh'   # last result
#      Unregister-ScheduledTask -TaskName 'AR Desk refresh'  # remove it
# ============================================================

$root   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$script = Join-Path $root 'refresh_site.ps1'
$name   = 'AR Desk refresh'

if (-not (Test-Path $script)) {
  Write-Error "refresh_site.ps1 not found next to this file ($root)."
  exit 1
}

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-ExecutionPolicy Bypass -NonInteractive -WindowStyle Hidden -File `"$script`"" `
  -WorkingDirectory $root

$triggers = @(
  (New-ScheduledTaskTrigger -Daily -At 7:00am),
  (New-ScheduledTaskTrigger -Daily -At 7:00pm)
)

# Laptop-specific settings that matter more than they look:
#   AllowStartIfOnBatteries / DontStopIfGoingOnBatteries - without these
#     Windows simply skips the run whenever you are not plugged in.
#   StartWhenAvailable - catches up a run missed while the machine slept.
#   IgnoreNew - a hung run must not have a second one launched on top of
#     it; both would fight over the same browser_profile folder.
#   ExecutionTimeLimit - a wedged Playwright browser gets killed at 1h
#     instead of blocking every later run.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
  -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 15)

$desc = 'Collect AR jobs, rebuild the AR Desk page, republish it to the same link.'
$user = "$env:USERDOMAIN\$env:USERNAME"

# S4U runs the task whether or not you are logged on, without storing a
# password. Some managed machines refuse it (it needs the "log on as a
# batch job" right), so fall back to a logged-on-only task rather than
# failing outright.
try {
  $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
  Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal -Description $desc -Force -ErrorAction Stop | Out-Null
  Write-Output "Registered '$name' - runs whether or not you are logged on."
} catch {
  Write-Output "Could not register the logged-off variant: $($_.Exception.Message)"
  Write-Output "Falling back to a task that runs only while you are logged on."
  $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal -Description $desc -Force | Out-Null
  Write-Output "Registered '$name' - runs at 7am/7pm while you are logged on."
}

Write-Output ''
Write-Output 'Test it right now with:'
Write-Output "    Start-ScheduledTask -TaskName '$name'"
Write-Output 'then watch refresh.log in this folder.'
