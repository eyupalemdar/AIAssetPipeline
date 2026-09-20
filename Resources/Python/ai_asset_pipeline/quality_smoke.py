"""Small engineering fixture: approved-alpha chain and bounded, contrasting cells.

Synthetic pixels are test data, never substitute for product art or its approval.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import shutil
import subprocess

from .pipeline import package_spec
from .quality import EditorConnection, run_sampling
from .sampling import load_recipe, read_json, require, sha256


def create_fixture(root, run):
    from PIL import Image
    root = Path(root).resolve()
    require(re.fullmatch(r"[A-Za-z0-9_]{1,48}", run), "Use a simple unique run name")
    base = root / "Saved/AIAssetPipeline/Quality" / run
    require(not base.exists(), "Run already exists; choose a new run to preserve evidence")
    base.mkdir(parents=True)
    source = base / "source"
    source.mkdir()
    atlas = Image.new("RGBA", (128, 128))
    atlas.putdata([(255, 0, 0, 255) if x < 64 and y < 64 else (0, 0, 255, 255)
                   for y in range(128) for x in range(128)])
    atlas.save(source / "atlas.png")
    rgba = Image.new("RGBA", (128, 128))
    rgba.putdata([(32, 160, 64, max(0, min(255, min(x, y, 127-x, 127-y) * 16)))
                  for y in range(128) for x in range(128)])
    rgba.save(source / "rgba.png")
    authority = base / "Fixture.md"
    authority.write_text("Engineering-only synthetic pixels. Not Image-generated or product artwork.\n", encoding="utf-8")
    package = "/Game/AIAssetPipelineTests/" + run
    relative = lambda p: p.relative_to(root).as_posix()
    settings = {"compression": "UserInterface2D", "source_format": "TSF_BGRA8", "srgb": True,
                "mip_gen": "LeaveExistingMips", "lod_group": "UI", "address_x": "Clamp", "address_y": "Clamp",
                "filter": "Trilinear", "never_stream": True}
    spec = {"$schema": "ai-asset-pipeline-spec-v1", "run_id": run,
            "validation_policy": "fail_closed", "source_contract": relative(authority),
            "runtime_output_dir": relative(base / "runtime"), "review_output_dir": relative(base / "review"),
            "manifest_name": "manifest.json", "processing": {"clear_outer_alpha_px": 0},
            "source_art": [], "components": [], "ue_import": {"texture_package_path": package, "texture_import_policy": settings}}
    for kind in ("atlas", "rgba"):
        path = source / (kind + ".png")
        spec["source_art"].append({"id": kind, "path": relative(path), "authority_files": [relative(authority)],
            "provenance": {"kind": "existing_approved", "provider": "synthetic-engineering-fixture", "source_sha256": sha256(path)}})
        component = {"component_id": kind, "source_art_id": kind, "selector": {"type": "full_image_raw"},
            "draw_rect": [0, 0, 128, 128], "target_size": [128, 128], "z_order": 0, "asset_suffix": kind,
            "runtime_asset_name": "T_" + kind, "ue_asset_name": "T_" + kind, "ue_package_path": package,
            "texture_type": "color", "processing_mode": "approved_rgba_resize", "approved_rgba_resize": {},
            "authored_mips": {"count": 5}, "ue_texture": dict(settings), "quality_gates": {"blocking": True}}
        if kind == "atlas":
            component["approved_rgba_resize"]["atlas_grid"] = [2, 2]
            component["authored_mips"]["max_sampled_lod"] = 4
        spec["components"].append(component)
    spec_path = base / "fixture.aiasset.json"
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    package_spec(spec_path, project_root=root)
    manifest_path = base / "review/manifest.json"
    recipe = {"$schema": "ai-material-sampling-v1", "manifest": relative(manifest_path),
              "manifest_sha256": sha256(manifest_path), "materials": []}
    for kind in ("atlas", "rgba"):
        binding = {"sample": "Sample", "component": kind, "bias": 0.0}
        if kind == "atlas":
            binding.update(uv="AtlasUV", cell="Cell")
        recipe["materials"].append({"asset": package + "/M_" + kind, "samples": [binding]})
    recipe_path = base / "sampling.json"
    recipe_path.write_text(json.dumps(recipe, indent=2), encoding="utf-8")
    load_recipe(recipe_path, root)
    return base, recipe_path, package


def build_fixture_materials(connection, package):
    """Use established MCP material commands; only new isolated paths."""
    for kind in ("atlas", "rgba"):
        target = package + "/M_" + kind
        connection.call("create_material", {"package_path": package, "asset_name": "M_" + kind, "domain": "UI", "blend_mode": "Translucent"}, write=True)
        require(connection.call("get_material_graph", {"asset_path": target}).get("domain") == "MD_UI", "Fixture material domain was not applied")
        for name, cls in [("UV", "TextureCoordinate"), ("Sample", "TextureSampleParameter2D")]:
            connection.call("add_expression", {"asset_path": target, "expression_class": cls, "node_name": name}, write=True)
        def prop(node, name, value):
            connection.call("set_expression_property", {"asset_path": target, "node_name": node, "property_name": name, "value": value}, write=True)
        def edge(src, out, dst, pin):
            connection.call("connect_expressions", {"asset_path": target, "from_node": src, "from_output": out, "to_node": dst, "to_input": pin}, write=True)
        prop("Sample", "ParameterName", "SourceTexture")
        prop("Sample", "Texture", package + "/T_" + kind + ".T_" + kind)
        if kind == "atlas":
            connection.call("add_expression", {"asset_path": target, "expression_class": "VectorParameter", "node_name": "Cell"}, write=True)
            prop("Cell", "ParameterName", "Cell")
            prop("Cell", "DefaultValue", "(R=0,G=0,B=0.5,A=0.5)")
            connection.call("add_expression", {"asset_path": target, "expression_class": "Custom", "node_name": "AtlasUV"}, write=True)
            prop("AtlasUV", "Code", "return Cell.xy + UV * Cell.zw;")
            prop("AtlasUV", "OutputType", "CMOT_Float2")
            prop("AtlasUV", "Inputs", '((InputName="UV"),(InputName="Cell"))')
            edge("UV", "", "AtlasUV", "UV")
            edge("Cell", "RGBA", "AtlasUV", "Cell")
            edge("AtlasUV", "", "Sample", "Coordinates")
        else:
            edge("UV", "", "Sample", "Coordinates")
        connection.call("connect_to_material_property", {"asset_path": target, "from_node": "Sample", "from_output": "RGB", "material_property": "EmissiveColor"}, write=True)
        connection.call("connect_to_material_property", {"asset_path": target, "from_node": "Sample", "from_output": "A", "material_property": "Opacity"}, write=True)
        connection.call("compile_material", {"asset_path": target}, write=True)


def verify_fixture_tree(tree, nodes):
    wrapper = tree.get("root", {})
    require(wrapper.get("parent_class") == "UserWidget", "Wrong fixture widget parent class")
    actual = []
    def visit(node, parent):
        actual.append((node.get("name"), node.get("type"), parent))
        for child in node.get("children", []):
            visit(child, node.get("name"))
    visit(wrapper.get("root", {}), "")
    expected = [(node["name"], node["widgetClass"], node["parentName"]) for node in nodes]
    require(sorted(actual) == sorted(expected), "Fixture WidgetTree differs from TSpec (name/class/parent)")


def build_fixture_widgets(connection, root, base, package):
    """Generate the complete TSpec first, then run the host's validator before writes."""
    state = read_json(root / "Tools/AIWorkflowBootstrap/state/project.json")
    spec_dir = root / state["uiSpecDir"]
    nodes = [{"name": "Root_Overlay", "parentName": "", "widgetClass": "Overlay"},
             {"name": "BG_Image", "parentName": "Root_Overlay", "widgetClass": "Image", "slot": {"hAlign": "Fill", "vAlign": "Fill"}},
             {"name": "Content_SafeZone", "parentName": "Root_Overlay", "widgetClass": "SafeZone"},
             {"name": "MainCanvas", "parentName": "Content_SafeZone", "widgetClass": "CanvasPanel"}]
    for kind in ("atlas", "rgba"):
        spec = {"$schema": "tspec-v1", "screen": base.name + "_" + kind,
                "pencilFile": (base / "Fixture.md").relative_to(root).as_posix(), "pencilFrameId": "engineering-fixture",
                "mode": "hybrid", "wbpPath": package + "/W_" + kind, "parentClass": "/Script/UMG.UserWidget",
                "rootShell": {"type": "hybrid-adaptive", "referenceSize": [128, 128]}, "nodes": copy.deepcopy(nodes),
                "sourceIntent": {"purpose": "Isolated sampling engineering acceptance, not product art"}}
        spec["nodes"][1]["properties"] = {"Brush.ResourceObject": package + "/M_" + kind + ".M_" + kind}
        spec_dir.mkdir(parents=True, exist_ok=True)
        (spec_dir / ("Quality_" + base.name + "_" + kind + ".tspec.json")).write_text(json.dumps(spec, indent=2), encoding="utf-8")
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    require(powershell, "PowerShell required for host TSpec validator")
    checked = subprocess.run([powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / state["validatorPath"]),
                              "-Root", str(root), "-SpecDirectory", state["uiSpecDir"]], cwd=root, capture_output=True, text=True)
    require(checked.returncode == 0, "Host TSpec validator failed: " + checked.stdout + checked.stderr)
    (base / "TSpecValidation.txt").write_text(checked.stdout + checked.stderr, encoding="utf-8")
    for kind in ("atlas", "rgba"):
        path = package + "/W_" + kind
        connection.call("create_widget_blueprint", {"package_path": package, "asset_name": "W_" + kind, "parent_class": "/Script/UMG.UserWidget"}, write=True)
        for node in nodes:
            connection.call("add_widget", {"asset_path": path, "widget_name": node["name"], "widget_class": node["widgetClass"], "parent_name": node["parentName"]}, write=True)
        connection.call("set_widget_property", {"asset_path": path, "widget_name": "BG_Image", "property_name": "Brush.ResourceObject", "value": package + "/M_" + kind + ".M_" + kind}, write=True)
        connection.call("set_slot_property", {"asset_path": path, "widget_name": "BG_Image", "property_name": "HorizontalAlignment", "value": "HAlign_Fill"}, write=True)
        connection.call("set_slot_property", {"asset_path": path, "widget_name": "BG_Image", "property_name": "VerticalAlignment", "value": "VAlign_Fill"}, write=True)
        connection.call("compile_and_save", {"asset_path": path}, write=True)
        connection.call("reload_asset", {"asset_path": path}, write=True)
        tree = connection.call("get_widget_tree", {"asset_path": path})
        (base / (kind + "_tree.json")).write_text(json.dumps(tree, indent=2), encoding="utf-8")
        verify_fixture_tree(tree, nodes)


def run_quality_smoke(root, run, port=None, native=False):
    root = Path(root).resolve()
    require(re.fullmatch(r"[A-Za-z0-9_]{1,48}", run), "Use a simple unique run name")
    base = root / "Saved/AIAssetPipeline/Quality" / run
    existed = base.exists()
    try:
        return _run_quality_smoke(root, run, port, native)
    except Exception as exc:
        if not existed and base.is_dir():
            (base / "Failure.json").write_text(json.dumps({"ok": False, "native_requested": native,
                "error": str(exc), "resume": "Inspect retained artifacts. Use a new run; no blind retries or overwrites."}, indent=2), encoding="utf-8")
        raise


def _run_quality_smoke(root, run, port=None, native=False):
    from PIL import Image
    root = Path(root).resolve()
    connection = None
    if native:
        connection = EditorConnection(root, port)
        configuration = root / "Config/DefaultGame.ini"
        require(configuration.is_file() and re.search(r'\+DirectoriesToNeverCook\s*=\s*\(Path="/Game/AIAssetPipelineTests"\)', configuration.read_text(encoding="utf-8-sig")),
                'Exclude /Game/AIAssetPipelineTests with +DirectoriesToNeverCook in DefaultGame.ini before native smoke')
    base, recipe_path, package = create_fixture(root, run)
    result = {"ok": True, "packaging_verified": True, "native_verified": False, "product_visual_acceptance": False,
              "recipe": recipe_path.relative_to(root).as_posix(), "captures": []}
    if native:
        recipe = read_json(recipe_path)
        connection.call("asset_pipeline_import_manifest", {"manifest_path": str(root / recipe["manifest"]), "force": False}, write=True)
        build_fixture_materials(connection, package)
        result["sampling"] = run_sampling(recipe_path, root, port, apply=True, save=True)
        build_fixture_widgets(connection, root, base, package)
        for kind in ("atlas", "rgba"):
            for size in (17, 31, 64, 128):
                path = base / (kind + "_" + str(size) + ".png")
                connection.call("capture_widget_preview", {"asset_path": package + "/W_" + kind, "width": size, "height": size,
                    "dpi_scale": 1.0, "preview_mode": "runtime", "warmup_frames": 4, "transparent_bg": True,
                    "return_base64": False, "output_path": str(path)}, write=True)
                with Image.open(path) as image:
                    require(image.size == (size, size), "Incorrect render dimensions")
                    pixels = list(image.convert("RGBA").getdata())
                    if kind == "atlas":
                        require(all(r >= 250 and g <= 2 and b <= 2 and a >= 250 for r, g, b, a in pixels), "Atlas cell leaked or failed to render")
                    else:
                        require(max(p[3] for p in pixels) >= 250 and min(p[3] for p in pixels) < 220, "RGBA alpha failed to render")
                        center = image.convert("RGBA").getpixel((size//2, size//2))
                        require(center[1] > center[0] and center[1] > center[2], "Wrong standalone colour/render fallback")
                result["captures"].append({"path": path.relative_to(root).as_posix(), "sha256": sha256(path), "size": [size, size]})
        # Re-read without applying. Success cannot be obtained by silently repairing.
        result["final_verification"] = run_sampling(recipe_path, root, port)
        result["native_verified"] = True
    receipt = base / "Receipt.json"
    receipt.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    result["receipt"] = str(receipt)
    return result
