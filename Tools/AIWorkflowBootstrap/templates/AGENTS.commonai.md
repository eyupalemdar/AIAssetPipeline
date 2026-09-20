# CommonAI Unreal Workflow Rules

For raster art/import/sampling work, read
`Plugins/AIAssetPipeline/Docs/IMAGE_QUALITY.md` and
`Docs/AI_UI_Transfer/component_recipes/UITexture_Minification.recipe.md`.
Run `doctor --strict` for installed capabilities, then the separate
`quality-smoke`/`ue-sampling` checks for native rendering. Record actual draw
sizes and project-specific sampling values; never copy another game's preset.

These rules protect the AI UI and asset automation workflow in this project.

- Do not mutate production Widget Blueprints before a TSpec exists and passes:

  ```powershell
  powershell -ExecutionPolicy Bypass -File Scripts/ValidateUITSpecs.ps1
  ```

- Use `/Game/UI/_AIProbe/...` for probes and smoke tests before touching
  production UI assets.
- AIAssetPipeline is ingest-only. Source model calls are not part of the runtime
  workflow; provider/model/generation metadata belongs in `source_art.provenance`.
- Production asset mutation requires an explicit manifest and write scope.
- Prefer `Tools/AIWorkflowBootstrap/bootstrap.py doctor --strict` before and
  after workflow updates.
