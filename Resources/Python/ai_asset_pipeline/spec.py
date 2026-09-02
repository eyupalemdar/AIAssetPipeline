from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SPEC_SCHEMA = "ai-asset-pipeline-spec-v1"
LEGACY_SPEC_SCHEMAS = {"image2-asset-spec-v1"}
MANIFEST_SCHEMA = "ai-asset-pipeline-manifest-v1"
LEGACY_MANIFEST_SCHEMAS = {"image2-asset-manifest-v1"}


class SpecError(ValueError):
    pass


def rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def discover_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if any(candidate.glob("*.uproject")):
            return candidate
    raise SpecError(f"Could not discover project root from {current}")


def normalize_project_root(project_root: Path | str | None, start: Path | None = None) -> Path:
    if project_root:
        root = Path(project_root).resolve()
        if not root.exists():
            raise SpecError(f"project_root does not exist: {root}")
        return root
    return discover_project_root(start)


def resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_spec(path: Path, project_root: Path | str | None = None) -> dict[str, Any]:
    root = normalize_project_root(project_root, path)
    spec = load_json(path)
    validate_spec(spec, root)
    return spec


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SpecError(message)


def _require_path(root: Path, owner: str, value: str) -> None:
    _require(bool(value), f"{owner} path is required")
    path = resolve_path(root, value)
    _require(path.exists(), f"{owner} path does not exist: {value}")


def _is_legacy_schema(schema: str) -> bool:
    return schema in LEGACY_SPEC_SCHEMAS


def _validate_approved_source_target_size(value: Any, owner: str) -> None:
    _require(isinstance(value, dict), f"{owner} must be an object")
    for field in (
        "alpha_open_iterations",
        "alpha_close_iterations",
        "pre_speckle_min_area",
        "post_speckle_min_area",
    ):
        if field in value:
            _require(
                type(value[field]) is int and value[field] >= 0,
                f"{owner}.{field} must be a non-negative integer",
            )
    for field in (
        "linear_light",
        "strict_hsv_post_cleanup",
        "fit_visible_alpha_to_safe_area",
    ):
        if field in value:
            _require(type(value[field]) is bool, f"{owner}.{field} must be a boolean")


def _validate_cutout_quality_gates(value: dict[str, Any], owner: str) -> None:
    integer_ranges = {
        "alpha_padding_threshold": (0, 254),
        "max_alpha_padding_px": (0, None),
        "chroma_shadow_alpha_threshold": (0, 254),
        "max_visible_chroma_shadow_pixels": (0, None),
    }
    for field, (minimum, maximum) in integer_ranges.items():
        if field not in value:
            continue
        actual = value[field]
        valid = type(actual) is int and actual >= minimum
        if maximum is not None:
            valid = valid and actual <= maximum
        range_label = f"{minimum}..{maximum}" if maximum is not None else f">={minimum}"
        _require(valid, f"{owner}.{field} must be an integer in {range_label}")
    if "max_alpha_padding_percent" in value:
        actual = value["max_alpha_padding_percent"]
        _require(
            type(actual) in {int, float} and 0 <= actual <= 100,
            f"{owner}.max_alpha_padding_percent must be a number in 0..100",
        )


def validate_spec(spec: dict[str, Any], root: Path) -> list[str]:
    schema = str(spec.get("$schema", ""))
    _require(
        schema == SPEC_SCHEMA or schema in LEGACY_SPEC_SCHEMAS,
        f"$schema must be {SPEC_SCHEMA} (legacy accepted: {', '.join(sorted(LEGACY_SPEC_SCHEMAS))})",
    )
    warnings: list[str] = []
    legacy = _is_legacy_schema(schema)
    if legacy:
        warnings.append(f"legacy schema accepted: {schema}")

    for field in ("run_id", "runtime_output_dir", "review_output_dir", "source_art", "components"):
        _require(field in spec, f"missing required field: {field}")
    _require(spec.get("validation_policy", "fail_closed") == "fail_closed", "v1 only supports fail_closed")
    _validate_approved_source_target_size(
        spec.get("approved_source_target_size", {}),
        "approved_source_target_size",
    )

    source_art = spec.get("source_art")
    _require(isinstance(source_art, list) and source_art, "source_art must be a non-empty array")
    source_ids: set[str] = set()
    for item in source_art:
        _require(isinstance(item, dict), "source_art entries must be objects")
        source_id = str(item.get("id", ""))
        _require(bool(source_id), "source_art entry missing id")
        _require(source_id not in source_ids, f"duplicate source_art id: {source_id}")
        source_ids.add(source_id)
        _require_path(root, f"source_art {source_id}", str(item.get("path", "")))
        prompts = item.get("prompt_files")
        _require(isinstance(prompts, list) and prompts, f"source_art {source_id} requires prompt_files")
        for prompt in prompts:
            _require_path(root, f"source_art {source_id} prompt", str(prompt))

        provenance = item.get("provenance")
        if legacy and provenance is None:
            warnings.append(f"source_art {source_id} has no provenance; legacy compatibility mode")
        else:
            _require(isinstance(provenance, dict), f"source_art {source_id} requires provenance")
            for field in ("provider", "model", "generation_id"):
                _require(bool(str(provenance.get(field, ""))), f"source_art {source_id} provenance missing {field}")

    canonical_shapes = _validate_canonical_shapes(spec.get("canonical_shapes", []))
    component_ids: set[str] = set()
    for component in spec.get("components", []):
        _validate_component(component, source_ids, component_ids, canonical_shapes=canonical_shapes)
    for component in spec.get("derived_components", []):
        _validate_component(component, source_ids, component_ids, derived=True, canonical_shapes=canonical_shapes)
    shared_ids: set[str] = set()
    for contract in spec.get("shared_alpha_contracts", []):
        _require(isinstance(contract, dict), "shared_alpha_contracts entries must be objects")
        contract_id = str(contract.get("id", ""))
        _require(bool(contract_id), "shared alpha contract missing id")
        _require(contract_id not in shared_ids, f"duplicate shared alpha contract id: {contract_id}")
        shared_ids.add(contract_id)
        members = contract.get("component_ids")
        _require(isinstance(members, list) and len(members) >= 2, f"{contract_id} requires at least two component_ids")
        _require(len(set(str(value) for value in members)) == len(members), f"{contract_id} has duplicate component_ids")
        for component_id in members:
            _require(str(component_id) in component_ids, f"{contract_id} references unknown component_id: {component_id}")
        shape_id = str(contract.get("canonical_shape_id", ""))
        if shape_id:
            _require(shape_id in canonical_shapes, f"{contract_id} references unknown canonical_shape_id: {shape_id}")
    return warnings


def _validate_canonical_shapes(value: Any) -> dict[str, dict[str, Any]]:
    _require(isinstance(value, list), "canonical_shapes must be an array")
    shapes: dict[str, dict[str, Any]] = {}
    for shape in value:
        _require(isinstance(shape, dict), "canonical_shapes entries must be objects")
        shape_id = str(shape.get("id", ""))
        _require(bool(shape_id), "canonical shape missing id")
        _require(shape_id not in shapes, f"duplicate canonical shape id: {shape_id}")
        _require(str(shape.get("type", "")) in {"rounded_rectangle", "circle"}, f"{shape_id} has unsupported type")
        canvas = shape.get("canvas_size")
        bbox = shape.get("bbox")
        _require(isinstance(canvas, list) and len(canvas) == 2, f"{shape_id} needs canvas_size [w,h]")
        _require(isinstance(bbox, list) and len(bbox) == 4, f"{shape_id} needs bbox [x0,y0,x1,y1)")
        width, height = (int(value) for value in canvas)
        x0, y0, x1, y1 = (int(value) for value in bbox)
        _require(width > 0 and height > 0, f"{shape_id} canvas_size must be positive")
        _require(0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height, f"{shape_id} bbox must be inside canvas")
        if str(shape.get("type")) == "circle":
            _require((x1 - x0) == (y1 - y0), f"{shape_id} circle bbox must be square")
        if str(shape.get("type")) == "rounded_rectangle":
            radius = float(shape.get("radius_px", -1))
            _require(0 <= radius <= min(x1 - x0, y1 - y0) / 2.0, f"{shape_id} radius_px is invalid")
        _require(int(shape.get("supersample", 4)) >= 1, f"{shape_id} supersample must be >= 1")
        shapes[shape_id] = shape
    return shapes


def _validate_component(
    component: dict[str, Any],
    source_ids: set[str],
    component_ids: set[str],
    derived: bool = False,
    canonical_shapes: dict[str, dict[str, Any]] | None = None,
) -> None:
    _require(isinstance(component, dict), "component entries must be objects")
    component_id = str(component.get("component_id", ""))
    _require(bool(component_id), "component missing component_id")
    _require(component_id not in component_ids, f"duplicate component_id: {component_id}")
    component_ids.add(component_id)
    _require(isinstance(component.get("draw_rect"), list) and len(component["draw_rect"]) == 4, f"{component_id} needs draw_rect [x,y,w,h]")
    _require(bool(component.get("asset_suffix")), f"{component_id} missing asset_suffix")
    _require(bool(component.get("runtime_asset_name")), f"{component_id} missing runtime_asset_name")
    _require(bool(component.get("ue_asset_name")), f"{component_id} missing ue_asset_name")
    if derived:
        _require(component.get("type") == "derived_glow_from_assembly", f"{component_id} uses unsupported derived type")
        _require(isinstance(component.get("source_component_ids"), list), f"{component_id} missing source_component_ids")
    else:
        processing_mode = str(component.get("processing_mode", "resize_premultiplied"))
        shape_id = str(component.get("canonical_shape_id", ""))
        if shape_id:
            _require(shape_id in (canonical_shapes or {}), f"{component_id} references unknown canonical_shape_id: {shape_id}")
            if "target_size" in component:
                canvas = [int(value) for value in (canonical_shapes or {})[shape_id]["canvas_size"]]
                _require([int(value) for value in component["target_size"]] == canvas, f"{component_id} target_size must match canonical shape canvas_size")
        if processing_mode in {"canonical_shape_color", "canonical_shape_shadow_mask"}:
            _require(bool(shape_id), f"{component_id} requires canonical_shape_id")
        quality_gates = component.get("quality_gates", {})
        _require(isinstance(quality_gates, dict), f"{component_id} quality_gates must be an object")
        _validate_cutout_quality_gates(quality_gates, f"{component_id}.quality_gates")
        _validate_approved_source_target_size(
            component.get("approved_source_target_size", {}),
            f"{component_id}.approved_source_target_size",
        )
        ue_texture = component.get("ue_texture", {})
        _require(isinstance(ue_texture, dict), f"{component_id} ue_texture must be an object")
        if processing_mode == "canonical_shape_shadow_mask":
            _require(str(component.get("texture_type", "")) == "mask", f"{component_id} canonical shadow must use texture_type=mask")
            return
        if processing_mode == "vector_sdf_icon":
            vector_icon = component.get("vector_icon")
            _require(isinstance(vector_icon, dict), f"{component_id} missing vector_icon object")
            _require(bool(str(vector_icon.get("glyph", ""))), f"{component_id} vector_icon missing glyph")
            return
        source_art_id = str(component.get("source_art_id", ""))
        _require(source_art_id in source_ids, f"{component_id} references unknown source_art_id: {source_art_id}")
        selector = component.get("selector")
        _require(isinstance(selector, dict), f"{component_id} missing selector object")
        chroma_key_mode = selector.get("chroma_key_mode", "standard")
        _require(
            chroma_key_mode in {"standard", "strict_hsv"},
            f"{component_id} selector.chroma_key_mode must be standard or strict_hsv",
        )


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    schema = str(manifest.get("$schema", ""))
    _require(
        schema == MANIFEST_SCHEMA or schema in LEGACY_MANIFEST_SCHEMAS,
        f"$schema must be {MANIFEST_SCHEMA} (legacy accepted: {', '.join(sorted(LEGACY_MANIFEST_SCHEMAS))})",
    )
    warnings: list[str] = []
    if schema in LEGACY_MANIFEST_SCHEMAS:
        warnings.append(f"legacy manifest schema accepted: {schema}")

    _require(isinstance(manifest.get("outputs"), list) and manifest["outputs"], "manifest outputs must be non-empty")
    contract = manifest.get("alpha_contract", {})
    _require(contract.get("component_count") == len(manifest["outputs"]), "alpha_contract component_count mismatch")
    required_true_fields = (
        ("all_transparent_corners", "transparent corners"),
        ("all_transparent_outer_edges", "transparent outer edges"),
        ("all_no_visible_chroma_key", "visible chroma key"),
        ("all_no_visible_magenta_fringe", "visible magenta fringe"),
        ("all_no_low_alpha_saturated_chroma_fringe", "low-alpha saturated chroma fringe"),
        ("all_no_hidden_saturated_chroma", "hidden saturated chroma in transparent RGB"),
        ("all_no_low_alpha_saturated_rgb_artifacts", "low-alpha saturated RGB artifacts"),
        ("all_no_hidden_saturated_rgb_artifacts", "hidden saturated RGB artifacts in transparent RGB"),
    )
    for field, label in required_true_fields:
        _require(contract.get(field) is True, f"alpha contract failed: {label}")
    optional_true_fields = (
        ("all_no_timer_dark_right_edge_band", "timer dark right-edge band"),
        ("all_timer_edge_boundary_within_tolerance", "timer edge boundary alignment"),
        ("all_timer_edge_has_boundary_face", "timer edge boundary face coverage"),
        ("all_nameplate_timer_aaa_aspect_ratio_within_tolerance", "nameplate timer AAA aspect ratio"),
        ("all_nameplate_timer_aaa_final_runtime_safe", "nameplate timer AAA runtime-safe strategy"),
        ("all_nameplate_timer_aaa_edge_flipbook_contract", "nameplate timer AAA edge flipbook atlas contract"),
    )
    for field, label in optional_true_fields:
        if field in contract:
            _require(contract.get(field) is True, f"alpha contract failed: {label}")
    if "all_shared_alpha_contracts_pass" in contract:
        _require(contract.get("all_shared_alpha_contracts_pass") is True, "alpha contract failed: shared alpha byte equality")

    canonical = manifest.get("canonical_shape_contract")
    if isinstance(canonical, dict) and int(canonical.get("component_count", 0)) > 0:
        _require(canonical.get("all_shapes_resolved") is True, "canonical shape contract failed: unresolved shape")
        _require(canonical.get("all_canonical_alpha_exact") is True, "canonical shape contract failed: alpha mismatch")
        _require(
            canonical.get("all_linear_light_premultiplied_aspect_preserving") is True,
            "canonical shape contract failed: resize policy",
        )

    shared = manifest.get("shared_alpha_contracts", [])
    if shared:
        _require(all(bool(item.get("pass")) for item in shared), "shared alpha contract failed")

    quality = manifest.get("quality_contract")
    if isinstance(quality, dict) and int(quality.get("component_count", 0)) > 0:
        failed_quality = {
            component_id: details
            for component_id, details in quality.get("components", {}).items()
            if not bool(details.get("all_pass"))
        }
        _require(
            quality.get("all_quality_gates_pass") is True,
            f"component quality gate failed: {json.dumps(failed_quality, sort_keys=True)}",
        )

    ue_texture = manifest.get("ue_texture_contract")
    if isinstance(ue_texture, dict):
        _require(ue_texture.get("component_count") == len(manifest["outputs"]), "UE texture contract component_count mismatch")
        _require(ue_texture.get("all_ue_texture_settings_valid") is True, "UE texture settings contract failed")
    return warnings


def source_provenance(item: dict[str, Any], schema: str) -> dict[str, Any]:
    provenance = item.get("provenance")
    if isinstance(provenance, dict):
        return dict(provenance)
    provider = "image2" if schema in LEGACY_SPEC_SCHEMAS else "unknown"
    return {
        "provider": provider,
        "model": "legacy-unspecified",
        "generation_id": "",
        "notes": "Inferred compatibility metadata from legacy image2-asset-spec-v1 source_art.",
    }
