param(
    [Parameter(Mandatory = $true)]
    [string]$Project,

    [string]$RepoRoot = "",
    [string]$AIAssetPipelineRepo = "https://github.com/eyupalemdar/AIAssetPipeline.git",
    [string]$MCPToolkitRepo = "https://github.com/eyupalemdar/UnrealMCPToolkit.git",
    [string]$Profile = "commonui",
    [ValidateSet("auto", "install", "update")]
    [string]$Mode = "auto",
    [switch]$DryRun,
    [switch]$Force,
    [switch]$UseSsh,
    [switch]$SkipDoctor,
    [switch]$InstallGitHubCli
)

$ErrorActionPreference = "Stop"

function Resolve-FullPath([string]$PathValue) {
    $executionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($PathValue)
}

function Require-Command([string]$Name, [string]$InstallHint) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "$Name was not found. $InstallHint"
    }
    return $command.Source
}

if ($UseSsh) {
    $AIAssetPipelineRepo = "git@github.com:eyupalemdar/AIAssetPipeline.git"
    $MCPToolkitRepo = "git@github.com:eyupalemdar/UnrealMCPToolkit.git"
}

if ($InstallGitHubCli -and -not (Get-Command gh -ErrorAction SilentlyContinue)) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        & $winget.Source install --id GitHub.cli --exact --source winget
    }
    else {
        throw "GitHub CLI is not installed and winget was not found. Install from https://cli.github.com/."
    }
}

$python = Require-Command "python" "Install Python 3 and make sure it is on PATH."
$git = Require-Command "git" "Install Git from https://git-scm.com/download/win."

$projectPath = Resolve-FullPath $Project
if (-not (Test-Path -LiteralPath $projectPath)) {
    throw "Project path does not exist: $projectPath"
}
$projectItem = Get-Item -LiteralPath $projectPath
if ($projectItem.PSIsContainer) {
    $projectRootPath = $projectPath
}
elseif ([System.IO.Path]::GetExtension($projectPath) -eq ".uproject") {
    $projectRootPath = Split-Path -Parent $projectPath
}
else {
    throw "Project must be a UE project directory or .uproject file: $projectPath"
}

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Join-Path $env:LOCALAPPDATA "CommonAI\Repos"
}
$repoRootPath = Resolve-FullPath $RepoRoot
New-Item -ItemType Directory -Force -Path $repoRootPath | Out-Null

$assetRoot = Join-Path $repoRootPath "AIAssetPipeline"
$mcpRoot = Join-Path $repoRootPath "UnrealMCPToolkit"

function Sync-Repo([string]$Url, [string]$PathValue) {
    if (Test-Path -LiteralPath (Join-Path $PathValue ".git")) {
        & $git -C $PathValue pull --ff-only
    }
    elseif (Test-Path -LiteralPath $PathValue) {
        throw "Target exists but is not a git repo: $PathValue"
    }
    else {
        & $git clone $Url $PathValue
    }
}

Sync-Repo $AIAssetPipelineRepo $assetRoot
Sync-Repo $MCPToolkitRepo $mcpRoot

$bootstrap = Join-Path $assetRoot "Tools\AIWorkflowBootstrap\bootstrap.py"
if (-not (Test-Path -LiteralPath $bootstrap)) {
    throw "Bootstrap script not found: $bootstrap"
}

$targetBootstrap = Join-Path $projectRootPath "Tools\AIWorkflowBootstrap\bootstrap.py"
$targetLock = Join-Path $projectRootPath "commonai.lock.json"
$bootstrapCommand = $Mode.ToLowerInvariant()
if ($bootstrapCommand -eq "auto") {
    if ((Test-Path -LiteralPath $targetBootstrap) -or (Test-Path -LiteralPath $targetLock)) {
        $bootstrapCommand = "update"
    }
    else {
        $bootstrapCommand = "install"
    }
}

$installArgs = @(
    $bootstrap,
    $bootstrapCommand,
    "--project", $projectRootPath,
    "--asset-source-root", $assetRoot,
    "--mcp-source-root", $mcpRoot,
    "--profile", $Profile
)
if ($DryRun) {
    $installArgs += "--dry-run"
}
elseif ($bootstrapCommand -eq "update") {
    $installArgs += "--apply"
}
if ($Force) { $installArgs += "--force" }

& $python @installArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $DryRun) {
    if (-not (Test-Path -LiteralPath $targetBootstrap)) {
        throw "Target bootstrap was not installed: $targetBootstrap"
    }
    if (-not $SkipDoctor) {
        & $python $targetBootstrap doctor --project $projectRootPath --strict
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
}

Write-Host ""
Write-Host "CommonAI workflow $bootstrapCommand completed." -ForegroundColor Green
Write-Host "Next: regenerate project files, rebuild the Editor target, then open Unreal Editor."
