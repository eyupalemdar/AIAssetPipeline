"""Size standalone approved RGBA art from measured physical draw footprints.

This plans review candidates; it does not change production bindings or claim
that texel coverage is a perceptual quality score or a device memory benchmark.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path

from PIL import Image

from .sampling import asset_path, checked_hash, project_path, read_json, require, sha256
from .spec import load_spec, validate_spec

POLICY_SCHEMA = "ai-texture-density-policy-v1"
MEASUREMENT_SCHEMA = "ai-widget-pixel-footprint-v1"


def positive(value, name):
    require(type(value) in (int, float) and math.isfinite(value) and value > 0, name + " must be finite and positive")
    return float(value)


def pair(value, name):
    require(isinstance(value, list) and len(value) == 2, name + " needs two dimensions")
    return [positive(v, name) for v in value]


def rgba8_mip_bytes(size):
    """Logical full-chain payload only; excludes GPU alignment and allocator overhead."""
    width, height = size
    result = 0
    while True:
        result += width * height * 4
        if width == height == 1:
            return result
        width, height = max(1, width // 2), max(1, height // 2)


def plan_density(policy_path, root):
    root = Path(root).resolve()
    policy_path = project_path(root, str(policy_path))
    policy = read_json(policy_path)
    require(policy.get("$schema") == POLICY_SCHEMA, "Unsupported density policy")
    require(set(policy) <= {"$schema", "spec", "spec_sha256", "evidence", "required_scenarios", "expected_viewports", "texels_per_pixel",
        "max_texture_dimension", "max_rgba8_mip_bytes", "allow_downsize", "candidate_package", "notes"}, "Unknown density policy field")
    spec_path = project_path(root, policy.get("spec"))
    checked_hash(spec_path, policy.get("spec_sha256"), "density input spec")
    spec = load_spec(spec_path, root)
    require(not spec.get("derived_components"), "Density v1 requires standalone components, not derived assemblies")
    target = positive(policy.get("texels_per_pixel"), "texels_per_pixel")
    limit = policy.get("max_texture_dimension")
    budget = policy.get("max_rgba8_mip_bytes")
    require(type(limit) is int and limit > 0, "Explicit positive max_texture_dimension required")
    require(type(budget) is int and budget > 0, "Explicit positive max_rgba8_mip_bytes required")
    downsize = policy.get("allow_downsize", False)
    require(type(downsize) is bool, "allow_downsize must be boolean")
    package = asset_path(policy.get("candidate_package"))
    require("/_aiprobe/" not in (package + "/").lower(), "Density candidates are production derivatives, not original-density probes")
    components = {row["component_id"]: row for row in spec["components"]}
    sources = {row["id"]: row for row in spec["source_art"]}
    source_sizes = {}
    for key, source in sources.items():
        path = project_path(root, source["path"])
        checked_hash(path, source.get("provenance", {}).get("source_sha256"), "approved source")
        with Image.open(path) as image:
            source_sizes[key] = list(image.size)
    require(isinstance(policy.get("evidence"), list) and policy["evidence"], "Native footprint evidence required")
    required = policy.get("required_scenarios")
    require(isinstance(required, list) and required and len(set(required)) == len(required)
            and all(isinstance(v, str) and v for v in required), "Unique required_scenarios required")
    expected_viewports = policy.get("expected_viewports")
    require(isinstance(expected_viewports, dict) and set(expected_viewports) == set(required), "Missing expected_viewports for required scenarios")
    for size in expected_viewports.values():
        pair(size, "expected viewport")
        require(all(type(v) is int for v in size), "Expected viewport dimensions must be integers")
    samples = {key: [] for key in components}
    scenarios = set()
    evidence_refs = []
    for ref in policy["evidence"]:
        require(isinstance(ref, dict) and set(ref) == {"path", "sha256"}, "Evidence needs path and SHA-256")
        path = project_path(root, ref["path"])
        checked_hash(path, ref["sha256"], "footprint evidence")
        data = read_json(path)
        require(data.get("$schema") == MEASUREMENT_SCHEMA and data.get("ok") is True, "Invalid footprint evidence")
        require(data.get("method") == "ue_local_to_viewport", "Physical UE viewport measurements required")
        scenario = data.get("scenario")
        require(scenario in required and scenario not in scenarios, "Unexpected/duplicate footprint scenario")
        scenarios.add(scenario)
        pair(data.get("viewport_pixels"), "viewport_pixels")
        require(data["viewport_pixels"] == expected_viewports[scenario], "Actual viewport differs from required scenario: " + scenario)
        layout = data.get("layout_assets")
        require(isinstance(layout, list) and layout, "Hash-pinned layout assets required")
        for binding in layout:
            checked_hash(project_path(root, binding["path"]), binding.get("sha256"), "measured layout asset")
        require(data.get("project_file") in [p.name for p in root.glob("*.uproject")], "Footprint belongs to another project")
        rows = data.get("samples")
        require(isinstance(rows, list) and rows, "No measured widget samples")
        for row in rows:
            key = row.get("component")
            require(key in components, "Unknown footprint component: " + str(key))
            require(type(row.get("visible")) is bool, "Explicit sample visibility required")
            require(row.get("sampling") == "full_uv", "Atlas/tiling/nine-slice needs a separate sampling footprint contract")
            if not row["visible"]:
                continue  # Never use stale geometry from a hidden state.
            require(row.get("intermediate_render_target") is False, "Retainer/render-target density must be measured separately")
            pixels = pair(row.get("pixel_size"), "pixel_size")
            samples[key].append({"scenario": scenario, "widget": row.get("widget"), "pixel_size": pixels})
        evidence_refs.append(ref)
    require(scenarios == set(required), "Missing required viewport scenario")
    results = []
    candidate = copy.deepcopy(spec)
    for component in candidate["components"]:
        key = component["component_id"]
        require(component.get("processing_mode") == "approved_rgba_resize", key + ": approved_rgba_resize required")
        settings = component.get("approved_rgba_resize", {})
        require(not settings.get("atlas_grid"), key + ": standalone density planning only; atlas cell bindings are required separately")
        old = component["target_size"]
        pair(old, key + " target_size")
        require(all(type(v) is int for v in old), key + ": integer target size required")
        source_size = source_sizes[component["source_art_id"]]
        box = settings.get("sampling_box", [0, 0, *source_size])
        span = [box[2] - box[0], box[3] - box[1]]
        # Empty padding is not evidence of additional source detail.
        available = [max(0, min(box[axis + 2], source_size[axis]) - max(0, box[axis])) for axis in (0, 1)]
        require(min(available) > 0, key + ": sampling box has no source pixels")
        max_scale = min(*(a / b for a, b in zip(available, old)), *(limit / n for n in old))
        observations = samples[key]
        measured = bool(observations)
        footprint = [max(s["pixel_size"][axis] for s in observations) for axis in (0, 1)] if measured else None
        needed = max(target * px / n for px, n in zip(footprint, old)) if measured else 1.0
        requested = needed if downsize and measured else max(1.0, needed)
        scale = min(requested, max_scale)
        # Round upward to cover the measured demand, but never round over source/cap limits.
        proposed = [min(math.ceil(n * scale - 1e-9), math.floor(a), limit) for n, a in zip(old, available)]
        proposed = [max(1, n) for n in proposed]
        if not measured:
            proposed = list(old)  # Missing evidence never authorizes an automatic reduction.
        coverage = min(n / px for n, px in zip(proposed, footprint)) if measured else None
        current_coverage = min(n / px for n, px in zip(old, footprint)) if measured else None
        source_limited = requested > min(a / b for a, b in zip(available, old)) + 1e-9
        platform_limited = requested > min(limit / n for n in old) + 1e-9
        chain = component.get("authored_mips", {}).get("count", 1)
        require(chain <= max(proposed).bit_length(), key + ": target cannot support the declared authored mip count")
        status = "unmeasured_retained" if not measured else "source_or_platform_limited" if coverage + 1e-6 < target else "increase" if proposed != old and requested > 1 else "reduce" if proposed != old else "retain"
        results.append({"component": key, "source_size": source_size, "source_sampling_span": span,
            "current_size": old, "candidate_size": proposed, "max_draw_pixels": footprint,
            "current_texels_per_pixel": current_coverage, "candidate_texels_per_pixel": coverage,
            "source_limited": source_limited, "platform_limited": platform_limited,
            "meets_target": measured and coverage + 1e-6 >= target, "status": status,
            "measured_scenarios": sorted({s["scenario"] for s in observations}), "observations": observations,
            "current_rgba8_mip_bytes": rgba8_mip_bytes(old), "candidate_rgba8_mip_bytes": rgba8_mip_bytes(proposed)})
        component["target_size"] = proposed
        component["ue_package_path"] = package + "/Textures"
        component.setdefault("ue_texture", {})["compression"] = "UserInterface2D"
    current_bytes = sum(r["current_rgba8_mip_bytes"] for r in results)
    proposed_bytes = sum(r["candidate_rgba8_mip_bytes"] for r in results)
    coverage_complete = all(r["meets_target"] and set(r["measured_scenarios"]) == set(required) for r in results)
    candidate.setdefault("ue_import", {})["texture_package_path"] = package + "/Textures"
    candidate["ue_import"].setdefault("texture_import_policy", {})["compression"] = "UserInterface2D"
    candidate["policy"] = "Measured pixel-footprint review candidate. Approved originals and sampling boxes preserved. No production promotion or perceptual-quality guarantee."
    report = {"$schema": "ai-texture-density-plan-v1", "ok": True, "policy_sha256": sha256(policy_path),
        "spec_sha256": sha256(spec_path), "evidence": evidence_refs, "texels_per_pixel": target,
        "required_scenarios": required, "expected_viewports": expected_viewports, "components": results, "coverage_complete": coverage_complete,
        "within_payload_budget": proposed_bytes <= budget, "current_rgba8_mip_bytes": current_bytes,
        "candidate_rgba8_mip_bytes": proposed_bytes, "max_rgba8_mip_bytes": budget,
        "production_ready": False, "visual_review_required": True,
        "limitations": ["Payload excludes GPU allocation alignment, compression and allocator overhead; verify native texture memory.",
            "Coverage is geometric, not a perceptual quality score. Keep original-density reference and native rendered review.",
            "Unmeasured states retain current textures and cannot claim complete coverage."]}
    validate_spec(candidate, root)
    return report, candidate


def write_density_plan(policy_path, root, output_dir):
    import json
    root = Path(root).resolve()
    report, candidate = plan_density(policy_path, root)
    require(report["within_payload_budget"], "Candidate exceeds logical RGBA8 mip payload budget; no files written")
    output = project_path(root, str(output_dir))
    require(not output.exists(), "Use a fresh density output directory")
    package = candidate["ue_import"]["texture_package_path"]
    require(not (root / "Content" / package.removeprefix("/Game/")).parent.exists(), "Candidate UE namespace already exists")
    candidate["run_id"] = "density_" + output.name
    candidate["runtime_output_dir"] = (output / "Runtime").relative_to(root).as_posix()
    candidate["review_output_dir"] = (output / "Review").relative_to(root).as_posix()
    candidate["manifest_name"] = "Density_manifest.json"
    output.mkdir(parents=True)
    for name, value in (("Plan.json", report), ("Candidate.aiasset.json", candidate)):
        (output / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "plan": (output / "Plan.json").relative_to(root).as_posix(),
        "candidate_spec": (output / "Candidate.aiasset.json").relative_to(root).as_posix(),
        "coverage_complete": report["coverage_complete"], "production_ready": False,
        "changed_components": sum(r["candidate_size"] != r["current_size"] for r in report["components"])}


def measure_widgets(bindings_path, root, port, scenario):
    import json
    import uuid
    from .quality import EditorConnection
    root = Path(root).resolve()
    bindings_path = project_path(root, str(bindings_path))
    require(isinstance(scenario, str) and scenario.strip(), "Explicit viewport/state scenario required")
    connection = EditorConnection(root, port)
    require(connection.call("pie_status").get("pie_active") is True, "Start and settle the intended owned PIE screen before measuring")
    request_id = uuid.uuid4().hex
    folder = root / "Saved/AIAssetPipeline/Density" / request_id
    folder.mkdir(parents=True)
    result_path, request_path = folder / "result.json", folder / "request.json"
    bindings = read_json(bindings_path)
    require(bindings.get("$schema") == "ai-widget-footprint-bindings-v1", "Invalid footprint bindings")
    require(set(bindings) <= {"$schema", "widget_class", "components", "layout_assets", "notes"}, "Unknown footprint binding field")
    class_path = bindings.get("widget_class", "")
    require(class_path.startswith("/Game/") and "." in class_path, "Explicit generated WBP class required")
    require(isinstance(bindings.get("layout_assets"), list) and bindings["layout_assets"], "Pin measured layout assets")
    layout = [{"path": project_path(root, value).relative_to(root).as_posix(), "sha256": sha256(project_path(root, value))}
              for value in bindings["layout_assets"]]
    require("Content/" + class_path.split(".")[0].removeprefix("/Game/") + ".uasset" in [v["path"] for v in layout], "Pin the measured WBP")
    request = {"widget_class": class_path, "components": bindings["components"]}
    request_path.write_text(json.dumps(request), encoding="utf-8")
    result = connection.call("asset_pipeline_measure_widgets", request)
    for row in layout:
        checked_hash(project_path(root, row["path"]), row["sha256"], "layout changed during measurement")
    result.update({"$schema": MEASUREMENT_SCHEMA, "scenario": scenario, "request_id": request_id,
        "layout_assets": layout, "bindings_sha256": sha256(bindings_path), "editor": connection.identity})
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "evidence": result_path.relative_to(root).as_posix(), "sha256": sha256(result_path),
        "viewport_pixels": result["viewport_pixels"], "samples": len(result["samples"])}
