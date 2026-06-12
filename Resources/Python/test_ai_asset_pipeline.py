from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from PIL import Image

import sys

PYTHON_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PYTHON_DIR.parents[3]
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from ai_asset_pipeline.pipeline import package_spec
from ai_asset_pipeline.smoke import create_smoke_fixture
from ai_asset_pipeline.spec import SpecError, validate_spec
from ai_asset_pipeline.tspec import validate_tspec_links
from ai_asset_pipeline.ue_import import plan_import


V10_SPEC = PROJECT_ROOT / "Docs/Tasarim/UI_Mockups/SeatPlate_Concepts_Image2_2026_06_09/asset_specs/DarkIntegratedPanel_ComponentProbe_V10.image2asset.json"
V10_MANIFEST = PROJECT_ROOT / "Docs/Tasarim/UI_Mockups/SeatPlate_Concepts_Image2_2026_06_09/assets/review/dark_integrated_panel_component_probe_v10_body_chroma_clean_glow_trimmed/SeatPlate_DarkIntegratedPanel_probe_manifest.json"
V10_TSPEC = PROJECT_ROOT / "Docs/Tasarim/UI_TSpecs/Probe_SeatPlate_DarkIntegratedPanel_ComponentProbe_V5.tspec.json"


class AIAssetPipelineTests(unittest.TestCase):
    def test_legacy_v10_spec_validates_with_warning(self) -> None:
        result = package_spec(V10_SPEC, validate_only=True, project_root=PROJECT_ROOT)
        self.assertTrue(result["ok"])
        self.assertTrue(any("legacy schema accepted" in warning for warning in result["warnings"]))

    def test_provider_agnostic_spec_requires_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "source.png"
            Image.new("RGBA", (8, 8), (255, 0, 216, 255)).save(source)
            prompt = root / "prompt.md"
            prompt.write_text("prompt", encoding="utf-8")

            spec = {
                "$schema": "ai-asset-pipeline-spec-v1",
                "run_id": "missing_provenance",
                "runtime_output_dir": "runtime",
                "review_output_dir": "review",
                "source_art": [{"id": "source", "path": "source.png", "prompt_files": ["prompt.md"]}],
                "components": [
                    {
                        "component_id": "body",
                        "source_art_id": "source",
                        "selector": {"type": "full_image"},
                        "draw_rect": [0, 0, 8, 8],
                        "asset_suffix": "Body",
                        "runtime_asset_name": "T_Body",
                        "ue_asset_name": "T_Body",
                    }
                ],
            }

            with self.assertRaises(SpecError):
                validate_spec(spec, root)

    def test_provider_agnostic_spec_packages_in_temp_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "source.png"
            image = Image.new("RGBA", (32, 32), (255, 0, 216, 255))
            for y in range(8, 24):
                for x in range(8, 24):
                    image.putpixel((x, y), (42, 36, 30, 255))
            image.save(source)
            prompt = root / "prompt.md"
            prompt.write_text("prompt", encoding="utf-8")
            spec_path = root / "spec.json"
            spec_path.write_text(
                """
{
  "$schema": "ai-asset-pipeline-spec-v1",
  "run_id": "temp_package",
  "validation_policy": "fail_closed",
  "reference_size": [16, 16],
  "runtime_output_dir": "runtime",
  "review_output_dir": "review",
  "source_art": [
    {
      "id": "source",
      "path": "source.png",
      "prompt_files": ["prompt.md"],
      "provenance": {
        "provider": "fixture",
        "model": "unit-test",
        "generation_id": "fixture-1",
        "notes": "Synthetic test image."
      }
    }
  ],
  "components": [
    {
      "component_id": "body",
      "source_art_id": "source",
      "selector": {"type": "alpha_largest", "pad": 4, "min_area": 20},
      "draw_rect": [0, 0, 16, 16],
      "asset_suffix": "Body",
      "runtime_asset_name": "T_Body",
      "ue_package_path": "/Game/UI/_AIProbe/Temp",
      "ue_asset_name": "T_Body",
      "texture_type": "color"
    }
  ]
}
""".strip(),
                encoding="utf-8",
            )

            result = package_spec(spec_path, project_root=root)
            self.assertTrue(result["ok"])
            self.assertEqual(result["component_count"], 1)
            self.assertEqual(result["alpha_contract"]["all_no_visible_chroma_key"], True)
            self.assertTrue((root / result["manifest"]).exists())

    def test_plan_import_reads_v10_manifest(self) -> None:
        result = plan_import(V10_MANIFEST, project_root=PROJECT_ROOT)
        self.assertTrue(result["ok"])
        self.assertEqual(result["component_count"], 15)
        self.assertEqual(result["imports"][0]["params"]["compression"], "UserInterface2D")

    def test_tspec_asset_pipeline_links_validate(self) -> None:
        result = validate_tspec_links(V10_TSPEC, project_root=PROJECT_ROOT)
        self.assertTrue(result["ok"], result["failures"])
        self.assertGreaterEqual(result["pipeline_count"], 1)

    def test_tspec_links_accept_multiple_texture_package_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            prompt = root / "prompt.md"
            prompt.write_text("fixture", encoding="utf-8")

            for name, color in (("local.png", (48, 48, 48, 255)), ("shared.png", (96, 96, 96, 255))):
                image = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
                for y in range(2, 6):
                    for x in range(2, 6):
                        image.putpixel((x, y), color)
                image.save(root / name)

            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "$schema": "ai-asset-pipeline-spec-v1",
                        "run_id": "multi_package",
                        "validation_policy": "fail_closed",
                        "runtime_output_dir": "runtime",
                        "review_output_dir": "review",
                        "source_art": [
                            {
                                "id": "local",
                                "path": "local.png",
                                "prompt_files": ["prompt.md"],
                                "provenance": {"provider": "fixture", "model": "unit-test", "generation_id": "local-1"},
                            },
                            {
                                "id": "shared",
                                "path": "shared.png",
                                "prompt_files": ["prompt.md"],
                                "provenance": {"provider": "fixture", "model": "unit-test", "generation_id": "shared-1"},
                            },
                        ],
                        "components": [
                            {
                                "component_id": "local_body",
                                "source_art_id": "local",
                                "selector": {"type": "full_image"},
                                "draw_rect": [0, 0, 8, 8],
                                "asset_suffix": "LocalBody",
                                "runtime_asset_name": "T_LocalBody",
                                "ue_package_path": "/Game/UI/Local",
                                "ue_asset_name": "T_LocalBody",
                                "texture_type": "color",
                                "target_size": [8, 8],
                            },
                            {
                                "component_id": "shared_mask",
                                "source_art_id": "shared",
                                "selector": {"type": "full_image"},
                                "draw_rect": [0, 0, 8, 8],
                                "asset_suffix": "SharedMask",
                                "runtime_asset_name": "T_SharedMask",
                                "ue_package_path": "/Game/UI/Shared",
                                "ue_asset_name": "T_SharedMask",
                                "texture_type": "mask",
                                "target_size": [8, 8],
                            },
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            packaged = package_spec(spec_path, project_root=root)
            tspec_path = root / "screen.tspec.json"
            tspec_path.write_text(
                json.dumps(
                    {
                        "$schema": "tspec-v1",
                        "screen": "MultiPackageFixture",
                        "assetPipelines": [
                            {
                                "id": "multi_package",
                                "spec": "spec.json",
                                "manifest": packaged["manifest"],
                                "texturePackagePaths": ["/Game/UI/Local", "/Game/UI/Shared"],
                                "requiredComponentIds": ["local_body", "shared_mask"],
                            }
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = validate_tspec_links(tspec_path, project_root=root)
            self.assertTrue(result["ok"], result["failures"])

    def test_tspec_link_scan_tolerates_bom_and_incomplete_legacy_source_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            tspec_dir = root / "tspecs"
            tspec_dir.mkdir()
            (tspec_dir / "legacy.tspec.json").write_text(
                "\ufeff" + json.dumps(
                    {
                        "$schema": "tspec-v1",
                        "screen": "LegacyIncompleteSourceIntent",
                        "sourceIntent": {
                            "manifest": "Docs/old_manifest.json",
                            "texturePackagePath": "/Game/UI/Legacy",
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = validate_tspec_links(tspec_dir, project_root=root)
            self.assertTrue(result["ok"], result["failures"])
            self.assertEqual(result["pipeline_count"], 0)

    def test_smoke_fixture_packages_and_plans_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            fixture = create_smoke_fixture(project_root=root)
            packaged = package_spec(root / fixture["spec"], project_root=root)
            self.assertTrue(packaged["ok"])
            self.assertEqual(packaged["component_count"], 1)
            planned = plan_import(root / packaged["manifest"], project_root=root)
            self.assertTrue(planned["ok"])
            self.assertEqual(planned["imports"][0]["ue_asset_path"], "/Game/UI/_AIProbe/AIAssetPipelineSmoke/Textures/T_AIAssetPipelineSmoke_Badge")

    def test_passthrough_texture_can_waive_intentional_rgb_alpha_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "atlas.png"
            image = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
            image.putpixel((3, 3), (220, 40, 30, 96))
            image.putpixel((4, 3), (220, 40, 30, 255))
            image.save(source)
            prompt = root / "prompt.md"
            prompt.write_text("procedural atlas", encoding="utf-8")
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "$schema": "ai-asset-pipeline-spec-v1",
                        "run_id": "passthrough_texture",
                        "validation_policy": "fail_closed",
                        "runtime_output_dir": "runtime",
                        "review_output_dir": "review",
                        "source_art": [
                            {
                                "id": "atlas",
                                "path": "atlas.png",
                                "prompt_files": ["prompt.md"],
                                "provenance": {
                                    "provider": "fixture",
                                    "model": "unit-test",
                                    "generation_id": "atlas-1",
                                },
                            }
                        ],
                        "components": [
                            {
                                "component_id": "atlas",
                                "source_art_id": "atlas",
                                "selector": {"type": "full_image"},
                                "draw_rect": [0, 0, 8, 8],
                                "asset_suffix": "Atlas",
                                "runtime_asset_name": "T_Atlas",
                                "ue_package_path": "/Game/UI/_AIProbe/Atlas",
                                "ue_asset_name": "T_Atlas",
                                "texture_type": "color",
                                "target_size": [8, 8],
                                "processing_mode": "passthrough",
                                "alpha_contract_policy": {
                                    "allow_low_alpha_saturated_rgb_artifacts": True,
                                    "reason": "Intentional antialiased colored atlas edge.",
                                },
                            }
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            packaged = package_spec(spec_path, project_root=root)
            self.assertTrue(packaged["ok"])
            manifest = json.loads((root / packaged["manifest"]).read_text(encoding="utf-8"))
            self.assertTrue(manifest["alpha_contract"]["all_no_low_alpha_saturated_rgb_artifacts"])
            self.assertEqual(manifest["alpha_contract"]["waivers"][0]["component_id"], "atlas")


if __name__ == "__main__":
    raise SystemExit(unittest.main())
