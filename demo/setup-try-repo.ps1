<#
  "Try Doppel": a public repo where anyone opens an issue with a pull request link and Doppel replies with the result.

  Before running:
    1. On GitHub, create an EMPTY public repo (no README, no license) named doppel-try.
    2. In that repo: Settings -> Secrets and variables -> Actions -> add NEBIUS_API_KEY and NEBIUS_PROJECT_ID.

  Then, from the doppel folder:
    powershell -ExecutionPolicy Bypass -File demo\setup-try-repo.ps1 -Owner <your-github-name>

  It pushes try\ (the workflow, the issue form, the script) to the repo's main branch, and try\site\index.html to
  the root of its gh-pages branch, next to the check pages earlier runs published (those stay). Run it again after
  changing anything under try\: main is replaced, gh-pages only gets the new page.
#>
param(
  [Parameter(Mandatory = $true)][string]$Owner,
  [string]$Repo = "doppel-try",
  [string]$Work = "$env:TEMP\doppel-try-build"
)
$ErrorActionPreference = "Stop"
$doppel = (Get-Location).Path
$src = Join-Path $doppel "try"
if (-not (Test-Path (Join-Path $src "scripts\try_pr.py"))) { throw "Run this from the doppel folder (try\scripts\try_pr.py not found)." }
$remote = "https://github.com/$Owner/$Repo.git"
$who = @("-c", "user.name=$Owner", "-c", "user.email=$Owner@users.noreply.github.com")

# main: the files under try\, with try\github\ as .github\ (kept without the dot in the doppel repo, where it
# would otherwise look like the doppel repo's own workflows)
if (Test-Path $Work) { Remove-Item -Recurse -Force $Work }
New-Item -ItemType Directory -Force $Work | Out-Null
Copy-Item -Recurse "$src\*" $Work
Rename-Item (Join-Path $Work "github") ".github"
Get-ChildItem -Recurse -Directory -Filter __pycache__ $Work | Remove-Item -Recurse -Force
Set-Location $Work
git init -q -b main
git config core.autocrlf false
git add -A
git @who commit -qm "Try Doppel on any public pull request"
git remote add origin $remote
git push -q -f origin main

# gh-pages: the Try page at the root; the runs' check pages under try/<issue>/ are left as they are
$pages = "$Work-pages"
if (Test-Path $pages) { Remove-Item -Recurse -Force $pages }
$hasPages = git ls-remote --heads $remote gh-pages
if ($hasPages) {
  git clone -q --depth 1 --branch gh-pages $remote $pages
} else {
  New-Item -ItemType Directory -Force $pages | Out-Null
  git -C $pages init -q -b gh-pages
  git -C $pages remote add origin $remote
}
Copy-Item "$src\site\index.html" (Join-Path $pages "index.html") -Force
New-Item -ItemType File -Force (Join-Path $pages ".nojekyll") | Out-Null
git -C $pages add -A
if (git -C $pages status --porcelain) {
  git -C $pages @who commit -qm "Try page"
  git -C $pages push -q origin gh-pages
}

Set-Location $doppel
Write-Host ""
Write-Host "Done: https://github.com/$Owner/$Repo"
if (-not $hasPages) {
  Write-Host "First time only: Settings -> Pages -> Deploy from a branch -> gh-pages, folder / (root) -> Save."
}
Write-Host "The Try page: https://$($Owner.ToLower()).github.io/$Repo/ (about a minute after Pages is on)"
Write-Host "Test it: open an issue from the Try Doppel form with https://github.com/Maukthik/doppel-demo/pull/1"
Start-Process "https://github.com/$Owner/$Repo/issues/new?template=try.yml"
