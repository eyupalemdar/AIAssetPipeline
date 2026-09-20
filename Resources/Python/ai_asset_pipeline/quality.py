"""Guarded editor orchestration for portable sampling acceptance."""
from __future__ import annotations

import json
from pathlib import Path
import socket
import time
import uuid

from .sampling import load_recipe, project_path, read_json, require, sha256, validate_native_readback


class EditorConnection:
    def __init__(self, root, port):
        self.root = Path(root).resolve()
        require(type(port) is int and 0 < port < 65536, "Explicit editor port required; discover the current editor first")
        self.port = port
        self.identity = self.call("editor_identity")
        require(Path(self.identity["project_dir"]).resolve() == self.root, "Wrong editor project; no mutation sent")

    def call(self, command, params=None, write=False, scope=None):
        if hasattr(self, "identity") and (write or scope in ("write", "destructive")):
            current = self._request("editor_identity")
            require(current.get("editor_id") == self.identity.get("editor_id")
                    and current.get("pid") == self.identity.get("pid")
                    and Path(current["project_dir"]).resolve() == self.root,
                    "Editor changed during the operation; no mutation sent")
        return self._request(command, params, write, scope)

    def _request(self, command, params=None, write=False, scope=None):
        payload = {"type": command, "params": params or {}, "meta": {"scope": scope or ("write" if write else "read")}}
        # A timed-out mutation is never retried: first inspect its receipt/assets.
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as connection:
            connection.settimeout(300)
            connection.sendall(json.dumps(payload).encode("utf-8"))
            connection.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        response = json.loads(b"".join(chunks).decode("utf-8"))
        require(response.get("success") is True, f"{command}: {response.get('error', response)}")
        result = response.get("data", response)
        require(result.get("success") is not False, f"{command}: {result}")
        return result


def texture_readback(connection, manifest_path, timeout=30):
    """Cold loads may schedule texture compilation. Retry reads, never mutations."""
    deadline = time.monotonic() + timeout
    attempts = 0
    while True:
        data = connection.call("asset_pipeline_verify_assets", {"manifest_path": str(manifest_path)})
        attempts += 1
        source_ok = all(data.get(key) is True for key in
            ("all_assets_exist", "all_sizes_match", "all_settings_match", "all_source_mips_match"))
        # UE can expose a ready placeholder resource before its full resolution
        # arrives. Bound this read-only wait; persistent platform caps still fail.
        pending = any(row.get("runtime_resource_ready") is False or any(
            row.get("runtime_" + axis) != row[axis]
            for axis in ("width", "height") if axis in row
        ) for row in data.get("assets", []))
        if not source_ok or not pending:
            return data, attempts
        require(time.monotonic() < deadline, "Texture resources are still compiling or have a persistent resolution cap; inspect native readback")
        time.sleep(0.2)


def run_sampling(recipe_path, root, port, apply=False, save=False, import_textures=False):
    root = Path(root).resolve()
    recipe_path = project_path(root, str(recipe_path))
    recipe, manifest, outputs = load_recipe(recipe_path, root)
    require(not import_textures or apply, "Import requires explicit --apply")
    require(not save or apply, "Save requires explicit --apply")
    connection = EditorConnection(root, port)
    if apply:
        require(connection.call("pie_status").get("pie_active") is False,
                "Stop PIE before applying/importing/saving material sampling; verification is read-only and remains available")
    manifest_path = project_path(root, recipe["manifest"])
    if import_textures:
        connection.call("asset_pipeline_import_manifest", {"manifest_path": str(manifest_path), "force": False}, write=True)
    readback, readback_attempts = texture_readback(connection, manifest_path)
    validate_native_readback(readback, outputs)
    request_id = uuid.uuid4().hex
    run_dir = root / "Saved/AIAssetPipeline/Sampling" / request_id
    run_dir.mkdir(parents=True)
    result_path = run_dir / "result.json"
    request_path = run_dir / "request.json"
    request = {"root": str(root), "recipe": str(recipe_path), "apply": apply, "save": save,
               "request_id": request_id, "result": str(result_path)}
    request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
    python_root = root / "Plugins/AIAssetPipeline/Resources/Python"
    script_path = run_dir / "invoke.py"
    # repr is Python literal escaping. No shell interpolation is used.
    script_path.write_text("import sys, importlib\n"
        + f"sys.path.insert(0, {str(python_root)!r})\n"
        + "import ai_asset_pipeline.sampling as sampling\nimport ai_asset_pipeline.ue_sampling as adapter\n"
        + "importlib.reload(sampling)\nimportlib.reload(adapter)\n"
        + f"adapter.run_request({str(request_path)!r})\n", encoding="utf-8")
    connection.call("editor_console_command", {"command": 'py "' + script_path.as_posix() + '"'}, scope="destructive")
    deadline = time.monotonic() + 180
    while not result_path.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    require(result_path.exists(), f"No native receipt; inspect editor log. Request: {request_path}")
    result = read_json(result_path)
    require(result.get("request_id") == request_id, "Stale editor receipt")
    result.update(editor=connection.identity, native_readback=readback, native_readback_attempts=readback_attempts, manifest_sha256=sha256(manifest_path),
                  receipt=str(result_path.relative_to(root)).replace("\\", "/"))
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    require(result.get("ok") is True, f"Native sampling failed: {result.get('error')}; receipt: {result_path}")
    return result
