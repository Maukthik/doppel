<#
  Doppel's real-world test: a fresh public repo where Doppel runs exactly as it would for any outside user.

  Before running:
    1. On GitHub, create an EMPTY public repo (no README, no license), e.g. doppel-demo.
    2. In that repo: Settings -> Secrets and variables -> Actions -> add NEBIUS_API_KEY and NEBIUS_PROJECT_ID.

  Then, from the doppel folder:
    powershell -ExecutionPolicy Bypass -File demo\setup-demo-repo.ps1 -Owner <your-github-name> -Repo doppel-demo

  What it builds: microblog-api (MIT) at the commit Doppel's benchmark uses, plus ONE workflow file (no doppel.toml:
  test mode, zero setup). Then a branch with a pull request that says it only documents a field, and in fact puts
  every user's password hash into the API's responses. All of microblog-api's own tests still pass on it.
#>
param(
  [Parameter(Mandatory = $true)][string]$Owner,
  [string]$Repo = "doppel-demo",
  [string]$Work = "$env:TEMP\doppel-demo-build"
)
$ErrorActionPreference = "Stop"
$doppel = (Get-Location).Path
$patch = Join-Path $doppel "bench\microblog\prs\r17-hash-leak.patch"
if (-not (Test-Path $patch)) { throw "Run this from the doppel folder (bench\microblog\prs\r17-hash-leak.patch not found)." }

if (Test-Path $Work) { Remove-Item -Recurse -Force $Work }
git clone -q -c core.autocrlf=false https://github.com/miguelgrinberg/microblog-api.git $Work
Set-Location $Work
git checkout -q e250d71
Remove-Item -Recurse -Force .git
git init -q -b main
git config core.autocrlf false

# microblog-api's own CI runs lint, 15 test combinations and a Codecov upload that needs a token this repo doesn't
# have: replace it with one plain "tests" check, so the PR shows what matters (the tests pass, Doppel doesn't)
Remove-Item -Force .github\workflows\*.yml
@'
name: tests
on: [push, pull_request]
jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install tox
      - run: tox -e py312
'@ | Set-Content -Encoding utf8 .github\workflows\tests.yml
New-Item -ItemType Directory -Force .github\workflows | Out-Null
@'
name: doppel
on: pull_request
jobs:
  doppel:
    runs-on: ubuntu-latest
    permissions: { contents: write, pull-requests: write }   # write: to publish the check page
    steps:
      - uses: Maukthik/doppel@v1
        with:
          nebius-api-key: ${{ secrets.NEBIUS_API_KEY }}
          nebius-project-id: ${{ secrets.NEBIUS_PROJECT_ID }}
'@ | Set-Content -Encoding utf8 .github\workflows\doppel.yml

git add -A
git -c user.name="$Owner" -c user.email="$Owner@users.noreply.github.com" commit -qm "microblog-api, checked by Doppel on every pull request"
git remote add origin "https://github.com/$Owner/$Repo.git"
git push -q -u origin main

git checkout -q -b describe-has-password
git apply --whitespace=nowarn $patch
git add -A
git -c user.name="$Owner" -c user.email="$Owner@users.noreply.github.com" commit -qm "Describe has_password in the API schema"
git push -q -u origin describe-has-password

Add-Type -AssemblyName System.Web
$title = [System.Web.HttpUtility]::UrlEncode("Describe has_password in the API schema")
$body = [System.Web.HttpUtility]::UrlEncode("Back ``has_password`` with the actual column so the OpenAPI docs describe it.")
Set-Location $doppel
Write-Host ""
$url = "https://github.com/$Owner/$Repo/compare/main...describe-has-password?expand=1&title=$title&body=$body"
Write-Host "Done. Your browser opens the pull request page with its title and description filled in;"
Write-Host "check both, then press 'Create pull request'. (If it doesn't open, copy this whole line:)"
Write-Host $url
Start-Process $url      # a terminal wraps long links and a click opens only part of it: open the full one directly
Write-Host ""
Write-Host "After the first run (it creates the gh-pages branch): Settings -> Pages -> Deploy from a branch -> gh-pages,"
Write-Host "then re-run the job (Actions tab -> the run -> Re-run jobs) so the comment links the check page."
