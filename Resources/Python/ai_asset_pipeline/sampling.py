"""Project-neutral sampling contracts. Stdlib only; also imported inside UE."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

RECIPE_SCHEMA = "ai-material-sampling-v1"
CAPABILITIES = {
    "approved_rgba_resize": 1,
    "cell_isolated_authored_mips": 1,
    "native_texture_readback": 1,
    "bounded_atlas_sampling": 1,
    "sampling_graph_verification": 1,
    "project_identity_guard": 1,
    "quality_smoke": 1,
}
LOD_CODE = """float2 dx = ddx(UV) * TextureSize;
float2 dy = ddy(UV) * TextureSize;
float footprintSquared = max(dot(dx, dx), dot(dy, dy));
return clamp(0.5 * log2(max(footprintSquared, 1e-8)) + Bias, 0.0, MaxSampledLOD);"""
UV_CODE = """float2 inset = 0.5 * exp2(ceil(LOD)) / TextureSize;
return clamp(UV, Cell.xy + inset, Cell.xy + Cell.zw - inset);"""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def project_path(root, value):
    require(isinstance(value, str) and value, "Empty project path")
    path = (Path(root) / value).resolve()
    require(path.is_relative_to(Path(root).resolve()), f"Path escapes project: {value}")
    return path


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def asset_path(value):
    require(isinstance(value, str) and re.fullmatch(r"/Game/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+", value),
            f"Expected an explicit /Game package path: {value}")
    return value


def checked_hash(path, expected, label):
    require(isinstance(expected, str) and re.fullmatch(r"[a-fA-F0-9]{64}", expected), f"Missing {label} SHA-256")
    require(sha256(path) == expected.lower(), f"Changed {label}: {path}")


def validate_bundle(manifest, root):
    """Verify bytes, mip geometry and colour/data policy, not claimed booleans alone."""
    outputs = {}
    sources = {s["id"]: s for s in manifest.get("source_art", [])}
    for source in sources.values():
        checked_hash(project_path(root, source["path"]), source.get("sha256", source.get("provenance", {}).get("source_sha256")), "source")
    for row in manifest.get("outputs", []):
        key = row.get("component_id")
        require(isinstance(key, str) and key and key not in outputs, "Duplicate/empty component id")
        outputs[key] = row
        size = row.get("target_size")
        require(isinstance(size, list) and len(size) == 2 and all(type(n) is int and n > 0 for n in size), f"{key}: invalid dimensions")
        settings = row.get("ue_texture", {})
        if row.get("texture_type") in ("mask", "packed_mask"):
            require(settings.get("srgb") is False, f"{key}: data/mask must be linear")
        runtime = project_path(root, row["runtime_file"])
        checked_hash(runtime, row.get("runtime_sha256", row.get("dds_sha256")), f"{key} runtime")
        preserved = row.get("resize_contract", {}).get("approved_rgba", {})
        if row.get("processing_mode") == "approved_rgba_resize":
            source = sources.get(row.get("source_art_id"))
            # Older manifests retained a source path instead of the source id.
            if not source and row.get("source_file"):
                matches = [s for s in sources.values() if project_path(root, s["path"]) == project_path(root, row["source_file"])]
                if matches:
                    source = matches[0]  # aliases of the same verified file are equivalent
            if not source:
                matches = [s for s in sources.values() if s.get("sha256", s.get("provenance", {}).get("source_sha256")) == preserved.get("source_sha256")]
                require(len(matches) == 1, f"{key}: source provenance is ambiguous")
                source = matches[0]
            checked_hash(project_path(root, source["path"]), preserved.get("source_sha256"), f"{key} source")
        mips = row.get("source_mips", [])
        count = row.get("source_mip_count", 1)
        require(type(count) is int and 1 <= count <= 16, f"{key}: invalid mip count")
        if count > 1:
            require(len(mips) == count, f"{key}: missing authored mip records")
            require(settings.get("mip_gen") in ("LeaveExistingMips", "TMGS_LeaveExistingMips"), f"{key}: authored mip import required")
            for index, mip in enumerate(mips):
                require(mip.get("index") == index and mip.get("size") == [max(1, n >> index) for n in size], f"{key}: mip geometry mismatch")
                checked_hash(project_path(root, mip["png"]), mip.get("sha256"), f"{key} mip {index}")
        atlas = row.get("atlas_mip_contract")
        if atlas:
            grid = atlas.get("grid")
            require(isinstance(grid, list) and len(grid) == 2 and all(type(n) is int and n > 0 for n in grid), f"{key}: invalid atlas grid")
            require(count > 1 and type(atlas.get("max_sampled_lod")) is int and atlas["max_sampled_lod"] == count - 1, f"{key}: unsafe atlas LOD")
            require(atlas.get("renderer_clamp_required") is True and atlas.get("engine_generated_tail_must_not_be_sampled") is True, f"{key}: missing renderer obligation")
            for mip in mips:
                require(all(n % cells == 0 and n // cells >= 4 for n, cells in zip(mip["size"], grid)), f"{key}: partial or undersized atlas cell")
        asset_path(row["ue_asset_path"])
    require(outputs, "Empty manifest")
    return outputs


def load_recipe(path, root):
    recipe = read_json(project_path(root, str(path)))
    require(recipe.get("$schema") == RECIPE_SCHEMA, "Unsupported sampling recipe schema")
    require(set(recipe) <= {"$schema", "manifest", "manifest_sha256", "materials", "notes"}, "Unknown recipe field")
    manifest_path = project_path(root, recipe.get("manifest"))
    checked_hash(manifest_path, recipe.get("manifest_sha256"), "manifest")
    manifest = read_json(manifest_path)
    outputs = validate_bundle(manifest, root)
    materials = recipe.get("materials")
    require(isinstance(materials, list) and materials, "Recipe needs materials")
    seen = set()
    for material in materials:
        require(set(material) <= {"asset", "source_asset", "samples"}, "Unknown material field")
        target = asset_path(material.get("asset"))
        require(target not in seen, "Duplicate material")
        seen.add(target)
        if material.get("source_asset"):
            asset_path(material["source_asset"])
            require(material["source_asset"] != target, "Clone source must differ from target")
        samples = material.get("samples")
        require(isinstance(samples, list) and samples, "Material needs sample bindings")
        names = set()
        for binding in samples:
            require(set(binding) <= {"sample", "component", "uv", "uv_output", "cell", "cell_output", "bias"}, "Unknown sampling binding field")
            sample = binding.get("sample")
            require(isinstance(sample, str) and re.fullmatch(r"[A-Za-z0-9_]+", sample) and sample not in names, "Duplicate/invalid sample name")
            names.add(sample)
            require(binding.get("component") in outputs, f"{sample}: unknown component")
            output = outputs[binding["component"]]
            require(output.get("source_mip_count", 1) > 1, f"{sample}: authored mips required")
            require(output.get("ue_texture", {}).get("filter") in ("Trilinear", "TF_Trilinear"), f"{sample}: trilinear required")
            bias = binding.get("bias")
            require(type(bias) in (int, float) and math.isfinite(bias), f"{sample}: explicit finite bias required")
            if output.get("atlas_mip_contract"):
                require(all(isinstance(binding.get(k), str) and binding[k] for k in ("uv", "cell")), f"{sample}: explicit atlas UV and cell nodes required")
                require(binding.get("cell_output", "RGBA") == "RGBA", f"{sample}: complete cell vector required")
            else:
                require("cell" not in binding, f"{sample}: cell only applies to an atlas")
    return recipe, manifest, outputs


def validate_native_readback(data, outputs):
    for field in ("all_assets_exist", "all_sizes_match", "all_settings_match", "all_source_mips_match"):
        require(data.get(field) is True, f"Native import/readback failed: {field}")
    actual = {item["component_id"]: item for item in data.get("assets", [])}
    for key, output in outputs.items():
        row = actual.get(key, {})
        size = output["target_size"]
        require([row.get("runtime_width"), row.get("runtime_height")] == size, f"{key}: effective resource resolution differs (LOD group/platform cap)")
        require(row.get("runtime_mip_count", 0) >= output.get("source_mip_count", 1), f"{key}: runtime mip chain too short")
    return {"ok": True, "components": len(outputs)}


def expected_graph(binding, output):
    name = binding["sample"]
    prefix = "AIP_" + name
    bias = prefix + "_Bias"
    result = {"sample": name, "texture": output["ue_asset_path"], "bias": binding["bias"],
              "bias_node": bias, "mode": "TMVM_MIP_BIAS", "edges": [(bias, name, "Bias", "")], "nodes": {}}
    if not output.get("atlas_mip_contract"):
        return result
    size, cap, lod, uv = (prefix + "_" + tag for tag in ("Size", "MaxLOD", "LOD", "UV"))
    result.update(mode="TMVM_MIP_LEVEL", size=output["target_size"], size_node=size,
        max_lod=output["atlas_mip_contract"]["max_sampled_lod"], cap_node=cap,
        nodes={lod: {"code": LOD_CODE, "inputs": ["UV", "TextureSize", "MaxSampledLOD", "Bias"], "output": "CMOT_FLOAT1"},
               uv: {"code": UV_CODE, "inputs": ["UV", "TextureSize", "LOD", "Cell"], "output": "CMOT_FLOAT2"}},
        edges=[(binding["uv"], lod, "UV", binding.get("uv_output", "")), (size, lod, "TextureSize", ""),
               (cap, lod, "MaxSampledLOD", ""), (bias, lod, "Bias", ""),
               (binding["uv"], uv, "UV", binding.get("uv_output", "")), (size, uv, "TextureSize", ""),
               (lod, uv, "LOD", ""), (binding["cell"], uv, "Cell", "RGBA"),
               (lod, name, "Level", ""), (uv, name, "@uv", "")])
    return result
