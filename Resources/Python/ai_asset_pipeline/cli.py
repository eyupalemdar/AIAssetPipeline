from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from ai_asset_pipeline.pipeline import package_spec, validate_manifest_file
    from ai_asset_pipeline.smoke import create_smoke_fixture
    from ai_asset_pipeline.tspec import validate_tspec_links
    from ai_asset_pipeline.ue_import import import_manifest, plan_import
    from ai_asset_pipeline.sampling import CAPABILITIES, load_recipe
    from ai_asset_pipeline.quality import run_sampling
else:
    from .pipeline import package_spec, validate_manifest_file
    from .smoke import create_smoke_fixture
    from .tspec import validate_tspec_links
    from .ue_import import import_manifest, plan_import
    from .sampling import CAPABILITIES, load_recipe
    from .quality import run_sampling


def _add_project_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", type=Path, default=None, help="Project root override. Defaults to nearest .uproject.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Provider-agnostic AI source art to UE-ready asset pipeline.")
    sub = parser.add_subparsers(dest="command", required=True)

    validate_spec_cmd = sub.add_parser("validate-spec", help="Validate an asset pipeline spec without writing outputs.")
    validate_spec_cmd.add_argument("spec", type=Path)
    _add_project_root(validate_spec_cmd)

    package_cmd = sub.add_parser("package", help="Generate runtime PNGs, review images, and manifest.")
    package_cmd.add_argument("spec", type=Path)
    _add_project_root(package_cmd)

    validate_manifest_cmd = sub.add_parser("validate-manifest", help="Validate an output manifest and alpha contract.")
    validate_manifest_cmd.add_argument("manifest", type=Path)
    _add_project_root(validate_manifest_cmd)

    plan_import_cmd = sub.add_parser("plan-import", help="Plan manifest texture import calls without connecting to UE.")
    plan_import_cmd.add_argument("manifest", type=Path)
    plan_import_cmd.add_argument("--force", action="store_true", help="Plan overwrites for existing UE texture assets.")
    _add_project_root(plan_import_cmd)

    import_cmd = sub.add_parser("ue-import", help="Import manifest textures via the AIAssetPipeline editor command.")
    import_cmd.add_argument("manifest", type=Path)
    import_cmd.add_argument("--force", action="store_true", help="Overwrite existing UE texture assets.")
    import_cmd.add_argument("--dry-run", action="store_true", help="Return the import plan without connecting to UE.")
    import_cmd.add_argument("--port", type=int, default=None, help="Override MCPToolkit TCP port.")
    _add_project_root(import_cmd)

    validate_links_cmd = sub.add_parser("validate-tspec-links", help="Validate TSpec assetPipelines links.")
    validate_links_cmd.add_argument("tspec_path_or_dir", type=Path)
    _add_project_root(validate_links_cmd)

    smoke_cmd = sub.add_parser("create-smoke-fixture", help="Create a deterministic probe-only smoke spec and source PNG.")
    smoke_cmd.add_argument("--output-dir", default="Docs/AIAssetPipeline/Smoke")
    _add_project_root(smoke_cmd)

    sub.add_parser("capabilities", help="Report versioned portable pipeline capabilities.")
    sampling_cmd = sub.add_parser("validate-sampling", help="Validate recipe, source/output hashes and mip policy without UE.")
    sampling_cmd.add_argument("recipe", type=Path)
    sampling_cmd.add_argument("--project-root", type=Path, required=True)
    native_cmd = sub.add_parser("ue-sampling", help="Verify explicit material bindings; opt in to apply/save/import.")
    native_cmd.add_argument("recipe", type=Path)
    native_cmd.add_argument("--project-root", type=Path, required=True)
    native_cmd.add_argument("--port", type=int, required=True)
    native_cmd.add_argument("--apply", action="store_true")
    native_cmd.add_argument("--save", action="store_true")
    native_cmd.add_argument("--import-textures", action="store_true")
    quality_cmd = sub.add_parser("quality-smoke", help="Create and validate an isolated engineering fixture; optional native render.")
    quality_cmd.add_argument("--project-root", type=Path, required=True)
    quality_cmd.add_argument("--run", required=True)
    quality_cmd.add_argument("--port", type=int)
    quality_cmd.add_argument("--native", action="store_true")

    args = parser.parse_args()
    try:
        if args.command == "capabilities":
            result = {"ok": True, "capabilities": CAPABILITIES}
        elif args.command == "validate-sampling":
            recipe, manifest, outputs = load_recipe(args.recipe, args.project_root)
            result = {"ok": True, "materials": len(recipe["materials"]), "components": len(outputs), "native_verified": False}
        elif args.command == "ue-sampling":
            result = run_sampling(args.recipe, args.project_root, args.port, args.apply, args.save, args.import_textures)
        elif args.command == "quality-smoke":
            from ai_asset_pipeline.quality_smoke import run_quality_smoke
            result = run_quality_smoke(args.project_root, args.run, args.port, args.native)
        elif args.command == "validate-spec":
            result = package_spec(args.spec, validate_only=True, project_root=args.project_root)
        elif args.command == "package":
            result = package_spec(args.spec, project_root=args.project_root)
        elif args.command == "validate-manifest":
            result = validate_manifest_file(args.manifest, project_root=args.project_root)
        elif args.command == "plan-import":
            result = plan_import(args.manifest, project_root=args.project_root, force=args.force)
        elif args.command == "ue-import":
            result = import_manifest(
                args.manifest,
                force=args.force,
                dry_run=args.dry_run,
                port=args.port,
                project_root=args.project_root,
            )
        elif args.command == "validate-tspec-links":
            result = validate_tspec_links(args.tspec_path_or_dir, project_root=args.project_root)
            if not result.get("ok"):
                print(json.dumps(result, indent=2), file=sys.stderr)
                return 1
        elif args.command == "create-smoke-fixture":
            result = create_smoke_fixture(project_root=args.project_root, output_dir=args.output_dir)
        else:
            parser.error(f"Unsupported command: {args.command}")
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
