#!/usr/bin/env bash
set -euo pipefail

AI_ASSET_PIPELINE_REPO="https://github.com/eyupalemdar/AIAssetPipeline.git"
MCP_TOOLKIT_REPO="https://github.com/eyupalemdar/UnrealMCPToolkit.git"
REPO_ROOT="${HOME}/.commonai/repos"
PROFILE="commonui"
DRY_RUN=0
FORCE=0
USE_SSH=0
SKIP_DOCTOR=0
INSTALL_GH=0
PROJECT=""

usage() {
  cat <<'EOF'
Usage: install_commonai_macos.sh --project /path/to/UnrealProject [options]

Options:
  --repo-root PATH      Clone/update repos under PATH. Default: ~/.commonai/repos
  --profile NAME        Bootstrap profile. Default: commonui
  --dry-run             Print install plan without writing target project files
  --force               Overwrite unmanaged target conflicts
  --use-ssh             Use git@github.com remotes instead of HTTPS
  --skip-doctor         Do not run doctor --strict after install
  --install-gh          Install GitHub CLI with Homebrew if possible
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="${2:?}"; shift 2 ;;
    --repo-root) REPO_ROOT="${2:?}"; shift 2 ;;
    --profile) PROFILE="${2:?}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --force) FORCE=1; shift ;;
    --use-ssh) USE_SSH=1; shift ;;
    --skip-doctor) SKIP_DOCTOR=1; shift ;;
    --install-gh) INSTALL_GH=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "${PROJECT}" ]]; then
  usage
  exit 2
fi

command -v git >/dev/null || { echo "git is required. Install Xcode Command Line Tools with: xcode-select --install" >&2; exit 2; }
command -v python3 >/dev/null || { echo "python3 is required." >&2; exit 2; }

if [[ "${INSTALL_GH}" == "1" ]] && ! command -v gh >/dev/null; then
  if command -v brew >/dev/null; then
    brew install gh
  else
    echo "GitHub CLI install was requested, but Homebrew was not found. Install from https://cli.github.com/." >&2
    exit 2
  fi
fi

if [[ "${USE_SSH}" == "1" ]]; then
  AI_ASSET_PIPELINE_REPO="git@github.com:eyupalemdar/AIAssetPipeline.git"
  MCP_TOOLKIT_REPO="git@github.com:eyupalemdar/UnrealMCPToolkit.git"
fi

PROJECT="$(cd "$(dirname "${PROJECT}")" && pwd)/$(basename "${PROJECT}")"
mkdir -p "${REPO_ROOT}"
REPO_ROOT="$(cd "${REPO_ROOT}" && pwd)"

ASSET_ROOT="${REPO_ROOT}/AIAssetPipeline"
MCP_ROOT="${REPO_ROOT}/UnrealMCPToolkit"

sync_repo() {
  local url="$1"
  local path="$2"
  if [[ -d "${path}/.git" ]]; then
    git -C "${path}" pull --ff-only
  elif [[ -e "${path}" ]]; then
    echo "Target exists but is not a git repo: ${path}" >&2
    exit 2
  else
    git clone "${url}" "${path}"
  fi
}

sync_repo "${AI_ASSET_PIPELINE_REPO}" "${ASSET_ROOT}"
sync_repo "${MCP_TOOLKIT_REPO}" "${MCP_ROOT}"

BOOTSTRAP="${ASSET_ROOT}/Tools/AIWorkflowBootstrap/bootstrap.py"
INSTALL_ARGS=("${BOOTSTRAP}" install --project "${PROJECT}" --asset-source-root "${ASSET_ROOT}" --mcp-source-root "${MCP_ROOT}" --profile "${PROFILE}")
[[ "${DRY_RUN}" == "1" ]] && INSTALL_ARGS+=(--dry-run)
[[ "${FORCE}" == "1" ]] && INSTALL_ARGS+=(--force)

python3 "${INSTALL_ARGS[@]}"

if [[ "${DRY_RUN}" != "1" && "${SKIP_DOCTOR}" != "1" ]]; then
  python3 "${BOOTSTRAP}" doctor --project "${PROJECT}" --strict
fi

echo
echo "CommonAI workflow install completed."
echo "Next: regenerate project files, rebuild the Editor target, then open Unreal Editor."
