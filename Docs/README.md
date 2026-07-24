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

## Canonical Shape and Texture Quality Contracts

Specs can declare reusable `canonical_shapes` (`rounded_rectangle` or `circle`)
and reference them from components with `canonical_shape_id`.
`canonical_shape_color` performs an aspect-preserving cover resize in linear
light with premultiplied alpha, then replaces alpha with the deterministic
canonical mask. It never performs non-uniform stretch. Circle components may
request inward outer-band RGB repair and tangential blur. Warm tile bodies may
also use `directional_sidewall_grade` to preserve a declared sidewall width
while grading right/bottom regions darker and no more yellow than the face.

`canonical_shape_shadow_mask` creates a true single-channel PNG directly from
the canonical mask, with configurable offline blur and a zero outer border.
Use `texture_type: "mask"` and explicit grayscale settings for this output.

`shared_alpha_contracts` compare decoded alpha bytes (including SHA-256 and
maximum byte delta) across components. Component `quality_gates` support exact
canonical alpha/bbox checks, Lab region deltas, circle radial deviation, outer
ring Delta-E p99, single-channel mode, and zero-border checks. Gates are
blocking by default; `blocking: false` is reserved for review-only comparison
artifacts whose measured failure must remain in the manifest but must never be
selected for production.

Each component can declare `ue_texture`. UI color textures use
`UserInterface2D`, sRGB, UI LOD, NoMipmaps, Clamp, Bilinear, and NeverStream.
Single-channel masks use `Grayscale`, `TSF_G8`, and `srgb: false` with the same
LOD/address/filter/streaming policy. Packaging, import planning, editor import,
and verification reject unsupported settings fail-closed.

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

## Opaque Full-Frame Color Textures

Backgrounds and other edge-to-edge color textures may opt out of the normal
transparent-corner/transparent-edge contract with an explicit component policy:

```json
{
  "texture_type": "color",
  "processing_mode": "copy_exact",
  "alpha_contract_policy": {
    "allow_opaque_full_frame": true,
    "reason": "Approved edge-to-edge opaque background texture."
  }
}
```

This waiver is fail-closed. It is accepted only when every output alpha byte is
`255`, the alpha bounding box covers the complete image, and the component has
the explicit policy plus a provenance reason. Partially transparent edges do
not qualify. The waiver is recorded in the manifest audit trail.

## Nameplate Timer AAA Workflows

Use `processing_mode: "nameplate_timer_aaa_fill_resize"` for individually
generated timer body sources that already match the final timer aspect. This
mode removes the flat chroma background, crops to the generated alpha bbox,
validates the selected art against the target ratio, and fits it onto the target
canvas with uniform scaling. It does not replace the source alpha with a
synthetic rounded pill.

Use `processing_mode: "nameplate_timer_aaa_edge_resize"` for separately
generated boundary-fracture sources. This mode preserves the fracture face and
attached sparks as one edge texture; it does not crop down to particle-only
fragments. Pair it with `timer_boundary_diagnostics.require_boundary_face` so
`validate-manifest` fails when the edge texture lacks enough solid fill mass
near the active boundary.

Use `processing_mode: "nameplate_timer_aaa_edge_flipbook_atlas"` for V03-style
multi-frame boundary textures. The source is a 4x3 packed-channel sheet on a
black background, with twelve frames in row-major order. The pipeline splits
the raw sheet into cells, crops visible packed-mask pixels in each cell, and
fits the result into the contractual 256x128 runtime frames. This lets Image
2.0's generated canvas be normalized without changing the final atlas contract.
The runtime atlas keeps the channel contract `R=cut/erosion`, `G=detached
particles`, `B=fracture/hot face`, `A=coverage`, imports with `sRGB=false`, and
records per-frame diagnostics so the manifest fails if any frame is empty, lacks
a required channel, loses the boundary face, or contains the dark right-edge
band.

The static AAA fill/edge modes fail closed when `ratio_delta_pct` exceeds the
configured `aspect_ratio_tolerance_pct` default of `3.0`. The V03 flipbook mode
records the normalized atlas ratio against its fixed frame grid. The manifest
adds
`all_nameplate_timer_aaa_aspect_ratio_within_tolerance`,
`all_nameplate_timer_aaa_final_runtime_safe`, and, for boundary-face checks,
`all_timer_edge_has_boundary_face`.

Example:

```json
{
  "component_id": "nameplate_timer_green_body_704x180",
  "texture_type": "color",
  "processing_mode": "nameplate_timer_aaa_fill_resize",
  "target_size": [704, 180],
  "nameplate_timer_aaa_fill_resize": {
    "alpha_bbox_pad_px": 12,
    "aspect_ratio_tolerance_pct": 3.0
  }
}
```

```json
{
  "component_id": "nameplate_timer_edge_flipbook_rgba_1024x384",
  "texture_type": "packed_mask",
  "processing_mode": "nameplate_timer_aaa_edge_flipbook_atlas",
  "target_size": [1024, 384],
  "nameplate_timer_aaa_edge_flipbook_atlas": {
    "frame_size": [256, 128],
    "columns": 4,
    "rows": 3,
    "frame_count": 12,
    "alpha_bbox_pad_px": 18,
    "channel_min_pixels": 32,
    "alpha_min_pixels": 256
  },
  "timer_boundary_diagnostics": {
    "expected_boundary_x_pct": 0.5,
    "boundary_tolerance_px": 24,
    "dark_right_band_width_px": 14,
    "require_boundary_face": true
  }
}
```

```json
{
  "component_id": "nameplate_timer_green_edge_320x180",
  "texture_type": "glow",
  "processing_mode": "nameplate_timer_aaa_edge_resize",
  "target_size": [320, 180],
  "timer_boundary_diagnostics": {
    "expected_boundary_x_pct": 0.5,
    "boundary_tolerance_px": 24,
    "dark_right_band_width_px": 14,
    "require_boundary_face": true
  }
}
```

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

## Reference Color Pill and Edge Particle Workflows

Use `processing_mode: "reference_color_pill_resize"` when a source-approved
rounded timer/bar asset should keep its RGB, gloss, rim color, and internal
luminance detail, but needs deterministic clean rounded-pill alpha. The mode
chroma-keys/despills the source, premultiplied-resizes the crop, replaces the
alpha channel with an analytical rounded rectangle, and floors visible alpha so
color textures can pass the strict low-alpha artifact contract.

Use `processing_mode: "edge_particle_extract_resize"` for approved timer/bar
spark rows where the right-edge particle field is intentional. The mode crops
the source alpha bounding box's right side, suppresses the solid bar interior
with an X fade, and preserves small alpha components instead of treating them
as speckles. Mark these textures as `texture_type: "glow"` when the low-alpha
colored particles are intentional review/runtime material.

Timer edge outputs can opt into manifest-level boundary diagnostics with
`timer_boundary_diagnostics`. This is intended for nameplate timers where a
dark right-edge band must fail validation and edge particles should stay near
the active fill boundary:

```json
{
  "component_id": "timer_green_spark_edge",
  "texture_type": "glow",
  "processing_mode": "edge_particle_extract_resize",
  "timer_boundary_diagnostics": {
    "expected_boundary_x_pct": 0.5,
    "boundary_tolerance_px": 4,
    "dark_right_band_width_px": 8
  }
}
```

When enabled, output diagnostics include
`timer_dark_right_edge_band_pixels`, `timer_edge_particle_bbox`, and
`timer_edge_boundary_within_tolerance`. The manifest alpha contract adds
`all_no_timer_dark_right_edge_band` and, when an expected boundary is provided,
`all_timer_edge_boundary_within_tolerance`; `validate-manifest` fails if either
is false.

Example:

```json
{
  "component_id": "timer_green_base",
  "texture_type": "color",
  "processing_mode": "reference_color_pill_resize",
  "target_size": [158, 41],
  "reference_color_pill_resize": {
    "outer_box": [2.0, 3.0, 156.0, 38.0],
    "radius": 17.5,
    "alpha_cutoff": 0.58,
    "min_visible_alpha": 0.70
  }
}
```

```json
{
  "component_id": "timer_red_spark_edge",
  "texture_type": "glow",
  "processing_mode": "edge_particle_extract_resize",
  "target_size": [64, 41],
  "edge_particle_extract_resize": {
    "source_region_width_px": 150,
    "x_fade_start": 0.14,
    "x_fade_end": 0.48
  }
}
```

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
