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

    component_ids: set[str] = set()
    for component in spec.get("components", []):
        _validate_component(component, source_ids, component_ids)
    for component in spec.get("derived_components", []):
        _validate_component(component, source_ids, component_ids, derived=True)
    return warnings


def _validate_component(
    component: dict[str, Any],
    source_ids: set[str],
    component_ids: set[str],
    derived: bool = False,
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
        if processing_mode == "vector_sdf_icon":
            vector_icon = component.get("vector_icon")
            _require(isinstance(vector_icon, dict), f"{component_id} missing vector_icon object")
            _require(bool(str(vector_icon.get("glyph", ""))), f"{component_id} vector_icon missing glyph")
            return
        source_art_id = str(component.get("source_art_id", ""))
        _require(source_art_id in source_ids, f"{component_id} references unknown source_art_id: {source_art_id}")
        _require(isinstance(component.get("selector"), dict), f"{component_id} missing selector object")


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
