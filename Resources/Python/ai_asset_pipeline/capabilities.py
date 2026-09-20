"""Dependency-light installation check; deliberately distinct from native acceptance."""
import ast
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ai_asset_pipeline.sampling import CAPABILITIES, expected_graph, require


def check_installation(plugin_root):
    plugin_root = Path(plugin_root)
    contract = json.loads((plugin_root / "Resources/Capabilities/image_quality.v1.json").read_text(encoding="utf-8"))
    require(contract.get("schema") == "ai-image-quality-capabilities-v1", "Invalid capability catalog")
    require(contract.get("capabilities") == CAPABILITIES, "Capability catalog and implementation differ")
    descriptor = json.loads((plugin_root / "AIAssetPipeline.uplugin").read_text(encoding="utf-8-sig"))
    require(any(p.get("Name") == "PythonScriptPlugin" and p.get("Enabled") is True for p in descriptor.get("Plugins", [])),
            "PythonScriptPlugin dependency is required for native sampling")
    for name in contract["required_files"]:
        path = (plugin_root / name).resolve()
        require(path.is_relative_to(plugin_root.resolve()) and path.is_file(), "Missing capability file: " + name)
        if path.suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8-sig"))
    graph = expected_graph({"sample": "Test", "uv": "UV", "cell": "Cell", "bias": 0},
        {"ue_asset_path": "/Game/Test", "target_size": [128, 128], "atlas_mip_contract": {"max_sampled_lod": 4}})
    require(graph["mode"] == "TMVM_MIP_LEVEL" and len(graph["edges"]) == 10, "Broken bounded sampling contract")
    return {"ok": True, "capabilities": CAPABILITIES, "installation_verified": True, "native_render_verified": False}


if __name__ == "__main__":
    try:
        result = check_installation(Path(__file__).resolve().parents[3])
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        raise SystemExit(1)
    print(json.dumps(result))
