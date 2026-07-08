from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from PIL import Image
import numpy as np

import sys

PYTHON_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PYTHON_DIR.parents[3]
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from ai_asset_pipeline.pipeline import package_spec
from ai_asset_pipeline.image_ops import clean_button_icon_overlay, connected_component_stats, diagnostics
from ai_asset_pipeline.review import compose_review, save_matte_issue_overlay
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

    def test_low_alpha_saturated_rgb_below_legacy_threshold_is_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "source.png"
            image = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
            for y in range(4, 12):
                for x in range(4, 12):
                    image.putpixel((x, y), (4, 64, 16, 255))
            image.putpixel((3, 7), (0, 255, 0, 4))
            image.putpixel((7, 3), (255, 255, 0, 4))
            image.putpixel((12, 7), (255, 0, 0, 4))
            image.save(source)
            prompt = root / "prompt.md"
            prompt.write_text("synthetic low alpha edge artifact", encoding="utf-8")
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "$schema": "ai-asset-pipeline-spec-v1",
                        "run_id": "low_alpha_edge",
                        "validation_policy": "fail_closed",
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
                                    "generation_id": "low-alpha-1",
                                },
                            }
                        ],
                        "components": [
                            {
                                "component_id": "body",
                                "source_art_id": "source",
                                "selector": {"type": "full_image"},
                                "draw_rect": [0, 0, 16, 16],
                                "asset_suffix": "Body",
                                "runtime_asset_name": "T_Body",
                                "ue_package_path": "/Game/UI/_AIProbe/LowAlpha",
                                "ue_asset_name": "T_Body",
                                "texture_type": "color",
                                "target_size": [16, 16],
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
            diagnostics = manifest["outputs"][0]["diagnostics"]
            self.assertEqual(diagnostics["low_alpha_saturated_rgb_artifact_pixels"], 0)
            runtime = Image.open(root / manifest["outputs"][0]["runtime_file"]).convert("RGBA")
            self.assertEqual(runtime.getpixel((3, 7))[3], 0)
            self.assertEqual(runtime.getpixel((7, 3))[3], 0)
            self.assertEqual(runtime.getpixel((12, 7))[3], 0)

    def test_soft_glow_resize_feathers_noisy_runtime_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "source_glow.png"

            width, height = 96, 72
            yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
            cx, cy = 48.0, 42.0
            rx, ry = 43.0, 29.0
            distance = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
            alpha = np.clip((1.08 - distance) / 1.08, 0.0, 1.0)
            perimeter = (distance > 0.92) & (distance < 1.16)
            alpha[perimeter] = np.maximum(alpha[perimeter], 0.13 + (((xx[perimeter] + yy[perimeter]) % 5) * 0.025))
            arr = np.zeros((height, width, 4), dtype=np.uint8)
            arr[:, :, 0] = 180
            arr[:, :, 1] = 255
            arr[:, :, 2] = 24
            arr[:, :, 3] = np.clip(np.rint(alpha * 255.0), 0, 255).astype(np.uint8)
            Image.fromarray(arr, "RGBA").save(source)

            prompt = root / "prompt.md"
            prompt.write_text("synthetic soft glow with noisy low-alpha perimeter", encoding="utf-8")
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "$schema": "ai-asset-pipeline-spec-v1",
                        "run_id": "soft_glow_resize",
                        "validation_policy": "fail_closed",
                        "runtime_output_dir": "runtime",
                        "review_output_dir": "review",
                        "source_art": [
                            {
                                "id": "glow",
                                "path": "source_glow.png",
                                "prompt_files": ["prompt.md"],
                                "provenance": {
                                    "provider": "fixture",
                                    "model": "unit-test",
                                    "generation_id": "soft-glow-1",
                                },
                            }
                        ],
                        "components": [
                            {
                                "component_id": "active_halo",
                                "source_art_id": "glow",
                                "selector": {"type": "full_image"},
                                "draw_rect": [0, 0, 64, 48],
                                "target_size": [32, 24],
                                "asset_suffix": "ActiveHalo",
                                "runtime_asset_name": "T_ActiveHalo",
                                "ue_package_path": "/Game/UI/_AIProbe/SoftGlow",
                                "ue_asset_name": "T_ActiveHalo",
                                "texture_type": "glow",
                                "processing_mode": "soft_glow_resize",
                                "soft_glow_resize": {
                                    "alpha_low_cutoff": 0.035,
                                    "alpha_high_cutoff": 0.92,
                                    "alpha_gamma": 1.18,
                                    "alpha_scale": 0.86,
                                    "border_fade_px": 4,
                                    "palette": {
                                        "outer": [47, 142, 79],
                                        "body": [101, 201, 65],
                                        "core": [183, 245, 29],
                                        "core_mix": 0.55,
                                    },
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
            output = manifest["outputs"][0]
            self.assertEqual(output["processing_mode"], "soft_glow_resize")
            self.assertEqual(output["recommended_strategy"], "soft_glow_resize_clean_alpha_falloff")
            self.assertTrue(output["is_final_runtime_safe"])
            self.assertEqual(output["diagnostics"]["edge_alpha_gt0"], 0)
            self.assertEqual(output["diagnostics"]["max_edge_alpha"], 0)
            runtime = Image.open(root / output["runtime_file"]).convert("RGBA")
            self.assertEqual(runtime.size, (32, 24))
            self.assertLessEqual(runtime.getchannel("A").getextrema()[1], 220)

    def test_luma_mask_resize_writes_grayscale_runtime_safe_mask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "source_luma.png"

            image = Image.new("RGBA", (64, 80), (255, 0, 216, 255))
            for y in range(14, 66):
                for x in range(18, 46):
                    shade = 64 + int(((x - 18) / 28) * 160)
                    alpha = 80 + int(((y - 14) / 52) * 175)
                    image.putpixel((x, y), (shade, shade, shade, alpha))
            image.save(source)

            prompt = root / "prompt.md"
            prompt.write_text("synthetic luma mask fixture", encoding="utf-8")
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "$schema": "ai-asset-pipeline-spec-v1",
                        "run_id": "luma_mask_resize",
                        "validation_policy": "fail_closed",
                        "runtime_output_dir": "runtime",
                        "review_output_dir": "review",
                        "source_art": [
                            {
                                "id": "luma",
                                "path": "source_luma.png",
                                "prompt_files": ["prompt.md"],
                                "provenance": {
                                    "provider": "fixture",
                                    "model": "unit-test",
                                    "generation_id": "luma-mask-1",
                                },
                            }
                        ],
                        "components": [
                            {
                                "component_id": "highlight_luma",
                                "source_art_id": "luma",
                                "selector": {"type": "alpha_bbox", "pad": 4},
                                "draw_rect": [0, 0, 48, 64],
                                "target_size": [48, 64],
                                "asset_suffix": "HighlightLuma",
                                "runtime_asset_name": "T_HighlightLuma",
                                "ue_package_path": "/Game/UI/_AIProbe/Luma",
                                "ue_asset_name": "T_HighlightLuma",
                                "texture_type": "mask",
                                "processing_mode": "luma_mask_resize",
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
            output = manifest["outputs"][0]
            self.assertEqual(output["processing_mode"], "luma_mask_resize")
            self.assertEqual(output["recommended_strategy"], "color_locked_luma_mask_resize")
            self.assertTrue(output["is_final_runtime_safe"])
            self.assertEqual(output["diagnostics"]["edge_alpha_gt0"], 0)
            runtime = Image.open(root / output["runtime_file"]).convert("RGBA")
            arr = np.asarray(runtime)
            self.assertTrue(np.all(arr[:, :, 0] == arr[:, :, 1]))
            self.assertTrue(np.all(arr[:, :, 1] == arr[:, :, 2]))

    def test_approved_source_target_size_removes_detached_speckles_and_writes_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "source.png"
            image = Image.new("RGBA", (64, 64), (255, 0, 216, 255))
            for y in range(18, 46):
                for x in range(16, 48):
                    image.putpixel((x, y), (42, 36, 30, 255))
            image.putpixel((4, 4), (50, 40, 35, 255))
            image.putpixel((58, 58), (80, 70, 60, 255))
            image.putpixel((8, 56), (255, 0, 216, 96))
            image.save(source)
            prompt = root / "prompt.md"
            prompt.write_text("approved source target size fixture", encoding="utf-8")
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "$schema": "ai-asset-pipeline-spec-v1",
                        "run_id": "approved_source_target_size",
                        "validation_policy": "fail_closed",
                        "runtime_output_dir": "runtime",
                        "review_output_dir": "review",
                        "approved_source_target_size": {
                            "pre_speckle_min_area": 8,
                            "post_speckle_min_area": 8,
                        },
                        "source_art": [
                            {
                                "id": "source",
                                "path": "source.png",
                                "prompt_files": ["prompt.md"],
                                "provenance": {
                                    "provider": "fixture",
                                    "model": "unit-test",
                                    "generation_id": "approved-source-1",
                                },
                            }
                        ],
                        "components": [
                            {
                                "component_id": "body",
                                "source_art_id": "source",
                                "selector": {"type": "full_image"},
                                "draw_rect": [0, 0, 32, 32],
                                "asset_suffix": "Body",
                                "runtime_asset_name": "T_Body",
                                "ue_package_path": "/Game/UI/_AIProbe/ApprovedSource",
                                "ue_asset_name": "T_Body",
                                "texture_type": "color",
                                "processing_mode": "approved_source_target_size",
                                "target_size": [32, 32],
                            }
                        ],
                        "reviews": {
                            "diagnostics": {
                                "alpha_mask": True,
                                "matte_issue_overlay": True,
                            },
                            "assemblies": [
                                {
                                    "id": "preview",
                                    "file": "preview.png",
                                    "size": [32, 32],
                                    "component_ids": ["body"],
                                }
                            ],
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            packaged = package_spec(spec_path, project_root=root)
            self.assertTrue(packaged["ok"])
            manifest = json.loads((root / packaged["manifest"]).read_text(encoding="utf-8"))
            diagnostics = manifest["outputs"][0]["diagnostics"]
            self.assertEqual(diagnostics["small_alpha_component_count"], 0)
            self.assertEqual(diagnostics["visible_chroma_key_pixels_alpha_gt_8"], 0)
            self.assertEqual(diagnostics["visible_magenta_fringe_pixels_alpha_gt_8"], 0)
            self.assertTrue((root / manifest["reviews"]["preview_alpha_mask"]).exists())
            self.assertTrue((root / manifest["reviews"]["preview_matte_issue_overlay"]).exists())

    def test_review_downscale_does_not_introduce_matte_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = root / "runtime"
            runtime_dir.mkdir()
            runtime = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            for y in range(7, 58):
                for x in range(5, 60):
                    dx = x - 32
                    dy = y - 32
                    if dx * dx + dy * dy <= 26 * 26:
                        runtime.putpixel((x, y), (186, 112, 58, 255))
            runtime.save(runtime_dir / "T_Body.png")

            preview = compose_review(
                root,
                [
                    {
                        "component_id": "body",
                        "runtime_file": "runtime/T_Body.png",
                        "draw_rect": [0, 0, 32, 32],
                    }
                ],
                {"size": [32, 32], "component_ids": ["body"]},
            )
            preview_diagnostics = diagnostics(preview)
            self.assertEqual(preview_diagnostics["low_alpha_saturated_rgb_artifact_pixels"], 0)

            overlay_path = root / "matte_issues.png"
            save_matte_issue_overlay(preview, overlay_path)
            overlay = Image.open(overlay_path).convert("RGB")
            marker_pixels = sum(
                1
                for y in range(overlay.height)
                for x in range(overlay.width)
                if overlay.getpixel((x, y)) == (255, 40, 220)
            )
            self.assertEqual(marker_pixels, 0)

    def test_button_icon_overlay_cleanup_removes_gray_haze_and_detached_noise(self) -> None:
        image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        for y in range(20, 78):
            for x in range(35, 65):
                image.putpixel((x, y), (184, 114, 60, 255))
        for y in range(72, 84):
            for x in range(24, 76):
                image.putpixel((x, y), (150, 82, 45, 230))
        for xy in ((18, 28), (74, 21), (79, 45), (67, 28), (63, 18)):
            for y in range(xy[1], xy[1] + 4):
                for x in range(xy[0], xy[0] + 4):
                    image.putpixel((x, y), (62, 62, 60, 150))
        for y in range(18, 84):
            image.putpixel((66, y), (58, 58, 56, 120))

        cleaned = clean_button_icon_overlay(image)
        arr = np.asarray(cleaned.convert("RGBA"), dtype=np.uint8)
        alpha = arr[:, :, 3]
        components = connected_component_stats(alpha > 8)
        self.assertTrue(all(component["area"] >= 96 for component in components))
        self.assertGreater(max(component["area"] for component in components), 1000)
        self.assertEqual(diagnostics(cleaned)["small_alpha_component_count"], 0)

        rgb = arr[:, :, :3]
        maximum = rgb.max(axis=2)
        minimum = rgb.min(axis=2)
        gray_haze = (alpha > 8) & (alpha < 230) & (maximum < 95) & ((maximum - minimum) < 35)
        self.assertEqual(int(gray_haze.sum()), 0)

    def test_vector_sdf_icon_packages_without_raster_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "audit_source.png"
            Image.new("RGBA", (4, 4), (255, 0, 216, 255)).save(source)
            prompt = root / "audit.prompt.md"
            prompt.write_text("audit-only source", encoding="utf-8")
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "$schema": "ai-asset-pipeline-spec-v1",
                        "run_id": "vector_sdf_icon",
                        "validation_policy": "fail_closed",
                        "runtime_output_dir": "runtime",
                        "review_output_dir": "review",
                        "source_art": [
                            {
                                "id": "audit",
                                "path": "audit_source.png",
                                "prompt_files": ["audit.prompt.md"],
                                "provenance": {
                                    "provider": "fixture",
                                    "model": "unit-test",
                                    "generation_id": "audit-1",
                                },
                            }
                        ],
                        "components": [
                            {
                                "component_id": "mic",
                                "draw_rect": [0, 0, 50, 50],
                                "target_size": [100, 100],
                                "asset_suffix": "Mic",
                                "runtime_asset_name": "T_Mic",
                                "ue_package_path": "/Game/UI/_AIProbe/VectorIcon",
                                "ue_asset_name": "T_Mic",
                                "texture_type": "icon",
                                "processing_mode": "vector_sdf_icon",
                                "vector_icon": {"glyph": "mic_unmuted", "supersample": 6},
                                "alpha_contract_policy": {
                                    "allow_low_alpha_saturated_rgb_artifacts": True,
                                    "reason": "Intentional clean bronze anti-aliased vector/SDF edge pixels.",
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
            output = manifest["outputs"][0]
            self.assertEqual(output["source_file"], "procedural-vector-icon")
            self.assertEqual(output["recommended_strategy"], "procedural_vector_sdf_icon")
            self.assertEqual(output["diagnostics"]["visible_chroma_key_pixels_alpha_gt_8"], 0)
            self.assertEqual(output["diagnostics"]["visible_magenta_fringe_pixels_alpha_gt_8"], 0)
            runtime = Image.open(root / output["runtime_file"]).convert("RGBA")
            self.assertEqual(runtime.size, (100, 100))
            self.assertGreater(output["diagnostics"]["opaque_or_translucent_pixels"], 500)

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
