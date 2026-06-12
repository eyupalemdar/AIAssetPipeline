#!/usr/bin/env python3
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

from fastmcp import FastMCP


SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = SCRIPT_DIR.parent
PROJECT_DIR = PLUGIN_DIR.parent.parent
PYTHON_DIR = PLUGIN_DIR / "Resources" / "Python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from ai_asset_pipeline.pipeline import package_spec, validate_manifest_file
from ai_asset_pipeline.tspec import validate_tspec_links
from ai_asset_pipeline.ue_import import plan_import


mcp = FastMCP(
    "ai-asset-pipeline",
    instructions=(
        "Provider-agnostic source-art packaging and UE texture import orchestration. "
        "Does not call image models and does not mutate Widget Blueprints."
    ),
)


def _port(project_root: str = "") -> int:
    root = Path(project_root) if project_root else PROJECT_DIR
    port_file = root / "Intermediate" / "MCTExport_port.txt"
    if port_file.exists():
        try:
            return int(port_file.read_text(encoding="utf-8").strip())
        except ValueError:
            pass
    return 55560


def _send(command: str, params: dict | None = None, project_root: str = "", scope: str = "", dry_run: bool = False) -> dict:
    envelope: dict = {"type": command}
    if params:
        envelope["params"] = params
    meta: dict = {}
    if scope:
        meta["scope"] = scope
    if dry_run:
        meta["dry_run"] = True
    if meta:
        envelope["meta"] = meta

    with socket.create_connection(("127.0.0.1", _port(project_root)), timeout=60) as sock:
        sock.sendall(json.dumps(envelope).encode("utf-8"))
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data.decode("utf-8")) if data else {"success": False, "error": "No response from editor"}


def _format(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


@mcp.tool()
def asset_pipeline_validate_spec(spec_path: str, project_root: str = "") -> str:
    return _format(package_spec(Path(spec_path), validate_only=True, project_root=project_root or None))


@mcp.tool()
def asset_pipeline_package(spec_path: str, project_root: str = "") -> str:
    return _format(package_spec(Path(spec_path), project_root=project_root or None))


@mcp.tool()
def asset_pipeline_validate_manifest(manifest_path: str, project_root: str = "") -> str:
    return _format(validate_manifest_file(Path(manifest_path), project_root=project_root or None))


@mcp.tool()
def asset_pipeline_plan_import(manifest_path: str, project_root: str = "", force: bool = False) -> str:
    return _format(plan_import(Path(manifest_path), project_root=project_root or None, force=force))


@mcp.tool()
def asset_pipeline_validate_tspec_links(tspec_path_or_dir: str, project_root: str = "") -> str:
    return _format(validate_tspec_links(Path(tspec_path_or_dir), project_root=project_root or None))


@mcp.tool()
def asset_pipeline_status(project_root: str = "") -> str:
    return _format(_send("asset_pipeline_status", project_root=project_root))


@mcp.tool()
def asset_pipeline_import_manifest(
    manifest_path: str,
    project_root: str = "",
    force: bool = False,
    scope: str = "write",
    dry_run: bool = False,
) -> str:
    return _format(
        _send(
            "asset_pipeline_import_manifest",
            {"manifest_path": manifest_path, "force": force},
            project_root=project_root,
            scope=scope,
            dry_run=dry_run,
        )
    )


@mcp.tool()
def asset_pipeline_verify_assets(manifest_path: str, project_root: str = "") -> str:
    return _format(_send("asset_pipeline_verify_assets", {"manifest_path": manifest_path}, project_root=project_root))


if __name__ == "__main__":
    mcp.run()
