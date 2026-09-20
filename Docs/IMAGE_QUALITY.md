# Portable image quality: approved source to Unreal pixels

This workflow ships with AIAssetPipeline 0.1.16. It applies to Image 2.5 or any
other source provider; use the model identity actually supplied by that tool.
Generation and approval remain outside the ingest plugin. Follow the consuming
project's art, probe, TSpec and release policies.

## Ownership

AIAssetPipeline owns approved-source processing, cell-isolated authored mips,
artifact hashes, texture import/readback, explicit material sampling recipes and
their verification. MCPToolkit provides the editor transport, reflection and
native captures. AIWorkflowBootstrap installs the code, schemas and shared
workflow recipes. A project owns its actual art, draw range, atlas layout,
material bindings, bias, platform settings and visual acceptance.

Do not hardcode one project's dimensions or sharpening bias into shared code.
Colour RGBA uses linear-light premultiplied filtering and straight RGBA output;
alpha-zero RGB dilation must preserve alpha/visible colour. Each mip samples
the original source and the same sampling box. Masks and packed data remain
linear and use their dedicated processing paths. Fonts have their own renderer.

## Two independent checks

`bootstrap.py doctor --strict` verifies installation, source/lock integrity and
the named capability catalog. It does **not** claim that a renderer or target
package passed. Run native sampling verification and render acceptance too.

```text
python Plugins/AIAssetPipeline/Resources/Python/ai_asset_pipeline/cli.py validate-sampling <sampling.json> --project-root <project>
python Plugins/AIAssetPipeline/Resources/Python/ai_asset_pipeline/cli.py ue-sampling <sampling.json> --project-root <project> --port <current-editor-port> --apply --save --import-textures
python Plugins/AIAssetPipeline/Resources/Python/ai_asset_pipeline/cli.py ue-sampling <sampling.json> --project-root <project> --port <current-editor-port>
```

The last command only verifies; it never repairs the graph to make its check
pass. All commands reject changed source/output/mip/manifest hashes. The editor
identity must match the requested project before the first mutation. Import is
non-forcing: an existing mismatch is an error. A timeout is not retried blindly.
Inspect the per-request JSON receipt and editor log before resuming.
Cold texture loads can temporarily have no GPU resource or a ready placeholder
at reduced resolution. Verification polls readback for at most 30 seconds while
source/settings/mips match. Persistent resolution caps fail; no mismatch is
accepted as success. Stop PIE before applying/importing/saving sampling recipes;
the orchestrator rejects an active game before its first mutation. Read-only
verification remains available during gameplay.
Legacy manifests without recorded source/runtime hashes must be regenerated
from their approved specs in a new output directory. Do not fill missing hashes
from whatever files happen to exist and describe that as historical verification.

## Recipe

See `Resources/Schemas/material_sampling.v1.json`. Example:

```json
{
  "$schema": "ai-material-sampling-v1",
  "manifest": "Saved/MyRun/review/manifest.json",
  "manifest_sha256": "<sha256 of that exact manifest>",
  "materials": [{
    "asset": "/Game/UI/Materials/M_Candidate",
    "source_asset": "/Game/UI/Materials/M_Current",
    "samples": [{
      "sample": "GlyphSample",
      "component": "glyph_atlas",
      "uv": "AtlasCoordinates",
      "cell": "CellUVTransform",
      "cell_output": "RGBA",
      "bias": 0.0
    }]
  }]
}
```

Bindings select unique material expression descriptions. `source_asset` clones
an existing material if the target is absent; omitting it requires a target
that already exists. It never changes which material a skin or widget selects.
Only named texture samples are modified. Existing composition and outputs stay
connected. Reapplication reuses reserved `AIP_<sample>_*` nodes.

For an atlas, `uv` must be the existing pre-clamp atlas UV expression and `cell`
must be its explicit `(offsetX,offsetY,sizeX,sizeY)` vector, including all RGBA
channels. The manifest supplies texture size and maximum safe LOD. LOD uses UV
derivatives before the clamp; cell UVs inset by half a texel at `ceil(LOD)` so
both trilinear levels remain inside the selected cell. Engine-added mip tails
must never be sampled. For a standalone colour sample, omit `cell` and `uv`;
the tool preserves its UV connection and applies the explicit mip bias.

All consumers overriding a texture parameter must obey the same dimensions,
grid and authored mip contract. The static verifier proves the recipe's named
materials and imported default textures, not every future runtime override.
Record active skin/MIC/MID bindings during actual gameplay acceptance.

## Fresh-project engineering acceptance

Use a disposable UE project for the initial native smoke. Exclude fixtures in
its `Config/DefaultGame.ini` before running:

```ini
[/Script/UnrealEd.ProjectPackagingSettings]
+DirectoriesToNeverCook=(Path="/Game/AIAssetPipelineTests")
```

```text
python Tools/AIWorkflowBootstrap/bootstrap.py quality-smoke --project <project> --run FirstCheck --native --port <current-editor-port>
```

Without `--native`, it checks only packaging and contracts. With `--native` it
checks the editor identity, creates two engineering textures/materials, writes
and validates TSpecs before creating WBPs, imports, applies/verifies sampling,
and captures native Slate output at four sizes. Contrasting red/blue cells test
leakage across cell edges; the standalone texture tests colour and alpha. These
synthetic images are test data, not model-generated or approved product art.
Each run is isolated and cannot overwrite an earlier run. Fixture paths must
remain excluded from every cook. Host validation failures are not bypassed.

Follow with the project's approved actual art at its real pixel sizes and in
Windows/mobile packages. Verify cooked resource sizes, runtime mip availability,
selected consumer overrides and shader errors. A successful editor smoke is not
a target-device performance or visual-quality guarantee.

## Updates

Inspect both plugin worktrees and run doctor before/after updates. Use dry-runs
and `--adopt-existing` for maintained host files. Preserve host-specific policy
and validators. Review every newly installed file and retain lock hashes. A
changed source without its corresponding manifest/recipe is a failure, not a
reason to fall back silently to NoMipmaps. Local changes are not published until
the corresponding plugin release/commit is actually distributed.

Engine references: [Texture mip modes](https://dev.epicgames.com/documentation/unreal-engine/API/Runtime/Engine/ETextureMipValueMode)
and [texture group/platform settings](https://dev.epicgames.com/documentation/unreal-engine/texture-format-support-and-settings-in-unreal-engine).
