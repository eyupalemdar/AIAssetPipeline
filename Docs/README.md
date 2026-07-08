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

## Approved Source Target-Size Workflow

Use `processing_mode: "approved_source_target_size"` when source art is already
approved and the task is to produce draw-size-aware runtime PNGs without asking
an image model to reinterpret the design.

The mode performs:

- chroma-key to alpha before resize
- optional alpha mask opening/closing
- detached alpha speckle removal before and after resize
- visible magenta/chroma despill
- premultiplied-alpha Lanczos resize
- transparent RGB dilation
- hidden saturated RGB/chroma neutralization
- outer alpha clear

Spec-level defaults can be set under `approved_source_target_size` and
overridden per component with the same object:

```json
{
  "approved_source_target_size": {
    "pre_speckle_min_area": 8,
    "post_speckle_min_area": 12,
    "alpha_open_iterations": 0,
    "alpha_close_iterations": 0
  }
}
```

For visual QC, enable diagnostic review outputs:

```json
{
  "reviews": {
    "diagnostics": {
      "alpha_mask": true,
      "matte_issue_overlay": true
    }
  }
}
```

The manifest diagnostics include alpha component counts and small-component
counts so target-size packages can be reviewed for residual speckles instead of
relying only on chroma-key counters.

## Soft Glow Runtime-Size Workflow

Use `processing_mode: "soft_glow_resize"` for approved halo, bloom, glow,
reflection, and soft-shadow source art when the final runtime texture is much
smaller than the source-quality Image 2.0 output.

This is not the default for all small UI textures. It is an opt-in path for
soft alpha/falloff assets where normal premultiplied Lanczos resize can compress
source perimeter noise into a visible outer rim or band.

The mode performs:

- premultiplied-alpha resize
- alpha median/gaussian denoise
- weak-perimeter alpha feathering
- configurable border fade
- optional palette remap driven by source brightness
- transparent RGB dilation and hidden artifact neutralization

Example:

```json
{
  "component_id": "active_avatar_back_halo",
  "source_art_id": "approved_halo",
  "selector": { "type": "full_image" },
  "draw_rect": [-107, -80, 631, 471],
  "target_size": [284, 212],
  "texture_type": "glow",
  "processing_mode": "soft_glow_resize",
  "soft_glow_resize": {
    "alpha_low_cutoff": 0.035,
    "alpha_high_cutoff": 0.92,
    "alpha_gamma": 1.18,
    "alpha_scale": 0.86,
    "border_fade_px": 7,
    "palette": {
      "outer": [47, 142, 79],
      "body": [101, 201, 65],
      "core": [183, 245, 29],
      "core_mix": 0.55
    }
  }
}
```

Review assemblies and contact sheets also use premultiplied-alpha downscaling.
Assembly previews are matte-cleaned before the alpha mask and matte-issue
overlay are written, so the review image does not introduce low-alpha RGB or
chroma artifacts that are absent from the runtime PNGs.

For icon-only overlays derived from existing button art, enable the optional
postprocess cleanup:

```json
{
  "postprocess": {
    "button_icon_overlay_cleanup": {
      "small_component_min_area": 96,
      "neutral_haze_max_rgb": 118,
      "neutral_haze_max_saturation": 48,
      "weak_dark_max_alpha": 170,
      "weak_dark_max_rgb": 105,
      "very_weak_alpha": 34
    }
  }
}
```

This keeps the accepted bronze/gold icon material while removing neutral gray
difference-mask residue and detached alpha speckles from derived button-icon
overlays. It is a salvage path for already accepted derived art, not the
preferred AAA route for small glyphs.

## Vector/SDF Icon Workflow

Use `processing_mode: "vector_sdf_icon"` for small UI glyphs that must be
clean, repeatable, and free from Image 2.0 painterly noise or button-face
difference-mask residue.

The mode does not read a raster source image for the component. Instead, it
renders an analytical glyph mask at high supersampling, applies a controlled
canonical bronze material fill, uses optional signed-distance data for bevel
edge shading when OpenCV is available, and writes a normal runtime PNG through
the same manifest/review path as other components.

```json
{
  "component_id": "mic_icon_unmuted",
  "draw_rect": [220, 56, 50, 50],
  "target_size": [100, 100],
  "texture_type": "icon",
  "processing_mode": "vector_sdf_icon",
  "vector_icon": {
    "glyph": "mic_unmuted",
    "supersample": 8,
    "bevel_edge_px": 2.0,
    "alpha_floor": 4,
    "transparent_rgb_dilation": 16
  },
  "alpha_contract_policy": {
    "allow_low_alpha_saturated_rgb_artifacts": true,
    "reason": "Intentional clean bronze anti-aliased vector/SDF edge pixels."
  }
}
```

Supported glyphs: `mic_unmuted`, `mic_muted`, `add_friend`, and
`self_balance`.

Use this mode when the source problem is icon geometry/material contamination,
especially full-button-minus-shell extraction. Chroma keying, rembg-style
segmentation, and trimap matting can refine a recoverable matte, but they cannot
reconstruct a clean tiny glyph after the icon and textured button face have
already been mixed together.
