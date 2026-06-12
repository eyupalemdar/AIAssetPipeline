#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import bootstrap


class BootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source_root = Path(__file__).resolve().parents[2]
        self.asset_source_root = self.resolve_asset_source_root()
        self.mcp_source_root = self.resolve_mcp_source_root()

    def resolve_asset_source_root(self) -> Path:
        if (self.source_root / "AIAssetPipeline.uplugin").is_file():
            return self.source_root
        return self.source_root / "Plugins" / "AIAssetPipeline"

    def resolve_mcp_source_root(self) -> Path:
        candidates = [
            self.source_root / "Plugins" / "MCPToolkit",
            self.source_root / "UnrealMCPToolkit",
            self.source_root.parent / "UnrealMCPToolkit",
            self.source_root.parent / "MCPToolkit",
        ]
        for candidate in candidates:
            if (candidate / "MCPToolkit.uplugin").is_file():
                return candidate
        return self.source_root / "Plugins" / "MCPToolkit"

    def source_args(self) -> list[str]:
        return [
            "--source-root",
            str(self.source_root),
            "--mcp-source-root",
            str(self.mcp_source_root),
            "--asset-source-root",
            str(self.asset_source_root),
        ]

    def make_project(self, root: Path) -> Path:
        project = root / "TargetGame"
        project.mkdir()
        uproject = project / "TargetGame.uproject"
        uproject.write_text(
            json.dumps({"FileVersion": 3, "Plugins": []}, indent=2) + "\n",
            encoding="utf-8",
        )
        return project

    def run_cli(self, args: list[str]) -> tuple[int, dict]:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = bootstrap.main(args)
        return code, json.loads(stream.getvalue())

    def run_text(self, args: list[str]) -> tuple[int, str]:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = bootstrap.main(args)
        return code, stream.getvalue()

    def test_install_dry_run_does_not_write_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(Path(tmp))
            code, payload = self.run_cli(
                [
                    "install",
                    "--project",
                    str(project),
                    *self.source_args(),
                    "--dry-run",
                ]
            )

            self.assertEqual(code, 0)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["dryRun"])
            self.assertGreater(payload["summary"]["planned"], 0)
            self.assertFalse((project / bootstrap.CONFIG_NAME).exists())
            self.assertFalse((project / "Plugins" / "MCPToolkit").exists())

    def test_install_writes_workflow_and_doctor_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(Path(tmp))
            code, payload = self.run_cli(
                [
                    "install",
                    "--project",
                    str(project),
                    *self.source_args(),
                ]
            )

            self.assertEqual(code, 0)
            self.assertTrue(payload["ok"])
            self.assertFalse(payload["dryRun"])
            self.assertTrue((project / bootstrap.CONFIG_NAME).is_file())
            self.assertTrue((project / bootstrap.LOCK_NAME).is_file())
            self.assertTrue((project / "Plugins" / "MCPToolkit" / "MCPToolkit.uplugin").is_file())
            self.assertTrue((project / "Plugins" / "AIAssetPipeline" / "AIAssetPipeline.uplugin").is_file())
            self.assertTrue((project / "Tools" / "AIWorkflowBootstrap" / "bootstrap.py").is_file())
            self.assertTrue((project / "Tools" / "AIWorkflowBootstrap" / "bootstrap.ps1").is_file())
            self.assertTrue((project / "Tools" / "AIWorkflowBootstrap" / "install_commonai_windows.ps1").is_file())
            self.assertTrue((project / "Tools" / "AIWorkflowBootstrap" / "install_commonai_linux.sh").is_file())
            self.assertTrue((project / "Tools" / "AIWorkflowBootstrap" / "install_commonai_macos.sh").is_file())
            self.assertTrue((project / "AGENTS.md").is_file())
            self.assertTrue((project / "Docs" / "AI_UI_Transfer" / "README.md").is_file())
            self.assertTrue((project / "Docs" / "UI_TSpec" / "tspec.schema.json").is_file())
            self.assertTrue((project / "Docs" / "AIAssetPipeline" / "Schemas" / "asset_pipeline_spec.v1.json").is_file())
            self.assertTrue((project / "Scripts" / "ValidateUITSpecs.ps1").is_file())

            lock = json.loads((project / bootstrap.LOCK_NAME).read_text(encoding="utf-8"))
            self.assertEqual(lock["sourceManifest"]["schema"], "commonai-source-manifest-v1")
            self.assertGreater(len(lock["sourceManifest"]["fingerprint"]), 20)

            uproject = json.loads((project / "TargetGame.uproject").read_text(encoding="utf-8"))
            plugin_names = {entry["Name"]: entry for entry in uproject["Plugins"]}
            self.assertTrue(plugin_names["MCPToolkit"]["Enabled"])
            self.assertTrue(plugin_names["AIAssetPipeline"]["Enabled"])

            doctor_code, doctor_payload = self.run_cli(["doctor", "--project", str(project)])
            self.assertEqual(doctor_code, 0)
            self.assertTrue(doctor_payload["ok"])

            strict_code, strict_payload = self.run_cli(["doctor", "--project", str(project), "--strict"])
            self.assertEqual(strict_code, 0)
            self.assertTrue(strict_payload["ok"])

            update_code, update_payload = self.run_cli(
                [
                    "update",
                    "--project",
                    str(project),
                    *self.source_args(),
                ]
            )
            self.assertEqual(update_code, 0)
            self.assertTrue(update_payload["ok"])
            self.assertTrue(update_payload["dryRun"])
            self.assertEqual(update_payload["summary"].get("conflict", 0), 0)

            validate_code, validate_payload = self.run_cli(["validate-tspecs", "--project", str(project)])
            self.assertEqual(validate_code, 0)
            self.assertTrue(validate_payload["ok"])
            self.assertIn("Validated", validate_payload["stdout"])

            markdown_code, markdown = self.run_text(["diff", "--project", str(project), *self.source_args(), "--format", "markdown"])
            self.assertEqual(markdown_code, 0)
            self.assertIn("# AIWorkflowBootstrap diff", markdown)

    def test_split_source_install_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(Path(tmp))
            code, payload = self.run_cli(
                [
                    "install",
                    "--project",
                    str(project),
                    *self.source_args(),
                ]
            )

            self.assertEqual(code, 0)
            self.assertTrue(payload["ok"])
            self.assertIn("backupId", payload)
            self.assertTrue((project / bootstrap.CONFIG_NAME).exists())

            rollback_code, rollback_payload = self.run_cli(["rollback", "--project", str(project), "--backup", payload["backupId"]])
            self.assertEqual(rollback_code, 0)
            self.assertTrue(rollback_payload["ok"])
            self.assertFalse((project / bootstrap.CONFIG_NAME).exists())
            self.assertFalse((project / "Plugins" / "MCPToolkit" / "MCPToolkit.uplugin").exists())

    def test_manifest_reports_source_fingerprint(self) -> None:
        code, payload = self.run_cli(["manifest", *self.source_args()])

        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["manifest"]["schema"], "commonai-source-manifest-v1")
        self.assertGreater(payload["manifest"]["packages"][0]["fileCount"], 0)
        self.assertGreater(len(payload["manifest"]["fingerprint"]), 20)


if __name__ == "__main__":
    unittest.main()
