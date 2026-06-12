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

- `Tools/AIWorkflowBootstrap/state/project.json`: selected profile and workflow
  paths
- `Tools/AIWorkflowBootstrap/state/lock.json`: hashes for managed files
- `Tools/AIWorkflowBootstrap/state/.gitignore`: keeps rollback backups out of
  source control while leaving project/lock state visible

Existing unmanaged files are not overwritten unless `--force` is passed.

`AIWorkflowBootstrap` is intentionally not an Unreal plugin. It is a
distribution and maintenance tool that the installer copies into each target
project at `Tools/AIWorkflowBootstrap`. Unreal loads only `Plugins/MCPToolkit`
and `Plugins/AIAssetPipeline`; the bootstrap folder exists so the target can run
`doctor`, `update`, `rollback`, `diff`, and TSpec validation without manually
copying files from the source repository.

The platform installer scripts own this lifecycle. In their default `auto`
mode, they check the target project for `Tools/AIWorkflowBootstrap/bootstrap.py`
or `Tools/AIWorkflowBootstrap/state/lock.json`: missing targets use `install`,
existing managed targets use `update --apply`. Legacy root
`commonai.project.json` and `commonai.lock.json` are also detected and migrated.
After writing files, the scripts verify that the target bootstrap exists and
run `doctor --strict` through that copied target tool.

## Install

### One-Command Public Install

Run directly from GitHub:

Windows PowerShell:

```powershell
$u='https://raw.githubusercontent.com/eyupalemdar/AIAssetPipeline/main/Tools/AIWorkflowBootstrap/install_commonai_windows.ps1'; $p="$env:TEMP\install_commonai_windows.ps1"; Invoke-WebRequest $u -OutFile $p; powershell -ExecutionPolicy Bypass -File $p -Project D:\Path\To\OtherProject
```

Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/eyupalemdar/AIAssetPipeline/main/Tools/AIWorkflowBootstrap/install_commonai_linux.sh | bash -s -- --project /path/to/OtherProject
```

macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/eyupalemdar/AIAssetPipeline/main/Tools/AIWorkflowBootstrap/install_commonai_macos.sh | bash -s -- --project /path/to/OtherProject
```

Or run from an existing checkout:

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File Tools\AIWorkflowBootstrap\install_commonai_windows.ps1 -Project D:\Path\To\OtherProject
```

Linux:

```bash
bash Tools/AIWorkflowBootstrap/install_commonai_linux.sh --project /path/to/OtherProject
```

macOS:

```bash
bash Tools/AIWorkflowBootstrap/install_commonai_macos.sh --project /path/to/OtherProject
```

These scripts clone or fast-forward update:

- `https://github.com/eyupalemdar/AIAssetPipeline.git`
- `https://github.com/eyupalemdar/UnrealMCPToolkit.git`

Default clone cache:

- Windows: `%LOCALAPPDATA%\CommonAI\Repos`
- Linux/macOS: `~/.commonai/repos`

GitHub CLI is optional. Public HTTPS clone uses only `git`. Use
`-InstallGitHubCli` on Windows or `--install-gh` on Linux/macOS when you want
the script to install `gh` through the available platform package manager.

Use `-Mode install` or `-Mode update` on Windows, and `--mode install` or
`--mode update` on Linux/macOS, when you need to bypass auto-detection.

For an existing project that already has customized workflow docs, `AGENTS.md`,
or validators, run bootstrap with `--adopt-existing`. Existing unmanaged files
are preserved and recorded in the lock while missing managed files and state are
installed.

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

Adopt an existing manual install without overwriting customized files:

```powershell
python Tools/AIWorkflowBootstrap/bootstrap.py update --project D:\Path\To\OtherProject --apply --adopt-existing
```

Applied installs and updates create
`Tools/AIWorkflowBootstrap/state/backups/<id>/` unless `--no-backup` is passed.
Rollback can still read legacy `.commonai/backups/<id>/` entries.
Commit `state/project.json`, `state/lock.json`, and `state/.gitignore`; do not
commit `state/backups/`.

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

The same manifest is stored in `Tools/AIWorkflowBootstrap/state/lock.json`
after install/update.

When installing into a project without `AGENTS.md`, bootstrap writes a CommonAI
policy template. If `AGENTS.md` already exists, it is treated as an unmanaged
conflict unless `--force` is passed.

## Profiles

- `commonui`: default UE CommonUI workflow paths.
- `generic`: same layout, intended for projects that do not want a CommonUI label.
- `projectokey`: keeps TSpecs under `Docs/Tasarim/UI_TSpecs`.

After install or an applied update, regenerate project files and rebuild the
target UE project because plugin source/module files may have changed.
