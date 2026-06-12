# AIAssetPipeline

AIAssetPipeline is a provider-agnostic Unreal Editor plugin for converting
already-generated source art plus metadata into UE-ready runtime texture assets.

It does not call image models and does not mutate Widget Blueprints. Providers
such as Image 2.0 or future models are represented only as
`source_art[].provenance` metadata in the spec.

## Python CLI

```powershell
python Plugins/AIAssetPipeline/Resources/Python/ai_asset_pipeline/cli.py validate-spec <spec.json>
python Plugins/AIAssetPipeline/Resources/Python/ai_asset_pipeline/cli.py package <spec.json>
python Plugins/AIAssetPipeline/Resources/Python/ai_asset_pipeline/cli.py validate-manifest <manifest.json>
python Plugins/AIAssetPipeline/Resources/Python/ai_asset_pipeline/cli.py plan-import <manifest.json>
python Plugins/AIAssetPipeline/Resources/Python/ai_asset_pipeline/cli.py validate-tspec-links Docs/Tasarim/UI_TSpecs
python Plugins/AIAssetPipeline/Resources/Python/ai_asset_pipeline/cli.py create-smoke-fixture --project-root .
```

`ue-import` calls the editor-side `asset_pipeline_import_manifest` command
through MCPToolkit and requires a running editor with the plugin loaded.

## Editor Commands

These commands are registered through MCPToolkit's extension command registry:

- `asset_pipeline_status()`
- `asset_pipeline_import_manifest(manifest_path, force=false)` with write scope
- `asset_pipeline_verify_assets(manifest_path)`

The import command validates the manifest alpha contract before touching assets
and only imports textures listed by the manifest.

## TSpec Link

TSpecs may include a top-level optional `assetPipelines` array:

```json
{
  "assetPipelines": [
    {
      "id": "example",
      "spec": "Docs/.../Example.aiasset.json",
      "manifest": "Docs/.../Example_manifest.json",
      "requiredComponentIds": ["body", "button"],
      "texturePackagePath": "/Game/UI/_AIProbe/Example/Textures"
    }
  ]
}
```

Use `texturePackagePaths` instead when one manifest imports into multiple UE
packages, for example a skin-local texture folder plus a shared texture folder.

V1 validates links only. TSpec remains owned by the MCPToolkit UI transfer
workflow, and no TSpec-to-WBP mutation is performed by this plugin.
