#!/usr/bin/env bash
set -euo pipefail

AI_ASSET_PIPELINE_REPO="https://github.com/eyupalemdar/AIAssetPipeline.git"
MCP_TOOLKIT_REPO="https://github.com/eyupalemdar/UnrealMCPToolkit.git"
REPO_ROOT="${HOME}/.commonai/repos"
PROFILE="commonui"
MODE="auto"
DRY_RUN=0
FORCE=0
USE_SSH=0
SKIP_DOCTOR=0
INSTALL_GH=0
PROJECT=""

usage() {
  cat <<'EOF'
Usage: install_commonai_linux.sh --project /path/to/UnrealProject [options]

Options:
  --repo-root PATH      Clone/update repos under PATH. Default: ~/.commonai/repos
  --profile NAME        Bootstrap profile. Default: commonui
  --mode MODE           auto, install, or update. Default: auto
  --dry-run             Print install plan without writing target project files
  --force               Overwrite unmanaged target conflicts
  --use-ssh             Use git@github.com remotes instead of HTTPS
  --skip-doctor         Do not run doctor --strict after install
  --install-gh          Install GitHub CLI if possible
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="${2:?}"; shift 2 ;;
    --repo-root) REPO_ROOT="${2:?}"; shift 2 ;;
    --profile) PROFILE="${2:?}"; shift 2 ;;
    --mode) MODE="${2:?}"; shift 2 ;;
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

case "${MODE}" in
  auto|install|update) ;;
  *) echo "Invalid --mode: ${MODE}. Use auto, install, or update." >&2; exit 2 ;;
esac

command -v git >/dev/null || { echo "git is required." >&2; exit 2; }
command -v python3 >/dev/null || { echo "python3 is required." >&2; exit 2; }

if [[ "${INSTALL_GH}" == "1" ]] && ! command -v gh >/dev/null; then
  if command -v apt-get >/dev/null; then
    if ! type -p curl >/dev/null; then
      sudo apt-get update
      sudo apt-get install -y curl
    fi
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg >/dev/null
    sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
    sudo apt-get update
    sudo apt-get install -y gh
  else
    echo "GitHub CLI install was requested, but this script only auto-installs gh on apt-based Linux. Install from https://cli.github.com/." >&2
    exit 2
  fi
fi

if [[ "${USE_SSH}" == "1" ]]; then
  AI_ASSET_PIPELINE_REPO="git@github.com:eyupalemdar/AIAssetPipeline.git"
  MCP_TOOLKIT_REPO="git@github.com:eyupalemdar/UnrealMCPToolkit.git"
fi

PROJECT="$(realpath "${PROJECT}")"
if [[ -f "${PROJECT}" ]]; then
  case "${PROJECT}" in
    *.uproject) PROJECT_ROOT="$(dirname "${PROJECT}")" ;;
    *) echo "Project must be a UE project directory or .uproject file: ${PROJECT}" >&2; exit 2 ;;
  esac
elif [[ -d "${PROJECT}" ]]; then
  PROJECT_ROOT="${PROJECT}"
else
  echo "Project path does not exist: ${PROJECT}" >&2
  exit 2
fi
REPO_ROOT="$(realpath -m "${REPO_ROOT}")"
mkdir -p "${REPO_ROOT}"

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
TARGET_BOOTSTRAP="${PROJECT_ROOT}/Tools/AIWorkflowBootstrap/bootstrap.py"
TARGET_LOCK="${PROJECT_ROOT}/commonai.lock.json"
BOOTSTRAP_COMMAND="${MODE}"
if [[ "${BOOTSTRAP_COMMAND}" == "auto" ]]; then
  if [[ -f "${TARGET_BOOTSTRAP}" || -f "${TARGET_LOCK}" ]]; then
    BOOTSTRAP_COMMAND="update"
  else
    BOOTSTRAP_COMMAND="install"
  fi
fi

INSTALL_ARGS=("${BOOTSTRAP}" "${BOOTSTRAP_COMMAND}" --project "${PROJECT_ROOT}" --asset-source-root "${ASSET_ROOT}" --mcp-source-root "${MCP_ROOT}" --profile "${PROFILE}")
[[ "${DRY_RUN}" == "1" ]] && INSTALL_ARGS+=(--dry-run)
[[ "${DRY_RUN}" != "1" && "${BOOTSTRAP_COMMAND}" == "update" ]] && INSTALL_ARGS+=(--apply)
[[ "${FORCE}" == "1" ]] && INSTALL_ARGS+=(--force)

python3 "${INSTALL_ARGS[@]}"

if [[ "${DRY_RUN}" != "1" ]]; then
  if [[ ! -f "${TARGET_BOOTSTRAP}" ]]; then
    echo "Target bootstrap was not installed: ${TARGET_BOOTSTRAP}" >&2
    exit 2
  fi
  if [[ "${SKIP_DOCTOR}" != "1" ]]; then
    python3 "${TARGET_BOOTSTRAP}" doctor --project "${PROJECT_ROOT}" --strict
  fi
fi

echo
echo "CommonAI workflow ${BOOTSTRAP_COMMAND} completed."
echo "Next: regenerate project files, rebuild the Editor target, then open Unreal Editor."
