from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .spec import normalize_project_root, rel, resolve_path, validate_manifest, validate_spec


def _iter_tspec_files(path_or_dir: Path) -> list[Path]:
    if path_or_dir.is_dir():
        return sorted(path_or_dir.glob("*.tspec.json"))
    return [path_or_dir]


def _legacy_pipeline_from_source_intent(tspec: dict[str, Any]) -> dict[str, Any] | None:
    source_intent = tspec.get("sourceIntent")
    if not isinstance(source_intent, dict):
        return None
    spec_path = source_intent.get("assetPipelineSpec")
    manifest_path = source_intent.get("assetPipelineManifest") or source_intent.get("manifest")
    texture_package_path = source_intent.get("texturePackagePath")
    if not spec_path or not manifest_path:
        return None
    return {
        "id": "legacy-sourceIntent",
        "spec": spec_path,
        "manifest": manifest_path,
        "texturePackagePath": texture_package_path,
        "requiredComponentIds": [],
        "compatibility": "sourceIntent",
    }


def _pipeline_entries(tspec: dict[str, Any]) -> list[dict[str, Any]]:
    entries = tspec.get("assetPipelines")
    result: list[dict[str, Any]] = []
    if isinstance(entries, list):
        result.extend(item for item in entries if isinstance(item, dict))
    elif isinstance(entries, dict):
        result.append(entries)
    if result:
        return result
    legacy = _legacy_pipeline_from_source_intent(tspec)
    if legacy:
        result.append(legacy)
    return result


def _texture_package_paths(entry: dict[str, Any]) -> list[str]:
    values: list[str] = []
    singular = entry.get("texturePackagePath") or entry.get("texture_package_path")
    if singular:
        values.append(str(singular))

    plural = entry.get("texturePackagePaths") or entry.get("texture_package_paths")
    if isinstance(plural, list):
        values.extend(str(item) for item in plural if str(item))
    elif plural:
        values.append(str(plural))

    return list(dict.fromkeys(values))


def validate_tspec_links(path_or_dir: Path, project_root: Path | str | None = None) -> dict[str, Any]:
    root = normalize_project_root(project_root, path_or_dir)
    files = _iter_tspec_files(path_or_dir)
    failures: list[str] = []
    checked: list[dict[str, Any]] = []

    for file_path in files:
        relative = rel(root, file_path.resolve())
        try:
            tspec = json.loads(file_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            failures.append(f"{relative}: invalid JSON - {exc}")
            continue

        for index, entry in enumerate(_pipeline_entries(tspec)):
            label = str(entry.get("id") or f"assetPipelines[{index}]")
            spec_ref = entry.get("spec") or entry.get("specPath")
            manifest_ref = entry.get("manifest") or entry.get("manifestPath")
            required_component_ids = entry.get("requiredComponentIds") or entry.get("required_component_ids") or []
            texture_package_paths = _texture_package_paths(entry)

            if not spec_ref:
                failures.append(f"{relative}: {label} missing spec path")
                continue
            if not manifest_ref:
                failures.append(f"{relative}: {label} missing manifest path")
                continue
            if not isinstance(required_component_ids, list):
                failures.append(f"{relative}: {label} requiredComponentIds must be an array")
                continue

            spec_path = resolve_path(root, str(spec_ref))
            manifest_path = resolve_path(root, str(manifest_ref))
            if not spec_path.exists():
                failures.append(f"{relative}: {label} spec path does not exist: {spec_ref}")
                continue
            if not manifest_path.exists():
                failures.append(f"{relative}: {label} manifest path does not exist: {manifest_ref}")
                continue

            try:
                spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
                spec_warnings = validate_spec(spec, root)
                manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
                manifest_warnings = validate_manifest(manifest)
            except Exception as exc:
                failures.append(f"{relative}: {label} validation failed - {exc}")
                continue

            output_ids = {str(item.get("component_id", "")) for item in manifest.get("outputs", [])}
            missing_ids = [str(component_id) for component_id in required_component_ids if str(component_id) not in output_ids]
            if missing_ids:
                failures.append(f"{relative}: {label} manifest missing required component ids: {', '.join(missing_ids)}")

            if texture_package_paths:
                allowed_package_paths = set(texture_package_paths)
                mismatched = [
                    str(item.get("component_id", ""))
                    for item in manifest.get("outputs", [])
                    if str(item.get("ue_package_path", manifest.get("texture_package_path", ""))) not in allowed_package_paths
                ]
                if mismatched:
                    failures.append(
                        f"{relative}: {label} texturePackagePath mismatch for component ids: {', '.join(mismatched)}"
                    )

            checked.append(
                {
                    "tspec": relative,
                    "pipeline": label,
                    "spec": rel(root, spec_path.resolve()),
                    "manifest": rel(root, manifest_path.resolve()),
                    "component_count": len(output_ids),
                    "required_component_count": len(required_component_ids),
                    "texture_package_paths": texture_package_paths,
                    "warnings": spec_warnings + manifest_warnings,
                }
            )

    return {
        "ok": not failures,
        "project_root": str(root),
        "tspec_count": len(files),
        "pipeline_count": len(checked),
        "checked": checked,
        "failures": failures,
    }
