from __future__ import annotations

import tempfile
import unittest
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


if __name__ == "__main__":
    raise SystemExit(unittest.main())
