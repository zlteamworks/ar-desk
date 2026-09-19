# ============================================================
#  AR DESK  -  unattended refresh
# ============================================================
#  Collect -> rebuild the page -> push, which deploys it.
#
#  The live site is https://zlteamworks.github.io/ar-desk/ - GitHub Pages
#  redeploys it on every push to main, via .github/workflows/pages.yml.
#
#  Run it by hand:
#      powershell -ExecutionPolicy Bypass -File "refresh_site.ps1"
#
#  It is registered with Task Scheduler as "AR Desk refresh"
#  (7am and 7pm daily). To inspect or change the schedule:
#      Get-ScheduledTask -TaskName 'AR Desk refresh'
#      Start-ScheduledTask -TaskName 'AR Desk refresh'   # run now
#      Unregister-ScheduledTask -TaskName 'AR Desk refresh'
#
#  The published link NEVER changes, so anyone you shared it with
#  keeps seeing the latest run without doing anything.
# ============================================================

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

# The public link. GitHub Pages serves whatever is on main, so this URL
# is fixed - every refresh replaces the page behind it.
$SiteUrl = 'https://zlteamworks.github.io/ar-desk/'

$log = Join-Path $root 'refresh.log'
function Say($msg) {
  # Timestamp per line, not per run - a run spans minutes and the
  # whole point of the log is seeing WHERE a slow run went.
  $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
  Write-Output $line
  Add-Content -Path $log -Value $line -Encoding utf8
}

# ---- locate the tools -------------------------------------------------
# Task Scheduler hands the task a minimal PATH, so nothing can be assumed
# to be "just there" the way it is in an interactive shell.

function Find-Python {
  $c = Get-Command python -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  foreach ($p in @("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
                   'C:\Program Files\Python313\python.exe',
                   'C:\Program Files\Python312\python.exe')) {
    if (Test-Path $p) { return $p }
  }
  return $null
}

function Find-Git {
  # winget cannot install on this machine (its package source is broken
  # and repairing it needs admin), so git is a portable MinGit unpacked
  # under LOCALAPPDATA. It is not on PATH, and Task Scheduler would not
  # see it even if it were, so resolve it explicitly.
  $c = Get-Command git -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  foreach ($p in @("$env:LOCALAPPDATA\Programs\MinGit\cmd\git.exe",
                   'C:\Program Files\Git\cmd\git.exe')) {
    if (Test-Path $p) { return $p }
  }
  return $null
}

Say '--- refresh started ---'

$python = Find-Python
if (-not $python) {
  Say 'python not found - cannot collect. Stopping.'
  exit 1
}

# ---- 1. Collect from Naukri, LinkedIn and Workday ---------------------
Say "collecting (python: $python)"
& $python 'naukri_job_bot.py'
if ($LASTEXITCODE -ne 0) {
  Say "bot exited with code $LASTEXITCODE - keeping the previous data"
} else {
  Say 'collection finished'
}

if (-not (Test-Path (Join-Path $root 'jobs.json'))) {
  Say 'no jobs.json produced - stopping before the rebuild'
  exit 1
}

# ---- 2. Bake the new data into the page -------------------------------
& $python 'build_site.py'
if ($LASTEXITCODE -ne 0) {
  Say 'build_site.py failed - the live page keeps its previous data'
  exit 1
}
Say 'page rebuilt at site\index.html'

# ---- 3. Push, which is what deploys -----------------------------------
# The pages.yml workflow runs on every push to main. There is no build
# step - it uploads site/ and Pages serves it. So "publishing" here is
# nothing more than a commit and a push.

$git = Find-Git
if (-not $git) {
  Say 'git not found - the page is rebuilt locally but NOT deployed.'
  Say 'Expected it at %LOCALAPPDATA%\Programs\MinGit\cmd\git.exe'
  exit 1
}

& $git add site/index.html jobs.json
# Nothing to commit is a perfectly normal outcome: a run that finds the
# same jobs produces a byte-identical page. Treat it as success, not
# failure, or the scheduled task reports a red error twice a day.
& $git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
  Say 'no change since the last run - nothing to deploy'
  exit 0
}

& $git commit -m "refresh $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Out-Null
if ($LASTEXITCODE -ne 0) {
  Say "commit failed with code $LASTEXITCODE"
  exit 1
}

& $git push origin main
if ($LASTEXITCODE -ne 0) {
  Say "push failed with code $LASTEXITCODE - the live site keeps its previous data"
  Say 'If this says authentication failed, the stored GitHub credential has'
  Say 'expired; push once by hand to refresh it.'
  exit 1
}
Say "deployed - $SiteUrl updates in about a minute"
