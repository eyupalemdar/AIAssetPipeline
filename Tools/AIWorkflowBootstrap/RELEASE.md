# AIWorkflowBootstrap Release Flow

Use this checklist when publishing or syncing a new workflow version to another
Unreal Engine project.

## Source Checks

```powershell
python -m py_compile Tools/AIWorkflowBootstrap/bootstrap.py Tools/AIWorkflowBootstrap/install.py Tools/AIWorkflowBootstrap/update.py Tools/AIWorkflowBootstrap/doctor.py Tools/AIWorkflowBootstrap/test_bootstrap.py
python Tools/AIWorkflowBootstrap/test_bootstrap.py
python Tools/AIWorkflowBootstrap/bootstrap.py manifest --mcp-source-root D:\Repos\UnrealMCPToolkit
```

The `manifest` command prints a deterministic fingerprint for the source
distribution: plugins, workflow docs, TSpec pack, schemas, validator, and the
bootstrap tool itself.

## Target Update

For a new target project, prefer the platform installer scripts:

The platform scripts default to `auto` mode. They install a missing workflow,
apply updates for an existing `Tools/AIWorkflowBootstrap` or
`Tools/AIWorkflowBootstrap/state/lock.json`, then run strict diagnostics from
the target project's copied bootstrap tool. Legacy root `commonai.lock.json`
also triggers update mode and is migrated by the applied update.

```powershell
$u='https://raw.githubusercontent.com/eyupalemdar/AIAssetPipeline/main/Tools/AIWorkflowBootstrap/install_commonai_windows.ps1'; $p="$env:TEMP\install_commonai_windows.ps1"; Invoke-WebRequest $u -OutFile $p; powershell -ExecutionPolicy Bypass -File $p -Project D:\Path\To\OtherProject
```

```bash
curl -fsSL https://raw.githubusercontent.com/eyupalemdar/AIAssetPipeline/main/Tools/AIWorkflowBootstrap/install_commonai_linux.sh | bash -s -- --project /path/to/OtherProject
curl -fsSL https://raw.githubusercontent.com/eyupalemdar/AIAssetPipeline/main/Tools/AIWorkflowBootstrap/install_commonai_macos.sh | bash -s -- --project /path/to/OtherProject
```

From an existing checkout:

```powershell
powershell -ExecutionPolicy Bypass -File Tools\AIWorkflowBootstrap\install_commonai_windows.ps1 -Project D:\Path\To\OtherProject
```

```bash
bash Tools/AIWorkflowBootstrap/install_commonai_linux.sh --project /path/to/OtherProject
bash Tools/AIWorkflowBootstrap/install_commonai_macos.sh --project /path/to/OtherProject
```

Always inspect the update first:

```powershell
python Tools/AIWorkflowBootstrap/bootstrap.py update --project D:\Path\To\OtherProject
```

For split public repos:

```powershell
python Tools/AIWorkflowBootstrap/bootstrap.py update --project D:\Path\To\OtherProject --asset-source-root D:\Repos\AIAssetPipeline --mcp-source-root D:\Repos\UnrealMCPToolkit
```

For an existing manually maintained project, inspect and apply with adoption so
custom project files are not overwritten:

```powershell
python Tools/AIWorkflowBootstrap/bootstrap.py update --project D:\Path\To\OtherProject --adopt-existing
python Tools/AIWorkflowBootstrap/bootstrap.py update --project D:\Path\To\OtherProject --apply --adopt-existing
```

The first adopted update records local files as `adopted` in
`Tools/AIWorkflowBootstrap/state/lock.json`; later ordinary updates preserve
those local files unless `--force` is passed.

Apply only after the dry-run has no unexpected conflicts:

```powershell
python Tools/AIWorkflowBootstrap/bootstrap.py update --project D:\Path\To\OtherProject --apply
```

Then validate the installed workflow:

```powershell
python D:\Path\To\OtherProject\Tools\AIWorkflowBootstrap\bootstrap.py doctor --project D:\Path\To\OtherProject
python D:\Path\To\OtherProject\Tools\AIWorkflowBootstrap\bootstrap.py doctor --project D:\Path\To\OtherProject --strict
python D:\Path\To\OtherProject\Tools\AIWorkflowBootstrap\bootstrap.py validate-tspecs --project D:\Path\To\OtherProject
```

Rollback if the target update is wrong:

```powershell
python D:\Path\To\OtherProject\Tools\AIWorkflowBootstrap\bootstrap.py rollback --project D:\Path\To\OtherProject
```

## Unreal Build Step

If plugin source, module files, `.uplugin`, `.Build.cs`, `.uproject`, `.h`, or
`.cpp` files changed, regenerate project files and rebuild the target Unreal
project. Pure documentation/schema/script updates do not require a C++ build.
