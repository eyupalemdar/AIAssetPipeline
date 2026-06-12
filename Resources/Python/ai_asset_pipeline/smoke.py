from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .spec import normalize_project_root, rel


def create_smoke_fixture(project_root: Path | str | None = None, output_dir: str = "Docs/AIAssetPipeline/Smoke") -> dict[str, Any]:
    root = normalize_project_root(project_root)
    base = root / output_dir
    source_dir = base / "source"
    prompt_dir = base / "prompts"
    spec_dir = base / "specs"
    runtime_dir = base / "runtime"
    review_dir = base / "review"
    for path in (source_dir, prompt_dir, spec_dir, runtime_dir, review_dir):
        path.mkdir(parents=True, exist_ok=True)

    source_path = source_dir / "AIAssetPipelineSmoke_Source.png"
    image = Image.new("RGBA", (64, 64), (255, 0, 216, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((14, 18, 50, 46), radius=6, fill=(32, 96, 160, 255))
    draw.rectangle((24, 28, 40, 36), fill=(210, 238, 255, 255))
    image.save(source_path)

    prompt_path = prompt_dir / "AIAssetPipelineSmoke.prompt.md"
    prompt_path.write_text(
        "Synthetic AIAssetPipeline smoke fixture. No model call; provider metadata is fixture-only.\n",
        encoding="utf-8",
    )

    spec_path = spec_dir / "AIAssetPipelineSmoke.aiasset.json"
    spec = {
        "$schema": "ai-asset-pipeline-spec-v1",
        "run_id": "ai_asset_pipeline_smoke_v1",
        "validation_policy": "fail_closed",
        "reference_size": [32, 32],
        "runtime_output_dir": rel(root, runtime_dir),
        "review_output_dir": rel(root, review_dir),
        "manifest_name": "AIAssetPipelineSmoke_manifest.json",
        "source_art": [
            {
                "id": "smoke_source",
                "path": rel(root, source_path),
                "prompt_files": [rel(root, prompt_path)],
                "provenance": {
                    "provider": "fixture",
                    "model": "synthetic-smoke",
                    "generation_id": "ai-asset-pipeline-smoke-v1",
                    "notes": "Generated locally for pipeline smoke validation; not production art.",
                },
            }
        ],
        "components": [
            {
                "component_id": "smoke_badge",
                "source_art_id": "smoke_source",
                "selector": {"type": "alpha_largest", "pad": 4, "min_area": 20},
                "draw_rect": [0, 0, 32, 32],
                "target_size": [64, 64],
                "asset_suffix": "SmokeBadge",
                "runtime_asset_name": "T_AIAssetPipelineSmoke_Badge",
                "ue_package_path": "/Game/UI/_AIProbe/AIAssetPipelineSmoke/Textures",
                "ue_asset_name": "T_AIAssetPipelineSmoke_Badge",
                "texture_type": "color",
                "z_order": 0,
            }
        ],
        "ue_import": {
            "texture_package_path": "/Game/UI/_AIProbe/AIAssetPipelineSmoke/Textures",
            "policy": "probe-only-smoke",
        },
        "reviews": {
            "contact_sheet": "AIAssetPipelineSmoke_contact_sheet.png",
            "assemblies": [
                {
                    "id": "smoke_assembly",
                    "file": "AIAssetPipelineSmoke_assembly.png",
                    "size": [96, 96],
                    "component_ids": ["smoke_badge"],
                }
            ],
        },
    }
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "project_root": str(root),
        "fixture_dir": rel(root, base),
        "spec": rel(root, spec_path),
        "source": rel(root, source_path),
        "prompt": rel(root, prompt_path),
    }
