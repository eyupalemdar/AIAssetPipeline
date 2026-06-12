# CommonAI Unreal Workflow Rules

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
