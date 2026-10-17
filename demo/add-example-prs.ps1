<#
  Two more pull requests on doppel-demo, so "Try Doppel" has an example of each outcome:
    #2 "Small cleanup in auth error handlers": says it tidies two handlers, renames a field in every 401 and 403
       answer (benchmark case r03, a real fix in microblog-api, reverted).
    #3 "Better endpoint descriptions": only docstrings change, and it says so (benchmark case s02).
  #1 is the password-hash leak setup-demo-repo.ps1 made.

  From the doppel folder:
    powershell -ExecutionPolicy Bypass -File demo\add-example-prs.ps1 -Owner <your-github-name>

  Your browser opens two "create pull request" pages with the title and description filled in: press
  'Create pull request' on each, #2 first. Doppel runs on both in doppel-demo too.
#>
param(
  [Parameter(Mandatory = $true)][string]$Owner,
  [string]$Repo = "doppel-demo",
  [string]$Work = "$env:TEMP\doppel-demo-examples"
)
$ErrorActionPreference = "Stop"
$doppel = (Get-Location).Path
$prs = Join-Path $doppel "bench\microblog\prs"
if (-not (Test-Path (Join-Path $prs "r03-auth-error-field.patch"))) { throw "Run this from the doppel folder." }
$who = @("-c", "user.name=$Owner", "-c", "user.email=$Owner@users.noreply.github.com")
$examples = @(
  @{ Patch = "r03-auth-error-field.patch"; Branch = "tidy-auth-errors"; Title = "Small cleanup in auth error handlers";
     Body = "Tidy the two error handlers in ``api/auth.py``." },
  @{ Patch = "s02-docstrings.patch"; Branch = "better-endpoint-docs"; Title = "Better endpoint descriptions";
     Body = "Improve the docstrings that become the API documentation. Only the docs text changes." }
)

if (Test-Path $Work) { Remove-Item -Recurse -Force $Work }
git clone -q -c core.autocrlf=false "https://github.com/$Owner/$Repo.git" $Work
Set-Location $Work
Add-Type -AssemblyName System.Web
$urls = @()
foreach ($e in $examples) {
  git checkout -q main
  git checkout -q -B $e.Branch
  git apply --whitespace=nowarn (Join-Path $prs $e.Patch)
  git add -A
  git @who commit -qm $e.Title
  git push -q -f -u origin $e.Branch
  $t = [System.Web.HttpUtility]::UrlEncode($e.Title)
  $b = [System.Web.HttpUtility]::UrlEncode($e.Body)
  $urls += "https://github.com/$Owner/$Repo/compare/main...$($e.Branch)?expand=1&title=$t&body=$b"
}
Set-Location $doppel
Write-Host ""
Write-Host "Pushed. Create the pull requests in this order (copy a line if the browser doesn't open it):"
foreach ($u in $urls) { Write-Host $u; Start-Process $u; Start-Sleep -Seconds 2 }
Write-Host ""
Write-Host "If they don't get the numbers 2 and 3, change the example links in try\site\index.html,"
Write-Host "try\README.md and try\github\ISSUE_TEMPLATE\try.yml."
