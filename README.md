# AIAssetPipeline

Provider-agnostic Unreal Editor plugin for turning already-generated source art
plus metadata into UE-ready texture packages with manifest validation, review
outputs, and guarded editor import.

AIAssetPipeline does not call image models and does not mutate Widget
Blueprints. Image providers such as Image 2.0 or other generators are recorded
only as `source_art[].provenance` metadata.

## What It Provides

- JSON spec and manifest contracts for source art ingestion.
- Python packaging CLI for alpha cleanup, component selection, runtime PNG
  output, review sheets, and import planning.
- Unreal Editor extension commands registered through MCPToolkit:
  `asset_pipeline_status`, `asset_pipeline_import_manifest`, and
  `asset_pipeline_verify_assets`.
- TSpec asset-link validation through optional top-level `assetPipelines`.
- `AIWorkflowBootstrap` tooling for installing MCPToolkit + AIAssetPipeline +
  TSpec workflow files into another Unreal project.

## Quick Start

Install both required plugins into a target Unreal project:

```powershell
python Tools/AIWorkflowBootstrap/bootstrap.py install --project D:\Path\To\UnrealProject --asset-source-root . --mcp-source-root D:\Path\To\UnrealMCPToolkit
```

Create and package a deterministic smoke fixture:

```powershell
python Plugins/AIAssetPipeline/Resources/Python/ai_asset_pipeline/cli.py create-smoke-fixture --project-root D:\Path\To\UnrealProject
python Plugins/AIAssetPipeline/Resources/Python/ai_asset_pipeline/cli.py package D:\Path\To\UnrealProject\Docs\AIAssetPipeline\Smoke\specs\AIAssetPipelineSmoke.aiasset.json --project-root D:\Path\To\UnrealProject
```

Plan the Unreal import without touching assets:

```powershell
python Plugins/AIAssetPipeline/Resources/Python/ai_asset_pipeline/cli.py plan-import D:\Path\To\UnrealProject\Docs\AIAssetPipeline\Smoke\review\AIAssetPipelineSmoke_manifest.json --project-root D:\Path\To\UnrealProject
```

## Documentation

- [Docs/README.md](Docs/README.md) - workflow and command details.
- [Docs/MIGRATION.md](Docs/MIGRATION.md) - migration notes from the older
  Image2-specific ProjectOkey scripts.
- [Tools/AIWorkflowBootstrap/README.md](Tools/AIWorkflowBootstrap/README.md) -
  cross-project installation and update tool.

## License

AIAssetPipeline is released under the MIT License.

Runtime editor import depends on
[UnrealMCPToolkit](https://github.com/eyupalemdar/UnrealMCPToolkit), which is a
separate plugin with its own license. Review that dependency before
redistributing combined plugin bundles.
