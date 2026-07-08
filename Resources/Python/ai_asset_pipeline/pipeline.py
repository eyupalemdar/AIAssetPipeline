from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .image_ops import (
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
    resize_luma_mask,
    resize_premultiplied,
    resize_soft_glow,
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
            diagnostics(runtime),
            selector_info,
        )

    source_info = source_art[str(component["source_art_id"])]
    source_path = resolve_path(root, str(source_info["path"]))
    selected, selector_info = select_crop(load_rgba(source_path), component["selector"])

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
    return _output_item(root, spec, component, source_info, runtime_file, selected.size, target_size, diagnostics(runtime), selector_info)


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


def _cleanup_config(spec: dict[str, Any], component: dict[str, Any]) -> dict[str, int]:
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
        **global_config,
        **component_config,
    }
    return {
        "alpha_open_iterations": int(merged.get("alpha_open_iterations", 0)),
        "alpha_close_iterations": int(merged.get("alpha_close_iterations", 0)),
        "pre_speckle_min_area": int(merged.get("pre_speckle_min_area", 0)),
        "post_speckle_min_area": int(merged.get("post_speckle_min_area", 12)),
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
        diagnostics(runtime),
        {"selector": "derived_glow_from_assembly"},
    )


def _target_size(spec: dict[str, Any], component: dict[str, Any]) -> tuple[int, int]:
    if "target_size" in component:
        return tuple(int(v) for v in component["target_size"])
    scale = float(component.get("target_scale", spec.get("processing", {}).get("target_scale", 2)))
    _, _, w, h = [int(v) for v in component["draw_rect"]]
    return int(round(w * scale)), int(round(h * scale))


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
    synthetic_source_paths = {"derived-from-runtime-silhouette", "procedural-vector-icon"}
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
        "scale_x": strategy_metadata["scale_x"],
        "scale_y": strategy_metadata["scale_y"],
        "recommended_strategy": strategy_metadata["recommended_strategy"],
        "is_final_runtime_safe": strategy_metadata["is_final_runtime_safe"],
        "z_order": int(component.get("z_order", 0)),
        "texture_type": component.get("texture_type", "color"),
        "processing_mode": component.get("processing_mode", "resize_premultiplied"),
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
    elif processing_mode == "luma_mask_resize":
        recommended = "color_locked_luma_mask_resize"
        final_safe = texture_type == "mask"
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


def _build_manifest(
    root: Path,
    spec_path: Path,
    spec: dict[str, Any],
    outputs: list[dict[str, Any]],
    reviews: dict[str, str],
) -> dict[str, Any]:
    color_outputs = [item for item in outputs if item.get("texture_type") != "glow"]
    waivers = _alpha_contract_waivers(outputs)
    alpha_contract = {
        "all_transparent_corners": all(item["diagnostics"]["transparent_corners"] for item in outputs),
        "all_transparent_outer_edges": all(item["diagnostics"]["edge_alpha_gt0"] == 0 for item in outputs),
        "all_no_visible_chroma_key": all(item["diagnostics"]["visible_chroma_key_pixels_alpha_gt_8"] == 0 for item in outputs),
        "all_no_visible_magenta_fringe": all(item["diagnostics"]["visible_magenta_fringe_pixels_alpha_gt_8"] == 0 for item in outputs),
        "all_no_low_alpha_saturated_chroma_fringe": all(
            item["diagnostics"]["low_alpha_saturated_chroma_fringe_pixels"] == 0 for item in outputs
        ),
        "all_no_hidden_saturated_chroma": all(
            item["diagnostics"]["hidden_saturated_chroma_pixels_alpha_eq_0"] == 0 for item in outputs
        ),
        "all_no_low_alpha_saturated_rgb_artifacts": all(_rgb_artifact_ok(item, "low_alpha_saturated_rgb_artifact_pixels", "allow_low_alpha_saturated_rgb_artifacts") for item in color_outputs),
        "all_no_hidden_saturated_rgb_artifacts": all(_rgb_artifact_ok(item, "hidden_saturated_rgb_artifact_pixels_alpha_eq_0", "allow_hidden_saturated_rgb_artifacts") for item in color_outputs),
        "component_count": len(outputs),
    }
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
