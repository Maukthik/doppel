<#
  Prepares the five apps of TESTING-APPS.md for a live demo, so the recording only shows Doppel running.

  For each app it makes, in the -Work folder:
    <app>-base       the app at the pinned commit (the code before the change)
    <app>-planted    a copy with one planted regression the app's own tests don't catch
    <app>-safe       a copy with only a docstring changed
    <app>-pr-base    a real merged pull request: the commit it started from
    <app>-pr-head    ... and the commit it ended at
    <app>-venv       the app's own Python, with the app and its test dependencies installed
  and prints the `doppel check` commands to run on camera.

  Needs git and uv (https://docs.astral.sh/uv/: `winget install --id astral-sh.uv -e`, or `pip install uv`);
  uv fetches the Python each app needs (3.8 for flasky, 3.12 or 3.13 for the others).

  From the doppel folder, with Doppel's .venv active:
    powershell -ExecutionPolicy Bypass -File demo\prepare-apps.ps1 -Work ..\apps
    powershell -ExecutionPolicy Bypass -File demo\prepare-apps.ps1 -Work ..\apps -Apps httpbin,flasky -Verify

  -Verify also runs each app's own test suite on base and on the planted copy, to show they pass on both.
  Run it again any time: existing folders are reset to their pinned state.
#>
param(
  [string]$Work = "..\apps",
  [string[]]$Apps = @("httpbin", "flasky", "allauth", "kinto", "datasette"),
  [switch]$Verify
)
$ErrorActionPreference = "Stop"
$Apps = @($Apps | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })   # -File passes "a,b" as one word
$onWindows = (-not (Test-Path variable:IsWindows)) -or $IsWindows   # Windows PowerShell 5.1 has no $IsWindows
$utf8 = New-Object System.Text.UTF8Encoding $false

# One entry per app: where it comes from, the pin, its Python, how to install it, the edits and the real PR.
# Edits are exact text: `n is a newline (the clones keep the repos' own LF line endings).
$catalog = [ordered]@{
  httpbin = @{
    Url = "https://github.com/psf/httpbin.git"; Pin = "f7b02ae"; Py = "3.12"; Short = "hb"
    Install = { param($py, $base) & $py -m pip install -q -e "$base[test]" }
    Suite = @("-m", "pytest", "-q", "tests")
    Planted = @{ File = "httpbin/core.py"
      Old = '    return jsonify(get_dict("url", "args", "headers", "origin"))'
      New = '    return jsonify(get_dict("url", "args", "headers"))'
      Say = "GET /get stops returning the caller's IP (origin). No test asserts it." }
    Safe = @{ File = "httpbin/core.py"
      Old = '    """Returns the requester''s IP Address.'
      New = '    """Returns the IP address of whoever sent the request.' }
    Pr = @{ Base = "07717168"; Head = "4e445e00"; Title = "#66 Fix invalid escape sequence SyntaxWarnings" }
    Command = ""
  }
  flasky = @{
    Url = "https://github.com/miguelgrinberg/flasky.git"; Pin = "3beedd6"; Py = "3.8"; Short = "fl"
    Install = { param($py, $base) & $py -m pip install -q -r (Join-Path $base "requirements/dev.txt") pytest }
    Suite = @("-m", "pytest", "-q")
    Env = @{ SERVER_NAME = "localhost" }
    Planted = @{ File = "app/models.py"
      Old = "            'author_url': url_for('api.get_user', id=self.author_id),`n            'comments_url': url_for('api.get_post_comments', id=self.id),"
      New = "            'author_url': url_for('api.get_user', id=self.author_id),`n            'author_email': self.author.email,`n            'comments_url': url_for('api.get_post_comments', id=self.id),"
      Say = "Every post in the API now carries its author's email address. The tests don't check for extra fields." }
    Safe = @{ File = "flasky.py"; Old = '"""Run the unit tests."""'; New = '"""Run the unit tests (optionally with coverage)."""' }
    Pr = @{ Base = "4770c59~1"; Head = "4770c59"; Title = "Chapter 16a: Logging of slow database queries (no PRs in this repo)" }
    Command = "SERVER_NAME=localhost python -m pytest"
  }
  allauth = @{
    Url = "https://github.com/pennersr/django-allauth.git"; Pin = "5ea117e"; Py = "3.13"; Short = "aa"
    Install = { param($py, $base)
      & $py -m pip install -q -e "$base[headless,socialaccount,mfa,idp-oidc,openid,steam]" "pytest>=8.3.5,<9" `
        "pytest-asyncio==1.3.0" "pytest-django>=4.11,<4.13" "Pillow>=9.0" "djangorestframework>=3.15.2,<4" `
        "django-ninja>=1.3.0,<2" "pyyaml>=6.0.2,<7" "psycopg[binary]>=3.2.10" "oauthlib>=4.0.0,<5" }
    Suite = @("-m", "pytest", "-q")
    Planted = @{ File = "allauth/headless/usersessions/response.py"
      Old = '    data = {"usersessions": {"track_activity": app_settings.TRACK_ACTIVITY}}'
      New = '    data = {"usersessions": {"track_activity": not app_settings.TRACK_ACTIVITY}}'
      Say = "The config endpoint now reports the opposite of the real session-tracking setting. The tests only check the keys." }
    Safe = @{ File = "allauth/headless/adapter.py"; Old = "Returns the basic user data"; New = "Return the basic user data" }
    Pr = @{ Base = "09eea91a"; Head = "1565ad65"; Title = "IdP: cache-control header on JWKs and key rotation" }
    Command = ""
  }
  kinto = @{
    Url = "https://github.com/Kinto/kinto.git"; Pin = "7c8fb41"; Py = "3.12"; Short = "kinto"
    Install = { param($py, $base) & $py -m pip install -q -e "$base[test]" pytest-xdist }
    Suite = @("-m", "pytest", "-q", "-n", "4")
    Planted = @{ File = "kinto/core/views/hello.py"
      Old = "    for setting in list(public_settings):`n        data[`"settings`"][setting] = settings[setting]`n"
      New = "    for setting in list(public_settings):`n        data[`"settings`"][setting] = settings[setting]`n    data[`"storage_backend`"] = settings[`"storage_backend`"]`n"
      Say = "The public root endpoint now exposes an internal setting, the storage backend." }
    Safe = @{ File = "kinto/core/views/hello.py"
      Old = '"""Return information regarding the current instance."""'
      New = '"""Return what this instance is: its name, version, settings and capabilities."""' }
    Pr = @{ Base = "91163c77~1"; Head = "91163c77"; Title = "#3793 Fix KeyError in memory storage purge_deleted" }
    Command = ""
  }
  datasette = @{
    Url = "https://github.com/simonw/datasette.git"; Pin = "c435a80"; Py = "3.12"; Short = "ds"
    Install = { param($py, $base)
      & $py -m pip install -q -U pip
      & $py -m pip install -q -e "$base" --group "$(Join-Path $base 'pyproject.toml'):dev" }
    Suite = @("-m", "pytest", "-q", "-n", "4", "-m", "not serial")
    Planted = @{ File = "datasette/app.py"
      Old = "            `"uvicorn`": uvicorn_version,`n"
      New = ""
      Say = "/-/versions.json stops reporting the server (uvicorn) version. The tests check the other keys." }
    Safe = @{ File = "datasette/app.py"
      Old = '"""Context manager to mark code as executing within a datasette.client request."""'
      New = '"""Context manager that marks code as running inside a datasette.client request."""' }
    Pr = @{ Base = "e4e6f919~1"; Head = "e4e6f919"; Title = "#2951 Fix missing space before the view count on the index page" }
    Command = ""
  }
}

function Invoke-Git([string[]]$GitArgs) {
  & git -c advice.detachedHead=false @GitArgs
  if ($LASTEXITCODE -ne 0) { throw "git $($GitArgs -join ' ') failed (exit $LASTEXITCODE)" }
}

function Get-Copy([string]$from, [string]$dest, [string]$commit) {
  # a fresh copy of the repo at one commit; an existing folder is reset instead of cloned again
  if (-not (Test-Path (Join-Path $dest ".git"))) {
    Invoke-Git @("clone", "-q", "-c", "core.autocrlf=false", $from, $dest)
  }
  Invoke-Git @("-C", $dest, "checkout", "-q", "--force", $commit)
  Invoke-Git @("-C", $dest, "clean", "-q", "-fd")
}

function Set-Edit([string]$root, $edit) {
  $path = Join-Path $root $edit.File
  $text = [System.IO.File]::ReadAllText($path)
  $count = ([regex]::Matches($text, [regex]::Escape($edit.Old))).Count
  if ($count -ne 1) { throw "$($edit.File): expected the text to edit once, found it $count times" }
  [System.IO.File]::WriteAllText($path, $text.Replace($edit.Old, $edit.New), $utf8)
}

function Get-VenvPython([string]$venv) {
  if ($onWindows) { return (Join-Path $venv "Scripts\python.exe") }
  return (Join-Path $venv "bin/python")
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "git is not installed." }
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  throw "uv is not installed. Install it with: winget install --id astral-sh.uv -e   (or: pip install uv)"
}
New-Item -ItemType Directory -Force $Work | Out-Null
$Work = (Resolve-Path $Work).Path
$commands = @()

foreach ($name in $Apps) {
  $app = $catalog[$name]
  if (-not $app) { throw "unknown app '$name' (known: $($catalog.Keys -join ', '))" }
  $s = $app.Short
  Write-Host "`n=== $name ===" -ForegroundColor Cyan
  $base = Join-Path $Work "$s-base"
  if (-not (Test-Path (Join-Path $base ".git"))) {
    Write-Host "cloning $($app.Url)"
    Invoke-Git @("clone", "-q", "-c", "core.autocrlf=false", $app.Url, $base)
  }
  Invoke-Git @("-C", $base, "checkout", "-q", "--force", $app.Pin)

  foreach ($kind in @("planted", "safe")) {
    $dest = Join-Path $Work "$s-$kind"
    Get-Copy $base $dest $app.Pin
    $edit = if ($kind -eq "planted") { $app.Planted } else { $app.Safe }
    Set-Edit $dest $edit
    Write-Host "$s-$kind`: $($edit.File) changed"
  }
  Get-Copy $base (Join-Path $Work "$s-pr-base") $app.Pr.Base
  Get-Copy $base (Join-Path $Work "$s-pr-head") $app.Pr.Head
  Write-Host "$s-pr-base / $s-pr-head`: $($app.Pr.Title)"

  $venv = Join-Path $Work "$s-venv"
  $py = Get-VenvPython $venv
  if (-not (Test-Path $py)) {
    Write-Host "making $s-venv with Python $($app.Py) (uv downloads it if needed)"
    & uv venv -q -p $app.Py --seed $venv
    if ($LASTEXITCODE -ne 0) { throw "uv venv failed for $name" }
  }
  Write-Host "installing $name and its test dependencies into $s-venv"
  & $app.Install $py $base
  if ($LASTEXITCODE -ne 0) { throw "installing $name failed (exit $LASTEXITCODE)" }

  if ($Verify) {
    $saved = @{}
    if ($app.Env) { foreach ($e in $app.Env.GetEnumerator()) { $saved[$e.Key] = [Environment]::GetEnvironmentVariable($e.Key); [Environment]::SetEnvironmentVariable($e.Key, $e.Value) } }
    foreach ($folder in @($base, (Join-Path $Work "$s-planted"))) {
      Write-Host "running $name's own tests in $(Split-Path $folder -Leaf): python $($app.Suite -join ' ')"
      Push-Location $folder
      $before = $ErrorActionPreference
      $ErrorActionPreference = "Continue"   # pytest's warnings on stderr are not errors (Windows PowerShell 5.1)
      try {
        $out = & $py @($app.Suite) 2>&1 | ForEach-Object { "$_" } | Select-Object -Last 1
        Write-Host "  $out"
      } finally { $ErrorActionPreference = $before; Pop-Location }
    }
    foreach ($e in $saved.GetEnumerator()) { [Environment]::SetEnvironmentVariable($e.Key, $e.Value) }
  }
}

# the commands for every app prepared so far (this run or an earlier one), in the order of the catalog
foreach ($name in $catalog.Keys) {
  $app = $catalog[$name]
  $s = $app.Short
  if (-not (Test-Path (Join-Path $Work "$s-base"))) { continue }
  $pyArg = if ($onWindows) { "$s-venv\Scripts\python.exe" } else { "$s-venv/bin/python" }
  $cmdArg = if ($app.Command) { " --command `"$($app.Command)`"" } else { "" }
  $commands += ""
  $commands += "# $name, planted: $($app.Planted.Say)"
  $commands += "doppel check --backend local --no-twin --base $s-base --head $s-planted --python $pyArg$cmdArg --strict --out $s-planted.md --json $s-planted.json"
  $commands += "# $name, safe change (expect no regressions)"
  $commands += "doppel check --backend local --no-twin --base $s-base --head $s-safe --python $pyArg$cmdArg --strict --out $s-safe.md"
  $commands += "# $name, real PR: $($app.Pr.Title)"
  $commands += "doppel check --backend local --no-twin --base $s-pr-base --head $s-pr-head --python $pyArg$cmdArg --strict --out $s-pr.md"
}

$list = Join-Path $Work "commands.txt"
[System.IO.File]::WriteAllText($list, ("# run from $Work with Doppel's .venv active`n" + ($commands -join "`n") + "`n"), $utf8)
Write-Host "`nReady. The commands to run on camera are in $list" -ForegroundColor Green
Write-Host "cd $Work" -ForegroundColor Green
