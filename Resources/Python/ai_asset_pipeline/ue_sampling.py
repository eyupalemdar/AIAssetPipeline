"""UE editor adapter. Explicit bindings only; never infer or change composition."""
from __future__ import annotations

import json
from pathlib import Path
import traceback

from .sampling import expected_graph, load_recipe, require, sha256


def run(recipe_path, root, apply=False, save=False):
    import unreal as ue
    root = Path(root).resolve()
    require(Path(ue.Paths.project_dir()).resolve() == root, "Wrong Unreal project")
    require(not save or apply, "Save requires apply")
    recipe, manifest, outputs = load_recipe(recipe_path, root)
    lib, assets = ue.MaterialEditingLibrary, ue.EditorAssetLibrary

    def nodes_for(material):
        nodes = {}
        for node in lib.get_material_expressions(material):
            name = str(node.get_editor_property("desc"))
            if name:
                require(name not in nodes, f"Ambiguous expression description: {name}")
                nodes[name] = node
        return nodes

    # Validate ALL targets and textures before creating or editing a node.
    work = []
    for definition in recipe["materials"]:
        material = ue.load_asset(definition["asset"])
        exists = material is not None
        if not exists:
            require(apply and definition.get("source_asset"), f"Missing material: {definition['asset']}")
            material = ue.load_asset(definition["source_asset"])
        require(isinstance(material, ue.Material), "Sampling target must be a Material")
        require(material.get_editor_property("material_domain") == ue.MaterialDomain.MD_UI, "Recipe supports UI materials only")
        nodes = nodes_for(material)
        reachable = set()
        def visit(current):
            if current is None or current.get_path_name() in reachable:
                return
            reachable.add(current.get_path_name())
            for parent in lib.get_inputs_for_material_expression(material, current):
                visit(parent)
        for prop in (ue.MaterialProperty.MP_EMISSIVE_COLOR, ue.MaterialProperty.MP_OPACITY):
            visit(lib.get_material_property_input_node(material, prop))
        for binding in definition["samples"]:
            output = outputs[binding["component"]]
            require(binding["sample"] in nodes and isinstance(nodes[binding["sample"]], ue.MaterialExpressionTextureSampleParameter2D), "Missing/nontyped texture sample: " + binding["sample"])
            sample = nodes[binding["sample"]]
            require(sample.get_path_name() in reachable, "Sample is disconnected from material outputs")
            sample_names = list(lib.get_material_expression_input_names(sample))
            sample_inputs = list(lib.get_inputs_for_material_expression(material, sample))
            for pin, parent in zip(sample_names, sample_inputs):
                if "View" in pin and "Bias" in pin:
                    require(parent is None, "A connected automatic-view-bias input overrides the sampling contract")
            if output.get("atlas_mip_contract"):
                require(binding["uv"] in nodes and binding["cell"] in nodes, "Missing explicit atlas UV/cell")
            texture = ue.load_asset(output["ue_asset_path"])
            require(isinstance(texture, ue.Texture2D), "Texture must be imported before material application")
            require([texture.blueprint_get_size_x(), texture.blueprint_get_size_y()] == output["target_size"], "Effective texture size mismatch")
            require(texture.get_editor_property("filter") == ue.TextureFilter.TF_TRILINEAR, "Texture must use trilinear")
            require(texture.get_editor_property("mip_gen_settings") == ue.TextureMipGenSettings.TMGS_LEAVE_EXISTING_MIPS, "Authored mip import required")
            require(texture.get_editor_property("lod_bias") == 0, "Nonzero texture LOD bias changes the atlas contract")
            cap = texture.get_editor_property("max_texture_size")
            require(cap == 0 or cap >= max(output["target_size"]), "MaxTextureSize removes required source level")
            # Preflight type collisions for idempotent reapplication.
            expected = expected_graph(binding, output)
            types = {expected["bias_node"]: ue.MaterialExpressionConstant}
            if "size_node" in expected:
                types.update({expected["size_node"]: ue.MaterialExpressionConstant2Vector, expected["cap_node"]: ue.MaterialExpressionConstant})
            types.update({name: ue.MaterialExpressionCustom for name in expected["nodes"]})
            for name, cls in types.items():
                require(name not in nodes or isinstance(nodes[name], cls), "Reserved sampling node type collision: " + name)
        work.append((definition, material, exists))

    rows = []
    for definition, material, exists in work:
        if apply and not exists:
            material = assets.duplicate_asset(definition["source_asset"], definition["asset"])
            require(material is not None, "Material clone failed")
        nodes = nodes_for(material)

        def node(name, cls):
            if name not in nodes and apply:
                nodes[name] = lib.create_material_expression(material, cls, -500, 500)
                require(nodes[name] is not None, "Expression creation failed")
                nodes[name].set_editor_property("desc", name)
            require(name in nodes and isinstance(nodes[name], cls), "Missing/invalid sampling node: " + name)
            return nodes[name]

        def equal(actual, expected, label):
            require(actual == expected, f"{definition['asset']}: {label}: {actual!r} != {expected!r}")

        for binding in definition["samples"]:
            output = outputs[binding["component"]]
            expected = expected_graph(binding, output)
            sample = nodes[binding["sample"]]
            bias = node(expected["bias_node"], ue.MaterialExpressionConstant)
            if apply:
                bias.set_editor_property("r", float(binding["bias"]))
                sample.set_editor_property("texture", ue.load_asset(output["ue_asset_path"]))
                sample.set_editor_property("mip_value_mode", getattr(ue.TextureMipValueMode, expected["mode"]))
                sample.set_editor_property("sampler_source", ue.SamplerSourceMode.SSM_FROM_TEXTURE_ASSET)
                sample.set_editor_property("automatic_view_mip_bias", False)
            require(abs(bias.get_editor_property("r") - binding["bias"]) < 1e-6, "Sampling bias mismatch")
            equal(sample.get_editor_property("texture").get_path_name().split(".")[0], output["ue_asset_path"], "Texture binding")
            equal(sample.get_editor_property("mip_value_mode"), getattr(ue.TextureMipValueMode, expected["mode"]), "Mip mode")
            equal(sample.get_editor_property("sampler_source"), ue.SamplerSourceMode.SSM_FROM_TEXTURE_ASSET, "Sampler source")
            equal(sample.get_editor_property("automatic_view_mip_bias"), False, "Automatic view bias")
            if "size" in expected:
                size = node(expected["size_node"], ue.MaterialExpressionConstant2Vector)
                cap = node(expected["cap_node"], ue.MaterialExpressionConstant)
                if apply:
                    size.set_editor_property("r", float(expected["size"][0]))
                    size.set_editor_property("g", float(expected["size"][1]))
                    cap.set_editor_property("r", float(expected["max_lod"]))
                equal([size.get_editor_property("r"), size.get_editor_property("g")], expected["size"], "Atlas dimensions")
                equal(cap.get_editor_property("r"), expected["max_lod"], "Atlas LOD cap")
            for name, contract in expected["nodes"].items():
                custom = node(name, ue.MaterialExpressionCustom)
                if apply:
                    custom.set_editor_property("code", contract["code"])
                    custom.set_editor_property("output_type", getattr(ue.CustomMaterialOutputType, contract["output"]))
                    inputs = []
                    for label in contract["inputs"]:
                        item = ue.CustomInput()
                        item.set_editor_property("input_name", label)
                        inputs.append(item)
                    custom.set_editor_property("inputs", inputs)
                equal(custom.get_editor_property("code"), contract["code"], "Sampling shader")
                equal(custom.get_editor_property("output_type"), getattr(ue.CustomMaterialOutputType, contract["output"]), "Custom output type")
                equal([str(x.get_editor_property("input_name")) for x in custom.get_editor_property("inputs")], contract["inputs"], "Custom input contract")
            for source, target, pin, output_pin in expected["edges"]:
                src, dst = nodes[source], nodes[target]
                if pin == "@uv":
                    pin = lib.get_material_expression_input_names(dst)[0]
                if apply:
                    require(lib.connect_material_expressions(src, output_pin, dst, pin), f"Connection failed: {source} -> {target}.{pin}")
                names = list(lib.get_material_expression_input_names(dst))
                inputs = list(lib.get_inputs_for_material_expression(material, dst))
                require(pin in names and inputs[names.index(pin)] == src, f"Disconnected/wrong input: {target}.{pin}")
                actual_output = lib.get_input_node_output_name_for_material_expression(dst, src)
                # UE returns the output name or None (out-parameter wrapper).
                require(actual_output is not None, f"Unresolved output: {source}")
                if output_pin:
                    equal(str(actual_output), output_pin, "Output channel")
                else:
                    require(str(actual_output) in ("", "None"), "Unexpected output channel: " + str(actual_output))
            rows.append({"material": definition["asset"], "sample": binding["sample"], "component": binding["component"], "mode": expected["mode"], "graph_verified": True})
        if apply:
            lib.recompile_material(material)
            if save:
                require(assets.save_loaded_asset(material, False), "Material save failed")
    return {"ok": True, "applied": apply, "saved": save, "recipe_sha256": sha256(recipe_path), "samples": rows,
            "scope": "Editor material graph and resources; actual native render and target cook remain separate acceptance gates."}


def run_request(request_path):
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    try:
        result = run(request["recipe"], request["root"], request["apply"], request["save"])
    except Exception as exc:
        result = {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}
    result["request_id"] = request["request_id"]
    path = Path(request["result"])
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
