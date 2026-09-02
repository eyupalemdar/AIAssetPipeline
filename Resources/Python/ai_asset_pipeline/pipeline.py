from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .image_ops import (
    alpha_bbox,
    chroma_to_alpha,
    clear_outer_alpha,
    clean_button_icon_overlay,
    clean_existing_source_target_size,
    diagnostics,
    dilate_transparent_rgb,
    despill_visible_magenta,
    hidden_saturated_chroma_mask,
    hidden_saturated_rgb_artifact_mask,
    load_rgba,
    resize_edge_particle_extract,
    resize_luma_mask,
    resize_linear_light_premultiplied,
    resize_nameplate_timer_aaa_edge,
    resize_nameplate_timer_aaa_edge_flipbook_atlas,
    resize_nameplate_timer_aaa_fill,
    resize_nameplate_timer_image1_body_luma,
    resize_nameplate_timer_image1_edge_flipbook_atlas,
    resize_premultiplied,
    resize_reference_color_pill,
    resize_soft_glow,
    strict_hsv_chroma_shadow_mask,
    timer_boundary_diagnostics,
)
from .review import (
    clean_review_matte,
    compose_review,
    gaussian_glow_from_alpha,
    make_contact_sheet,
    save_alpha_mask,
    save_checker,
    save_matte_issue_overlay,
)
from .selectors import select_crop
from .spec import (
    LEGACY_SPEC_SCHEMAS,
    MANIFEST_SCHEMA,
    rel,
    resolve_path,
    normalize_project_root,
    source_provenance,
    validate_manifest,
    validate_spec,
)
from .vector_icons import render_vector_sdf_icon


def _canonical_shape_map(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in spec.get("canonical_shapes", [])}


def _canonical_shape_mask(shape: dict[str, Any], target_size: tuple[int, int]) -> Image.Image:
    canvas_size = tuple(int(value) for value in shape.get("canvas_size", target_size))
    if canvas_size != target_size:
        raise ValueError(
            f"canonical shape {shape.get('id', '')} canvas_size {canvas_size} "
            f"does not match component target_size {target_size}"
        )
    x0, y0, x1, y1 = (int(value) for value in shape["bbox"])
    supersample = max(1, int(shape.get("supersample", 4)))
    high_size = (target_size[0] * supersample, target_size[1] * supersample)
    mask = Image.new("L", high_size, 0)
    draw = ImageDraw.Draw(mask)
    box = (
        x0 * supersample,
        y0 * supersample,
        x1 * supersample - 1,
        y1 * supersample - 1,
    )
    shape_type = str(shape.get("type", ""))
    if shape_type == "rounded_rectangle":
        radius = float(shape.get("radius_px", 0.0)) * supersample
        draw.rounded_rectangle(box, radius=radius, fill=255)
    elif shape_type == "circle":
        draw.ellipse(box, fill=255)
    else:
        raise ValueError(f"Unsupported canonical shape type: {shape_type}")
    if supersample > 1:
        mask = mask.resize(target_size, Image.Resampling.LANCZOS)
    return mask


def _cover_linear_light_premultiplied(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    cleaned = chroma_to_alpha(image.convert("RGBA"))
    crop = cleaned.crop(alpha_bbox(cleaned))
    scale = max(float(size[0]) / float(crop.width), float(size[1]) / float(crop.height))
    resized_size = (
        max(size[0], int(math.ceil(crop.width * scale))),
        max(size[1], int(math.ceil(crop.height * scale))),
    )
    resized = resize_linear_light_premultiplied(crop, resized_size, 0)
    left = (resized.width - size[0]) // 2
    top = (resized.height - size[1]) // 2
    return resized.crop((left, top, left + size[0], top + size[1]))


def _repair_circle_outer_rgb(
    rgb: np.ndarray,
    shape: dict[str, Any],
    band_px: float,
    tangential_blur_px: float,
) -> np.ndarray:
    if band_px <= 0:
        return rgb
    x0, y0, x1, y1 = (float(value) for value in shape["bbox"])
    center_x = (x0 + x1 - 1.0) * 0.5
    center_y = (y0 + y1 - 1.0) * 0.5
    radius = min(x1 - x0, y1 - y0) * 0.5
    yy, xx = np.mgrid[0 : rgb.shape[0], 0 : rgb.shape[1]].astype(np.float32)
    dx = xx - center_x
    dy = yy - center_y
    distance = np.sqrt(dx * dx + dy * dy)
    ring = (distance <= radius) & (distance >= radius - float(band_px))
    safe_distance = np.maximum(distance, 1.0e-5)
    sample_radius = np.maximum(0.0, radius - float(band_px) - 0.5)
    sample_x = np.clip(np.rint(center_x + dx * sample_radius / safe_distance), 0, rgb.shape[1] - 1).astype(np.int32)
    sample_y = np.clip(np.rint(center_y + dy * sample_radius / safe_distance), 0, rgb.shape[0] - 1).astype(np.int32)
    repaired = rgb.copy()
    repaired[ring] = rgb[sample_y[ring], sample_x[ring]]
    if tangential_blur_px > 0:
        blurred = np.asarray(
            Image.fromarray(repaired.astype(np.uint8), "RGB").filter(
                ImageFilter.GaussianBlur(radius=float(tangential_blur_px))
            ),
            dtype=np.uint8,
        )
        repaired[ring] = blurred[ring]
    return repaired


def _canonical_shape_color(
    selected: Image.Image,
    target_size: tuple[int, int],
    shape: dict[str, Any],
    config: dict[str, Any],
) -> Image.Image:
    x0, y0, x1, y1 = (int(value) for value in shape["bbox"])
    region_size = (x1 - x0, y1 - y0)
    fitted = _cover_linear_light_premultiplied(selected, region_size)
    canvas = Image.new("RGBA", target_size, (0, 0, 0, 0))
    canvas.alpha_composite(fitted, (x0, y0))
    arr = np.asarray(canvas, dtype=np.uint8).copy()
    arr = dilate_transparent_rgb(arr, iterations=max(target_size))
    if str(shape.get("type")) == "circle":
        arr[:, :, :3] = _repair_circle_outer_rgb(
            arr[:, :, :3],
            shape,
            float(config.get("rgb_repair_inner_band_px", 0.0)),
            float(config.get("tangential_blur_px", 0.0)),
        )
    sidewall_grade = config.get("directional_sidewall_grade")
    if isinstance(sidewall_grade, dict):
        arr[:, :, :3] = _grade_directional_sidewalls(arr, shape, sidewall_grade)
    arr[:, :, 3] = np.asarray(_canonical_shape_mask(shape, target_size), dtype=np.uint8)
    return Image.fromarray(arr, "RGBA")


def _canonical_shape_shadow_mask(
    target_size: tuple[int, int],
    shape: dict[str, Any],
    config: dict[str, Any],
) -> Image.Image:
    blur_radius = float(config.get("blur_radius_px", 4.5))
    clear_outer = max(1, int(config.get("clear_outer_px", 1)))
    mask = _canonical_shape_mask(shape, target_size)
    if blur_radius > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    arr = np.asarray(mask, dtype=np.uint8).copy()
    arr[:clear_outer, :] = 0
    arr[-clear_outer:, :] = 0
    arr[:, :clear_outer] = 0
    arr[:, -clear_outer:] = 0
    return Image.fromarray(arr, "L")


def package_spec(
    spec_path: Path,
    validate_only: bool = False,
    project_root: Path | str | None = None,
) -> dict[str, Any]:
    spec_path = spec_path.resolve()
    root = normalize_project_root(project_root, spec_path)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    warnings = validate_spec(spec, root)
    if validate_only:
        return {"ok": True, "spec": rel(root, spec_path), "project_root": str(root), "warnings": warnings}

    runtime_dir = resolve_path(root, spec["runtime_output_dir"])
    review_dir = resolve_path(root, spec["review_output_dir"])
    runtime_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)

    source_art = {str(item["id"]): item for item in spec["source_art"]}
    outputs: list[dict[str, Any]] = []
    for component in spec["components"]:
        outputs.append(_write_component(root, spec, source_art, component, runtime_dir))

    for component in spec.get("derived_components", []):
        outputs.append(_write_derived_component(root, spec, outputs, component, runtime_dir))

    outputs.sort(key=lambda item: int(item["z_order"]))
    reviews = _write_reviews(root, spec, outputs, review_dir)
    manifest = _build_manifest(root, spec_path, spec, outputs, reviews)
    manifest_warnings = validate_manifest(manifest)
    manifest_path = review_dir / str(spec.get("manifest_name", "ai_asset_pipeline_manifest.json"))
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "manifest": rel(root, manifest_path),
        "project_root": str(root),
        "component_count": len(outputs),
        "alpha_contract": manifest["alpha_contract"],
        "warnings": warnings + manifest_warnings,
    }


def validate_manifest_file(manifest_path: Path, project_root: Path | str | None = None) -> dict[str, Any]:
    root = normalize_project_root(project_root, manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    warnings = validate_manifest(manifest)
    return {
        "ok": True,
        "manifest": rel(root, manifest_path.resolve()),
        "project_root": str(root),
        "component_count": len(manifest["outputs"]),
        "warnings": warnings,
    }


def _write_component(
    root: Path,
    spec: dict[str, Any],
    source_art: dict[str, Any],
    component: dict[str, Any],
    runtime_dir: Path,
) -> dict[str, Any]:
    processing_mode = str(component.get("processing_mode", "resize_premultiplied"))
    clear_outer_pixels = int(spec.get("processing", {}).get("clear_outer_alpha_px", 1))
    target_size = _target_size(spec, component)
    canonical_shapes = _canonical_shape_map(spec)
    canonical_shape_id = str(component.get("canonical_shape_id", ""))
    canonical_shape = canonical_shapes.get(canonical_shape_id) if canonical_shape_id else None

    if processing_mode == "canonical_shape_shadow_mask":
        if canonical_shape is None:
            raise ValueError(f"{component['component_id']} requires a valid canonical_shape_id")
        runtime = _canonical_shape_shadow_mask(
            target_size,
            canonical_shape,
            component.get("canonical_shape_shadow_mask", {}),
        )
        runtime_file = runtime_dir / f"{component['runtime_asset_name']}.png"
        runtime.save(runtime_file)
        source_info = {
            "path": "derived-from-canonical-shape",
            "prompt_files": [],
            "provenance": {
                "provider": "local-deterministic-processing",
                "model": "ai_asset_pipeline_canonical_shape_shadow_mask",
                "generation_id": f"{component.get('component_id', '')}:{canonical_shape_id}",
                "notes": "Generated from the declared canonical shape; no model-generated shadow pixels are used.",
            },
        }
        return _output_item(
            root,
            spec,
            component,
            source_info,
            runtime_file,
            target_size,
            target_size,
            _component_diagnostics(runtime, spec, component),
            {"selector": "canonical_shape_shadow_mask", "canonical_shape_id": canonical_shape_id},
        )

    if processing_mode == "vector_sdf_icon":
        runtime = render_vector_sdf_icon(target_size, component.get("vector_icon", {}))
        runtime = _apply_postprocess(runtime, component.get("postprocess", {}))
        runtime_file = runtime_dir / f"{component['runtime_asset_name']}.png"
        runtime.save(runtime_file)
        source_info = _procedural_vector_icon_source_info(component)
        selector_info = {
            "selector": "vector_sdf_icon",
            "glyph": str(component.get("vector_icon", {}).get("glyph", "")),
        }
        return _output_item(
            root,
            spec,
            component,
            source_info,
            runtime_file,
            target_size,
            target_size,
            _component_diagnostics(runtime, spec, component),
            selector_info,
        )

    source_info = source_art[str(component["source_art_id"])]
    source_path = resolve_path(root, str(source_info["path"]))
    selected, selector_info = select_crop(load_rgba(source_path), component["selector"])
    metadata_source_size = selected.size

    if processing_mode == "source_quality_clean":
        runtime = _clean_source_quality(selected, clear_outer_pixels)
        target_size = runtime.size

    if processing_mode in {"passthrough", "copy_exact"}:
        if selected.size != target_size:
            raise ValueError(
                f"{component['component_id']} uses {processing_mode} but source size "
                f"{selected.size} does not match target_size {target_size}"
            )
        runtime = selected
    elif processing_mode == "approved_source_target_size":
        runtime = clean_existing_source_target_size(
            selected,
            target_size,
            clear_outer_pixels,
            **_cleanup_config(spec, component),
        )
    elif processing_mode in {"resize_premultiplied", "resize"}:
        runtime = resize_premultiplied(
            selected,
            target_size,
            clear_outer_pixels,
        )
    elif processing_mode == "canonical_shape_color":
        if canonical_shape is None:
            raise ValueError(f"{component['component_id']} requires a valid canonical_shape_id")
        runtime = _canonical_shape_color(
            selected,
            target_size,
            canonical_shape,
            component.get("canonical_shape_color", {}),
        )
    elif processing_mode == "soft_glow_resize":
        runtime = resize_soft_glow(
            selected,
            target_size,
            clear_outer_pixels=max(2, clear_outer_pixels),
            **_soft_glow_config(spec, component),
        )
    elif processing_mode == "luma_mask_resize":
        runtime = resize_luma_mask(
            selected,
            target_size,
            clear_outer_pixels=max(1, clear_outer_pixels),
            **_luma_mask_config(spec, component),
        )
    elif processing_mode == "reference_color_pill_resize":
        runtime = resize_reference_color_pill(
            selected,
            target_size,
            clear_outer_pixels=max(1, clear_outer_pixels),
            **_reference_color_pill_config(spec, component),
        )
    elif processing_mode == "edge_particle_extract_resize":
        runtime = resize_edge_particle_extract(
            selected,
            target_size,
            clear_outer_pixels=max(1, clear_outer_pixels),
            **_edge_particle_extract_config(spec, component),
        )
    elif processing_mode == "nameplate_timer_aaa_fill_resize":
        fill_config = _nameplate_timer_aaa_fill_config(spec, component)
        metadata_source_size = _nameplate_timer_aaa_metadata_source_size(selected, fill_config)
        runtime = resize_nameplate_timer_aaa_fill(
            selected,
            target_size,
            clear_outer_pixels=max(1, clear_outer_pixels),
            **fill_config,
        )
    elif processing_mode == "nameplate_timer_aaa_edge_resize":
        edge_config = _nameplate_timer_aaa_edge_config(spec, component)
        metadata_source_size = _nameplate_timer_aaa_metadata_source_size(selected, edge_config)
        runtime = resize_nameplate_timer_aaa_edge(
            selected,
            target_size,
            clear_outer_pixels=max(1, clear_outer_pixels),
            **edge_config,
        )
    elif processing_mode == "nameplate_timer_aaa_edge_flipbook_atlas":
        atlas_config = _nameplate_timer_aaa_edge_flipbook_atlas_config(spec, component)
        metadata_source_size = _nameplate_timer_aaa_flipbook_metadata_source_size(selected, atlas_config)
        runtime = resize_nameplate_timer_aaa_edge_flipbook_atlas(
            selected,
            target_size,
            clear_outer_pixels=max(1, clear_outer_pixels),
            **atlas_config,
        )
    elif processing_mode == "nameplate_timer_image1_body_luma":
        body_config = _nameplate_timer_image1_body_luma_config(spec, component)
        metadata_source_size = target_size
        runtime = resize_nameplate_timer_image1_body_luma(
            selected,
            target_size,
            clear_outer_pixels=max(1, clear_outer_pixels),
            **body_config,
        )
    elif processing_mode == "nameplate_timer_image1_edge_flipbook_atlas":
        atlas_config = _nameplate_timer_image1_edge_flipbook_atlas_config(spec, component)
        metadata_source_size = _nameplate_timer_aaa_flipbook_metadata_source_size(selected, atlas_config)
        runtime = resize_nameplate_timer_image1_edge_flipbook_atlas(
            selected,
            target_size,
            clear_outer_pixels=max(1, clear_outer_pixels),
            **atlas_config,
        )
    elif processing_mode in {"fit_aspect_premultiplied", "contain_premultiplied"}:
        runtime = _fit_aspect_premultiplied(selected, target_size, clear_outer_pixels)
    elif processing_mode == "nine_slice_prerender":
        runtime = _nine_slice_prerender(selected, target_size, component, clear_outer_pixels)
    elif processing_mode == "source_quality_clean":
        pass
    else:
        raise ValueError(f"Unsupported processing_mode for {component['component_id']}: {processing_mode}")
    runtime = _apply_postprocess(runtime, component.get("postprocess", {}))
    runtime_file = runtime_dir / f"{component['runtime_asset_name']}.png"
    runtime.save(runtime_file)
    return _output_item(
        root,
        spec,
        component,
        source_info,
        runtime_file,
        metadata_source_size,
        target_size,
        _component_diagnostics(runtime, spec, component),
        selector_info,
    )


def _procedural_vector_icon_source_info(component: dict[str, Any]) -> dict[str, Any]:
    glyph = str(component.get("vector_icon", {}).get("glyph", ""))
    return {
        "path": "procedural-vector-icon",
        "prompt_files": [],
        "provenance": {
            "provider": "local-deterministic-processing",
            "model": "ai_asset_pipeline_vector_sdf_icon",
            "generation_id": f"{component.get('component_id', '')}:{glyph}",
            "notes": "Generated from an analytical vector/SDF-style icon mask and controlled bronze material fill; no derived button-difference source was used.",
        },
    }


def _cleanup_config(spec: dict[str, Any], component: dict[str, Any]) -> dict[str, int | bool]:
    global_config = spec.get("approved_source_target_size", {})
    component_config = component.get("approved_source_target_size", {})
    if not isinstance(global_config, dict):
        global_config = {}
    if not isinstance(component_config, dict):
        component_config = {}
    merged = {
        "alpha_open_iterations": 0,
        "alpha_close_iterations": 0,
        "pre_speckle_min_area": 0,
        "post_speckle_min_area": 12,
        "linear_light": False,
        "strict_hsv_post_cleanup": False,
        "fit_visible_alpha_to_safe_area": False,
        **global_config,
        **component_config,
    }
    return {
        "alpha_open_iterations": int(merged.get("alpha_open_iterations", 0)),
        "alpha_close_iterations": int(merged.get("alpha_close_iterations", 0)),
        "pre_speckle_min_area": int(merged.get("pre_speckle_min_area", 0)),
        "post_speckle_min_area": int(merged.get("post_speckle_min_area", 12)),
        "linear_light": bool(merged.get("linear_light", False)),
        "strict_hsv_post_cleanup": bool(merged.get("strict_hsv_post_cleanup", False)),
        "fit_visible_alpha_to_safe_area": bool(merged.get("fit_visible_alpha_to_safe_area", False)),
    }


def _soft_glow_config(spec: dict[str, Any], component: dict[str, Any]) -> dict[str, Any]:
    global_config = spec.get("soft_glow_resize", {})
    component_config = component.get("soft_glow_resize", {})
    if not isinstance(global_config, dict):
        global_config = {}
    if not isinstance(component_config, dict):
        component_config = {}
    merged: dict[str, Any] = {
        "alpha_median_size": 3,
        "alpha_blur_radius": 1.15,
        "alpha_low_cutoff": 0.035,
        "alpha_high_cutoff": 0.92,
        "alpha_gamma": 1.18,
        "alpha_scale": 0.86,
        "alpha_cap": 0.86,
        "border_fade_px": 7.0,
        "brightness_blur_radius": 1.25,
        **global_config,
        **component_config,
    }
    palette = merged.get("palette")
    return {
        "alpha_median_size": int(merged.get("alpha_median_size", 3)),
        "alpha_blur_radius": float(merged.get("alpha_blur_radius", 1.15)),
        "alpha_low_cutoff": float(merged.get("alpha_low_cutoff", 0.035)),
        "alpha_high_cutoff": float(merged.get("alpha_high_cutoff", 0.92)),
        "alpha_gamma": float(merged.get("alpha_gamma", 1.18)),
        "alpha_scale": float(merged.get("alpha_scale", 0.86)),
        "alpha_cap": float(merged.get("alpha_cap", 0.86)),
        "border_fade_px": float(merged.get("border_fade_px", 7.0)),
        "brightness_blur_radius": float(merged.get("brightness_blur_radius", 1.25)),
        "palette": palette if isinstance(palette, dict) else None,
    }


def _luma_mask_config(spec: dict[str, Any], component: dict[str, Any]) -> dict[str, Any]:
    global_config = spec.get("luma_mask_resize", {})
    component_config = component.get("luma_mask_resize", {})
    if not isinstance(global_config, dict):
        global_config = {}
    if not isinstance(component_config, dict):
        component_config = {}
    merged: dict[str, Any] = {
        "luma_gamma": 1.0,
        "luma_scale": 1.0,
        "luma_floor": 0.0,
        "alpha_scale": 1.0,
        "alpha_cap": 1.0,
        "transparent_rgb_dilation": 64,
        **global_config,
        **component_config,
    }
    return {
        "luma_gamma": float(merged.get("luma_gamma", 1.0)),
        "luma_scale": float(merged.get("luma_scale", 1.0)),
        "luma_floor": float(merged.get("luma_floor", 0.0)),
        "alpha_scale": float(merged.get("alpha_scale", 1.0)),
        "alpha_cap": float(merged.get("alpha_cap", 1.0)),
        "transparent_rgb_dilation": int(merged.get("transparent_rgb_dilation", 64)),
    }


def _reference_color_pill_config(spec: dict[str, Any], component: dict[str, Any]) -> dict[str, Any]:
    global_config = spec.get("reference_color_pill_resize", {})
    component_config = component.get("reference_color_pill_resize", {})
    if not isinstance(global_config, dict):
        global_config = {}
    if not isinstance(component_config, dict):
        component_config = {}
    merged: dict[str, Any] = {
        "outer_box": None,
        "radius": 17.5,
        "supersample": 16,
        "edge_blur_px": 0.02,
        "alpha_cutoff": 0.58,
        "min_visible_alpha": 0.68,
        "transparent_rgb_dilation": 0,
        **global_config,
        **component_config,
    }
    return {
        "outer_box": merged.get("outer_box"),
        "radius": float(merged.get("radius", 17.5)),
        "supersample": int(merged.get("supersample", 16)),
        "edge_blur_px": float(merged.get("edge_blur_px", 0.02)),
        "alpha_cutoff": float(merged.get("alpha_cutoff", 0.58)),
        "min_visible_alpha": float(merged.get("min_visible_alpha", 0.68)),
        "transparent_rgb_dilation": int(merged.get("transparent_rgb_dilation", 0)),
    }


def _edge_particle_extract_config(spec: dict[str, Any], component: dict[str, Any]) -> dict[str, Any]:
    global_config = spec.get("edge_particle_extract_resize", {})
    component_config = component.get("edge_particle_extract_resize", {})
    if not isinstance(global_config, dict):
        global_config = {}
    if not isinstance(component_config, dict):
        component_config = {}
    merged: dict[str, Any] = {
        "source_region_width_px": 150,
        "source_right_pad_px": 8,
        "source_vertical_pad_px": 6,
        "x_fade_start": 0.18,
        "x_fade_end": 0.52,
        "alpha_gamma": 0.85,
        "alpha_scale": 1.0,
        "min_alpha": 0.015,
        **global_config,
        **component_config,
    }
    return {
        "source_region_width_px": int(merged.get("source_region_width_px", 150)),
        "source_right_pad_px": int(merged.get("source_right_pad_px", 8)),
        "source_vertical_pad_px": int(merged.get("source_vertical_pad_px", 6)),
        "x_fade_start": float(merged.get("x_fade_start", 0.18)),
        "x_fade_end": float(merged.get("x_fade_end", 0.52)),
        "alpha_gamma": float(merged.get("alpha_gamma", 0.85)),
        "alpha_scale": float(merged.get("alpha_scale", 1.0)),
        "min_alpha": float(merged.get("min_alpha", 0.015)),
    }


def _nameplate_timer_aaa_config(
    spec: dict[str, Any],
    component: dict[str, Any],
    mode_key: str,
    defaults: dict[str, Any],
) -> dict[str, Any]:
    global_config = spec.get(mode_key, {})
    component_config = component.get(mode_key, {})
    if not isinstance(global_config, dict):
        global_config = {}
    if not isinstance(component_config, dict):
        component_config = {}
    merged: dict[str, Any] = {
        **defaults,
        **global_config,
        **component_config,
    }
    return {
        "alpha_bbox_pad_px": int(merged.get("alpha_bbox_pad_px", defaults["alpha_bbox_pad_px"])),
        "aspect_ratio_tolerance_pct": float(
            merged.get("aspect_ratio_tolerance_pct", defaults["aspect_ratio_tolerance_pct"])
        ),
        "min_visible_alpha": float(merged.get("min_visible_alpha", defaults["min_visible_alpha"])),
        "alpha_floor_cutoff": float(merged.get("alpha_floor_cutoff", defaults["alpha_floor_cutoff"])),
        "transparent_rgb_dilation": int(
            merged.get("transparent_rgb_dilation", defaults["transparent_rgb_dilation"])
        ),
    }


def _nameplate_timer_aaa_fill_config(spec: dict[str, Any], component: dict[str, Any]) -> dict[str, Any]:
    return _nameplate_timer_aaa_config(
        spec,
        component,
        "nameplate_timer_aaa_fill_resize",
        {
            "alpha_bbox_pad_px": 12,
            "aspect_ratio_tolerance_pct": 3.0,
            "min_visible_alpha": 0.66,
            "alpha_floor_cutoff": 0.025,
            "transparent_rgb_dilation": 64,
        },
    )


def _nameplate_timer_aaa_edge_config(spec: dict[str, Any], component: dict[str, Any]) -> dict[str, Any]:
    return _nameplate_timer_aaa_config(
        spec,
        component,
        "nameplate_timer_aaa_edge_resize",
        {
            "alpha_bbox_pad_px": 18,
            "aspect_ratio_tolerance_pct": 3.0,
            "min_visible_alpha": 0.0,
            "alpha_floor_cutoff": 0.015,
            "transparent_rgb_dilation": 64,
        },
    )


def _nameplate_timer_aaa_edge_flipbook_atlas_config(
    spec: dict[str, Any],
    component: dict[str, Any],
) -> dict[str, Any]:
    global_config = spec.get("nameplate_timer_aaa_edge_flipbook_atlas", {})
    component_config = component.get("nameplate_timer_aaa_edge_flipbook_atlas", {})
    if not isinstance(global_config, dict):
        global_config = {}
    if not isinstance(component_config, dict):
        component_config = {}
    merged: dict[str, Any] = {
        "frame_size": [256, 128],
        "columns": 4,
        "rows": 3,
        "frame_count": 12,
        "alpha_bbox_pad_px": 18,
        "aspect_ratio_tolerance_pct": 8.0,
        "min_visible_alpha": 0.0,
        "alpha_floor_cutoff": 0.012,
        "transparent_rgb_dilation": 64,
        "background_max_rgb_cutoff": 6,
        **global_config,
        **component_config,
    }
    frame_size = merged.get("frame_size", [256, 128])
    if not isinstance(frame_size, list) or len(frame_size) != 2:
        raise ValueError("nameplate_timer_aaa_edge_flipbook_atlas.frame_size must be [width, height]")
    return {
        "frame_size": (int(frame_size[0]), int(frame_size[1])),
        "columns": int(merged.get("columns", 4)),
        "rows": int(merged.get("rows", 3)),
        "frame_count": int(merged.get("frame_count", 12)),
        "alpha_bbox_pad_px": int(merged.get("alpha_bbox_pad_px", 18)),
        "aspect_ratio_tolerance_pct": float(merged.get("aspect_ratio_tolerance_pct", 8.0)),
        "min_visible_alpha": float(merged.get("min_visible_alpha", 0.0)),
        "alpha_floor_cutoff": float(merged.get("alpha_floor_cutoff", 0.012)),
        "transparent_rgb_dilation": int(merged.get("transparent_rgb_dilation", 64)),
        "background_max_rgb_cutoff": int(merged.get("background_max_rgb_cutoff", 6)),
    }


def _nameplate_timer_image1_body_luma_config(
    spec: dict[str, Any],
    component: dict[str, Any],
) -> dict[str, Any]:
    global_config = spec.get("nameplate_timer_image1_body_luma", {})
    component_config = component.get("nameplate_timer_image1_body_luma", {})
    if not isinstance(global_config, dict):
        global_config = {}
    if not isinstance(component_config, dict):
        component_config = {}
    merged: dict[str, Any] = {
        "row_index": 0,
        "component_min_area": 1800,
        "horizontal_inset_pct": 0.012,
        "vertical_inset_pct": 0.075,
        "corner_radius_pct": 0.45,
        "luma_gamma": 0.92,
        "luma_scale": 1.0,
        "transparent_rgb_dilation": 64,
        **global_config,
        **component_config,
    }
    return {
        "row_index": int(merged.get("row_index", 0)),
        "component_min_area": int(merged.get("component_min_area", 1800)),
        "horizontal_inset_pct": float(merged.get("horizontal_inset_pct", 0.012)),
        "vertical_inset_pct": float(merged.get("vertical_inset_pct", 0.075)),
        "corner_radius_pct": float(merged.get("corner_radius_pct", 0.45)),
        "luma_gamma": float(merged.get("luma_gamma", 0.92)),
        "luma_scale": float(merged.get("luma_scale", 1.0)),
        "transparent_rgb_dilation": int(merged.get("transparent_rgb_dilation", 64)),
    }


def _nameplate_timer_image1_edge_flipbook_atlas_config(
    spec: dict[str, Any],
    component: dict[str, Any],
) -> dict[str, Any]:
    global_config = spec.get("nameplate_timer_image1_edge_flipbook_atlas", {})
    component_config = component.get("nameplate_timer_image1_edge_flipbook_atlas", {})
    if not isinstance(global_config, dict):
        global_config = {}
    if not isinstance(component_config, dict):
        component_config = {}
    merged: dict[str, Any] = {
        "frame_size": [256, 128],
        "columns": 4,
        "rows": 3,
        "frame_count": 12,
        "row_indices": [1, 2, 3, 4],
        "component_min_area": 900,
        "boundary_left_px": 92,
        "boundary_right_px": 108,
        "vertical_pad_px": 12,
        "alpha_floor_cutoff": 0.010,
        "transparent_rgb_dilation": 8,
        "aspect_ratio_tolerance_pct": 8.0,
        **global_config,
        **component_config,
    }
    frame_size = merged.get("frame_size", [256, 128])
    if not isinstance(frame_size, list) or len(frame_size) != 2:
        raise ValueError("nameplate_timer_image1_edge_flipbook_atlas.frame_size must be [width, height]")
    row_indices = merged.get("row_indices", [1, 2, 3, 4])
    if not isinstance(row_indices, list):
        row_indices = [1, 2, 3, 4]
    return {
        "frame_size": (int(frame_size[0]), int(frame_size[1])),
        "columns": int(merged.get("columns", 4)),
        "rows": int(merged.get("rows", 3)),
        "frame_count": int(merged.get("frame_count", 12)),
        "row_indices": tuple(int(value) for value in row_indices),
        "component_min_area": int(merged.get("component_min_area", 900)),
        "boundary_left_px": int(merged.get("boundary_left_px", 92)),
        "boundary_right_px": int(merged.get("boundary_right_px", 108)),
        "vertical_pad_px": int(merged.get("vertical_pad_px", 12)),
        "alpha_floor_cutoff": float(merged.get("alpha_floor_cutoff", 0.010)),
        "transparent_rgb_dilation": int(merged.get("transparent_rgb_dilation", 8)),
        "aspect_ratio_tolerance_pct": float(merged.get("aspect_ratio_tolerance_pct", 8.0)),
    }


def _nameplate_timer_aaa_aspect_tolerance(spec: dict[str, Any], component: dict[str, Any]) -> float | None:
    processing_mode = str(component.get("processing_mode", "resize_premultiplied"))
    if processing_mode == "nameplate_timer_aaa_fill_resize":
        return _nameplate_timer_aaa_fill_config(spec, component)["aspect_ratio_tolerance_pct"]
    if processing_mode == "nameplate_timer_aaa_edge_resize":
        return _nameplate_timer_aaa_edge_config(spec, component)["aspect_ratio_tolerance_pct"]
    if processing_mode == "nameplate_timer_aaa_edge_flipbook_atlas":
        return _nameplate_timer_aaa_edge_flipbook_atlas_config(spec, component)["aspect_ratio_tolerance_pct"]
    if processing_mode == "nameplate_timer_image1_body_luma":
        return 0.0
    if processing_mode == "nameplate_timer_image1_edge_flipbook_atlas":
        return _nameplate_timer_image1_edge_flipbook_atlas_config(spec, component)["aspect_ratio_tolerance_pct"]
    return None


def _nameplate_timer_aaa_metadata_source_size(
    selected: Image.Image,
    config: dict[str, Any],
) -> tuple[int, int]:
    cleaned = chroma_to_alpha(selected)
    x0, y0, x1, y1 = alpha_bbox(cleaned)
    pad = max(0, int(config.get("alpha_bbox_pad_px", 0)))
    left = max(0, x0 - pad)
    top = max(0, y0 - pad)
    right = min(cleaned.width, x1 + pad)
    bottom = min(cleaned.height, y1 + pad)
    return max(1, right - left), max(1, bottom - top)


def _nameplate_timer_aaa_flipbook_metadata_source_size(
    selected: Image.Image,
    config: dict[str, Any],
) -> tuple[int, int]:
    frame_size = config.get("frame_size", [256, 128])
    frame_w, frame_h = int(frame_size[0]), int(frame_size[1])
    columns = int(config.get("columns", 4))
    rows = int(config.get("rows", 3))
    return (frame_w * columns, frame_h * rows)


def _fit_aspect_premultiplied(
    image: Image.Image,
    size: tuple[int, int],
    clear_outer_pixels: int = 1,
) -> Image.Image:
    target_w, target_h = size
    if target_w <= 0 or target_h <= 0:
        raise ValueError(f"Invalid target size: {size}")
    source_w, source_h = image.size
    if source_w <= 0 or source_h <= 0:
        raise ValueError(f"Invalid source size: {image.size}")

    scale = min(target_w / source_w, target_h / source_h)
    fit_size = (
        max(1, int(round(source_w * scale))),
        max(1, int(round(source_h * scale))),
    )
    fitted = resize_premultiplied(image, fit_size, 0)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.alpha_composite(
        fitted,
        ((target_w - fit_size[0]) // 2, (target_h - fit_size[1]) // 2),
    )
    arr = np.asarray(canvas.convert("RGBA"), dtype=np.uint8)
    arr = dilate_transparent_rgb(arr, iterations=64)
    arr = clear_outer_alpha(arr, clear_outer_pixels)
    arr = dilate_transparent_rgb(arr, iterations=64)
    hidden_chroma = hidden_saturated_chroma_mask(arr)
    if hidden_chroma.any():
        arr[hidden_chroma, :3] = 0
    hidden_rgb_artifact = hidden_saturated_rgb_artifact_mask(arr)
    if hidden_rgb_artifact.any():
        arr[hidden_rgb_artifact, :3] = 0
    return Image.fromarray(arr, "RGBA")


def _clean_source_quality(image: Image.Image, clear_outer_pixels: int = 1) -> Image.Image:
    arr = np.asarray(chroma_to_alpha(image), dtype=np.uint8)
    arr = dilate_transparent_rgb(arr, iterations=8)
    arr = despill_visible_magenta(arr)
    arr = dilate_transparent_rgb(arr, iterations=12)
    arr = clear_outer_alpha(arr, clear_outer_pixels)
    arr = dilate_transparent_rgb(arr, iterations=12)
    hidden_chroma = hidden_saturated_chroma_mask(arr)
    if hidden_chroma.any():
        arr[hidden_chroma, :3] = 0
    hidden_rgb_artifact = hidden_saturated_rgb_artifact_mask(arr)
    if hidden_rgb_artifact.any():
        arr[hidden_rgb_artifact, :3] = 0
    return Image.fromarray(arr, "RGBA")


def _margin_tuple(value: Any, owner: str) -> tuple[int, int, int, int]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"{owner} must be [left, top, right, bottom]")
    margin = tuple(int(v) for v in value)
    if any(v < 0 for v in margin):
        raise ValueError(f"{owner} values must be >= 0")
    return margin


def _resize_rgba_premultiplied_raw(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    width, height = size
    if width <= 0 or height <= 0:
        return Image.new("RGBA", (max(0, width), max(0, height)), (0, 0, 0, 0))
    arr = np.asarray(image.convert("RGBA"), dtype=np.float32)
    alpha = arr[:, :, 3:4] / 255.0
    premul = arr.copy()
    premul[:, :, :3] *= alpha
    resized = Image.fromarray(np.clip(premul, 0, 255).astype(np.uint8), "RGBA").resize(
        size, Image.Resampling.LANCZOS
    )
    rarr = np.asarray(resized, dtype=np.float32)
    a = rarr[:, :, 3:4] / 255.0
    nonzero = a[:, :, 0] > 1.0 / 255.0
    rarr[nonzero, :3] = np.clip(rarr[nonzero, :3] / a[nonzero], 0, 255)
    return Image.fromarray(np.clip(rarr, 0, 255).astype(np.uint8), "RGBA")


def _nine_slice_prerender(
    image: Image.Image,
    size: tuple[int, int],
    component: dict[str, Any],
    clear_outer_pixels: int = 1,
) -> Image.Image:
    config = component.get("nine_slice", component.get("slice", {}))
    if not isinstance(config, dict):
        raise ValueError(f"{component['component_id']} nine_slice config must be an object")

    source_margin = _margin_tuple(config.get("source_margin"), f"{component['component_id']}.source_margin")
    target_margin = _margin_tuple(config.get("target_margin"), f"{component['component_id']}.target_margin")

    src = _clean_source_quality(image, 0)
    sw, sh = src.size
    tw, th = size
    sl, st, sr, sb = source_margin
    tl, tt, tr, tb = target_margin

    if sl + sr >= sw or st + sb >= sh:
        raise ValueError(f"{component['component_id']} source margins exceed source size {src.size}")
    if tl + tr >= tw or tt + tb >= th:
        raise ValueError(f"{component['component_id']} target margins exceed target size {size}")

    sx = [0, sl, sw - sr, sw]
    sy = [0, st, sh - sb, sh]
    tx = [0, tl, tw - tr, tw]
    ty = [0, tt, th - tb, th]

    out = Image.new("RGBA", size, (0, 0, 0, 0))
    for row in range(3):
        for col in range(3):
            src_box = (sx[col], sy[row], sx[col + 1], sy[row + 1])
            dst_box = (tx[col], ty[row], tx[col + 1], ty[row + 1])
            dst_size = (dst_box[2] - dst_box[0], dst_box[3] - dst_box[1])
            if dst_size[0] <= 0 or dst_size[1] <= 0:
                continue
            patch = _resize_rgba_premultiplied_raw(src.crop(src_box), dst_size)
            out.alpha_composite(patch, (dst_box[0], dst_box[1]))
    return _clean_source_quality(out, clear_outer_pixels)


def _apply_postprocess(image: Image.Image, config: Any) -> Image.Image:
    if not isinstance(config, dict) or not config:
        return image
    rgba = image.convert("RGBA")
    rounded_rect_luma_rim = config.get("rounded_rect_luma_rim")
    if rounded_rect_luma_rim:
        cleanup_config = rounded_rect_luma_rim if isinstance(rounded_rect_luma_rim, dict) else {}
        rgba = _rounded_rect_luma_rim_postprocess(rgba, cleanup_config)
    rounded_rect_luma_fill = config.get("rounded_rect_luma_fill")
    if rounded_rect_luma_fill:
        cleanup_config = rounded_rect_luma_fill if isinstance(rounded_rect_luma_fill, dict) else {}
        rgba = _rounded_rect_luma_fill_postprocess(rgba, cleanup_config)
    icon_cleanup = config.get("button_icon_overlay_cleanup")
    if icon_cleanup:
        cleanup_config = icon_cleanup if isinstance(icon_cleanup, dict) else {}
        rgba = clean_button_icon_overlay(
            rgba,
            alpha_threshold=int(cleanup_config.get("alpha_threshold", 8)),
            small_component_min_area=int(cleanup_config.get("small_component_min_area", 96)),
            neutral_haze_max_rgb=int(cleanup_config.get("neutral_haze_max_rgb", 118)),
            neutral_haze_max_saturation=int(cleanup_config.get("neutral_haze_max_saturation", 48)),
            weak_dark_max_alpha=int(cleanup_config.get("weak_dark_max_alpha", 170)),
            weak_dark_max_rgb=int(cleanup_config.get("weak_dark_max_rgb", 105)),
            very_weak_alpha=int(cleanup_config.get("very_weak_alpha", 34)),
            dilation_iterations=int(cleanup_config.get("dilation_iterations", 24)),
        )
    unsharp = config.get("unsharp_mask")
    if not isinstance(unsharp, dict):
        return rgba
    rgb = rgba.convert("RGB").filter(
        ImageFilter.UnsharpMask(
            radius=float(unsharp.get("radius", 0.6)),
            percent=int(unsharp.get("percent", 70)),
            threshold=int(unsharp.get("threshold", 2)),
        )
    )
    out = Image.merge("RGBA", (*rgb.split(), rgba.getchannel("A")))
    arr = np.asarray(chroma_to_alpha(out), dtype=np.uint8)
    arr = dilate_transparent_rgb(arr, iterations=18)
    arr = despill_visible_magenta(arr)
    arr = dilate_transparent_rgb(arr, iterations=64)
    hidden_chroma = hidden_saturated_chroma_mask(arr)
    if hidden_chroma.any():
        arr[hidden_chroma, :3] = 0
    hidden_rgb_artifact = hidden_saturated_rgb_artifact_mask(arr)
    if hidden_rgb_artifact.any():
        arr[hidden_rgb_artifact, :3] = 0
    out = Image.fromarray(arr, "RGBA")
    if icon_cleanup:
        cleanup_config = icon_cleanup if isinstance(icon_cleanup, dict) else {}
        out = clean_button_icon_overlay(
            out,
            alpha_threshold=int(cleanup_config.get("alpha_threshold", 8)),
            small_component_min_area=int(cleanup_config.get("small_component_min_area", 96)),
            neutral_haze_max_rgb=int(cleanup_config.get("neutral_haze_max_rgb", 118)),
            neutral_haze_max_saturation=int(cleanup_config.get("neutral_haze_max_saturation", 48)),
            weak_dark_max_alpha=int(cleanup_config.get("weak_dark_max_alpha", 170)),
            weak_dark_max_rgb=int(cleanup_config.get("weak_dark_max_rgb", 105)),
            very_weak_alpha=int(cleanup_config.get("very_weak_alpha", 34)),
            dilation_iterations=int(cleanup_config.get("dilation_iterations", 24)),
        )
    return out


def _postprocess_rect(config: dict[str, Any], key: str, default: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    raw = config.get(key, default)
    if not isinstance(raw, list) or len(raw) != 4:
        raise ValueError(f"rounded_rect_luma_rim postprocess requires {key}=[x0,y0,x1,y1]")
    return tuple(float(v) for v in raw)


def _rounded_rect_luma_rim_postprocess(image: Image.Image, config: dict[str, Any]) -> Image.Image:
    width, height = image.size
    scale = max(3, int(config.get("supersample", 16)))
    outer_box = _postprocess_rect(config, "outer_box", (0.0, 0.0, float(width), float(height)))
    inner_box = _postprocess_rect(config, "inner_box", (4.0, 4.0, float(width - 4), float(height - 4)))
    outer_radius = max(0.0, float(config.get("outer_radius", 8.0)))
    inner_radius = max(0.0, float(config.get("inner_radius", 5.0)))
    edge_blur_px = max(0.0, float(config.get("edge_blur_px", 0.0)))
    halo_blur_px = max(0.0, float(config.get("halo_blur_px", 0.0)))
    halo_alpha_scale = max(0.0, float(config.get("halo_alpha_scale", 0.0)))
    halo_luma = max(0.0, float(config.get("halo_luma", 0.55)))
    luma_value = int(np.clip(int(config.get("luma_value", 255)), 0, 255))
    luma_mode = str(config.get("luma_mode", "constant"))
    clear_outer_pixels = max(0, int(config.get("clear_outer_alpha_px", 1)))
    transparent_rgb_dilation = max(0, int(config.get("transparent_rgb_dilation", 64)))

    high_size = (width * scale, height * scale)
    alpha_hi = Image.new("L", high_size, 0)
    draw = ImageDraw.Draw(alpha_hi)
    scaled_outer = tuple(int(round(value * scale)) for value in outer_box)
    scaled_inner = tuple(int(round(value * scale)) for value in inner_box)
    draw.rounded_rectangle(scaled_outer, radius=int(round(outer_radius * scale)), fill=255)
    draw.rounded_rectangle(scaled_inner, radius=int(round(inner_radius * scale)), fill=0)
    if edge_blur_px > 0:
        alpha_hi = alpha_hi.filter(ImageFilter.GaussianBlur(radius=edge_blur_px * scale))
    alpha = np.asarray(alpha_hi.resize((width, height), Image.Resampling.LANCZOS), dtype=np.float32) / 255.0
    if halo_blur_px > 0 and halo_alpha_scale > 0:
        halo_image = Image.fromarray(np.clip(alpha * 255.0, 0, 255).astype(np.uint8), "L")
        halo_image = halo_image.filter(ImageFilter.GaussianBlur(radius=halo_blur_px))
        halo = np.asarray(halo_image, dtype=np.float32) / 255.0
        outside = np.clip(halo - alpha, 0.0, 1.0)
    else:
        outside = np.zeros((height, width), dtype=np.float32)
    alpha_out = np.clip(np.maximum(alpha, outside * halo_alpha_scale), 0.0, 1.0)

    out = np.zeros((height, width, 4), dtype=np.uint8)
    if luma_mode == "alpha_weighted":
        luma = np.clip(alpha * (float(luma_value) / 255.0), 0.0, 1.0)
    else:
        luma = np.where(alpha > 0, float(luma_value) / 255.0, 0.0).astype(np.float32)
    if halo_blur_px > 0 and halo_alpha_scale > 0:
        luma = np.maximum(luma, outside * halo_luma)
    luma8 = np.clip(np.rint(luma * 255.0), 0, 255).astype(np.uint8)
    alpha8 = np.clip(np.rint(alpha_out * 255.0), 0, 255).astype(np.uint8)
    out[:, :, 0] = luma8
    out[:, :, 1] = luma8
    out[:, :, 2] = luma8
    out[:, :, 3] = alpha8
    out[out[:, :, 3] == 0, :3] = 0
    if clear_outer_pixels:
        out = clear_outer_alpha(out, clear_outer_pixels)
    if transparent_rgb_dilation:
        out = dilate_transparent_rgb(out, iterations=transparent_rgb_dilation)
    return Image.fromarray(out, "RGBA")


def _rounded_rect_luma_fill_postprocess(image: Image.Image, config: dict[str, Any]) -> Image.Image:
    width, height = image.size
    scale = max(3, int(config.get("supersample", 16)))
    outer_box = _postprocess_rect(config, "outer_box", (0.0, 0.0, float(width), float(height)))
    radius = max(0.0, float(config.get("radius", 8.0)))
    rim_width = max(0.0, float(config.get("rim_width", 4.0)))
    center_luma = float(config.get("center_luma", 0.0))
    rim_luma = float(config.get("rim_luma", 0.95))
    halo_luma = float(config.get("halo_luma", 0.8))
    halo_alpha_scale = max(0.0, float(config.get("halo_alpha_scale", 0.72)))
    halo_blur_px = max(0.0, float(config.get("halo_blur_px", 5.0)))
    edge_blur_px = max(0.0, float(config.get("edge_blur_px", 0.05)))
    clear_outer_pixels = max(0, int(config.get("clear_outer_alpha_px", 1)))
    transparent_rgb_dilation = max(0, int(config.get("transparent_rgb_dilation", 0)))

    high_size = (width * scale, height * scale)
    outer_hi = Image.new("L", high_size, 0)
    draw = ImageDraw.Draw(outer_hi)
    scaled_outer = tuple(int(round(value * scale)) for value in outer_box)
    draw.rounded_rectangle(scaled_outer, radius=int(round(radius * scale)), fill=255)
    if edge_blur_px > 0:
        outer_hi = outer_hi.filter(ImageFilter.GaussianBlur(radius=edge_blur_px * scale))
    fill = np.asarray(outer_hi.resize((width, height), Image.Resampling.LANCZOS), dtype=np.float32) / 255.0

    inner_box = (
        outer_box[0] + rim_width,
        outer_box[1] + rim_width,
        outer_box[2] - rim_width,
        outer_box[3] - rim_width,
    )
    inner_hi = Image.new("L", high_size, 0)
    draw = ImageDraw.Draw(inner_hi)
    scaled_inner = tuple(int(round(value * scale)) for value in inner_box)
    draw.rounded_rectangle(scaled_inner, radius=int(round(max(0.0, radius - rim_width) * scale)), fill=255)
    if edge_blur_px > 0:
        inner_hi = inner_hi.filter(ImageFilter.GaussianBlur(radius=edge_blur_px * scale))
    inner = np.asarray(inner_hi.resize((width, height), Image.Resampling.LANCZOS), dtype=np.float32) / 255.0

    halo_image = Image.fromarray(np.clip(fill * 255.0, 0, 255).astype(np.uint8), "L")
    if halo_blur_px > 0:
        halo_image = halo_image.filter(ImageFilter.GaussianBlur(radius=halo_blur_px))
    halo = np.asarray(halo_image, dtype=np.float32) / 255.0
    outside = np.clip(halo - fill, 0.0, 1.0)
    rim = np.clip(fill - inner, 0.0, 1.0)

    alpha = np.clip(fill + outside * halo_alpha_scale, 0.0, 1.0)
    luma = np.clip(center_luma * inner + rim_luma * rim + halo_luma * outside, 0.0, 1.0)
    luma = np.minimum(luma, np.maximum(alpha, luma * alpha + (alpha > 0.35) * luma * (1.0 - alpha)))

    out = np.zeros((height, width, 4), dtype=np.uint8)
    luma8 = np.clip(np.rint(luma * 255.0), 0, 255).astype(np.uint8)
    alpha8 = np.clip(np.rint(alpha * 255.0), 0, 255).astype(np.uint8)
    out[:, :, 0] = luma8
    out[:, :, 1] = luma8
    out[:, :, 2] = luma8
    out[:, :, 3] = alpha8
    out[out[:, :, 3] == 0, :3] = 0
    if clear_outer_pixels:
        out = clear_outer_alpha(out, clear_outer_pixels)
    if transparent_rgb_dilation:
        out = dilate_transparent_rgb(out, iterations=transparent_rgb_dilation)
    return Image.fromarray(out, "RGBA")


def _write_derived_component(
    root: Path,
    spec: dict[str, Any],
    outputs: list[dict[str, Any]],
    component: dict[str, Any],
    runtime_dir: Path,
) -> dict[str, Any]:
    base_review = {
        "size": spec["reference_size"],
        "component_ids": component["source_component_ids"],
    }
    base = compose_review(root, outputs, base_review)
    glow_cfg = component.get("glow", {})
    glow = gaussian_glow_from_alpha(
        base,
        [int(v) for v in glow_cfg.get("tint", [255, 174, 63, 0])],
        int(glow_cfg.get("blur_radius", 18)),
        float(glow_cfg.get("alpha_scale", 0.7)),
        int(glow_cfg.get("alpha_cap", 160)),
    )
    target_size = _target_size(spec, component)
    resized = glow.resize(target_size, Image.Resampling.LANCZOS)
    arr = dilate_transparent_rgb(np.asarray(resized.convert("RGBA"), dtype=np.uint8))
    clear_px = int(glow_cfg.get("clear_outer_alpha_px", 2))
    if clear_px > 0:
        arr[:clear_px, :, 3] = 0
        arr[-clear_px:, :, 3] = 0
        arr[:, :clear_px, 3] = 0
        arr[:, -clear_px:, 3] = 0
    runtime = Image.fromarray(arr, "RGBA")
    runtime_file = runtime_dir / f"{component['runtime_asset_name']}.png"
    runtime.save(runtime_file)
    source_info = {
        "path": "derived-from-runtime-silhouette",
        "prompt_files": [],
        "provenance": {
            "provider": "derived",
            "model": "ai_asset_pipeline",
            "generation_id": str(component.get("component_id", "")),
            "notes": "Generated locally from packaged component alpha silhouette.",
        },
    }
    return _output_item(
        root,
        spec,
        component,
        source_info,
        runtime_file,
        tuple(int(v) for v in spec["reference_size"]),
        target_size,
        _component_diagnostics(runtime, spec, component),
        {"selector": "derived_glow_from_assembly"},
    )


def _rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    srgb = np.clip(rgb.astype(np.float64) / 255.0, 0.0, 1.0)
    linear = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
    xyz = np.tensordot(
        linear,
        np.array(
            [
                [0.4124564, 0.3575761, 0.1804375],
                [0.2126729, 0.7151522, 0.0721750],
                [0.0193339, 0.1191920, 0.9503041],
            ],
            dtype=np.float64,
        ).T,
        axes=1,
    )
    xyz /= np.array([0.95047, 1.0, 1.08883], dtype=np.float64)
    delta = 6.0 / 29.0
    fxyz = np.where(xyz > delta**3, np.cbrt(xyz), xyz / (3.0 * delta**2) + 4.0 / 29.0)
    return np.stack(
        (
            116.0 * fxyz[..., 1] - 16.0,
            500.0 * (fxyz[..., 0] - fxyz[..., 1]),
            200.0 * (fxyz[..., 1] - fxyz[..., 2]),
        ),
        axis=-1,
    )


def _lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    values = lab.astype(np.float64)
    fy = (values[..., 0] + 16.0) / 116.0
    fx = fy + values[..., 1] / 500.0
    fz = fy - values[..., 2] / 200.0
    delta = 6.0 / 29.0
    fxyz = np.stack((fx, fy, fz), axis=-1)
    xyz = np.where(
        fxyz > delta,
        fxyz**3,
        3.0 * delta**2 * (fxyz - 4.0 / 29.0),
    )
    xyz *= np.array([0.95047, 1.0, 1.08883], dtype=np.float64)
    linear = np.tensordot(
        xyz,
        np.array(
            [
                [3.2404542, -1.5371385, -0.4985314],
                [-0.9692660, 1.8760108, 0.0415560],
                [0.0556434, -0.2040259, 1.0572252],
            ],
            dtype=np.float64,
        ).T,
        axes=1,
    )
    linear = np.clip(linear, 0.0, 1.0)
    srgb = np.where(linear <= 0.0031308, linear * 12.92, 1.055 * linear ** (1.0 / 2.4) - 0.055)
    return np.clip(np.rint(srgb * 255.0), 0.0, 255.0).astype(np.uint8)


def _grade_directional_sidewalls(
    arr: np.ndarray,
    shape: dict[str, Any],
    config: dict[str, Any],
) -> np.ndarray:
    """Neutralize and darken right/bottom sidewalls without changing geometry.

    The grade is expressed relative to the component's central face color so it
    remains useful for warm, cool, and neutral source art. A short inner feather
    reaches a flat outer plateau, making the measured sidewall independent of
    antialiased silhouette pixels.
    """
    x0, y0, x1, y1 = (int(value) for value in shape["bbox"])
    canonical_alpha = np.asarray(_canonical_shape_mask(shape, (arr.shape[1], arr.shape[0])), dtype=np.uint8)
    visible = canonical_alpha >= 127
    center_x0 = int(round(x0 + (x1 - x0) * 0.35))
    center_x1 = int(round(x0 + (x1 - x0) * 0.65))
    center_y0 = int(round(y0 + (y1 - y0) * 0.35))
    center_y1 = int(round(y0 + (y1 - y0) * 0.65))
    center_keep = visible[center_y0:center_y1, center_x0:center_x1]
    lab = _rgb_to_lab(arr[:, :, :3])
    center_lab = np.median(lab[center_y0:center_y1, center_x0:center_x1][center_keep], axis=0)
    feather_fraction = max(0.05, min(1.0, float(config.get("inner_feather_fraction", 0.35))))
    neutral_offset = float(config.get("delta_b_offset", -0.75))

    def apply_grade(weight: np.ndarray, delta_l: float) -> None:
        active = visible & (weight > 0.0)
        if not active.any():
            return
        w = weight[active]
        current = lab[active]
        target_l = float(center_lab[0]) + float(delta_l)
        target_b = float(center_lab[2]) + neutral_offset
        current[:, 0] = np.minimum(current[:, 0], current[:, 0] * (1.0 - w) + target_l * w)
        chroma_target = np.minimum(current[:, 2], target_b)
        current[:, 2] = current[:, 2] * (1.0 - w) + chroma_target * w
        lab[active] = current

    right_width = max(0, int(config.get("right_width_px", 0)))
    if right_width > 0:
        start = max(x0, x1 - right_width)
        xx = np.arange(arr.shape[1], dtype=np.float64)[None, :]
        progress = np.clip((xx - start) / max(1.0, (right_width - 1) * feather_fraction), 0.0, 1.0)
        smooth = progress * progress * (3.0 - 2.0 * progress)
        apply_grade(np.broadcast_to(smooth, visible.shape), float(config.get("right_delta_l", -8.0)))

    bottom_height = max(0, int(config.get("bottom_height_px", 0)))
    if bottom_height > 0:
        start = max(y0, y1 - bottom_height)
        yy = np.arange(arr.shape[0], dtype=np.float64)[:, None]
        progress = np.clip((yy - start) / max(1.0, (bottom_height - 1) * feather_fraction), 0.0, 1.0)
        smooth = progress * progress * (3.0 - 2.0 * progress)
        apply_grade(np.broadcast_to(smooth, visible.shape), float(config.get("bottom_delta_l", -12.0)))

    repaired = arr[:, :, :3].copy()
    repaired[visible] = _lab_to_rgb(lab)[visible]
    return repaired


def _region_median_lab(
    arr: np.ndarray,
    alpha: np.ndarray,
    shape: dict[str, Any],
    region: list[float],
) -> np.ndarray:
    x0, y0, x1, y1 = (float(value) for value in shape["bbox"])
    rx0 = max(0, int(math.floor(x0 + (x1 - x0) * float(region[0]))))
    ry0 = max(0, int(math.floor(y0 + (y1 - y0) * float(region[1]))))
    rx1 = min(arr.shape[1], int(math.ceil(x0 + (x1 - x0) * float(region[2]))))
    ry1 = min(arr.shape[0], int(math.ceil(y0 + (y1 - y0) * float(region[3]))))
    keep = alpha[ry0:ry1, rx0:rx1] >= 127
    if not keep.any():
        raise ValueError(f"quality gate region has no visible pixels: {region}")
    lab = _rgb_to_lab(arr[ry0:ry1, rx0:rx1, :3][keep])
    return np.median(lab, axis=0)


def _bilinear_sample_plane(plane: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, float(plane.shape[1] - 1))
    y = np.clip(y, 0.0, float(plane.shape[0] - 1))
    x0 = np.floor(x).astype(np.int32)
    y0 = np.floor(y).astype(np.int32)
    x1 = np.minimum(x0 + 1, plane.shape[1] - 1)
    y1 = np.minimum(y0 + 1, plane.shape[0] - 1)
    wx = x - x0
    wy = y - y0
    return (
        plane[y0, x0] * (1.0 - wx) * (1.0 - wy)
        + plane[y0, x1] * wx * (1.0 - wy)
        + plane[y1, x0] * (1.0 - wx) * wy
        + plane[y1, x1] * wx * wy
    )


def _circle_radial_deviation(alpha: np.ndarray, shape: dict[str, Any]) -> float:
    x0, y0, x1, y1 = (float(value) for value in shape["bbox"])
    center_x = (x0 + x1 - 1.0) * 0.5
    center_y = (y0 + y1 - 1.0) * 0.5
    expected_radius = min(x1 - x0, y1 - y0) * 0.5
    radii = np.arange(max(0.0, expected_radius - 3.0), expected_radius + 3.0, 0.05, dtype=np.float64)
    measured: list[float] = []
    for angle in np.linspace(0.0, math.tau, 720, endpoint=False):
        values = _bilinear_sample_plane(
            alpha.astype(np.float64),
            center_x + np.cos(angle) * radii,
            center_y + np.sin(angle) * radii,
        )
        inside = np.where(values >= 127.5)[0]
        if inside.size:
            measured.append(float(radii[int(inside[-1])]))
    if not measured:
        return float("inf")
    values = np.asarray(measured, dtype=np.float64)
    return float(np.max(np.abs(values - np.median(values))))


def _circle_outer_ring_delta_e_p99(
    arr: np.ndarray,
    shape: dict[str, Any],
    band_px: float,
) -> float:
    x0, y0, x1, y1 = (float(value) for value in shape["bbox"])
    center_x = (x0 + x1 - 1.0) * 0.5
    center_y = (y0 + y1 - 1.0) * 0.5
    radius = min(x1 - x0, y1 - y0) * 0.5
    yy, xx = np.mgrid[0 : arr.shape[0], 0 : arr.shape[1]].astype(np.float64)
    dx = xx - center_x
    dy = yy - center_y
    distance = np.sqrt(dx * dx + dy * dy)
    ring = (arr[:, :, 3] >= 127) & (distance >= radius - float(band_px)) & (distance <= radius)
    if not ring.any():
        return float("inf")
    safe_distance = np.maximum(distance, 1.0e-6)
    sample_radius = max(0.0, radius - float(band_px) - 0.5)
    sample_x = np.clip(np.rint(center_x + dx * sample_radius / safe_distance), 0, arr.shape[1] - 1).astype(np.int32)
    sample_y = np.clip(np.rint(center_y + dy * sample_radius / safe_distance), 0, arr.shape[0] - 1).astype(np.int32)
    ring_lab = _rgb_to_lab(arr[:, :, :3][ring])
    inner_lab = _rgb_to_lab(arr[sample_y[ring], sample_x[ring], :3])
    delta_e = np.linalg.norm(ring_lab - inner_lab, axis=1)
    return float(np.percentile(delta_e, 99.0))


def _evaluate_quality_gates(
    image: Image.Image,
    component: dict[str, Any],
    shape: dict[str, Any] | None,
) -> dict[str, Any]:
    config = component.get("quality_gates", {})
    if not isinstance(config, dict) or not config:
        return {"configured": False, "all_pass": True, "results": {}}
    original = image.copy()
    rgba = np.asarray(original.convert("RGBA"), dtype=np.uint8)
    alpha = rgba[:, :, 3]
    results: dict[str, Any] = {}

    if bool(config.get("require_single_channel", False)):
        passed = original.mode == "L" and len(original.getbands()) == 1
        results["single_channel"] = {"pass": passed, "mode": original.mode, "channel_count": len(original.getbands())}

    if bool(config.get("require_outer_border_zero", False)):
        plane = np.asarray(original if original.mode == "L" else original.getchannel("A"), dtype=np.uint8)
        border = np.concatenate((plane[0, :], plane[-1, :], plane[:, 0], plane[:, -1]))
        maximum = int(border.max()) if border.size else 0
        results["outer_border_zero"] = {"pass": maximum == 0, "max_border_value": maximum}

    threshold = int(config.get("alpha_bbox_threshold", 127))
    if "expected_alpha_bbox" in config:
        ys, xs = np.where(alpha > threshold)
        actual = [int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)] if xs.size else None
        expected = [int(value) for value in config["expected_alpha_bbox"]]
        tolerance = int(config.get("alpha_bbox_tolerance_px", 0))
        passed = actual is not None and all(abs(actual[index] - expected[index]) <= tolerance for index in range(4))
        results["alpha_bbox"] = {"pass": passed, "actual": actual, "expected": expected, "tolerance_px": tolerance}

    if "max_alpha_padding_px" in config or "max_alpha_padding_percent" in config:
        padding_threshold = int(config.get("alpha_padding_threshold", 8))
        ys, xs = np.where(alpha > padding_threshold)
        if xs.size:
            left = int(xs.min())
            top = int(ys.min())
            right = int(alpha.shape[1] - (xs.max() + 1))
            bottom = int(alpha.shape[0] - (ys.max() + 1))
            padding = [left, top, right, bottom]
            padding_percent = [
                (left / float(alpha.shape[1])) * 100.0,
                (top / float(alpha.shape[0])) * 100.0,
                (right / float(alpha.shape[1])) * 100.0,
                (bottom / float(alpha.shape[0])) * 100.0,
            ]
            max_padding = max(padding)
            max_padding_percent = max(padding_percent)
            passed = True
            if "max_alpha_padding_px" in config:
                passed = passed and max_padding <= int(config["max_alpha_padding_px"])
            if "max_alpha_padding_percent" in config:
                passed = passed and max_padding_percent <= float(config["max_alpha_padding_percent"])
            results["alpha_padding"] = {
                "pass": bool(passed),
                "threshold": padding_threshold,
                "padding_ltrb_px": padding,
                "padding_ltrb_percent": [round(value, 4) for value in padding_percent],
                "max_padding_px": max_padding,
                "max_padding_percent": round(max_padding_percent, 4),
                "limit_px": int(config["max_alpha_padding_px"]) if "max_alpha_padding_px" in config else None,
                "limit_percent": float(config["max_alpha_padding_percent"]) if "max_alpha_padding_percent" in config else None,
            }
        else:
            results["alpha_padding"] = {
                "pass": False,
                "threshold": padding_threshold,
                "reason": "no alpha pixels above threshold",
            }

    if "max_visible_chroma_shadow_pixels" in config:
        shadow_threshold = int(config.get("chroma_shadow_alpha_threshold", 8))
        shadow = strict_hsv_chroma_shadow_mask(rgba[:, :, :3]) & (alpha > shadow_threshold)
        count = int(shadow.sum())
        limit = int(config["max_visible_chroma_shadow_pixels"])
        results["visible_chroma_shadow"] = {
            "pass": count <= limit,
            "pixel_count": count,
            "limit": limit,
            "alpha_threshold": shadow_threshold,
        }

    if bool(config.get("require_canonical_alpha_exact", False)):
        if shape is None:
            results["canonical_alpha_exact"] = {"pass": False, "reason": "canonical shape is missing"}
        else:
            canonical = np.asarray(_canonical_shape_mask(shape, original.size), dtype=np.uint8)
            maximum = int(np.abs(alpha.astype(np.int16) - canonical.astype(np.int16)).max())
            results["canonical_alpha_exact"] = {"pass": maximum == 0, "max_byte_delta": maximum}

    if shape is not None and str(shape.get("type")) == "circle":
        if "max_radial_deviation_px" in config:
            deviation = _circle_radial_deviation(alpha, shape)
            limit = float(config["max_radial_deviation_px"])
            results["radial_deviation"] = {"pass": deviation <= limit, "max_deviation_px": round(deviation, 4), "limit_px": limit}
        if "max_outer_ring_delta_e_p99" in config:
            band = float(config.get("outer_ring_width_px", 3.0))
            value = _circle_outer_ring_delta_e_p99(rgba, shape, band)
            limit = float(config["max_outer_ring_delta_e_p99"])
            results["outer_ring_delta_e_p99"] = {"pass": value <= limit, "delta_e_p99": round(value, 4), "limit": limit, "band_px": band}

    lab_gates = config.get("lab_region_deltas", [])
    if isinstance(lab_gates, list):
        for index, gate in enumerate(lab_gates):
            if not isinstance(gate, dict) or shape is None:
                continue
            gate_id = str(gate.get("id", f"lab_region_delta_{index}"))
            reference = _region_median_lab(rgba, alpha, shape, gate["reference_region"])
            sample = _region_median_lab(rgba, alpha, shape, gate["sample_region"])
            delta_l = float(sample[0] - reference[0])
            delta_b = float(sample[2] - reference[2])
            passed = True
            if "delta_l_max" in gate:
                passed = passed and delta_l <= float(gate["delta_l_max"])
            if "delta_l_min" in gate:
                passed = passed and delta_l >= float(gate["delta_l_min"])
            if "delta_b_max" in gate:
                passed = passed and delta_b <= float(gate["delta_b_max"])
            if "delta_b_min" in gate:
                passed = passed and delta_b >= float(gate["delta_b_min"])
            results[gate_id] = {
                "pass": bool(passed),
                "delta_l": round(delta_l, 4),
                "delta_b": round(delta_b, 4),
                "limits": {key: gate[key] for key in ("delta_l_max", "delta_l_min", "delta_b_max", "delta_b_min") if key in gate},
            }

    return {
        "configured": True,
        "blocking": bool(config.get("blocking", True)),
        "all_pass": all(bool(item.get("pass")) for item in results.values()),
        "results": results,
    }


def _component_diagnostics(image: Image.Image, spec: dict[str, Any], component: dict[str, Any]) -> dict[str, object]:
    result = diagnostics(image)
    result["image_mode"] = image.mode
    result["channel_count"] = len(image.getbands())
    processing_mode = str(component.get("processing_mode", ""))
    selector = component.get("selector", {})
    is_raw_packed_copy = (
        str(component.get("texture_type", "")) == "packed_mask"
        and processing_mode in {"copy_exact", "passthrough"}
        and isinstance(selector, dict)
        and str(selector.get("type", "")) == "full_image_raw"
    )
    if processing_mode in {
        "nameplate_timer_aaa_edge_flipbook_atlas",
        "nameplate_timer_image1_edge_flipbook_atlas",
    } or is_raw_packed_copy:
        if not is_raw_packed_copy:
            result.update(_nameplate_timer_aaa_edge_flipbook_atlas_diagnostics(image, spec, component))
        result.update(
            {
                "visible_chroma_key_pixels_alpha_gt_8": 0,
                "visible_magenta_fringe_pixels_alpha_gt_8": 0,
                "low_alpha_saturated_chroma_fringe_pixels": 0,
                "hidden_saturated_chroma_pixels_alpha_eq_0": 0,
                "low_alpha_saturated_rgb_artifact_pixels": 0,
                "hidden_saturated_rgb_artifact_pixels_alpha_eq_0": 0,
                "semantic_packed_mask_rgb_diagnostics_bypassed": True,
            }
        )
        return result

    timer_config = _timer_boundary_diagnostics_config(spec, component)
    if timer_config is not None:
        result.update(timer_boundary_diagnostics(image, timer_config))
    return result


def _timer_boundary_diagnostics_config(spec: dict[str, Any], component: dict[str, Any]) -> dict[str, Any] | None:
    global_config = spec.get("timer_boundary_diagnostics", {})
    component_config = component.get("timer_boundary_diagnostics")
    has_component_config = "timer_boundary_diagnostics" in component

    enabled = False
    merged: dict[str, Any] = {}
    if isinstance(global_config, dict):
        enabled = bool(global_config.get("enabled", False))
        merged.update(global_config)
    elif global_config is True:
        enabled = True

    if has_component_config:
        if isinstance(component_config, dict):
            enabled = bool(component_config.get("enabled", True))
            merged.update(component_config)
        else:
            enabled = bool(component_config)

    if not enabled:
        return None
    merged.pop("enabled", None)
    return merged


def _nameplate_timer_aaa_edge_flipbook_atlas_diagnostics(
    image: Image.Image,
    spec: dict[str, Any],
    component: dict[str, Any],
) -> dict[str, object]:
    processing_mode = str(component.get("processing_mode", ""))
    if processing_mode == "nameplate_timer_image1_edge_flipbook_atlas":
        cfg = _nameplate_timer_image1_edge_flipbook_atlas_config(spec, component)
        local_config = component.get("nameplate_timer_image1_edge_flipbook_atlas", {})
    else:
        cfg = _nameplate_timer_aaa_edge_flipbook_atlas_config(spec, component)
        local_config = component.get("nameplate_timer_aaa_edge_flipbook_atlas", {})
    if not isinstance(local_config, dict):
        local_config = {}
    frame_w, frame_h = cfg["frame_size"]
    columns = int(cfg["columns"])
    rows = int(cfg["rows"])
    frame_count = int(cfg["frame_count"])
    arr = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    expected_size = (frame_w * columns, frame_h * rows)
    size_ok = image.size == expected_size
    frame_summaries: list[dict[str, object]] = []
    timer_cfg = _timer_boundary_diagnostics_config(spec, component)

    channel_min_pixels = int(local_config.get("channel_min_pixels", 16))
    alpha_min_pixels = int(local_config.get("alpha_min_pixels", 64))
    all_channels_present = True
    all_frames_nonempty = True
    all_coverage_contains_rgb = True
    all_timer_no_dark_band = True
    all_timer_has_boundary_face = True
    all_timer_boundary_aligned = True
    any_timer_boundary = False
    any_timer_face = False

    for frame_index in range(frame_count):
        col = frame_index % columns
        row = frame_index // columns
        x0 = col * frame_w
        y0 = row * frame_h
        frame = arr[y0 : y0 + frame_h, x0 : x0 + frame_w, :]
        r_present = int((frame[:, :, 0] > 8).sum())
        g_present = int((frame[:, :, 1] > 8).sum())
        b_present = int((frame[:, :, 2] > 8).sum())
        a_present = int((frame[:, :, 3] > 8).sum())
        rgb_union = (
            (frame[:, :, 0] > 8)
            | (frame[:, :, 1] > 8)
            | (frame[:, :, 2] > 8)
        )
        coverage_contains_rgb = bool(np.all(frame[:, :, 3][rgb_union] > 0)) if rgb_union.any() else False
        frame_channels_present = (
            r_present >= channel_min_pixels
            and g_present >= channel_min_pixels
            and b_present >= channel_min_pixels
            and a_present >= alpha_min_pixels
        )
        all_channels_present = all_channels_present and frame_channels_present
        all_frames_nonempty = all_frames_nonempty and a_present >= alpha_min_pixels
        all_coverage_contains_rgb = all_coverage_contains_rgb and coverage_contains_rgb
        frame_summary: dict[str, object] = {
            "frame": frame_index,
            "r_pixels_gt8": r_present,
            "g_pixels_gt8": g_present,
            "b_pixels_gt8": b_present,
            "a_pixels_gt8": a_present,
            "channels_present": bool(frame_channels_present),
            "coverage_contains_rgb": coverage_contains_rgb,
        }
        if timer_cfg is not None:
            any_timer_boundary = True
            frame_diag = timer_boundary_diagnostics(Image.fromarray(frame, "RGBA"), timer_cfg)
            all_timer_no_dark_band = (
                all_timer_no_dark_band
                and int(frame_diag.get("timer_dark_right_edge_band_pixels", 0)) == 0
            )
            if frame_diag.get("timer_edge_boundary_within_tolerance") is not None:
                all_timer_boundary_aligned = (
                    all_timer_boundary_aligned
                    and bool(frame_diag.get("timer_edge_boundary_within_tolerance"))
                )
            if frame_diag.get("timer_edge_boundary_face_required") is True:
                any_timer_face = True
                all_timer_has_boundary_face = (
                    all_timer_has_boundary_face
                    and bool(frame_diag.get("timer_edge_has_boundary_face"))
                )
            frame_summary.update(
                {
                    "timer_dark_right_edge_band_pixels": frame_diag.get("timer_dark_right_edge_band_pixels"),
                    "timer_edge_boundary_within_tolerance": frame_diag.get("timer_edge_boundary_within_tolerance"),
                    "timer_edge_has_boundary_face": frame_diag.get("timer_edge_has_boundary_face"),
                }
            )
        frame_summaries.append(frame_summary)

    contract_ok = (
        size_ok
        and all_frames_nonempty
        and all_channels_present
        and all_coverage_contains_rgb
        and (not any_timer_boundary or all_timer_no_dark_band)
        and (not any_timer_boundary or all_timer_boundary_aligned)
        and (not any_timer_face or all_timer_has_boundary_face)
    )
    result: dict[str, object] = {
        "timer_edge_flipbook_frame_size": [frame_w, frame_h],
        "timer_edge_flipbook_columns": columns,
        "timer_edge_flipbook_rows": rows,
        "timer_edge_flipbook_frame_count": frame_count,
        "timer_edge_flipbook_size_ok": bool(size_ok),
        "timer_edge_flipbook_all_frames_nonempty": bool(all_frames_nonempty),
        "timer_edge_flipbook_all_channels_present": bool(all_channels_present),
        "timer_edge_flipbook_all_coverage_contains_rgb": bool(all_coverage_contains_rgb),
        "timer_edge_flipbook_contract_ok": bool(contract_ok),
        "timer_edge_flipbook_frames": frame_summaries,
    }
    if any_timer_boundary:
        result.update(
            {
                "timer_dark_right_edge_band_pixels": 0 if all_timer_no_dark_band else 1,
                "timer_edge_boundary_within_tolerance": bool(all_timer_boundary_aligned),
            }
        )
    if any_timer_face:
        result.update(
            {
                "timer_edge_boundary_face_required": True,
                "timer_edge_has_boundary_face": bool(all_timer_has_boundary_face),
            }
        )
    return result


def _target_size(spec: dict[str, Any], component: dict[str, Any]) -> tuple[int, int]:
    if "target_size" in component:
        return tuple(int(v) for v in component["target_size"])
    scale = float(component.get("target_scale", spec.get("processing", {}).get("target_scale", 2)))
    _, _, w, h = [int(v) for v in component["draw_rect"]]
    return int(round(w * scale)), int(round(h * scale))


def _resolved_ue_texture(spec: dict[str, Any], component: dict[str, Any]) -> dict[str, Any]:
    texture_type = str(component.get("texture_type", "color"))
    global_policy = spec.get("ue_import", {}).get("texture_import_policy", {})
    local_policy = component.get("ue_texture", {})
    if not isinstance(global_policy, dict):
        global_policy = {}
    if not isinstance(local_policy, dict):
        local_policy = {}
    merged = {**global_policy, **local_policy}
    requested_source_format = local_policy.get("source_format", local_policy.get("sourceFormat"))
    requested_compression = local_policy.get("compression")
    is_single_channel_mask = texture_type == "mask" and (
        str(component.get("processing_mode", "")) == "canonical_shape_shadow_mask"
        or requested_source_format == "TSF_G8"
        or requested_compression == "Grayscale"
    )
    is_linear_data = texture_type in {"mask", "packed_mask"}
    return {
        "compression": str(merged.get("compression", "Grayscale" if is_single_channel_mask else "UserInterface2D")),
        "source_format": str(merged.get("source_format", merged.get("sourceFormat", "TSF_G8" if is_single_channel_mask else "auto"))),
        "srgb": bool(merged.get("srgb", not is_linear_data)),
        "mip_gen": str(merged.get("mip_gen", merged.get("mipGen", "NoMipmaps"))),
        "lod_group": str(merged.get("lod_group", merged.get("lodGroup", "UI"))),
        "address_x": str(merged.get("address_x", merged.get("addressX", "Clamp"))),
        "address_y": str(merged.get("address_y", merged.get("addressY", "Clamp"))),
        "filter": str(merged.get("filter", "Bilinear")),
        "never_stream": bool(merged.get("never_stream", merged.get("neverStream", True))),
    }


def _output_item(
    root: Path,
    spec: dict[str, Any],
    component: dict[str, Any],
    source_info: dict[str, Any],
    runtime_file: Path,
    source_size: tuple[int, int],
    target_size: tuple[int, int],
    component_diagnostics: dict[str, object],
    selector_info: dict[str, object],
) -> dict[str, Any]:
    ue_import = spec.get("ue_import", {})
    ue_package_path = str(component.get("ue_package_path", ue_import.get("texture_package_path", "")))
    ue_asset_name = str(component["ue_asset_name"])
    schema = str(spec.get("$schema", ""))
    source_path = str(source_info.get("path", ""))
    prompt_files = [
        rel(root, resolve_path(root, str(path)))
        for path in source_info.get("prompt_files", [])
    ]
    strategy_metadata = _strategy_metadata(component, source_size, target_size)
    canonical_shape_id = str(component.get("canonical_shape_id", ""))
    canonical_shape = _canonical_shape_map(spec).get(canonical_shape_id) if canonical_shape_id else None
    with Image.open(runtime_file) as runtime_image:
        quality_gates = _evaluate_quality_gates(runtime_image.copy(), component, canonical_shape)
    synthetic_source_paths = {
        "derived-from-runtime-silhouette",
        "derived-from-canonical-shape",
        "procedural-vector-icon",
    }
    approved_cleanup = (
        _cleanup_config(spec, component)
        if component.get("processing_mode") == "approved_source_target_size"
        else {}
    )
    return {
        "component_id": component["component_id"],
        "asset_suffix": component["asset_suffix"],
        "source_file": rel(root, resolve_path(root, source_path))
        if source_path not in synthetic_source_paths
        else source_path,
        "prompt_files": prompt_files,
        "source_provenance": source_provenance(source_info, schema),
        "selector": selector_info,
        "runtime_file": rel(root, runtime_file),
        "runtime_asset_name": component["runtime_asset_name"],
        "ue_package_path": ue_package_path,
        "ue_asset_name": ue_asset_name,
        "ue_asset_path": f"{ue_package_path}/{ue_asset_name}" if ue_package_path else "",
        "source_size": [int(source_size[0]), int(source_size[1])],
        "target_size": [int(target_size[0]), int(target_size[1])],
        "draw_rect": [int(v) for v in component["draw_rect"]],
        "source_ratio": strategy_metadata["source_ratio"],
        "target_ratio": strategy_metadata["target_ratio"],
        "ratio_delta_pct": strategy_metadata["ratio_delta_pct"],
        "aspect_ratio_tolerance_pct": _nameplate_timer_aaa_aspect_tolerance(spec, component),
        "scale_x": strategy_metadata["scale_x"],
        "scale_y": strategy_metadata["scale_y"],
        "recommended_strategy": strategy_metadata["recommended_strategy"],
        "is_final_runtime_safe": strategy_metadata["is_final_runtime_safe"],
        "z_order": int(component.get("z_order", 0)),
        "texture_type": component.get("texture_type", "color"),
        "processing_mode": component.get("processing_mode", "resize_premultiplied"),
        "resize_contract": {
            "linear_light": (
                component.get("processing_mode") == "canonical_shape_color"
                or approved_cleanup.get("linear_light") is True
            ),
            "premultiplied": (
                component.get("processing_mode") == "canonical_shape_color"
                or component.get("processing_mode") == "approved_source_target_size"
            ),
            "preserve_aspect_ratio": component.get("processing_mode") == "canonical_shape_color",
            "non_uniform_stretch": False if component.get("processing_mode") == "canonical_shape_color" else None,
            "strict_hsv_post_cleanup": (
                approved_cleanup.get("strict_hsv_post_cleanup") is True
                if component.get("processing_mode") == "approved_source_target_size"
                else None
            ),
            "fit_visible_alpha_to_safe_area": (
                approved_cleanup.get("fit_visible_alpha_to_safe_area") is True
                if component.get("processing_mode") == "approved_source_target_size"
                else None
            ),
        },
        "canonical_shape_id": canonical_shape_id,
        "canonical_shape": canonical_shape or {},
        "quality_gates": quality_gates,
        "ue_texture": _resolved_ue_texture(spec, component),
        "postprocess": component.get("postprocess", {}),
        "diagnostics": component_diagnostics,
        "alpha_contract_policy": component.get("alpha_contract_policy", {}),
        "note": component.get("note", ""),
    }


def _strategy_metadata(
    component: dict[str, Any],
    source_size: tuple[int, int],
    target_size: tuple[int, int],
) -> dict[str, Any]:
    source_w, source_h = source_size
    target_w, target_h = target_size
    source_ratio = float(source_w) / float(source_h) if source_h else 0.0
    target_ratio = float(target_w) / float(target_h) if target_h else 0.0
    ratio_delta = abs(source_ratio - target_ratio) / target_ratio * 100.0 if target_ratio else 0.0
    processing_mode = str(component.get("processing_mode", "resize_premultiplied"))
    texture_type = str(component.get("texture_type", "color"))
    component_id = str(component.get("component_id", ""))

    if processing_mode == "source_quality_clean":
        recommended = "authoring_only_preserve_source_ratio"
        final_safe = False
    elif processing_mode == "vector_sdf_icon":
        recommended = "procedural_vector_sdf_icon"
        final_safe = texture_type == "icon"
    elif processing_mode == "nine_slice_prerender":
        recommended = "nine_slice_prerender_explicit_margins"
        final_safe = True
    elif processing_mode == "approved_source_target_size":
        recommended = "approved_source_target_size_cleanup_resize"
        final_safe = ratio_delta <= 35.0 or texture_type in {"icon", "mask"}
    elif processing_mode == "soft_glow_resize":
        recommended = "soft_glow_resize_clean_alpha_falloff"
        final_safe = texture_type == "glow"
    elif processing_mode == "canonical_shape_color":
        recommended = "canonical_shape_linear_light_premultiplied_aspect_preserving"
        final_safe = texture_type == "color"
    elif processing_mode == "canonical_shape_shadow_mask":
        recommended = "canonical_shape_single_channel_preblurred_mask"
        final_safe = texture_type == "mask"
    elif processing_mode == "luma_mask_resize":
        recommended = "color_locked_luma_mask_resize"
        final_safe = texture_type == "mask"
    elif processing_mode == "reference_color_pill_resize":
        recommended = "reference_color_pill_resize_rgb_preserving"
        final_safe = texture_type == "color"
    elif processing_mode == "edge_particle_extract_resize":
        recommended = "edge_particle_extract_resize_particle_preserving"
        final_safe = texture_type in {"color", "glow"}
    elif processing_mode == "nameplate_timer_aaa_fill_resize":
        recommended = "nameplate_timer_aaa_fill_no_stretch_canvas"
        final_safe = texture_type == "color" and ratio_delta <= 3.0
    elif processing_mode == "nameplate_timer_aaa_edge_resize":
        recommended = "nameplate_timer_aaa_edge_fracture_face_no_stretch_canvas"
        final_safe = texture_type == "glow" and ratio_delta <= 3.0
    elif processing_mode == "nameplate_timer_aaa_edge_flipbook_atlas":
        recommended = "nameplate_timer_aaa_edge_flipbook_rgba_atlas"
        final_safe = texture_type in {"glow", "packed_mask"}
    elif processing_mode == "nameplate_timer_image1_body_luma":
        recommended = "nameplate_timer_image1_timer_only_body_luma"
        final_safe = texture_type == "mask"
    elif processing_mode == "nameplate_timer_image1_edge_flipbook_atlas":
        recommended = "nameplate_timer_image1_timer_only_edge_flipbook_rgba_atlas"
        final_safe = texture_type == "packed_mask"
    elif texture_type == "glow":
        recommended = "derived_glow_or_soft_glow_resize"
        final_safe = True
    elif ratio_delta <= 5.0:
        recommended = "premultiplied_resize_2x_draw_size"
        final_safe = True
    elif any(token in component_id for token in ("frame", "panel", "body", "connector", "content")):
        recommended = "ue_box_or_border_or_nine_slice"
        final_safe = False
    elif texture_type in {"icon", "mask"} or abs(target_ratio - 1.0) <= 0.05:
        recommended = "preserve_shape_or_regenerate_for_target_ratio"
        final_safe = False
    else:
        recommended = "regenerate_source_for_final_ratio"
        final_safe = False

    return {
        "source_ratio": round(source_ratio, 6),
        "target_ratio": round(target_ratio, 6),
        "ratio_delta_pct": round(ratio_delta, 3),
        "scale_x": round(float(target_w) / float(source_w), 6) if source_w else 0.0,
        "scale_y": round(float(target_h) / float(source_h), 6) if source_h else 0.0,
        "recommended_strategy": recommended,
        "is_final_runtime_safe": final_safe,
    }


def _write_reviews(root: Path, spec: dict[str, Any], outputs: list[dict[str, Any]], review_dir: Path) -> dict[str, str]:
    reviews: dict[str, str] = {}
    for review_spec in spec.get("reviews", {}).get("assemblies", []):
        image = clean_review_matte(compose_review(root, outputs, review_spec))
        file_name = str(review_spec["file"])
        path = review_dir / file_name
        image.save(path)
        reviews[str(review_spec.get("id", file_name))] = rel(root, path)
        if review_spec.get("checker", True):
            checker_path = review_dir / file_name.replace(".png", "_checker.png")
            save_checker(image, checker_path)
            reviews[f"{review_spec.get('id', file_name)}_checker"] = rel(root, checker_path)
        diagnostics_cfg = spec.get("reviews", {}).get("diagnostics", {})
        if isinstance(diagnostics_cfg, dict) and diagnostics_cfg.get("alpha_mask", False):
            alpha_path = review_dir / file_name.replace(".png", "_alpha_mask.png")
            save_alpha_mask(image, alpha_path)
            reviews[f"{review_spec.get('id', file_name)}_alpha_mask"] = rel(root, alpha_path)
        if isinstance(diagnostics_cfg, dict) and diagnostics_cfg.get("matte_issue_overlay", False):
            matte_path = review_dir / file_name.replace(".png", "_matte_issues.png")
            save_matte_issue_overlay(image, matte_path)
            reviews[f"{review_spec.get('id', file_name)}_matte_issue_overlay"] = rel(root, matte_path)

    contact_name = str(spec.get("reviews", {}).get("contact_sheet", "runtime_components_contact_sheet.png"))
    contact_path = review_dir / contact_name
    make_contact_sheet(root, outputs, contact_path)
    reviews["contact_sheet"] = rel(root, contact_path)
    return reviews


def _is_single_channel_mask_output(item: dict[str, Any]) -> bool:
    return str(item.get("texture_type", "")) == "mask" and int(item.get("diagnostics", {}).get("channel_count", 0)) == 1


def _shared_alpha_contract_results(
    root: Path,
    spec: dict[str, Any],
    outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {str(item["component_id"]): item for item in outputs}
    results: list[dict[str, Any]] = []
    for contract in spec.get("shared_alpha_contracts", []):
        component_ids = [str(value) for value in contract.get("component_ids", [])]
        alpha_planes: list[np.ndarray] = []
        hashes: dict[str, str] = {}
        for component_id in component_ids:
            output = by_id[component_id]
            with Image.open(resolve_path(root, str(output["runtime_file"]))) as image:
                alpha = np.asarray(image.convert("RGBA").getchannel("A"), dtype=np.uint8)
            alpha_planes.append(alpha)
            hashes[component_id] = hashlib.sha256(alpha.tobytes()).hexdigest().upper()
        same_shape = bool(alpha_planes) and all(plane.shape == alpha_planes[0].shape for plane in alpha_planes)
        maximum = 255
        identical = False
        if same_shape:
            maximum = max(
                (int(np.abs(plane.astype(np.int16) - alpha_planes[0].astype(np.int16)).max()) for plane in alpha_planes[1:]),
                default=0,
            )
            identical = maximum == 0
        results.append(
            {
                "id": str(contract["id"]),
                "component_ids": component_ids,
                "canonical_shape_id": str(contract.get("canonical_shape_id", "")),
                "require_identical_bytes": bool(contract.get("require_identical_bytes", True)),
                "alpha_bytes_identical": bool(identical),
                "max_byte_delta": int(maximum),
                "alpha_sha256": hashes,
                "pass": bool(identical) if bool(contract.get("require_identical_bytes", True)) else bool(same_shape),
            }
        )
    return results


def _canonical_shape_contract(outputs: list[dict[str, Any]]) -> dict[str, Any]:
    shaped = [item for item in outputs if item.get("canonical_shape_id")]
    color = [item for item in shaped if item.get("processing_mode") == "canonical_shape_color"]
    return {
        "component_count": len(shaped),
        "all_shapes_resolved": all(bool(item.get("canonical_shape")) for item in shaped),
        "all_canonical_alpha_exact": all(
            bool(item.get("quality_gates", {}).get("results", {}).get("canonical_alpha_exact", {}).get("pass"))
            for item in color
            if item.get("quality_gates", {}).get("configured")
        ),
        "linear_light_component_count": len(color),
        "all_linear_light_premultiplied_aspect_preserving": all(
            item.get("resize_contract", {}).get("linear_light") is True
            and item.get("resize_contract", {}).get("premultiplied") is True
            and item.get("resize_contract", {}).get("preserve_aspect_ratio") is True
            and item.get("resize_contract", {}).get("non_uniform_stretch") is False
            for item in color
        ),
    }


def _quality_contract(outputs: list[dict[str, Any]]) -> dict[str, Any]:
    configured = [item for item in outputs if item.get("quality_gates", {}).get("configured")]
    blocking = [item for item in configured if item["quality_gates"].get("blocking", True)]
    return {
        "component_count": len(configured),
        "blocking_component_count": len(blocking),
        "all_quality_gates_pass": all(bool(item["quality_gates"].get("all_pass")) for item in blocking),
        "informational_failure_component_ids": [
            str(item["component_id"])
            for item in configured
            if not item["quality_gates"].get("blocking", True) and not item["quality_gates"].get("all_pass")
        ],
        "components": {
            str(item["component_id"]): item["quality_gates"]
            for item in configured
        },
    }


def _ue_texture_contract(outputs: list[dict[str, Any]]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for item in outputs:
        settings = item.get("ue_texture", {})
        texture_type = str(item.get("texture_type", "color"))
        valid = (
            settings.get("compression") in {"UserInterface2D", "Grayscale", "Masks"}
            and settings.get("mip_gen") == "NoMipmaps"
            and settings.get("lod_group") == "UI"
            and settings.get("address_x") == "Clamp"
            and settings.get("address_y") == "Clamp"
            and settings.get("filter") == "Bilinear"
            and settings.get("never_stream") is True
        )
        if texture_type == "mask" and settings.get("source_format") == "TSF_G8":
            valid = (
                valid
                and settings.get("compression") == "Grayscale"
                and settings.get("srgb") is False
                and int(item.get("diagnostics", {}).get("channel_count", 0)) == 1
            )
        elif texture_type == "mask":
            valid = valid and settings.get("compression") == "UserInterface2D" and settings.get("source_format") == "auto" and settings.get("srgb") is False
        elif texture_type == "packed_mask":
            valid = valid and settings.get("srgb") is False
        else:
            valid = valid and settings.get("compression") == "UserInterface2D" and settings.get("srgb") is True
        results[str(item["component_id"])] = {"pass": bool(valid), **settings}
    return {
        "component_count": len(outputs),
        "all_ue_texture_settings_valid": all(bool(item["pass"]) for item in results.values()),
        "components": results,
    }


def _build_manifest(
    root: Path,
    spec_path: Path,
    spec: dict[str, Any],
    outputs: list[dict[str, Any]],
    reviews: dict[str, str],
) -> dict[str, Any]:
    alpha_outputs = [item for item in outputs if not _is_single_channel_mask_output(item)]
    color_outputs = [item for item in outputs if item.get("texture_type") in {"color", "icon"}]
    waivers = _alpha_contract_waivers(outputs)
    timer_contract = _timer_boundary_contract(outputs)
    aaa_timer_contract = _nameplate_timer_aaa_contract(outputs)
    opaque_full_frame_outputs = [item for item in outputs if _opaque_full_frame_ok(item)]
    alpha_contract = {
        "all_transparent_corners": all(
            item["diagnostics"]["transparent_corners"] or _opaque_full_frame_ok(item)
            for item in alpha_outputs
        ),
        "all_transparent_outer_edges": all(
            item["diagnostics"]["edge_alpha_gt0"] == 0 or _opaque_full_frame_ok(item)
            for item in alpha_outputs
        ),
        "all_no_visible_chroma_key": all(item["diagnostics"]["visible_chroma_key_pixels_alpha_gt_8"] == 0 for item in alpha_outputs),
        "all_no_visible_magenta_fringe": all(item["diagnostics"]["visible_magenta_fringe_pixels_alpha_gt_8"] == 0 for item in alpha_outputs),
        "all_no_low_alpha_saturated_chroma_fringe": all(
            item["diagnostics"]["low_alpha_saturated_chroma_fringe_pixels"] == 0 for item in alpha_outputs
        ),
        "all_no_hidden_saturated_chroma": all(
            item["diagnostics"]["hidden_saturated_chroma_pixels_alpha_eq_0"] == 0 for item in alpha_outputs
        ),
        "all_no_low_alpha_saturated_rgb_artifacts": all(_rgb_artifact_ok(item, "low_alpha_saturated_rgb_artifact_pixels", "allow_low_alpha_saturated_rgb_artifacts") for item in color_outputs),
        "all_no_hidden_saturated_rgb_artifacts": all(_rgb_artifact_ok(item, "hidden_saturated_rgb_artifact_pixels_alpha_eq_0", "allow_hidden_saturated_rgb_artifacts") for item in color_outputs),
        "component_count": len(outputs),
    }
    if opaque_full_frame_outputs:
        alpha_contract["opaque_full_frame_component_count"] = len(opaque_full_frame_outputs)
        alpha_contract["all_opaque_full_frame_components_fully_opaque"] = all(
            _opaque_full_frame_ok(item) for item in opaque_full_frame_outputs
        )
    alpha_contract.update(timer_contract)
    alpha_contract.update(aaa_timer_contract)
    if waivers:
        alpha_contract["waivers"] = waivers
    expected = spec.get("alpha_contract", {}).get("expected_component_count")
    if expected is not None:
        alpha_contract["expected_component_count"] = int(expected)
        alpha_contract["component_count_matches_expected"] = int(expected) == len(outputs)
        if int(expected) != len(outputs):
            raise RuntimeError(f"Expected {expected} components, generated {len(outputs)}")

    schema = str(spec.get("$schema", ""))
    source_art = []
    for item in spec.get("source_art", []):
        source_art.append(
            {
                "id": item.get("id", ""),
                "path": rel(root, resolve_path(root, str(item.get("path", "")))),
                "prompt_files": [
                    rel(root, resolve_path(root, str(path)))
                    for path in item.get("prompt_files", [])
                ],
                "provenance": source_provenance(item, schema),
            }
        )

    shared_alpha_contracts = _shared_alpha_contract_results(root, spec, outputs)
    canonical_shape_contract = _canonical_shape_contract(outputs)
    quality_contract = _quality_contract(outputs)
    ue_texture_contract = _ue_texture_contract(outputs)
    alpha_contract["all_shared_alpha_contracts_pass"] = all(bool(item["pass"]) for item in shared_alpha_contracts)

    return {
        "$schema": MANIFEST_SCHEMA,
        "version": spec["run_id"],
        "created": spec.get("created", str(date.today())),
        "policy": spec.get("policy", "AI source art processing only; no WBP mutation."),
        "spec_file": rel(root, spec_path),
        "spec_schema": schema,
        "compatibility": {"legacy_spec": schema in LEGACY_SPEC_SCHEMAS},
        "source_contract": spec.get("source_contract", ""),
        "source_art": source_art,
        "texture_package_path": spec.get("ue_import", {}).get("texture_package_path", ""),
        "probe_wbp_path": spec.get("ue_import", {}).get("probe_wbp_path", ""),
        "ue_import": spec.get("ue_import", {}),
        "canonical_shapes": spec.get("canonical_shapes", []),
        "canonical_shape_contract": canonical_shape_contract,
        "shared_alpha_contracts": shared_alpha_contracts,
        "quality_contract": quality_contract,
        "ue_texture_contract": ue_texture_contract,
        "edge_policy": spec.get("processing", {}).get("edge_policy", ""),
        "outputs": outputs,
        "reviews": reviews,
        "alpha_contract": alpha_contract,
    }


def _rgb_artifact_ok(item: dict[str, Any], diagnostic_key: str, policy_key: str) -> bool:
    count = int(item["diagnostics"].get(diagnostic_key, 0))
    if count == 0:
        return True
    policy = item.get("alpha_contract_policy", {})
    return bool(isinstance(policy, dict) and policy.get(policy_key))


def _opaque_full_frame_ok(item: dict[str, Any]) -> bool:
    policy = item.get("alpha_contract_policy", {})
    if not isinstance(policy, dict) or not policy.get("allow_opaque_full_frame"):
        return False
    diagnostics = item.get("diagnostics", {})
    size = diagnostics.get("size", [])
    if not isinstance(size, list) or len(size) != 2:
        return False
    width, height = int(size[0]), int(size[1])
    pixel_count = width * height
    return (
        pixel_count > 0
        and int(diagnostics.get("min_alpha", -1)) == 255
        and int(diagnostics.get("fully_opaque_pixels", -1)) == pixel_count
        and diagnostics.get("alpha_bbox") == [0, 0, width, height]
    )


def _timer_boundary_contract(outputs: list[dict[str, Any]]) -> dict[str, Any]:
    timer_outputs = [
        item
        for item in outputs
        if "timer_dark_right_edge_band_pixels" in item.get("diagnostics", {})
    ]
    if not timer_outputs:
        return {}

    aligned_outputs = [
        item
        for item in timer_outputs
        if item["diagnostics"].get("timer_edge_boundary_within_tolerance") is not None
    ]
    contract: dict[str, Any] = {
        "timer_boundary_component_count": len(timer_outputs),
        "all_no_timer_dark_right_edge_band": all(
            int(item["diagnostics"].get("timer_dark_right_edge_band_pixels", 0)) == 0
            for item in timer_outputs
        ),
    }
    if aligned_outputs:
        contract["all_timer_edge_boundary_within_tolerance"] = all(
            bool(item["diagnostics"].get("timer_edge_boundary_within_tolerance"))
            for item in aligned_outputs
        )

    face_outputs = [
        item
        for item in timer_outputs
        if item["diagnostics"].get("timer_edge_boundary_face_required") is True
    ]
    if face_outputs:
        contract["all_timer_edge_has_boundary_face"] = all(
            bool(item["diagnostics"].get("timer_edge_has_boundary_face"))
            for item in face_outputs
        )
    return contract


def _nameplate_timer_aaa_contract(outputs: list[dict[str, Any]]) -> dict[str, Any]:
    aaa_outputs = [
        item
        for item in outputs
        if item.get("processing_mode")
        in {
            "nameplate_timer_aaa_fill_resize",
            "nameplate_timer_aaa_edge_resize",
            "nameplate_timer_aaa_edge_flipbook_atlas",
            "nameplate_timer_image1_body_luma",
            "nameplate_timer_image1_edge_flipbook_atlas",
        }
    ]
    if not aaa_outputs:
        return {}

    contract = {
        "nameplate_timer_aaa_component_count": len(aaa_outputs),
        "all_nameplate_timer_aaa_aspect_ratio_within_tolerance": all(
            float(item.get("ratio_delta_pct", 999.0)) <= float(item.get("aspect_ratio_tolerance_pct") or 3.0)
            for item in aaa_outputs
        ),
        "all_nameplate_timer_aaa_final_runtime_safe": all(
            bool(item.get("is_final_runtime_safe"))
            for item in aaa_outputs
        ),
    }
    atlas_outputs = [
        item
        for item in aaa_outputs
        if "timer_edge_flipbook_frame_count" in item.get("diagnostics", {})
    ]
    if atlas_outputs:
        contract["all_nameplate_timer_aaa_edge_flipbook_contract"] = all(
            bool(item["diagnostics"].get("timer_edge_flipbook_contract_ok"))
            for item in atlas_outputs
        )
    return contract


def _alpha_contract_waivers(outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    waivers: list[dict[str, Any]] = []
    for item in outputs:
        policy = item.get("alpha_contract_policy", {})
        if not isinstance(policy, dict):
            continue
        waived_fields = [key for key, value in policy.items() if key != "reason" and value]
        if not waived_fields:
            continue
        waivers.append(
            {
                "component_id": item["component_id"],
                "fields": waived_fields,
                "reason": str(policy.get("reason", "Intentional texture data accepted by spec.")),
            }
        )
    return waivers
