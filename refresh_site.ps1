# ============================================================
#  AR DESK  -  unattended refresh
# ============================================================
#  Collect -> rebuild the page -> deploy the protected Worker -> push source.
#
#  The live site is the authenticated Cloudflare Worker. GitHub Pages serves
#  only a redirect so the old public URL cannot bypass login.
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
$SiteUrl = 'https://talenttap.talenttap-finance-india.workers.dev/'

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

# ---- 3. Deploy the protected website ---------------------------------
$node = Join-Path $root '.tools\node-v24.21.0-win-x64\node.exe'
$wrangler = Join-Path $root '.tools\cloudflare-cli\node_modules\wrangler\bin\wrangler.js'
if (-not (Test-Path $node) -or -not (Test-Path $wrangler)) {
  Say 'Cloudflare deployment tools are missing - keeping the previous live site.'
  exit 1
}
Say 'deploying protected TalentTap Worker'
Push-Location (Join-Path $root 'cloudflare')
& $node $wrangler deploy
$deployExit = $LASTEXITCODE
Pop-Location
if ($deployExit -ne 0) {
  Say "Cloudflare deploy failed with code $deployExit - keeping the previous live site"
  exit 1
}
Say "protected website deployed - $SiteUrl"

# ---- 4. Push source and update the legacy redirect --------------------

$git = Find-Git
if (-not $git) {
  Say 'git not found - live Worker updated, but source was not pushed.'
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
Say "source pushed; legacy GitHub Pages URL redirects to $SiteUrl"
