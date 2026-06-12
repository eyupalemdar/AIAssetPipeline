# Changelog

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
