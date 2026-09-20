# Texture size from actual pixel use

AIAssetPipeline 0.1.17 adds `measure-widget-pixels` and `plan-density`. They
produce a review candidate from the approved full-resolution sources. They do
not select a new production visual set, change a WBP or change device profiles.

The native read-only command `asset_pipeline_measure_widgets` reads live PIE
geometry and calls Unreal's `LocalToViewport` before serializing numeric pixel
coordinates. This includes the accumulated layout/render transform and the
actual viewport mapping. Do not multiply these pixels by DPI a second time.
Keep FGeometry in native code: in the tested UE 5.8 session its Python
round-trip yielded zero sizes while the native reader returned valid geometry.

After updating the native plugin source, regenerate/build the consuming
project's editor and restart it. Check `asset_pipeline_status` for
`widget_pixel_footprints_supported`; a descriptor version alone does not prove
that the new DLL is loaded.

## Bindings and measurement

Use explicit widget names and hash-pin the WBP and the layout assets that
control its placement. Example project-owned bindings:

```json
{
  "$schema": "ai-widget-footprint-bindings-v1",
  "widget_class": "/Game/UI/W_Example.W_Example_C",
  "layout_assets": ["Content/UI/W_Example.uasset", "Content/UI/W_HUD.uasset"],
  "components": [
    {"component": "portrait", "widgets": ["PortraitImage"], "sampling": "full_uv"}
  ]
}
```

Start the intended screen in an owned PIE, let it paint, then run:

```powershell
python Plugins/AIAssetPipeline/Resources/Python/ai_asset_pipeline/cli.py measure-widget-pixels Docs/Art/Bindings.json --project-root . --port <discovered-port> --scenario Desktop_1920x1080
```

The result names a receipt and its SHA-256. Check `viewport_pixels` against the
intended test; window systems can clamp the requested dimensions. A scenario
name does not prove its resolution. Hidden/unarranged instances do not become
valid samples. Measure every visible state and supported viewport needed by
the project, including zoom/animation extrema. Off-screen/clipped full quads
are measured conservatively; the planner does not infer an occlusion discount.

For composite-material cards or rounded/nine-slice widgets, a binding may use
`sampling: "geometry_only"` to read the outer widget footprint without making
a texture-UV claim. `plan-density` deliberately rejects these samples. Audit
the actual renderer's destination rectangles, atlas cell fractions and source
UV crops before calculating texture coverage. Geometry-only measurement never
authorizes resizing an atlas as if its whole image covered the widget.

## Planning policy

```json
{
  "$schema": "ai-texture-density-policy-v1",
  "spec": "Docs/Art/Approved.aiasset.json",
  "spec_sha256": "<64 hex digits>",
  "evidence": [{"path": "Saved/AIAssetPipeline/Density/<run>/result.json", "sha256": "<64 hex digits>"}],
  "required_scenarios": ["Desktop_1920x1080"],
  "expected_viewports": {"Desktop_1920x1080": [1920, 1080]},
  "texels_per_pixel": 1.25,
  "max_texture_dimension": 1024,
  "max_rgba8_mip_bytes": 8388608,
  "allow_downsize": false,
  "candidate_package": "/Game/UI/DensityCandidates/Example"
}
```

`1.25` is an explicit project choice of sampling headroom, not a universal
quality threshold. The planner takes each component's maximum measured width
and height, applies that headroom, and preserves the existing texture aspect
and source sampling box. It keeps adequate existing textures by default. Source
detail is bounded by both the original canvas and the sampling span; empty
padding does not count as extra source detail. Source/platform limits are
reported as unmet demand rather than passed coverage. A role unmeasured in any
required scenario prevents complete coverage; a wholly unmeasured role keeps
its current size even when downsizing is enabled.
Each receipt must also match that scenario's `expected_viewports` dimensions;
a mislabeled or OS-clamped viewport is rejected before any candidate is written.

```powershell
python Plugins/AIAssetPipeline/Resources/Python/ai_asset_pipeline/cli.py plan-density Docs/Art/DensityPolicy.json --project-root . --output-dir Docs/Art/DensityReview01
python Plugins/AIAssetPipeline/Resources/Python/ai_asset_pipeline/cli.py package Docs/Art/DensityReview01/Candidate.aiasset.json --project-root .
```

The output directory and UE candidate namespace must be fresh. The generated
spec retains original paths/hashes and samples every size/mip from those
originals; it never enlarges a smaller production derivative to invent detail.
Import compression is explicitly BGRA8/UI so dimensions alone cannot switch a
candidate to block compression. Existing source approval, original-density
probe, authored-mip, material sampling and production approval rules apply.

## Acceptance and limits

- `Plan.json` records current/candidate sizes, source limits, per-scenario
  observations, texels per pixel and full-chain logical RGBA8 payload.
- Payload is not measured GPU allocation: alignment and allocator overhead
  are excluded. Shared uses of one component do not multiply its texture cost.
  Verify actual native texture memory and target-device performance separately.
- V1 handles standalone approved RGBA with full UVs. Atlas cells, tiled UVs,
  nine-slice regions and material UV remapping need their own footprint
  contract; do not declare them full UV just to pass a check. Retainer/render
  targets are rejected pending measurement of their intermediate resolution.
- Native texture/resource dimensions, mips and filtering still require
  `asset_pipeline_verify_assets`. Coverage cannot prove visible sharpness;
  compare the candidate against the current version and original-density
  reference at equal physical draw size, including the important small details.
- `production_ready` remains false. No automatic visual-set promotion,
  texture replacement, UI layout edit or device-profile change is performed.

The source image model is independent of this contract. Keep each approved
source's real provenance when using Image 2.5 or another model in a new project.
