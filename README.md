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
- Canonical rounded-rectangle/circle masks, linear-light premultiplied
  aspect-preserving color packaging, byte-identical shared-alpha contracts,
  and true single-channel canonical shadow masks.
- Per-component fail-closed quality gates and guarded UE texture settings for
  UI color and grayscale mask imports.
- Unreal Editor extension commands registered through MCPToolkit:
  `asset_pipeline_status`, `asset_pipeline_import_manifest`, and
  `asset_pipeline_verify_assets`.
- TSpec asset-link validation through optional top-level `assetPipelines`.
- `AIWorkflowBootstrap` tooling for installing MCPToolkit + AIAssetPipeline +
  TSpec workflow files into another Unreal project.

## Quick Start

One-command installer scripts clone/update both public plugin repositories,
install the workflow into a target Unreal project, and run `doctor --strict`.
By default they run in `auto` mode: a new target uses `install`; a target that
already has `Tools/AIWorkflowBootstrap` or
`Tools/AIWorkflowBootstrap/state/lock.json` uses
`update --apply`. The final strict doctor check is executed through the target
project's copied `Tools/AIWorkflowBootstrap/bootstrap.py`.

Run directly from GitHub:

Windows PowerShell:

```powershell
$u='https://raw.githubusercontent.com/eyupalemdar/AIAssetPipeline/main/Tools/AIWorkflowBootstrap/install_commonai_windows.ps1'; $p="$env:TEMP\install_commonai_windows.ps1"; Invoke-WebRequest $u -OutFile $p; powershell -ExecutionPolicy Bypass -File $p -Project D:\Path\To\UnrealProject
```

Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/eyupalemdar/AIAssetPipeline/main/Tools/AIWorkflowBootstrap/install_commonai_linux.sh | bash -s -- --project /path/to/UnrealProject
```

macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/eyupalemdar/AIAssetPipeline/main/Tools/AIWorkflowBootstrap/install_commonai_macos.sh | bash -s -- --project /path/to/UnrealProject
```

Or run from an existing checkout:

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File Tools\AIWorkflowBootstrap\install_commonai_windows.ps1 -Project D:\Path\To\UnrealProject
```

Linux:

```bash
bash Tools/AIWorkflowBootstrap/install_commonai_linux.sh --project /path/to/UnrealProject
```

macOS:

```bash
bash Tools/AIWorkflowBootstrap/install_commonai_macos.sh --project /path/to/UnrealProject
```

GitHub CLI is optional. The scripts use `git` over public HTTPS by default.
Pass `--install-gh` or `-InstallGitHubCli` if you want the script to install
`gh` where the platform package manager supports it.

`AIWorkflowBootstrap` is not an Unreal plugin. It is a stdlib-only management
tool copied to `Tools/AIWorkflowBootstrap` inside each target project. If an
older target such as a previous LyraStarterGame install does not have that
folder yet, rerun one of the installer scripts or `bootstrap.py install/update`;
the Unreal-facing pieces remain the two plugins under `Plugins/`.
Project workflow state is stored next to the bootstrap tool under
`Tools/AIWorkflowBootstrap/state/`. Commit `project.json`, `lock.json`, and the
managed `.gitignore`; rollback backups under `state/backups/` are ignored.

To force a specific bootstrap path, pass `-Mode install` or `-Mode update` on
Windows, or `--mode install` / `--mode update` on Linux and macOS.

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
