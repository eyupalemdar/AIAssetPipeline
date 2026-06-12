from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from typing import Any

from .spec import normalize_project_root, rel, validate_manifest


HOST = "127.0.0.1"
TIMEOUT = 60.0
PAUSE = 0.3
RETRY_ATTEMPTS = 4
RETRY_BACKOFF = 1.0


def discover_port(project_root: Path) -> int:
    port_file = project_root / "Intermediate/MCTExport_port.txt"
    if port_file.exists():
        text = port_file.read_text(encoding="utf-8", errors="ignore").strip()
        try:
            return int(text)
        except ValueError:
            pass
    return 55560


def _send_once(cmd_type: str, params: dict[str, Any] | None, port: int, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(TIMEOUT)
    try:
        sock.connect((HOST, port))
        command: dict[str, Any] = {"type": cmd_type}
        if params:
            command["params"] = params
        if meta:
            command["meta"] = meta
        sock.sendall(json.dumps(command).encode("utf-8"))
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
    finally:
        sock.close()
    if not data:
        return {"success": False, "error": "no data"}
    try:
        return json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return {"success": False, "error": f"json decode: {exc}"}


def send(
    cmd_type: str,
    params: dict[str, Any] | None = None,
    port: int | None = None,
    project_root: Path | str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = normalize_project_root(project_root)
    port = port or discover_port(root)
    last: dict[str, Any] | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            result = _send_once(cmd_type, params, port, meta=meta)
        except (socket.timeout, ConnectionError, OSError) as exc:
            result = {"success": False, "error": f"socket: {exc}"}
        if result.get("success"):
            time.sleep(PAUSE)
            return result
        last = result
        time.sleep(RETRY_BACKOFF * (attempt + 1))
    return last or {"success": False, "error": "retry exhausted"}


def plan_import(manifest_path: Path, project_root: Path | str | None = None, force: bool = False) -> dict[str, Any]:
    root = normalize_project_root(project_root, manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    warnings = validate_manifest(manifest)
    planned: list[dict[str, Any]] = []
    for item in manifest["outputs"]:
        source_path = root / str(item["runtime_file"])
        package_path = str(item["ue_package_path"])
        asset_name = str(item["ue_asset_name"])
        disk_asset = root / "Content" / package_path.removeprefix("/Game/") / f"{asset_name}.uasset"
        texture_type = str(item.get("texture_type", "color"))
        planned.append(
            {
                "component_id": item.get("component_id", ""),
                "source_path": str(source_path).replace("\\", "/"),
                "package_path": package_path,
                "asset_name": asset_name,
                "ue_asset_path": item.get("ue_asset_path") or f"{package_path}/{asset_name}",
                "exists_on_disk": disk_asset.exists(),
                "would_import": force or not disk_asset.exists(),
                "params": {
                    "source_path": str(source_path).replace("\\", "/"),
                    "package_path": package_path,
                    "asset_name": asset_name,
                    "compression": "UserInterface2D",
                    "srgb": texture_type != "mask",
                    "mip_gen": "NoMipmaps",
                    "lod_group": "UI",
                },
            }
        )
    return {
        "ok": True,
        "manifest": rel(root, manifest_path.resolve()),
        "project_root": str(root),
        "force": force,
        "component_count": len(planned),
        "imports": planned,
        "warnings": warnings,
    }


def import_manifest(
    manifest_path: Path,
    force: bool = False,
    dry_run: bool = False,
    port: int | None = None,
    project_root: Path | str | None = None,
) -> dict[str, Any]:
    plan = plan_import(manifest_path, project_root=project_root, force=force)
    if dry_run:
        return {**plan, "dry_run": True}

    root = Path(plan["project_root"])
    result = send(
        "asset_pipeline_import_manifest",
        {"manifest_path": str(manifest_path), "force": force},
        port=port,
        project_root=root,
        meta={"scope": "write"},
    )
    if not result.get("success"):
        raise RuntimeError(f"asset_pipeline_import_manifest failed: {result}")
    return {"ok": True, "manifest": rel(root, manifest_path.resolve()), "result": result.get("data", result)}
