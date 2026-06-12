from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .image_ops import diagnostics, dilate_transparent_rgb, load_rgba, resize_premultiplied
from .review import compose_review, gaussian_glow_from_alpha, make_contact_sheet, save_checker
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
    source_info = source_art[str(component["source_art_id"])]
    source_path = resolve_path(root, str(source_info["path"]))
    selected, selector_info = select_crop(load_rgba(source_path), component["selector"])
    target_size = _target_size(spec, component)
    processing_mode = str(component.get("processing_mode", "resize_premultiplied"))
    if processing_mode in {"passthrough", "copy_exact"}:
        if selected.size != target_size:
            raise ValueError(
                f"{component['component_id']} uses {processing_mode} but source size "
                f"{selected.size} does not match target_size {target_size}"
            )
        runtime = selected
    else:
        runtime = resize_premultiplied(
            selected,
            target_size,
            int(spec.get("processing", {}).get("clear_outer_alpha_px", 1)),
        )
    runtime_file = runtime_dir / f"{component['runtime_asset_name']}.png"
    runtime.save(runtime_file)
    return _output_item(root, spec, component, source_info, runtime_file, selected.size, target_size, diagnostics(runtime), selector_info)


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
    return {
        "component_id": component["component_id"],
        "asset_suffix": component["asset_suffix"],
        "source_file": rel(root, resolve_path(root, source_path))
        if source_path != "derived-from-runtime-silhouette"
        else "derived-from-runtime-silhouette",
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
        "z_order": int(component.get("z_order", 0)),
        "texture_type": component.get("texture_type", "color"),
        "diagnostics": component_diagnostics,
        "alpha_contract_policy": component.get("alpha_contract_policy", {}),
        "note": component.get("note", ""),
    }


def _write_reviews(root: Path, spec: dict[str, Any], outputs: list[dict[str, Any]], review_dir: Path) -> dict[str, str]:
    reviews: dict[str, str] = {}
    for review_spec in spec.get("reviews", {}).get("assemblies", []):
        image = compose_review(root, outputs, review_spec)
        file_name = str(review_spec["file"])
        path = review_dir / file_name
        image.save(path)
        reviews[str(review_spec.get("id", file_name))] = rel(root, path)
        if review_spec.get("checker", True):
            checker_path = review_dir / file_name.replace(".png", "_checker.png")
            save_checker(image, checker_path)
            reviews[f"{review_spec.get('id', file_name)}_checker"] = rel(root, checker_path)

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
