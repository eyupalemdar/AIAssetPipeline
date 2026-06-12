# AIWorkflowBootstrap

`AIWorkflowBootstrap` installs and updates the shared AI UI/asset workflow in
another Unreal Engine project without manual file copying.

It copies the reusable pieces from this repo:

- `Plugins/MCPToolkit`
- `Plugins/AIAssetPipeline`
- generic AI UI transfer docs from `MCPToolkit`
- generic TSpec schema/templates/examples from `MCPToolkit`
- `ValidateUITSpecs.ps1`
- AI asset pipeline JSON schemas
- `Tools/AIWorkflowBootstrap` itself, so the target project can run `doctor`,
  `manifest`, and `validate-tspecs` locally

The tool writes two target-project files:

- `commonai.project.json`: selected profile and workflow paths
- `commonai.lock.json`: hashes for managed files

Existing unmanaged files are not overwritten unless `--force` is passed.

## Install

From this repo:

```powershell
python Tools/AIWorkflowBootstrap/bootstrap.py install --project D:\Path\To\OtherProject
```

From split public plugin repos:

```powershell
python Tools/AIWorkflowBootstrap/bootstrap.py install --project D:\Path\To\OtherProject --asset-source-root D:\Repos\AIAssetPipeline --mcp-source-root D:\Repos\UnrealMCPToolkit
```

PowerShell wrapper:

```powershell
powershell -ExecutionPolicy Bypass -File Tools\AIWorkflowBootstrap\bootstrap.ps1 install --project D:\Path\To\OtherProject
```

Dry-run first:

```powershell
python Tools/AIWorkflowBootstrap/bootstrap.py install --project D:\Path\To\OtherProject --dry-run
```

## Update

`update` defaults to dry-run:

```powershell
python Tools/AIWorkflowBootstrap/bootstrap.py update --project D:\Path\To\OtherProject
```

Apply the update:

```powershell
python Tools/AIWorkflowBootstrap/bootstrap.py update --project D:\Path\To\OtherProject --apply
```

Applied installs and updates create `.commonai/backups/<id>/` unless
`--no-backup` is passed.

Rollback the latest bootstrap write:

```powershell
python Tools/AIWorkflowBootstrap/bootstrap.py rollback --project D:\Path\To\OtherProject
```

Markdown diff summary:

```powershell
python Tools/AIWorkflowBootstrap/bootstrap.py diff --project D:\Path\To\OtherProject --format markdown
```

## Doctor

```powershell
python Tools/AIWorkflowBootstrap/bootstrap.py doctor --project D:\Path\To\OtherProject
python Tools/AIWorkflowBootstrap/bootstrap.py doctor --project D:\Path\To\OtherProject --strict
```

## TSpec Validation

After install, the target project can run its own validator:

```powershell
python D:\Path\To\OtherProject\Tools\AIWorkflowBootstrap\bootstrap.py validate-tspecs --project D:\Path\To\OtherProject
```

## Source Manifest

To fingerprint the current distributable workflow:

```powershell
python Tools/AIWorkflowBootstrap/bootstrap.py manifest
```

The same manifest is stored in `commonai.lock.json` after install/update.

When installing into a project without `AGENTS.md`, bootstrap writes a CommonAI
policy template. If `AGENTS.md` already exists, it is treated as an unmanaged
conflict unless `--force` is passed.

## Profiles

- `commonui`: default UE CommonUI workflow paths.
- `generic`: same layout, intended for projects that do not want a CommonUI label.
- `projectokey`: keeps TSpecs under `Docs/Tasarim/UI_TSpecs`.

After install or an applied update, regenerate project files and rebuild the
target UE project because plugin source/module files may have changed.
