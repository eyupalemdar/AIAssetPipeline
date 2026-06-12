# AIWorkflowBootstrap Release Flow

Use this checklist when publishing or syncing a new workflow version to another
Unreal Engine project.

## Source Checks

```powershell
python -m py_compile Tools/AIWorkflowBootstrap/bootstrap.py Tools/AIWorkflowBootstrap/install.py Tools/AIWorkflowBootstrap/update.py Tools/AIWorkflowBootstrap/doctor.py Tools/AIWorkflowBootstrap/test_bootstrap.py
python Tools/AIWorkflowBootstrap/test_bootstrap.py
python Tools/AIWorkflowBootstrap/bootstrap.py manifest
```

The `manifest` command prints a deterministic fingerprint for the source
distribution: plugins, workflow docs, TSpec pack, schemas, validator, and the
bootstrap tool itself.

## Target Update

Always inspect the update first:

```powershell
python Tools/AIWorkflowBootstrap/bootstrap.py update --project D:\Path\To\OtherProject
```

For split public repos:

```powershell
python Tools/AIWorkflowBootstrap/bootstrap.py update --project D:\Path\To\OtherProject --asset-source-root D:\Repos\AIAssetPipeline --mcp-source-root D:\Repos\UnrealMCPToolkit
```

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
