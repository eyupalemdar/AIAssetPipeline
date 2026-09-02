from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from PIL import Image, ImageDraw
import numpy as np

import sys

PYTHON_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PYTHON_DIR.parents[3]
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from ai_asset_pipeline.pipeline import _evaluate_quality_gates, package_spec
from ai_asset_pipeline.image_ops import (
    alpha_bbox,
    chroma_to_alpha,
    chroma_to_alpha_strict_hsv,
    clean_button_icon_overlay,
    clean_existing_source_target_size,
    connected_component_stats,
    diagnostics,
)
from ai_asset_pipeline.review import compose_review, save_matte_issue_overlay
from ai_asset_pipeline.smoke import create_smoke_fixture
from ai_asset_pipeline.spec import SpecError, validate_spec
from ai_asset_pipeline.tspec import validate_tspec_links
from ai_asset_pipeline.ue_import import plan_import


V10_SPEC = PROJECT_ROOT / "Docs/Tasarim/UI_Mockups/SeatPlate_Concepts_Image2_2026_06_09/asset_specs/DarkIntegratedPanel_ComponentProbe_V10.image2asset.json"
V10_MANIFEST = PROJECT_ROOT / "Docs/Tasarim/UI_Mockups/SeatPlate_Concepts_Image2_2026_06_09/assets/review/dark_integrated_panel_component_probe_v10_body_chroma_clean_glow_trimmed/SeatPlate_DarkIntegratedPanel_probe_manifest.json"
V10_TSPEC = PROJECT_ROOT / "Docs/Tasarim/UI_TSpecs/Probe_SeatPlate_DarkIntegratedPanel_ComponentProbe_V5.tspec.json"


class AIAssetPipelineTests(unittest.TestCase):
    def test_strict_hsv_chroma_removes_dark_key_shadow_and_keeps_brown_art(self) -> None:
        image = Image.new("RGBA", (12, 10), (255, 0, 216, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((4, 2, 8, 7), fill=(116, 72, 38, 255))
        draw.line((0, 8, 11, 8), fill=(80, 10, 55, 255), width=1)
        image.putpixel((10, 4), (58, 4, 0, 255))

        standard = np.asarray(chroma_to_alpha(image), dtype=np.uint8)
        strict = np.asarray(chroma_to_alpha_strict_hsv(image), dtype=np.uint8)
        self.assertEqual(int(standard[8, 5, 3]), 255)
        self.assertEqual(int(strict[8, 5, 3]), 0)
        self.assertEqual(int(strict[4, 6, 3]), 255)
        self.assertEqual(int(strict[4, 10, 3]), 255)

        target = clean_existing_source_target_size(
            Image.fromarray(strict, "RGBA"),
            (6, 5),
            clear_outer_pixels=1,
            linear_light=True,
            strict_hsv_post_cleanup=True,
            post_speckle_min_area=0,
        )
        target_arr = np.asarray(target, dtype=np.uint8)
        self.assertEqual(target.size, (6, 5))
        self.assertEqual(int(target_arr[0, :, 3].max()), 0)
        self.assertGreater(int(target_arr[2, 3, 3]), 0)

    def test_strict_cutout_spec_contract_validates_and_is_receipted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source_path = root / "source.png"
            source = Image.new("RGBA", (32, 32), (255, 0, 216, 255))
            draw = ImageDraw.Draw(source)
            draw.rectangle((8, 7, 23, 23), fill=(116, 72, 38, 255))
            draw.line((6, 25, 25, 25), fill=(80, 10, 55, 255), width=1)
            source.save(source_path)
            (root / "prompt.md").write_text("synthetic strict cutout fixture", encoding="utf-8")

            spec = {
                "$schema": "ai-asset-pipeline-spec-v1",
                "run_id": "strict-cutout-contract",
                "validation_policy": "fail_closed",
                "runtime_output_dir": "runtime",
                "review_output_dir": "review",
                "approved_source_target_size": {
                    "linear_light": True,
                    "strict_hsv_post_cleanup": False,
                    "fit_visible_alpha_to_safe_area": True,
                    "post_speckle_min_area": 0,
                },
                "source_art": [
                    {
                        "id": "source",
                        "path": "source.png",
                        "prompt_files": ["prompt.md"],
                        "provenance": {
                            "provider": "fixture",
                            "model": "unit-test",
                            "generation_id": "strict-cutout-1",
                        },
                    }
                ],
                "components": [
                    {
                        "component_id": "body",
                        "source_art_id": "source",
                        "selector": {
                            "type": "alpha_bbox",
                            "chroma_key_mode": "strict_hsv",
                            "pad": 1,
                            "min_area": 4,
                        },
                        "draw_rect": [0, 0, 16, 16],
                        "target_size": [16, 16],
                        "asset_suffix": "Body",
                        "runtime_asset_name": "T_Body",
                        "ue_asset_name": "T_Body",
                        "processing_mode": "approved_source_target_size",
                        "clear_outer_alpha_px": 1,
                        "quality_gates": {
                            "max_alpha_padding_px": 1,
                            "max_visible_chroma_shadow_pixels": 0,
                        },
                    }
                ],
            }
            validate_spec(spec, root)
            spec_path = root / "spec.json"
            spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
            packaged = package_spec(spec_path, project_root=root)
            manifest = json.loads((root / packaged["manifest"]).read_text(encoding="utf-8"))
            output = manifest["outputs"][0]
            self.assertEqual(output["selector"]["chroma_key_mode"], "strict_hsv")
            self.assertTrue(output["resize_contract"]["linear_light"])
            self.assertTrue(output["resize_contract"]["premultiplied"])
            self.assertFalse(output["resize_contract"]["strict_hsv_post_cleanup"])
            self.assertTrue(output["resize_contract"]["fit_visible_alpha_to_safe_area"])
            self.assertTrue(output["quality_gates"]["results"]["alpha_padding"]["pass"])
            self.assertTrue(output["quality_gates"]["results"]["visible_chroma_shadow"]["pass"])

            invalid_selector = json.loads(json.dumps(spec))
            invalid_selector["components"][0]["selector"]["chroma_key_mode"] = "aggressive"
            with self.assertRaises(SpecError):
                validate_spec(invalid_selector, root)

            invalid_resize = json.loads(json.dumps(spec))
            invalid_resize["approved_source_target_size"]["linear_light"] = "true"
            with self.assertRaises(SpecError):
                validate_spec(invalid_resize, root)

            invalid_safe_fit = json.loads(json.dumps(spec))
            invalid_safe_fit["approved_source_target_size"]["fit_visible_alpha_to_safe_area"] = "true"
            with self.assertRaises(SpecError):
                validate_spec(invalid_safe_fit, root)

            invalid_gate = json.loads(json.dumps(spec))
            invalid_gate["components"][0]["quality_gates"]["max_alpha_padding_px"] = -1
            with self.assertRaises(SpecError):
                validate_spec(invalid_gate, root)

    def test_safe_area_fit_preserves_alpha_instead_of_clearing_frame_edges(self) -> None:
        frame = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        ImageDraw.Draw(frame).ellipse((0, 0, 99, 99), outline=(116, 72, 38, 255), width=8)

        destructive = clean_existing_source_target_size(
            frame,
            (20, 20),
            clear_outer_pixels=1,
            post_speckle_min_area=0,
            linear_light=True,
            fit_visible_alpha_to_safe_area=False,
        )
        safe = clean_existing_source_target_size(
            frame,
            (20, 20),
            clear_outer_pixels=1,
            post_speckle_min_area=0,
            linear_light=True,
            fit_visible_alpha_to_safe_area=True,
        )
        destructive_alpha = np.asarray(destructive, dtype=np.uint8)[:, :, 3]
        safe_alpha = np.asarray(safe, dtype=np.uint8)[:, :, 3]
        self.assertEqual(alpha_bbox(safe, threshold=8), (1, 1, 19, 19))
        self.assertGreater(int(safe_alpha.sum()), int(destructive_alpha.sum()))
        self.assertGreater(int(safe_alpha[1, 10]), int(destructive_alpha[1, 10]))

    def test_alpha_padding_quality_gate_reports_edges_and_fails_closed(self) -> None:
        image = Image.new("RGBA", (10, 8), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((2, 1, 7, 6), fill=(120, 90, 60, 255))

        passed = _evaluate_quality_gates(
            image,
            {"quality_gates": {"max_alpha_padding_px": 2, "max_alpha_padding_percent": 20}},
            None,
        )
        self.assertTrue(passed["all_pass"])
        self.assertEqual(passed["results"]["alpha_padding"]["padding_ltrb_px"], [2, 1, 2, 1])

        failed = _evaluate_quality_gates(
            image,
            {"quality_gates": {"max_alpha_padding_px": 1}},
            None,
        )
        self.assertFalse(failed["all_pass"])
        self.assertEqual(failed["results"]["alpha_padding"]["max_padding_px"], 2)

        shadow = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
        shadow.putpixel((3, 3), (80, 10, 55, 255))
        shadow_gate = _evaluate_quality_gates(
            shadow,
            {"quality_gates": {"max_visible_chroma_shadow_pixels": 0}},
            None,
        )
        self.assertFalse(shadow_gate["all_pass"])
        self.assertEqual(shadow_gate["results"]["visible_chroma_shadow"]["pixel_count"], 1)

    def test_chroma_to_alpha_removes_border_connected_green_key(self) -> None:
        image = Image.new("RGBA", (32, 32), (42, 196, 78, 255))
        ImageDraw.Draw(image).rounded_rectangle((5, 4, 26, 28), radius=4, fill=(239, 220, 178, 255))
        cleaned = np.asarray(chroma_to_alpha(image), dtype=np.uint8)
        self.assertEqual(int(cleaned[0, 0, 3]), 0)
        self.assertEqual(int(cleaned[16, 16, 3]), 255)
        self.assertGreater(int(cleaned[:, :, 3].max()), 0)

    def test_canonical_shapes_shared_alpha_single_channel_mask_and_ue_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            prompt = root / "prompt.md"
            prompt.write_text("synthetic canonical shape source", encoding="utf-8")
            for name, shade in (("front", 156), ("back", 132), ("bowl", 146)):
                Image.new("RGBA", (80, 112), (shade, shade, shade, 255)).save(root / f"{name}.png")

            def source(source_id: str) -> dict[str, object]:
                return {
                    "id": source_id,
                    "path": f"{source_id}.png",
                    "prompt_files": ["prompt.md"],
                    "provenance": {"provider": "test", "model": "synthetic", "generation_id": source_id},
                }

            color_ue = {
                "compression": "UserInterface2D", "source_format": "auto", "srgb": True,
                "mip_gen": "NoMipmaps", "lod_group": "UI", "address_x": "Clamp",
                "address_y": "Clamp", "filter": "Bilinear", "never_stream": True,
            }
            mask_ue = {
                "compression": "Grayscale", "source_format": "TSF_G8", "srgb": False,
                "mip_gen": "NoMipmaps", "lod_group": "UI", "address_x": "Clamp",
                "address_y": "Clamp", "filter": "Bilinear", "never_stream": True,
            }
            body_gate = {
                "require_canonical_alpha_exact": True,
                "expected_alpha_bbox": [6, 5, 314, 427],
                "alpha_bbox_threshold": 127,
                "alpha_bbox_tolerance_px": 0,
                "lab_region_deltas": [
                    {"id": "right", "reference_region": [0.35, 0.35, 0.65, 0.65], "sample_region": [0.975, 0.2, 0.992, 0.8], "delta_l_max": -6, "delta_b_max": 0},
                    {"id": "bottom", "reference_region": [0.35, 0.35, 0.65, 0.65], "sample_region": [0.2, 0.975, 0.8, 0.992], "delta_l_max": -10, "delta_b_max": 0},
                ],
            }
            base_component = {
                "draw_rect": [0, 0, 320, 432], "target_size": [320, 432],
                "selector": {"type": "full_image"}, "asset_suffix": "Body",
                "ue_package_path": "/Game/UI/Test", "texture_type": "color",
                "processing_mode": "canonical_shape_color", "canonical_shape_id": "body",
                "canonical_shape_color": {"directional_sidewall_grade": {
                    "right_width_px": 11, "bottom_height_px": 13,
                    "right_delta_l": -8, "bottom_delta_l": -12,
                    "delta_b_offset": -0.75, "inner_feather_fraction": 0.35,
                }},
                "quality_gates": body_gate, "ue_texture": color_ue,
            }
            components = []
            for component_id in ("front", "back"):
                components.append({
                    **base_component,
                    "component_id": component_id,
                    "source_art_id": component_id,
                    "runtime_asset_name": f"T_{component_id}",
                    "ue_asset_name": f"T_{component_id}",
                })
            components.append({
                "component_id": "bowl", "source_art_id": "bowl", "selector": {"type": "full_image"},
                "draw_rect": [0, 0, 260, 260], "target_size": [260, 260], "asset_suffix": "Bowl",
                "runtime_asset_name": "T_Bowl", "ue_package_path": "/Game/UI/Test", "ue_asset_name": "T_Bowl",
                "texture_type": "color", "processing_mode": "canonical_shape_color", "canonical_shape_id": "bowl",
                "canonical_shape_color": {"rgb_repair_inner_band_px": 3, "tangential_blur_px": 1},
                "quality_gates": {
                    "require_canonical_alpha_exact": True, "expected_alpha_bbox": [8, 8, 252, 252],
                    "alpha_bbox_threshold": 127, "max_radial_deviation_px": 0.5,
                    "max_outer_ring_delta_e_p99": 12, "outer_ring_width_px": 3,
                },
                "ue_texture": color_ue,
            })
            components.append({
                "component_id": "shadow", "draw_rect": [0, 0, 320, 432], "target_size": [320, 432],
                "asset_suffix": "Shadow", "runtime_asset_name": "T_Shadow", "ue_package_path": "/Game/UI/Test",
                "ue_asset_name": "T_Shadow", "texture_type": "mask", "processing_mode": "canonical_shape_shadow_mask",
                "canonical_shape_id": "body", "canonical_shape_shadow_mask": {"blur_radius_px": 4.5, "clear_outer_px": 1},
                "quality_gates": {"require_single_channel": True, "require_outer_border_zero": True},
                "ue_texture": mask_ue,
            })
            spec = {
                "$schema": "ai-asset-pipeline-spec-v1", "run_id": "canonical-test",
                "runtime_output_dir": "runtime", "review_output_dir": "review",
                "source_art": [source("front"), source("back"), source("bowl")],
                "canonical_shapes": [
                    {"id": "body", "type": "rounded_rectangle", "canvas_size": [320, 432], "bbox": [6, 5, 314, 427], "radius_px": 25, "supersample": 4},
                    {"id": "bowl", "type": "circle", "canvas_size": [260, 260], "bbox": [8, 8, 252, 252], "supersample": 4},
                ],
                "shared_alpha_contracts": [{"id": "body-alpha", "component_ids": ["front", "back"], "canonical_shape_id": "body", "require_identical_bytes": True}],
                "components": components,
            }
            spec_path = root / "spec.json"
            spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
            packaged = package_spec(spec_path, project_root=root)
            manifest = json.loads((root / packaged["manifest"]).read_text(encoding="utf-8"))
            self.assertTrue(manifest["shared_alpha_contracts"][0]["alpha_bytes_identical"])
            self.assertTrue(manifest["canonical_shape_contract"]["all_canonical_alpha_exact"])
            self.assertTrue(manifest["quality_contract"]["all_quality_gates_pass"])
            self.assertTrue(manifest["quality_contract"]["components"]["front"]["results"]["right"]["pass"])
            self.assertTrue(manifest["quality_contract"]["components"]["front"]["results"]["bottom"]["pass"])
            self.assertTrue(manifest["ue_texture_contract"]["all_ue_texture_settings_valid"])
            shadow = next(item for item in manifest["outputs"] if item["component_id"] == "shadow")
            with Image.open(root / shadow["runtime_file"]) as image:
                self.assertEqual(image.mode, "L")
                self.assertEqual(np.asarray(image)[0, :].max(), 0)
            plan = plan_import(root / packaged["manifest"], project_root=root)
            shadow_plan = next(item for item in plan["imports"] if item["component_id"] == "shadow")
            self.assertEqual(shadow_plan["params"]["compression"], "Grayscale")
            self.assertEqual(shadow_plan["params"]["source_format"], "TSF_G8")
            self.assertFalse(shadow_plan["params"]["srgb"])

    @unittest.skipUnless(V10_SPEC.is_file(), "optional legacy V10 spec fixture is not installed")
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

    def test_reference_color_pill_resize_preserves_rgb_with_clean_alpha_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "source_color_pill.png"

            image = Image.new("RGBA", (128, 48), (255, 0, 216, 255))
            for y in range(10, 38):
                for x in range(16, 112):
                    shade = int(120 + (x / 127) * 90)
                    image.putpixel((x, y), (80, shade, 24, 255))
            image.save(source)

            prompt = root / "prompt.md"
            prompt.write_text("synthetic color pill fixture", encoding="utf-8")
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "$schema": "ai-asset-pipeline-spec-v1",
                        "run_id": "reference_color_pill_resize",
                        "validation_policy": "fail_closed",
                        "runtime_output_dir": "runtime",
                        "review_output_dir": "review",
                        "source_art": [
                            {
                                "id": "pill",
                                "path": "source_color_pill.png",
                                "prompt_files": ["prompt.md"],
                                "provenance": {
                                    "provider": "fixture",
                                    "model": "unit-test",
                                    "generation_id": "reference-color-pill-1",
                                },
                            }
                        ],
                        "components": [
                            {
                                "component_id": "green_base",
                                "source_art_id": "pill",
                                "selector": {"type": "full_image"},
                                "draw_rect": [0, 0, 64, 24],
                                "target_size": [64, 24],
                                "asset_suffix": "GreenBase",
                                "runtime_asset_name": "T_GreenBase",
                                "ue_package_path": "/Game/UI/_AIProbe/ReferenceColorPill",
                                "ue_asset_name": "T_GreenBase",
                                "texture_type": "color",
                                "processing_mode": "reference_color_pill_resize",
                                "reference_color_pill_resize": {
                                    "outer_box": [2, 3, 62, 21],
                                    "radius": 9,
                                    "alpha_cutoff": 0.55,
                                    "min_visible_alpha": 0.72,
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
            self.assertEqual(output["processing_mode"], "reference_color_pill_resize")
            self.assertEqual(output["recommended_strategy"], "reference_color_pill_resize_rgb_preserving")
            self.assertTrue(output["is_final_runtime_safe"])
            self.assertEqual(output["diagnostics"]["low_alpha_saturated_rgb_artifact_pixels"], 0)
            runtime = Image.open(root / output["runtime_file"]).convert("RGBA")
            arr = np.asarray(runtime)
            visible = arr[:, :, 3] > 8
            self.assertGreater(int(visible.sum()), 500)
            self.assertFalse(np.all(arr[visible, 0] == arr[visible, 1]))

    def test_edge_particle_extract_resize_preserves_small_particle_components(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "source_particles.png"

            image = Image.new("RGBA", (160, 64), (255, 0, 216, 255))
            for y in range(20, 44):
                for x in range(20, 118):
                    image.putpixel((x, y), (230, 42, 30, 255))
            for cx, cy, radius in ((124, 24, 2), (132, 30, 2), (142, 35, 3), (128, 42, 2)):
                for y in range(cy - radius, cy + radius + 1):
                    for x in range(cx - radius, cx + radius + 1):
                        if (x - cx) * (x - cx) + (y - cy) * (y - cy) <= radius * radius:
                            image.putpixel((x, y), (255, 96, 54, 210))
            image.save(source)

            prompt = root / "prompt.md"
            prompt.write_text("synthetic edge particle fixture", encoding="utf-8")
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "$schema": "ai-asset-pipeline-spec-v1",
                        "run_id": "edge_particle_extract_resize",
                        "validation_policy": "fail_closed",
                        "runtime_output_dir": "runtime",
                        "review_output_dir": "review",
                        "source_art": [
                            {
                                "id": "particles",
                                "path": "source_particles.png",
                                "prompt_files": ["prompt.md"],
                                "provenance": {
                                    "provider": "fixture",
                                    "model": "unit-test",
                                    "generation_id": "edge-particles-1",
                                },
                            }
                        ],
                        "components": [
                            {
                                "component_id": "red_spark_edge",
                                "source_art_id": "particles",
                                "selector": {"type": "full_image"},
                                "draw_rect": [0, 0, 64, 41],
                                "target_size": [64, 41],
                                "asset_suffix": "RedSparkEdge",
                                "runtime_asset_name": "T_RedSparkEdge",
                                "ue_package_path": "/Game/UI/_AIProbe/EdgeParticles",
                                "ue_asset_name": "T_RedSparkEdge",
                                "texture_type": "glow",
                                "processing_mode": "edge_particle_extract_resize",
                                "edge_particle_extract_resize": {
                                    "source_region_width_px": 54,
                                    "x_fade_start": 0.08,
                                    "x_fade_end": 0.35,
                                    "min_alpha": 0.02,
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
            self.assertEqual(output["processing_mode"], "edge_particle_extract_resize")
            self.assertEqual(output["recommended_strategy"], "edge_particle_extract_resize_particle_preserving")
            self.assertTrue(output["is_final_runtime_safe"])
            self.assertGreater(output["diagnostics"]["alpha_component_count"], 1)
            self.assertGreater(output["diagnostics"]["small_alpha_component_count"], 0)

    def test_nameplate_timer_aaa_fill_resize_preserves_ratio_without_synthetic_alpha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "source_aaa_fill.png"

            image = Image.new("RGBA", (420, 120), (255, 0, 216, 255))
            for y in range(14, 106):
                for x in range(30, 390):
                    shade = 110 + int(((x - 30) / 360) * 110)
                    image.putpixel((x, y), (70, min(255, shade + 55), 28, 255))
            image.save(source)

            prompt = root / "prompt.md"
            prompt.write_text("synthetic AAA timer fill fixture", encoding="utf-8")
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "$schema": "ai-asset-pipeline-spec-v1",
                        "run_id": "nameplate_timer_aaa_fill",
                        "validation_policy": "fail_closed",
                        "runtime_output_dir": "runtime",
                        "review_output_dir": "review",
                        "source_art": [
                            {
                                "id": "fill",
                                "path": "source_aaa_fill.png",
                                "prompt_files": ["prompt.md"],
                                "provenance": {
                                    "provider": "fixture",
                                    "model": "unit-test",
                                    "generation_id": "aaa-fill-1",
                                },
                            }
                        ],
                        "components": [
                            {
                                "component_id": "green_body",
                                "source_art_id": "fill",
                                "selector": {"type": "full_image"},
                                "draw_rect": [0, 0, 352, 90],
                                "target_size": [176, 45],
                                "asset_suffix": "GreenBody",
                                "runtime_asset_name": "T_GreenBody",
                                "ue_package_path": "/Game/UI/_AIProbe/NameplateTimerAAA",
                                "ue_asset_name": "T_GreenBody",
                                "texture_type": "color",
                                "processing_mode": "nameplate_timer_aaa_fill_resize",
                                "nameplate_timer_aaa_fill_resize": {
                                    "alpha_bbox_pad_px": 0,
                                    "aspect_ratio_tolerance_pct": 3.0,
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
            self.assertEqual(output["processing_mode"], "nameplate_timer_aaa_fill_resize")
            self.assertEqual(output["recommended_strategy"], "nameplate_timer_aaa_fill_no_stretch_canvas")
            self.assertTrue(output["is_final_runtime_safe"])
            self.assertLessEqual(output["ratio_delta_pct"], 3.0)
            self.assertTrue(manifest["alpha_contract"]["all_nameplate_timer_aaa_aspect_ratio_within_tolerance"])
            self.assertEqual(output["diagnostics"]["low_alpha_saturated_rgb_artifact_pixels"], 0)
            runtime = Image.open(root / output["runtime_file"]).convert("RGBA")
            self.assertEqual(runtime.size, (176, 45))

    def test_nameplate_timer_aaa_fill_resize_fails_ratio_distortion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "source_square_fill.png"
            image = Image.new("RGBA", (128, 128), (255, 0, 216, 255))
            for y in range(24, 104):
                for x in range(24, 104):
                    image.putpixel((x, y), (80, 220, 32, 255))
            image.save(source)
            prompt = root / "prompt.md"
            prompt.write_text("synthetic square fill fixture", encoding="utf-8")
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "$schema": "ai-asset-pipeline-spec-v1",
                        "run_id": "nameplate_timer_aaa_fill_bad_ratio",
                        "validation_policy": "fail_closed",
                        "runtime_output_dir": "runtime",
                        "review_output_dir": "review",
                        "source_art": [
                            {
                                "id": "fill",
                                "path": "source_square_fill.png",
                                "prompt_files": ["prompt.md"],
                                "provenance": {
                                    "provider": "fixture",
                                    "model": "unit-test",
                                    "generation_id": "aaa-fill-bad-ratio-1",
                                },
                            }
                        ],
                        "components": [
                            {
                                "component_id": "green_body",
                                "source_art_id": "fill",
                                "selector": {"type": "full_image"},
                                "draw_rect": [0, 0, 352, 90],
                                "target_size": [176, 45],
                                "asset_suffix": "GreenBody",
                                "runtime_asset_name": "T_GreenBody",
                                "ue_package_path": "/Game/UI/_AIProbe/NameplateTimerAAA",
                                "ue_asset_name": "T_GreenBody",
                                "texture_type": "color",
                                "processing_mode": "nameplate_timer_aaa_fill_resize",
                                "nameplate_timer_aaa_fill_resize": {
                                    "alpha_bbox_pad_px": 0,
                                    "aspect_ratio_tolerance_pct": 3.0,
                                },
                            }
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                package_spec(spec_path, project_root=root)

    def test_nameplate_timer_aaa_edge_requires_boundary_face_not_particles_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "source_particle_only_edge.png"
            image = Image.new("RGBA", (220, 140), (255, 0, 216, 255))
            for cx, cy, radius in ((20, 20, 2), (180, 20, 2), (20, 110, 2), (180, 110, 2), (96, 66, 3)):
                for y in range(cy - radius, cy + radius + 1):
                    for x in range(cx - radius, cx + radius + 1):
                        if (x - cx) * (x - cx) + (y - cy) * (y - cy) <= radius * radius:
                            image.putpixel((x, y), (255, 92, 48, 230))
            image.save(source)

            prompt = root / "prompt.md"
            prompt.write_text("synthetic particle-only edge fixture", encoding="utf-8")
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "$schema": "ai-asset-pipeline-spec-v1",
                        "run_id": "nameplate_timer_aaa_edge_particle_only",
                        "validation_policy": "fail_closed",
                        "runtime_output_dir": "runtime",
                        "review_output_dir": "review",
                        "source_art": [
                            {
                                "id": "edge",
                                "path": "source_particle_only_edge.png",
                                "prompt_files": ["prompt.md"],
                                "provenance": {
                                    "provider": "fixture",
                                    "model": "unit-test",
                                    "generation_id": "aaa-edge-particle-only-1",
                                },
                            }
                        ],
                        "components": [
                            {
                                "component_id": "red_edge",
                                "source_art_id": "edge",
                                "selector": {"type": "full_image"},
                                "draw_rect": [0, 0, 320, 180],
                                "target_size": [160, 90],
                                "asset_suffix": "RedEdge",
                                "runtime_asset_name": "T_RedEdge",
                                "ue_package_path": "/Game/UI/_AIProbe/NameplateTimerAAA",
                                "ue_asset_name": "T_RedEdge",
                                "texture_type": "glow",
                                "processing_mode": "nameplate_timer_aaa_edge_resize",
                                "nameplate_timer_aaa_edge_resize": {
                                    "alpha_bbox_pad_px": 0,
                                    "aspect_ratio_tolerance_pct": 3.0,
                                },
                                "timer_boundary_diagnostics": {
                                    "expected_boundary_x_pct": 0.5,
                                    "boundary_tolerance_px": 5,
                                    "dark_right_band_width_px": 10,
                                    "require_boundary_face": True,
                                    "boundary_face_min_left_pixels": 80,
                                    "boundary_face_min_window_pixels": 120,
                                },
                            }
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            with self.assertRaises(SpecError):
                package_spec(spec_path, project_root=root)

    def test_nameplate_timer_aaa_edge_preserves_fracture_face_and_sparks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "source_aaa_edge.png"
            image = Image.new("RGBA", (220, 140), (255, 0, 216, 255))
            for y in range(20, 110):
                for x in range(20, 96):
                    image.putpixel((x, y), (220, 44, 24, 255))
            for cx, cy, radius in ((116, 38, 4), (132, 66, 5), (162, 84, 3), (180, 64, 2)):
                for y in range(cy - radius, cy + radius + 1):
                    for x in range(cx - radius, cx + radius + 1):
                        if (x - cx) * (x - cx) + (y - cy) * (y - cy) <= radius * radius:
                            image.putpixel((x, y), (255, 92, 48, 230))
            image.save(source)

            prompt = root / "prompt.md"
            prompt.write_text("synthetic AAA edge fixture", encoding="utf-8")
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "$schema": "ai-asset-pipeline-spec-v1",
                        "run_id": "nameplate_timer_aaa_edge",
                        "validation_policy": "fail_closed",
                        "runtime_output_dir": "runtime",
                        "review_output_dir": "review",
                        "source_art": [
                            {
                                "id": "edge",
                                "path": "source_aaa_edge.png",
                                "prompt_files": ["prompt.md"],
                                "provenance": {
                                    "provider": "fixture",
                                    "model": "unit-test",
                                    "generation_id": "aaa-edge-1",
                                },
                            }
                        ],
                        "components": [
                            {
                                "component_id": "red_edge",
                                "source_art_id": "edge",
                                "selector": {"type": "full_image"},
                                "draw_rect": [0, 0, 320, 180],
                                "target_size": [160, 90],
                                "asset_suffix": "RedEdge",
                                "runtime_asset_name": "T_RedEdge",
                                "ue_package_path": "/Game/UI/_AIProbe/NameplateTimerAAA",
                                "ue_asset_name": "T_RedEdge",
                                "texture_type": "glow",
                                "processing_mode": "nameplate_timer_aaa_edge_resize",
                                "nameplate_timer_aaa_edge_resize": {
                                    "alpha_bbox_pad_px": 0,
                                    "aspect_ratio_tolerance_pct": 3.0,
                                },
                                "timer_boundary_diagnostics": {
                                    "expected_boundary_x_pct": 0.5,
                                    "boundary_tolerance_px": 24,
                                    "dark_right_band_width_px": 10,
                                    "require_boundary_face": True,
                                    "boundary_face_min_left_pixels": 80,
                                    "boundary_face_min_window_pixels": 120,
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
            self.assertEqual(output["processing_mode"], "nameplate_timer_aaa_edge_resize")
            self.assertEqual(output["recommended_strategy"], "nameplate_timer_aaa_edge_fracture_face_no_stretch_canvas")
            self.assertTrue(output["is_final_runtime_safe"])
            self.assertTrue(manifest["alpha_contract"]["all_timer_edge_has_boundary_face"])
            self.assertTrue(manifest["alpha_contract"]["all_nameplate_timer_aaa_aspect_ratio_within_tolerance"])
            self.assertGreater(output["diagnostics"]["timer_edge_left_face_pixels"], 80)

    def test_nameplate_timer_aaa_edge_flipbook_atlas_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "source_aaa_edge_flipbook.png"
            source_frame_w, source_frame_h = 192, 192
            sheet = Image.new("RGBA", (source_frame_w * 4, source_frame_h * 3), (0, 0, 0, 255))
            for frame_index in range(12):
                ox = (frame_index % 4) * source_frame_w
                oy = (frame_index // 4) * source_frame_h
                phase = frame_index % 3
                for y in range(40, 152):
                    for x in range(42 + phase, 96 + phase):
                        sheet.putpixel((ox + x, oy + y), (210, 0, 0, 255))
                for y in range(44, 148):
                    for x in range(90, 132):
                        if (x + y + frame_index) % 4:
                            sheet.putpixel((ox + x, oy + y), (0, 0, 220, 255))
                for cx, cy, radius in ((134, 62, 5), (152, 96, 4), (170, 124, 3)):
                    cx += phase
                    for y in range(cy - radius, cy + radius + 1):
                        for x in range(cx - radius, cx + radius + 1):
                            if (x - cx) * (x - cx) + (y - cy) * (y - cy) <= radius * radius:
                                sheet.putpixel((ox + x, oy + y), (0, 210, 0, 230))
            sheet.save(source)

            prompt = root / "prompt.md"
            prompt.write_text("synthetic 12-frame packed edge flipbook fixture", encoding="utf-8")
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "$schema": "ai-asset-pipeline-spec-v1",
                        "run_id": "nameplate_timer_aaa_edge_flipbook",
                        "validation_policy": "fail_closed",
                        "runtime_output_dir": "runtime",
                        "review_output_dir": "review",
                        "source_art": [
                            {
                                "id": "edge_sheet",
                                "path": "source_aaa_edge_flipbook.png",
                                "prompt_files": ["prompt.md"],
                                "provenance": {
                                    "provider": "fixture",
                                    "model": "unit-test",
                                    "generation_id": "aaa-edge-flipbook-1",
                                },
                            }
                        ],
                        "components": [
                            {
                                "component_id": "edge_flipbook_rgba",
                                "source_art_id": "edge_sheet",
                                "selector": {"type": "full_image"},
                                "draw_rect": [0, 0, 1024, 384],
                                "target_size": [1024, 384],
                                "asset_suffix": "EdgeFlipbookRGBA",
                                "runtime_asset_name": "T_EdgeFlipbookRGBA",
                                "ue_package_path": "/Game/UI/_AIProbe/NameplateTimerAAA/V03",
                                "ue_asset_name": "T_EdgeFlipbookRGBA",
                                "texture_type": "packed_mask",
                                "processing_mode": "nameplate_timer_aaa_edge_flipbook_atlas",
                                "nameplate_timer_aaa_edge_flipbook_atlas": {
                                    "frame_size": [256, 128],
                                    "columns": 4,
                                    "rows": 3,
                                    "frame_count": 12,
                                    "alpha_bbox_pad_px": 2,
                                    "aspect_ratio_tolerance_pct": 20,
                                    "channel_min_pixels": 32,
                                    "alpha_min_pixels": 256,
                                },
                                "timer_boundary_diagnostics": {
                                    "expected_boundary_x_pct": 0.5,
                                    "boundary_tolerance_px": 80,
                                    "dark_right_band_width_px": 14,
                                    "require_boundary_face": True,
                                    "boundary_face_min_left_pixels": 64,
                                    "boundary_face_min_window_pixels": 96,
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
            self.assertEqual(output["processing_mode"], "nameplate_timer_aaa_edge_flipbook_atlas")
            self.assertEqual(output["texture_type"], "packed_mask")
            self.assertEqual(output["recommended_strategy"], "nameplate_timer_aaa_edge_flipbook_rgba_atlas")
            self.assertTrue(output["is_final_runtime_safe"])
            self.assertTrue(manifest["alpha_contract"]["all_nameplate_timer_aaa_edge_flipbook_contract"])
            self.assertTrue(manifest["alpha_contract"]["all_timer_edge_has_boundary_face"])
            self.assertEqual(output["diagnostics"]["timer_edge_flipbook_frame_count"], 12)
            self.assertTrue(output["diagnostics"]["timer_edge_flipbook_all_channels_present"])

    def test_nameplate_timer_image1_timer_only_extracts_body_and_edge_atlas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "source_image1_timer.png"
            image = Image.new("RGBA", (512, 768), (16, 20, 28, 255))
            draw = ImageDraw.Draw(image)
            rng = np.random.default_rng(20260710)
            rows = [
                (90, 1.00, (72, 235, 48, 255)),
                (220, 0.64, (72, 235, 48, 255)),
                (350, 0.50, (255, 166, 18, 255)),
                (480, 0.28, (230, 44, 24, 255)),
                (610, 0.09, (230, 44, 24, 255)),
            ]
            for y, percent, color in rows:
                draw.rounded_rectangle((118, y, 454, y + 56), radius=28, fill=(20, 46, 88, 255))
                active_right = int(118 + 336 * percent)
                draw.rounded_rectangle((118, y, active_right, y + 56), radius=28, fill=color)
                draw.rectangle((118 + 30, y + 8, max(118 + 32, active_right - 4), y + 18), fill=(180, 255, 128, 210))
                for _ in range(45):
                    cx = int(rng.normal(active_right, 13))
                    cy = int(rng.uniform(y + 6, y + 50))
                    if 0 <= cx < image.width:
                        radius = int(rng.integers(1, 4))
                        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=color)

            image.save(source)
            prompt = root / "prompt.md"
            prompt.write_text("synthetic Image #1 style timer-only extraction fixture", encoding="utf-8")
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "$schema": "ai-asset-pipeline-spec-v1",
                        "run_id": "nameplate_timer_image1_timer_only",
                        "validation_policy": "fail_closed",
                        "runtime_output_dir": "runtime",
                        "review_output_dir": "review",
                        "source_art": [
                            {
                                "id": "image1",
                                "path": "source_image1_timer.png",
                                "prompt_files": ["prompt.md"],
                                "provenance": {
                                    "provider": "fixture",
                                    "model": "unit-test",
                                    "generation_id": "image1-fixture",
                                },
                            }
                        ],
                        "components": [
                            {
                                "component_id": "image1_body_luma",
                                "source_art_id": "image1",
                                "selector": {"type": "full_image"},
                                "draw_rect": [0, 0, 352, 90],
                                "target_size": [352, 90],
                                "asset_suffix": "Image1BodyLuma",
                                "runtime_asset_name": "T_Image1BodyLuma",
                                "ue_package_path": "/Game/UI/_AIProbe/NameplateTimerImage1",
                                "ue_asset_name": "T_Image1BodyLuma",
                                "texture_type": "mask",
                                "processing_mode": "nameplate_timer_image1_body_luma",
                                "nameplate_timer_image1_body_luma": {
                                    "component_min_area": 600,
                                },
                            },
                            {
                                "component_id": "image1_edge_flipbook",
                                "source_art_id": "image1",
                                "selector": {"type": "full_image"},
                                "draw_rect": [0, 0, 256, 96],
                                "target_size": [256, 96],
                                "asset_suffix": "Image1EdgeFlipbook",
                                "runtime_asset_name": "T_Image1EdgeFlipbook",
                                "ue_package_path": "/Game/UI/_AIProbe/NameplateTimerImage1",
                                "ue_asset_name": "T_Image1EdgeFlipbook",
                                "texture_type": "packed_mask",
                                "processing_mode": "nameplate_timer_image1_edge_flipbook_atlas",
                                "nameplate_timer_image1_edge_flipbook_atlas": {
                                    "frame_size": [64, 32],
                                    "columns": 4,
                                    "rows": 3,
                                    "frame_count": 12,
                                    "component_min_area": 300,
                                    "boundary_left_px": 50,
                                    "boundary_right_px": 58,
                                    "channel_min_pixels": 4,
                                    "alpha_min_pixels": 18,
                                },
                                "timer_boundary_diagnostics": {
                                    "expected_boundary_x_pct": 0.5,
                                    "boundary_tolerance_px": 24,
                                    "dark_right_band_width_px": 5,
                                    "require_boundary_face": True,
                                    "boundary_face_min_left_pixels": 4,
                                    "boundary_face_min_window_pixels": 8,
                                },
                            },
                        ],
                        "alpha_contract": {
                            "expected_component_count": 2,
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            packaged = package_spec(spec_path, project_root=root)
            self.assertTrue(packaged["ok"])
            manifest = json.loads((root / packaged["manifest"]).read_text(encoding="utf-8"))
            body, edge = manifest["outputs"]
            self.assertEqual(body["processing_mode"], "nameplate_timer_image1_body_luma")
            self.assertEqual(edge["processing_mode"], "nameplate_timer_image1_edge_flipbook_atlas")
            self.assertEqual(body["diagnostics"]["visible_chroma_key_pixels_alpha_gt_8"], 0)
            self.assertTrue(edge["diagnostics"]["timer_edge_flipbook_contract_ok"])
            self.assertTrue(manifest["alpha_contract"]["all_nameplate_timer_aaa_edge_flipbook_contract"])
            self.assertTrue(manifest["alpha_contract"]["all_timer_edge_has_boundary_face"])

    def test_timer_boundary_diagnostics_fail_dark_right_edge_band(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "source_timer_band.png"
            image = Image.new("RGBA", (48, 16), (0, 0, 0, 0))
            for y in range(6, 10):
                for x in range(22, 26):
                    image.putpixel((x, y), (255, 96, 54, 220))
            for y in range(5, 11):
                for x in range(41, 46):
                    image.putpixel((x, y), (12, 10, 8, 180))
            image.save(source)

            prompt = root / "prompt.md"
            prompt.write_text("synthetic timer dark band fixture", encoding="utf-8")
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "$schema": "ai-asset-pipeline-spec-v1",
                        "run_id": "timer_dark_band",
                        "validation_policy": "fail_closed",
                        "runtime_output_dir": "runtime",
                        "review_output_dir": "review",
                        "source_art": [
                            {
                                "id": "timer",
                                "path": "source_timer_band.png",
                                "prompt_files": ["prompt.md"],
                                "provenance": {
                                    "provider": "fixture",
                                    "model": "unit-test",
                                    "generation_id": "timer-dark-band-1",
                                },
                            }
                        ],
                        "components": [
                            {
                                "component_id": "timer_edge",
                                "source_art_id": "timer",
                                "selector": {"type": "full_image"},
                                "draw_rect": [0, 0, 48, 16],
                                "target_size": [48, 16],
                                "asset_suffix": "TimerEdge",
                                "runtime_asset_name": "T_TimerEdge",
                                "ue_package_path": "/Game/UI/_AIProbe/TimerBoundary",
                                "ue_asset_name": "T_TimerEdge",
                                "texture_type": "glow",
                                "processing_mode": "passthrough",
                                "timer_boundary_diagnostics": {
                                    "expected_boundary_x_pct": 0.5,
                                    "boundary_tolerance_px": 3,
                                    "dark_right_band_width_px": 8,
                                },
                            }
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            with self.assertRaises(SpecError):
                package_spec(spec_path, project_root=root)

    def test_timer_boundary_diagnostics_record_aligned_edge_particles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "source_timer_edge.png"
            image = Image.new("RGBA", (48, 16), (0, 0, 0, 0))
            for y in range(6, 10):
                for x in range(22, 26):
                    image.putpixel((x, y), (255, 124, 42, 230))
            image.save(source)

            prompt = root / "prompt.md"
            prompt.write_text("synthetic timer aligned edge fixture", encoding="utf-8")
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "$schema": "ai-asset-pipeline-spec-v1",
                        "run_id": "timer_boundary_aligned",
                        "validation_policy": "fail_closed",
                        "runtime_output_dir": "runtime",
                        "review_output_dir": "review",
                        "source_art": [
                            {
                                "id": "timer",
                                "path": "source_timer_edge.png",
                                "prompt_files": ["prompt.md"],
                                "provenance": {
                                    "provider": "fixture",
                                    "model": "unit-test",
                                    "generation_id": "timer-aligned-1",
                                },
                            }
                        ],
                        "components": [
                            {
                                "component_id": "timer_edge",
                                "source_art_id": "timer",
                                "selector": {"type": "full_image"},
                                "draw_rect": [0, 0, 48, 16],
                                "target_size": [48, 16],
                                "asset_suffix": "TimerEdge",
                                "runtime_asset_name": "T_TimerEdge",
                                "ue_package_path": "/Game/UI/_AIProbe/TimerBoundary",
                                "ue_asset_name": "T_TimerEdge",
                                "texture_type": "glow",
                                "processing_mode": "passthrough",
                                "timer_boundary_diagnostics": {
                                    "expected_boundary_x_pct": 0.5,
                                    "boundary_tolerance_px": 3,
                                    "dark_right_band_width_px": 8,
                                },
                            }
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            packaged = package_spec(spec_path, project_root=root)
            manifest = json.loads((root / packaged["manifest"]).read_text(encoding="utf-8"))
            output = manifest["outputs"][0]
            self.assertTrue(manifest["alpha_contract"]["all_no_timer_dark_right_edge_band"])
            self.assertTrue(manifest["alpha_contract"]["all_timer_edge_boundary_within_tolerance"])
            self.assertEqual(output["diagnostics"]["timer_dark_right_edge_band_pixels"], 0)
            self.assertTrue(output["diagnostics"]["timer_edge_boundary_within_tolerance"])

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

    @unittest.skipUnless(V10_MANIFEST.is_file(), "optional legacy V10 manifest fixture is not installed")
    def test_plan_import_reads_v10_manifest(self) -> None:
        result = plan_import(V10_MANIFEST, project_root=PROJECT_ROOT)
        self.assertTrue(result["ok"])
        self.assertEqual(result["component_count"], 15)
        self.assertEqual(result["imports"][0]["params"]["compression"], "UserInterface2D")

    @unittest.skipUnless(V10_TSPEC.is_file(), "optional legacy TSpec fixture is not installed")
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

    def test_raw_copy_exact_packed_mask_preserves_semantic_channels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "packed.png"
            pixels = np.zeros((8, 8, 4), dtype=np.uint8)
            pixels[2:6, 2:6, 3] = 80
            pixels[3, 3] = [180, 0, 220, 80]
            pixels[4, 4] = [40, 190, 90, 80]
            Image.fromarray(pixels, "RGBA").save(source)
            (root / "prompt.md").write_text("packed semantic fixture", encoding="utf-8")
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "$schema": "ai-asset-pipeline-spec-v1",
                        "run_id": "raw_packed_copy",
                        "validation_policy": "fail_closed",
                        "runtime_output_dir": "runtime",
                        "review_output_dir": "review",
                        "source_art": [
                            {
                                "id": "packed",
                                "path": "packed.png",
                                "prompt_files": ["prompt.md"],
                                "provenance": {
                                    "provider": "fixture",
                                    "model": "unit-test",
                                    "generation_id": "raw-packed-1",
                                },
                            }
                        ],
                        "components": [
                            {
                                "component_id": "packed",
                                "source_art_id": "packed",
                                "selector": {"type": "full_image_raw"},
                                "draw_rect": [0, 0, 8, 8],
                                "target_size": [8, 8],
                                "asset_suffix": "Packed",
                                "runtime_asset_name": "T_Packed",
                                "ue_asset_name": "T_Packed",
                                "texture_type": "packed_mask",
                                "processing_mode": "copy_exact",
                            }
                        ],
                        "alpha_contract": {"expected_component_count": 1},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            packaged = package_spec(spec_path, project_root=root)
            self.assertTrue(packaged["ok"])
            manifest = json.loads((root / packaged["manifest"]).read_text(encoding="utf-8"))
            output = manifest["outputs"][0]
            runtime = np.asarray(Image.open(root / output["runtime_file"]).convert("RGBA"))
            np.testing.assert_array_equal(runtime, pixels)
            self.assertTrue(output["diagnostics"]["semantic_packed_mask_rgb_diagnostics_bypassed"])
            self.assertTrue(manifest["alpha_contract"]["all_no_low_alpha_saturated_chroma_fringe"])

    def test_copy_exact_opaque_full_frame_color_requires_explicit_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Test.uproject").write_text("{}", encoding="utf-8")
            source = root / "background.png"
            pixels = np.zeros((8, 12, 3), dtype=np.uint8)
            pixels[:, :, 0] = 17
            pixels[:, :, 1] = np.arange(12, dtype=np.uint8)[None, :] + 72
            pixels[:, :, 2] = 41
            Image.fromarray(pixels, "RGB").save(source)
            (root / "prompt.md").write_text("opaque full-frame fixture", encoding="utf-8")

            def write_spec(*, allow_opaque: bool) -> Path:
                policy = (
                    {
                        "allow_opaque_full_frame": True,
                        "reason": "Synthetic approved edge-to-edge background.",
                    }
                    if allow_opaque
                    else {}
                )
                path = root / ("spec_allowed.json" if allow_opaque else "spec_denied.json")
                path.write_text(
                    json.dumps(
                        {
                            "$schema": "ai-asset-pipeline-spec-v1",
                            "run_id": "opaque_allowed" if allow_opaque else "opaque_denied",
                            "validation_policy": "fail_closed",
                            "runtime_output_dir": "runtime_allowed" if allow_opaque else "runtime_denied",
                            "review_output_dir": "review_allowed" if allow_opaque else "review_denied",
                            "source_art": [
                                {
                                    "id": "background",
                                    "path": "background.png",
                                    "prompt_files": ["prompt.md"],
                                    "provenance": {
                                        "provider": "fixture",
                                        "model": "unit-test",
                                        "generation_id": "opaque-full-frame-1",
                                    },
                                }
                            ],
                            "components": [
                                {
                                    "component_id": "background",
                                    "source_art_id": "background",
                                    "selector": {"type": "full_image_raw"},
                                    "draw_rect": [0, 0, 12, 8],
                                    "target_size": [12, 8],
                                    "asset_suffix": "Background",
                                    "runtime_asset_name": "T_Background",
                                    "ue_asset_name": "T_Background",
                                    "texture_type": "color",
                                    "processing_mode": "copy_exact",
                                    "alpha_contract_policy": policy,
                                }
                            ],
                            "alpha_contract": {"expected_component_count": 1},
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                return path

            with self.assertRaises(SpecError):
                package_spec(write_spec(allow_opaque=False), project_root=root)

            packaged = package_spec(write_spec(allow_opaque=True), project_root=root)
            self.assertTrue(packaged["ok"])
            manifest = json.loads((root / packaged["manifest"]).read_text(encoding="utf-8"))
            output = manifest["outputs"][0]
            runtime = np.asarray(Image.open(root / output["runtime_file"]).convert("RGBA"))
            np.testing.assert_array_equal(runtime[:, :, :3], pixels)
            self.assertTrue(np.all(runtime[:, :, 3] == 255))
            self.assertEqual(output["diagnostics"]["min_alpha"], 255)
            self.assertEqual(output["diagnostics"]["fully_opaque_pixels"], 12 * 8)
            self.assertTrue(manifest["alpha_contract"]["all_transparent_corners"])
            self.assertTrue(manifest["alpha_contract"]["all_transparent_outer_edges"])
            self.assertEqual(manifest["alpha_contract"]["opaque_full_frame_component_count"], 1)
            self.assertEqual(
                manifest["alpha_contract"]["waivers"][0]["fields"],
                ["allow_opaque_full_frame"],
            )


if __name__ == "__main__":
    raise SystemExit(unittest.main())
