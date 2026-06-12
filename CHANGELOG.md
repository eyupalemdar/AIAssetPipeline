# Changelog

## 0.1.6 - Persistent Adopted Workflow Files

- Lock entries now mark adopted project-local files explicitly, so later
  updates preserve them without requiring `--adopt-existing` every time.
- Bootstrap apply/update behavior now treats adopted workflow files as managed
  local files instead of silently reverting them to source templates.

## 0.1.5 - Adopt Existing Workflow Files

- Added `--adopt-existing` to bootstrap install/update flows. Existing
  unmanaged project workflow files can now be preserved and recorded in the
  lock while missing managed files and state are installed.

## 0.1.4 - BOM-Tolerant Project JSON

- Bootstrap JSON loading now accepts UTF-8 files with or without a BOM. This
  keeps fresh installs working with `.uproject` files touched by Windows
  PowerShell tooling.

## 0.1.3 - Bootstrap State Directory

- Moved project workflow state to
  `Tools/AIWorkflowBootstrap/state/project.json` and
  `Tools/AIWorkflowBootstrap/state/lock.json`.
- Moved new rollback backups to `Tools/AIWorkflowBootstrap/state/backups/`.
- Added managed `Tools/AIWorkflowBootstrap/state/.gitignore` so backups stay
  local while project/lock state can be committed.
- Kept legacy root `commonai.project.json`, `commonai.lock.json`, and
  `.commonai/backups/` readable for migration and rollback.

## 0.1.2 - Bootstrap-Controlled Install/Update

- Installer scripts now auto-select `install` or `update --apply` based on the
  target project's `Tools/AIWorkflowBootstrap` and managed lock state.
- Post-install strict diagnostics now run through the target project's copied
  `Tools/AIWorkflowBootstrap/bootstrap.py`.
- Installers accept UE project directories or `.uproject` files.

## 0.1.1 - One-Command Installers

- Added Windows, Linux, and macOS installer scripts that clone/update the public
  AIAssetPipeline and UnrealMCPToolkit repositories, install the workflow into a
  target Unreal project, and run strict bootstrap diagnostics.
- Documented optional GitHub CLI installation and public HTTPS/SSH clone modes.

## 0.1.0 - Initial Public Release

- Added provider-agnostic asset pipeline spec and manifest contracts.
- Added Python CLI for validation, packaging, manifest validation, import
  planning, TSpec asset-link validation, and deterministic smoke fixture
  creation.
- Added Unreal Editor extension commands through MCPToolkit for status, guarded
  manifest import, and asset verification.
- Added AIWorkflowBootstrap for cross-project install/update/doctor workflows.
- Added MIT license and public repository metadata.
