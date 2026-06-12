#!/usr/bin/env python3
"""Install and maintain the shared AI UI/asset workflow in UE projects.

The bootstrapper is intentionally stdlib-only so a fresh Unreal project can run
it before any project-specific Python environment exists.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


TOOL_NAME = "AIWorkflowBootstrap"
TOOL_VERSION = "0.1.3"
STATE_DIR = Path("Tools") / "AIWorkflowBootstrap" / "state"
CONFIG_NAME = (STATE_DIR / "project.json").as_posix()
LOCK_NAME = (STATE_DIR / "lock.json").as_posix()
STATE_GITIGNORE_NAME = (STATE_DIR / ".gitignore").as_posix()
STATE_GITIGNORE_CONTENT = "backups/\n"
LEGACY_CONFIG_NAME = "commonai.project.json"
LEGACY_LOCK_NAME = "commonai.lock.json"
LEGACY_BACKUP_DIR = Path(".commonai") / "backups"
DEFAULT_PLUGINS = ("MCPToolkit", "AIAssetPipeline")

EXCLUDED_DIR_NAMES = {
    ".diversion",
    ".git",
    ".github",
    ".commonai",
    ".hg",
    ".idea",
    ".svn",
    ".vs",
    "__pycache__",
    "Binaries",
    "DerivedDataCache",
    "Intermediate",
    "Saved",
}
EXCLUDED_SUFFIXES = {
    ".dep.json",
    ".dll",
    ".exe",
    ".exp",
    ".ilk",
    ".lib",
    ".log",
    ".obj",
    ".pdb",
    ".pyc",
    ".pyo",
    ".rsp",
    ".sarif",
    ".tmp",
}

PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    "generic": {
        "uiSpecDir": "Docs/UI_TSpec",
        "uiWorkflowDir": "Docs/AI_UI_Transfer",
        "assetPipelineSchemaDir": "Docs/AIAssetPipeline/Schemas",
        "bootstrapToolPath": "Tools/AIWorkflowBootstrap/bootstrap.py",
        "validatorPath": "Scripts/ValidateUITSpecs.ps1",
        "probeRoot": "/Game/UI/_AIProbe",
        "productionMutationRequiresTSpec": True,
    },
    "commonui": {
        "uiSpecDir": "Docs/UI_TSpec",
        "uiWorkflowDir": "Docs/AI_UI_Transfer",
        "assetPipelineSchemaDir": "Docs/AIAssetPipeline/Schemas",
        "bootstrapToolPath": "Tools/AIWorkflowBootstrap/bootstrap.py",
        "validatorPath": "Scripts/ValidateUITSpecs.ps1",
        "probeRoot": "/Game/UI/_AIProbe",
        "productionMutationRequiresTSpec": True,
    },
    "projectokey": {
        "uiSpecDir": "Docs/Tasarim/UI_TSpecs",
        "uiWorkflowDir": "Docs/AI_UI_Transfer",
        "assetPipelineSchemaDir": "Docs/AIAssetPipeline/Schemas",
        "bootstrapToolPath": "Tools/AIWorkflowBootstrap/bootstrap.py",
        "validatorPath": "Scripts/ValidateUITSpecs.ps1",
        "probeRoot": "/Game/UI/_AIProbe",
        "productionMutationRequiresTSpec": True,
    },
}


@dataclass(frozen=True)
class FileIntent:
    source: Path
    destination: Path
    kind: str


@dataclass(frozen=True)
class SourceLayout:
    common_root: Path
    mcp_plugin_root: Path
    asset_plugin_root: Path
    bootstrap_root: Path


class BootstrapError(RuntimeError):
    pass


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def emit_plan(payload: dict[str, Any], output_format: str = "json") -> None:
    if output_format == "markdown":
        print(render_plan_markdown(payload))
    else:
        emit(payload)


def render_plan_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        f"# {payload.get('tool', TOOL_NAME)} {payload.get('command', 'plan')}",
        "",
        f"- Project: `{payload.get('projectRoot', '')}`",
        f"- Profile: `{payload.get('profile', '')}`",
        f"- Dry run: `{payload.get('dryRun', '')}`",
        f"- OK: `{payload.get('ok', '')}`",
        f"- Summary: planned `{summary.get('planned', 0)}`, overwrite `{summary.get('overwrite', 0)}`, unchanged `{summary.get('unchanged', 0)}`, conflicts `{summary.get('conflict', 0)}`",
        "",
        "## Changes",
    ]
    interesting = [
        op for op in payload.get("operations", [])
        if op.get("status") in {"planned", "overwrite", "conflict"}
    ]
    if not interesting:
        lines.append("- No changes.")
    for op in interesting[:200]:
        lines.append(f"- `{op.get('status')}` `{op.get('kind')}` `{op.get('path')}`")
        if op.get("message"):
            lines.append(f"  - {op['message']}")
    if len(interesting) > 200:
        lines.append(f"- ... {len(interesting) - 200} additional change(s) omitted.")
    if payload.get("backupId"):
        lines.extend(["", f"Backup: `{payload['backupId']}`"])
    if payload.get("message"):
        lines.extend(["", str(payload["message"])])
    return "\n".join(lines)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def same_path(left: Path, right: Path) -> bool:
    return left.resolve(strict=False) == right.resolve(strict=False)


def relpath(path: Path, root: Path) -> str:
    resolved = path.resolve(strict=False)
    base = root.resolve(strict=False)
    try:
        return resolved.relative_to(base).as_posix()
    except ValueError:
        return resolved.as_posix()


def parse_plugins(value: str | None) -> list[str]:
    if not value:
        return list(DEFAULT_PLUGINS)
    plugins = [item.strip() for item in value.split(",") if item.strip()]
    if not plugins:
        raise BootstrapError("At least one plugin must be selected.")
    return plugins


def resolve_plugin_root(root_value: str | Path, plugin_name: str) -> Path:
    root = Path(root_value).resolve()
    candidates = [
        root / "Plugins" / plugin_name,
        root / plugin_name,
        root,
    ]
    for candidate in candidates:
        if (candidate / f"{plugin_name}.uplugin").is_file():
            return candidate
    raise BootstrapError(f"Could not resolve {plugin_name} plugin root from {root}")


def resolve_bootstrap_root(common_root: Path, asset_plugin_root: Path) -> Path:
    candidates = [
        common_root / "Tools" / "AIWorkflowBootstrap",
        asset_plugin_root / "Tools" / "AIWorkflowBootstrap",
        Path(__file__).resolve().parent,
    ]
    for candidate in candidates:
        if (candidate / "bootstrap.py").is_file():
            return candidate.resolve()
    raise BootstrapError("Could not resolve Tools/AIWorkflowBootstrap source.")


def discover_source_layout(
    source_root: str | None,
    mcp_source_root: str | None = None,
    asset_source_root: str | None = None,
) -> SourceLayout:
    common_root = Path(source_root).resolve() if source_root else Path(__file__).resolve().parents[2]
    mcp_base = Path(mcp_source_root).resolve() if mcp_source_root else common_root
    asset_base = Path(asset_source_root).resolve() if asset_source_root else common_root
    mcp_plugin_root = resolve_plugin_root(mcp_base, "MCPToolkit")
    asset_plugin_root = resolve_plugin_root(asset_base, "AIAssetPipeline")
    return SourceLayout(
        common_root=common_root,
        mcp_plugin_root=mcp_plugin_root,
        asset_plugin_root=asset_plugin_root,
        bootstrap_root=resolve_bootstrap_root(common_root, asset_plugin_root),
    )


def find_uproject(project: str | None) -> tuple[Path, Path]:
    root = Path(project or ".").resolve()
    if root.suffix == ".uproject":
        if not root.is_file():
            raise BootstrapError(f"Project file not found: {root}")
        return root.parent, root
    if not root.exists():
        raise BootstrapError(f"Project root not found: {root}")
    matches = sorted(root.glob("*.uproject"))
    if len(matches) != 1:
        raise BootstrapError(f"Expected exactly one .uproject in {root}, found {len(matches)}.")
    return root, matches[0]


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise BootstrapError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BootstrapError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise BootstrapError(f"Expected a JSON object in {path}")
    return data


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def canonical_config_path(project_root: Path) -> Path:
    return project_root / CONFIG_NAME


def canonical_lock_path(project_root: Path) -> Path:
    return project_root / LOCK_NAME


def state_gitignore_path(project_root: Path) -> Path:
    return project_root / STATE_GITIGNORE_NAME


def legacy_config_path(project_root: Path) -> Path:
    return project_root / LEGACY_CONFIG_NAME


def legacy_lock_path(project_root: Path) -> Path:
    return project_root / LEGACY_LOCK_NAME


def config_path_for_read(project_root: Path) -> Path:
    canonical = canonical_config_path(project_root)
    if canonical.exists():
        return canonical
    return legacy_config_path(project_root)


def lock_path_for_read(project_root: Path) -> Path:
    canonical = canonical_lock_path(project_root)
    if canonical.exists():
        return canonical
    return legacy_lock_path(project_root)


def load_config(project_root: Path) -> tuple[dict[str, Any], Path] | tuple[None, Path]:
    path = config_path_for_read(project_root)
    if not path.exists():
        return None, canonical_config_path(project_root)
    return load_json(path), path


def load_lock_with_path(project_root: Path) -> tuple[dict[str, Any], Path] | tuple[None, Path]:
    path = lock_path_for_read(project_root)
    if not path.exists():
        return None, canonical_lock_path(project_root)
    return load_json(path), path


def legacy_state_files(project_root: Path) -> list[Path]:
    return [
        path
        for path in (legacy_config_path(project_root), legacy_lock_path(project_root))
        if path.exists()
    ]


def iter_source_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if any(part in EXCLUDED_DIR_NAMES for part in path.relative_to(root).parts):
            continue
        if not path.is_file():
            continue
        if any(path.name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES):
            continue
        yield path


def config_for(profile: str, plugins: list[str]) -> dict[str, Any]:
    defaults = PROFILE_DEFAULTS[profile]
    return {
        "schema": "commonai-project-config-v1",
        "toolVersion": TOOL_VERSION,
        "profile": profile,
        "plugins": plugins,
        **defaults,
    }


def digest_files(root: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    for source_file in iter_source_files(root):
        relative = source_file.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(source_file).encode("ascii"))
        digest.update(b"\0")
        count += 1
    return count, digest.hexdigest()


def plugin_root_for(layout: SourceLayout, plugin_name: str) -> Path:
    if plugin_name == "MCPToolkit":
        return layout.mcp_plugin_root
    if plugin_name == "AIAssetPipeline":
        return layout.asset_plugin_root
    return resolve_plugin_root(layout.common_root, plugin_name)


def build_source_manifest(layout: SourceLayout, config: dict[str, Any], plugins: list[str]) -> dict[str, Any]:
    package_roots: list[tuple[str, str, Path]] = []
    for plugin_name in plugins:
        package_roots.append((f"plugin:{plugin_name}", f"Plugins/{plugin_name}", plugin_root_for(layout, plugin_name)))
    package_roots.extend(
        [
            ("workflow-doc", "Plugins/MCPToolkit/Docs/AI_UI_Transfer", layout.mcp_plugin_root / "Docs" / "AI_UI_Transfer"),
            ("tspec-pack", "Plugins/MCPToolkit/Docs/UI_TSpec", layout.mcp_plugin_root / "Docs" / "UI_TSpec"),
            ("asset-pipeline-schema", "Plugins/AIAssetPipeline/Resources/Schemas", layout.asset_plugin_root / "Resources" / "Schemas"),
            ("bootstrap-tool", "Tools/AIWorkflowBootstrap", layout.bootstrap_root),
            ("validator", "Plugins/MCPToolkit/Resources/Scripts/ValidateUITSpecs.ps1", layout.mcp_plugin_root / "Resources" / "Scripts" / "ValidateUITSpecs.ps1"),
        ]
    )

    packages: list[dict[str, Any]] = []
    aggregate = hashlib.sha256()
    for kind, relative_root, root in package_roots:
        if root.is_file():
            count = 1
            package_hash = sha256_file(root)
        elif root.is_dir():
            count, package_hash = digest_files(root)
        else:
            raise BootstrapError(f"Distribution source not found: {root}")
        packages.append(
            {
                "kind": kind,
                "path": relative_root,
                "fileCount": count,
                "sha256": package_hash,
            }
        )
        aggregate.update(kind.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(relative_root.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(package_hash.encode("ascii"))
        aggregate.update(b"\0")

    return {
        "schema": "commonai-source-manifest-v1",
        "toolVersion": TOOL_VERSION,
        "profile": config["profile"],
        "plugins": plugins,
        "sourceRoots": {
            "common": str(layout.common_root),
            "mcp": str(layout.mcp_plugin_root),
            "asset": str(layout.asset_plugin_root),
            "bootstrap": str(layout.bootstrap_root),
        },
        "fingerprint": aggregate.hexdigest(),
        "packages": packages,
    }


def desired_file_intents(
    layout: SourceLayout,
    project_root: Path,
    config: dict[str, Any],
    plugins: list[str],
) -> list[FileIntent]:
    intents: list[FileIntent] = []

    for plugin_name in plugins:
        plugin_root = plugin_root_for(layout, plugin_name)
        if not plugin_root.is_dir():
            raise BootstrapError(f"Plugin source not found: {plugin_root}")
        destination_root = project_root / "Plugins" / plugin_name
        for source_file in iter_source_files(plugin_root):
            relative = source_file.relative_to(plugin_root)
            intents.append(FileIntent(source_file, destination_root / relative, "plugin"))

    workflow_source = layout.mcp_plugin_root / "Docs" / "AI_UI_Transfer"
    tspec_source = layout.mcp_plugin_root / "Docs" / "UI_TSpec"
    schema_source = layout.asset_plugin_root / "Resources" / "Schemas"
    bootstrap_source = layout.bootstrap_root
    validator_source = layout.mcp_plugin_root / "Resources" / "Scripts" / "ValidateUITSpecs.ps1"

    copy_roots = [
        (workflow_source, project_root / config["uiWorkflowDir"], "workflow-doc"),
        (tspec_source, project_root / config["uiSpecDir"], "tspec-pack"),
        (schema_source, project_root / config["assetPipelineSchemaDir"], "asset-pipeline-schema"),
        (bootstrap_source, project_root / "Tools" / "AIWorkflowBootstrap", "bootstrap-tool"),
    ]
    for source_dir, destination_dir, kind in copy_roots:
        if not source_dir.is_dir():
            raise BootstrapError(f"Workflow source directory not found: {source_dir}")
        for source_file in iter_source_files(source_dir):
            relative = source_file.relative_to(source_dir)
            if kind == "bootstrap-tool" and relative.parts and relative.parts[0] == "state":
                continue
            intents.append(FileIntent(source_file, destination_dir / relative, kind))

    if not validator_source.is_file():
        raise BootstrapError(f"TSpec validator source not found: {validator_source}")
    intents.append(FileIntent(validator_source, project_root / config["validatorPath"], "validator"))
    policy_source = bootstrap_source / "templates" / "AGENTS.commonai.md"
    if policy_source.is_file():
        intents.append(FileIntent(policy_source, project_root / "AGENTS.md", "agent-policy"))
    return intents


def load_lock(project_root: Path) -> dict[str, Any] | None:
    lock_data, _path = load_lock_with_path(project_root)
    return lock_data


def locked_hashes(lock_data: dict[str, Any] | None) -> dict[str, str]:
    if not lock_data:
        return {}
    hashes: dict[str, str] = {}
    for item in lock_data.get("managedFiles", []):
        if isinstance(item, dict) and item.get("path") and item.get("sha256"):
            hashes[str(item["path"])] = str(item["sha256"])
    return hashes


def managed_hash_matches(hashes: dict[str, str], relative_path: str, path: Path) -> bool:
    expected = hashes.get(relative_path)
    return bool(expected and path.exists() and sha256_file(path) == expected)


def classify_file_intent(
    intent: FileIntent,
    project_root: Path,
    source_root: Path,
    managed_hashes: dict[str, str],
    force: bool,
) -> dict[str, Any]:
    source_hash = sha256_file(intent.source)
    rel_destination = relpath(intent.destination, project_root)
    op = {
        "action": "copy",
        "kind": intent.kind,
        "path": rel_destination,
        "source": relpath(intent.source, source_root),
        "sourceSha256": source_hash,
    }

    if same_path(intent.source, intent.destination):
        op["status"] = "same-source"
        return op

    if not intent.destination.exists():
        op["status"] = "planned"
        return op

    destination_hash = sha256_file(intent.destination)
    op["currentSha256"] = destination_hash
    if destination_hash == source_hash:
        op["status"] = "unchanged"
        return op
    if force:
        op["status"] = "overwrite"
        return op
    if managed_hashes.get(rel_destination) == destination_hash:
        op["status"] = "planned"
        return op

    op["status"] = "conflict"
    op["message"] = "Destination exists and is not managed by this lock. Use --force to overwrite."
    return op


def read_uproject(path: Path) -> dict[str, Any]:
    return load_json(path)


def ensure_uproject_plugins(data: dict[str, Any], plugins: list[str]) -> tuple[dict[str, Any], bool]:
    changed = False
    plugin_entries = data.get("Plugins")
    if not isinstance(plugin_entries, list):
        plugin_entries = []
        data["Plugins"] = plugin_entries
        changed = True

    by_name: dict[str, dict[str, Any]] = {}
    for entry in plugin_entries:
        if isinstance(entry, dict) and isinstance(entry.get("Name"), str):
            by_name[entry["Name"]] = entry

    for plugin_name in plugins:
        entry = by_name.get(plugin_name)
        if entry is None:
            plugin_entries.append(
                {
                    "Name": plugin_name,
                    "Enabled": True,
                    "TargetAllowList": ["Editor"],
                    "SupportedTargetPlatforms": ["Win64", "Linux"],
                }
            )
            changed = True
            continue

        desired_fields = {
            "Enabled": True,
            "TargetAllowList": ["Editor"],
            "SupportedTargetPlatforms": ["Win64", "Linux"],
        }
        for key, value in desired_fields.items():
            if entry.get(key) != value:
                entry[key] = value
                changed = True

    return data, changed


def build_plan(
    project_root: Path,
    uproject_path: Path,
    layout: SourceLayout,
    profile: str,
    plugins: list[str],
    force: bool,
) -> tuple[dict[str, Any], list[FileIntent], dict[str, Any], dict[str, Any]]:
    config = config_for(profile, plugins)
    lock_data = load_lock(project_root)
    hashes = locked_hashes(lock_data)
    intents = desired_file_intents(layout, project_root, config, plugins)

    operations = [classify_file_intent(intent, project_root, layout.common_root, hashes, force) for intent in intents]

    config_path = canonical_config_path(project_root)
    current_config_path = config_path_for_read(project_root)
    config_op: dict[str, Any] = {"action": "write-json", "kind": "config", "path": CONFIG_NAME}
    if current_config_path.exists():
        try:
            current_config = load_json(current_config_path)
        except BootstrapError as exc:
            current_config = None
            config_op["message"] = str(exc)
        if config_path.exists() and current_config == config:
            config_op["status"] = "unchanged"
        elif current_config == config or force or managed_hash_matches(hashes, relpath(current_config_path, project_root), current_config_path):
            config_op["status"] = "planned"
        else:
            config_op["status"] = "conflict"
            config_op["message"] = config_op.get("message") or "Existing config is not managed by this lock."
    else:
        config_op["status"] = "planned"
    operations.append(config_op)

    state_gitignore = state_gitignore_path(project_root)
    state_gitignore_op: dict[str, Any] = {"action": "write-file", "kind": "state-gitignore", "path": STATE_GITIGNORE_NAME}
    if state_gitignore.exists():
        current_content = state_gitignore.read_text(encoding="utf-8")
        if current_content == STATE_GITIGNORE_CONTENT:
            state_gitignore_op["status"] = "unchanged"
        elif force or managed_hash_matches(hashes, STATE_GITIGNORE_NAME, state_gitignore):
            state_gitignore_op["status"] = "planned"
        else:
            state_gitignore_op["status"] = "conflict"
            state_gitignore_op["message"] = "Existing state .gitignore is not managed by this lock."
    else:
        state_gitignore_op["status"] = "planned"
    operations.append(state_gitignore_op)

    for legacy_path in legacy_state_files(project_root):
        operations.append(
            {
                "action": "remove-legacy",
                "kind": "legacy-state",
                "path": relpath(legacy_path, project_root),
                "status": "planned",
            }
        )

    uproject_data = read_uproject(uproject_path)
    desired_uproject, uproject_changed = ensure_uproject_plugins(uproject_data, plugins)
    uproject_rel = relpath(uproject_path, project_root)
    operations.append(
        {
            "action": "update-uproject",
            "kind": "project-config",
            "path": uproject_rel,
            "status": "planned" if uproject_changed else "unchanged",
        }
    )

    summary = summarize_operations(operations)
    plan = {
        "tool": TOOL_NAME,
        "toolVersion": TOOL_VERSION,
        "projectRoot": str(project_root),
        "sourceRoot": str(layout.common_root),
        "sourceRoots": {
            "common": str(layout.common_root),
            "mcp": str(layout.mcp_plugin_root),
            "asset": str(layout.asset_plugin_root),
            "bootstrap": str(layout.bootstrap_root),
        },
        "profile": profile,
        "plugins": plugins,
        "summary": summary,
        "operations": operations,
    }
    return plan, intents, config, desired_uproject


def summarize_operations(operations: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for op in operations:
        status = str(op.get("status", "unknown"))
        summary[status] = summary.get(status, 0) + 1
    summary["total"] = len(operations)
    return summary


def backup_root(project_root: Path) -> Path:
    return project_root / STATE_DIR / "backups"


def legacy_backup_root(project_root: Path) -> Path:
    return project_root / LEGACY_BACKUP_DIR


def create_backup(project_root: Path, plan: dict[str, Any]) -> str:
    backup_id = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = backup_root(project_root) / backup_id
    files_root = root / "files"
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    candidate_paths = [
        str(op.get("path", ""))
        for op in plan.get("operations", [])
        if op.get("status") in {"planned", "overwrite"} and op.get("path")
    ]
    candidate_paths.append(LOCK_NAME)
    for relative in candidate_paths:
        if not relative or relative in seen:
            continue
        seen.add(relative)
        target = project_root / relative
        backup_file = files_root / relative
        entry = {"path": relative, "existed": target.exists()}
        if target.is_file():
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup_file)
            entry["backupPath"] = relpath(backup_file, root)
            entry["sha256"] = sha256_file(target)
        entries.append(entry)
    root.mkdir(parents=True, exist_ok=True)
    write_json(
        root / "manifest.json",
        {
            "schema": "commonai-backup-v1",
            "toolVersion": TOOL_VERSION,
            "createdAt": utc_now(),
            "projectRoot": str(project_root),
            "entries": entries,
        },
    )
    return backup_id


def rollback(args: argparse.Namespace) -> int:
    try:
        project_root, _uproject_path = find_uproject(args.project)
        backup_id = args.backup
        if backup_id == "latest":
            candidates: list[tuple[str, Path]] = []
            for backups in (backup_root(project_root), legacy_backup_root(project_root)):
                if backups.exists():
                    candidates.extend((path.name, backups) for path in backups.iterdir() if path.is_dir())
            if not candidates:
                raise BootstrapError(f"No backups found under {backup_root(project_root)} or {legacy_backup_root(project_root)}")
            backup_id, backups = sorted(candidates, key=lambda item: item[0])[-1]
        else:
            backups = backup_root(project_root)
            if not (backups / backup_id).is_dir():
                legacy_backups = legacy_backup_root(project_root)
                if (legacy_backups / backup_id).is_dir():
                    backups = legacy_backups
        root = backups / backup_id
        manifest_path = root / "manifest.json"
        manifest_data = load_json(manifest_path)
        restored = 0
        removed = 0
        for entry in reversed(manifest_data.get("entries", [])):
            relative = str(entry.get("path", ""))
            if not relative:
                continue
            target = project_root / relative
            if entry.get("existed"):
                backup_path = root / str(entry.get("backupPath", ""))
                if backup_path.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup_path, target)
                    restored += 1
            elif target.is_file():
                target.unlink()
                removed += 1
        emit({"ok": True, "tool": TOOL_NAME, "command": "rollback", "projectRoot": str(project_root), "backup": backup_id, "restored": restored, "removed": removed})
        return 0
    except BootstrapError as exc:
        emit({"ok": False, "tool": TOOL_NAME, "command": "rollback", "error": str(exc)})
        return 2


def make_lock(
    project_root: Path,
    layout: SourceLayout,
    profile: str,
    plugins: list[str],
    intents: list[FileIntent],
) -> dict[str, Any]:
    managed_files: list[dict[str, Any]] = []
    for intent in intents:
        destination = intent.destination
        if not destination.exists():
            continue
        managed_files.append(
            {
                "path": relpath(destination, project_root),
                "sha256": sha256_file(destination),
                "source": relpath(intent.source, layout.common_root),
                "kind": intent.kind,
            }
        )

    config_path = project_root / CONFIG_NAME
    if config_path.exists():
        managed_files.append(
            {
                "path": CONFIG_NAME,
                "sha256": sha256_file(config_path),
                "source": "generated",
                "kind": "config",
            }
        )

    gitignore_path = state_gitignore_path(project_root)
    if gitignore_path.exists():
        managed_files.append(
            {
                "path": STATE_GITIGNORE_NAME,
                "sha256": sha256_file(gitignore_path),
                "source": "generated",
                "kind": "state-gitignore",
            }
        )

    plugin_entries = []
    for plugin_name in plugins:
        uplugin_path = project_root / "Plugins" / plugin_name / f"{plugin_name}.uplugin"
        version_name = ""
        if uplugin_path.exists():
            try:
                version_name = str(load_json(uplugin_path).get("VersionName", ""))
            except BootstrapError:
                version_name = ""
        plugin_entries.append(
            {
                "name": plugin_name,
                "path": f"Plugins/{plugin_name}",
                "versionName": version_name,
            }
        )

    return {
        "schema": "commonai-lock-v1",
        "generatedBy": TOOL_NAME,
        "generatedAt": utc_now(),
        "toolVersion": TOOL_VERSION,
        "sourceRoot": str(layout.common_root),
        "sourceRoots": {
            "common": str(layout.common_root),
            "mcp": str(layout.mcp_plugin_root),
            "asset": str(layout.asset_plugin_root),
            "bootstrap": str(layout.bootstrap_root),
        },
        "sourceManifest": build_source_manifest(layout, config_for(profile, plugins), plugins),
        "profile": profile,
        "plugins": plugin_entries,
        "managedFiles": managed_files,
    }


def apply_plan(
    project_root: Path,
    uproject_path: Path,
    intents: list[FileIntent],
    config: dict[str, Any],
    uproject_data: dict[str, Any],
    layout: SourceLayout,
    profile: str,
    plugins: list[str],
) -> None:
    for intent in intents:
        if same_path(intent.source, intent.destination):
            continue
        intent.destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(intent.source, intent.destination)

    write_json(canonical_config_path(project_root), config)
    state_gitignore = state_gitignore_path(project_root)
    state_gitignore.parent.mkdir(parents=True, exist_ok=True)
    state_gitignore.write_text(STATE_GITIGNORE_CONTENT, encoding="utf-8")
    write_json(uproject_path, uproject_data)
    lock_payload = make_lock(project_root, layout, profile, plugins, intents)
    write_json(canonical_lock_path(project_root), lock_payload)
    for legacy_path in legacy_state_files(project_root):
        legacy_path.unlink()


def install_or_update(args: argparse.Namespace, default_dry_run: bool) -> int:
    try:
        project_root, uproject_path = find_uproject(args.project)
        layout = discover_source_layout(args.source_root, args.mcp_source_root, args.asset_source_root)
        existing_config, _config_path = load_config(project_root)
        if not default_dry_run:
            existing_config = None
        plugins = list(existing_config.get("plugins", [])) if existing_config else parse_plugins(args.plugins)
        if not plugins:
            plugins = parse_plugins(args.plugins)
        profile = str(existing_config.get("profile", args.profile)) if existing_config else args.profile
        if profile not in PROFILE_DEFAULTS:
            raise BootstrapError(f"Unknown profile in {CONFIG_NAME}: {profile}")
        dry_run = args.dry_run or (default_dry_run and not args.apply)
        plan, intents, config, uproject_data = build_plan(
            project_root,
            uproject_path,
            layout,
            profile,
            plugins,
            args.force,
        )
        plan["command"] = args.command
        conflicts = [op for op in plan["operations"] if op.get("status") == "conflict"]
        plan["dryRun"] = dry_run
        plan["ok"] = not conflicts
        if conflicts:
            plan["message"] = "Conflicts detected; no files were written." if not dry_run else "Conflicts detected."
            emit_plan(plan, args.format)
            return 0 if dry_run else 2
        if not dry_run:
            if not args.no_backup:
                plan["backupId"] = create_backup(project_root, plan)
            apply_plan(project_root, uproject_path, intents, config, uproject_data, layout, profile, plugins)
            plan["message"] = "Install/update applied."
        else:
            plan["message"] = "Dry-run only; no files were written."
        emit_plan(plan, args.format)
        return 0
    except BootstrapError as exc:
        emit({"ok": False, "error": str(exc), "tool": TOOL_NAME})
        return 2


def check(condition: bool, name: str, detail: str = "") -> dict[str, Any]:
    return {"name": name, "ok": bool(condition), "detail": detail}


def doctor(args: argparse.Namespace) -> int:
    checks: list[dict[str, Any]] = []
    try:
        project_root, uproject_path = find_uproject(args.project)
    except BootstrapError as exc:
        emit({"ok": False, "tool": TOOL_NAME, "checks": [check(False, "project", str(exc))]})
        return 2

    config_data: dict[str, Any] | None = None
    lock_data: dict[str, Any] | None = None

    config_path = config_path_for_read(project_root)
    if config_path.exists():
        try:
            config_data = load_json(config_path)
            checks.append(check(config_data.get("schema") == "commonai-project-config-v1", "config-schema", relpath(config_path, project_root)))
        except BootstrapError as exc:
            checks.append(check(False, "config-json", str(exc)))
    else:
        checks.append(check(False, "config-present", f"Missing {CONFIG_NAME}"))

    lock_path = lock_path_for_read(project_root)
    if lock_path.exists():
        try:
            lock_data = load_json(lock_path)
            checks.append(check(lock_data.get("schema") == "commonai-lock-v1", "lock-schema", relpath(lock_path, project_root)))
        except BootstrapError as exc:
            checks.append(check(False, "lock-json", str(exc)))
    else:
        checks.append(check(False, "lock-present", f"Missing {LOCK_NAME}"))

    profile = str((config_data or {}).get("profile") or args.profile)
    if profile not in PROFILE_DEFAULTS:
        checks.append(check(False, "profile", f"Unknown profile: {profile}"))
        profile = "commonui"
    plugins = list((config_data or {}).get("plugins") or parse_plugins(args.plugins))
    effective_config = config_data if config_data else config_for(profile, plugins)

    checks.append(check(uproject_path.exists(), "uproject-present", relpath(uproject_path, project_root)))
    try:
        uproject_data = read_uproject(uproject_path)
        entries = {
            entry.get("Name"): entry
            for entry in uproject_data.get("Plugins", [])
            if isinstance(entry, dict)
        }
        for plugin_name in plugins:
            entry = entries.get(plugin_name)
            checks.append(check(bool(entry and entry.get("Enabled") is True), f"uproject-plugin-{plugin_name}", "enabled"))
    except BootstrapError as exc:
        checks.append(check(False, "uproject-json", str(exc)))

    for plugin_name in plugins:
        plugin_root = project_root / "Plugins" / plugin_name
        checks.append(check(plugin_root.is_dir(), f"plugin-dir-{plugin_name}", f"Plugins/{plugin_name}"))
        checks.append(check((plugin_root / f"{plugin_name}.uplugin").is_file(), f"plugin-descriptor-{plugin_name}", f"Plugins/{plugin_name}/{plugin_name}.uplugin"))

    expected_paths = [
        effective_config["uiWorkflowDir"],
        effective_config["uiSpecDir"],
        effective_config["assetPipelineSchemaDir"],
        effective_config["bootstrapToolPath"],
        effective_config["validatorPath"],
        STATE_GITIGNORE_NAME,
    ]
    for expected in expected_paths:
        path = project_root / expected
        checks.append(check(path.exists(), f"path-{expected}", expected))

    if lock_data:
        if args.strict:
            checks.append(check(lock_data.get("sourceManifest", {}).get("schema") == "commonai-source-manifest-v1", "source-manifest-schema", relpath(lock_path, project_root)))
        missing_managed = []
        changed_managed = []
        for item in lock_data.get("managedFiles", []):
            if not isinstance(item, dict) or not item.get("path") or not item.get("sha256"):
                continue
            path = project_root / str(item["path"])
            if not path.exists():
                missing_managed.append(str(item["path"]))
            elif sha256_file(path) != item["sha256"]:
                changed_managed.append(str(item["path"]))
        checks.append(check(not missing_managed, "managed-files-present", f"missing={len(missing_managed)}"))
        checks.append(check(not changed_managed, "managed-files-unchanged", f"changed={len(changed_managed)}"))

    if args.strict:
        checks.append(check(effective_config.get("productionMutationRequiresTSpec") is True, "policy-tspec-required", "productionMutationRequiresTSpec"))
        validator_path = project_root / str(effective_config["validatorPath"])
        if validator_path.is_file():
            completed = subprocess.run(
                [
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(validator_path),
                    "-Root",
                    str(project_root),
                    "-SpecDirectory",
                    str(effective_config["uiSpecDir"]),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            checks.append(check(completed.returncode == 0, "validate-tspecs", completed.stdout.strip() or completed.stderr.strip()))
        else:
            checks.append(check(False, "validate-tspecs", f"missing {validator_path}"))

    ok = all(item["ok"] for item in checks)
    emit({"ok": ok, "tool": TOOL_NAME, "projectRoot": str(project_root), "checks": checks})
    return 0 if ok else 1


def validate_tspecs(args: argparse.Namespace) -> int:
    try:
        project_root, _uproject_path = find_uproject(args.project)
        config_path = config_path_for_read(project_root)
        config_data = load_json(config_path) if config_path.exists() else config_for(args.profile, parse_plugins(args.plugins))
        spec_directory = args.spec_directory or str(config_data["uiSpecDir"])
        validator_path = project_root / str(config_data["validatorPath"])
        if not validator_path.is_file():
            raise BootstrapError(f"TSpec validator not found: {validator_path}")

        command = [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(validator_path),
            "-Root",
            str(project_root),
            "-SpecDirectory",
            spec_directory,
        ]
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=args.timeout_seconds,
        )
        payload = {
            "ok": completed.returncode == 0,
            "tool": TOOL_NAME,
            "command": "validate-tspecs",
            "projectRoot": str(project_root),
            "specDirectory": spec_directory,
            "returnCode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
        emit(payload)
        return 0 if completed.returncode == 0 else 1
    except subprocess.TimeoutExpired as exc:
        emit(
            {
                "ok": False,
                "tool": TOOL_NAME,
                "command": "validate-tspecs",
                "error": f"TSpec validator timed out after {exc.timeout} seconds.",
            }
        )
        return 2
    except FileNotFoundError as exc:
        emit(
            {
                "ok": False,
                "tool": TOOL_NAME,
                "command": "validate-tspecs",
                "error": f"PowerShell executable not found: {exc}",
            }
        )
        return 2
    except BootstrapError as exc:
        emit({"ok": False, "tool": TOOL_NAME, "command": "validate-tspecs", "error": str(exc)})
        return 2


def manifest(args: argparse.Namespace) -> int:
    try:
        layout = discover_source_layout(args.source_root, args.mcp_source_root, args.asset_source_root)
        plugins = parse_plugins(args.plugins)
        config = config_for(args.profile, plugins)
        emit({"ok": True, "tool": TOOL_NAME, "sourceRoot": str(layout.common_root), "manifest": build_source_manifest(layout, config, plugins)})
        return 0
    except BootstrapError as exc:
        emit({"ok": False, "tool": TOOL_NAME, "command": "manifest", "error": str(exc)})
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install/update CommonAI workflow files in a UE project.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--project", default=".", help="Target UE project root or .uproject path.")
        command.add_argument("--source-root", default=None, help="Source repo root containing both plugins, or the legacy monorepo root.")
        command.add_argument("--mcp-source-root", default=None, help="MCPToolkit repo/plugin root override.")
        command.add_argument("--asset-source-root", default=None, help="AIAssetPipeline repo/plugin root override.")
        command.add_argument("--profile", choices=sorted(PROFILE_DEFAULTS), default="commonui")
        command.add_argument("--plugins", default=",".join(DEFAULT_PLUGINS), help="Comma-separated plugin list.")
        command.add_argument("--force", action="store_true", help="Overwrite conflicting managed files.")
        command.add_argument("--format", choices=("json", "markdown"), default="json", help="Output format.")
        command.add_argument("--no-backup", action="store_true", help="Do not create a Tools/AIWorkflowBootstrap/state/backups entry before applying changes.")

    install_parser = subparsers.add_parser("install", help="Install workflow files into a project.")
    add_common(install_parser)
    install_parser.add_argument("--dry-run", action="store_true", help="Show the plan without writing files.")
    install_parser.add_argument("--apply", action="store_true", help=argparse.SUPPRESS)
    install_parser.set_defaults(func=lambda args: install_or_update(args, default_dry_run=False))

    update_parser = subparsers.add_parser("update", help="Update an existing install. Defaults to dry-run.")
    add_common(update_parser)
    update_parser.add_argument("--dry-run", action="store_true", help="Show the plan without writing files.")
    update_parser.add_argument("--apply", action="store_true", help="Apply the update plan.")
    update_parser.set_defaults(func=lambda args: install_or_update(args, default_dry_run=True))

    diff_parser = subparsers.add_parser("diff", help="Alias for update --dry-run.")
    add_common(diff_parser)
    diff_parser.add_argument("--dry-run", action="store_true", default=True)
    diff_parser.add_argument("--apply", action="store_true", default=False, help=argparse.SUPPRESS)
    diff_parser.set_defaults(func=lambda args: install_or_update(args, default_dry_run=True))

    doctor_parser = subparsers.add_parser("doctor", help="Validate a project install.")
    doctor_parser.add_argument("--project", default=".", help="Target UE project root or .uproject path.")
    doctor_parser.add_argument("--profile", choices=sorted(PROFILE_DEFAULTS), default="commonui")
    doctor_parser.add_argument("--plugins", default=",".join(DEFAULT_PLUGINS), help="Comma-separated plugin list.")
    doctor_parser.add_argument("--strict", action="store_true", help="Run validator and stricter lock/source checks.")
    doctor_parser.set_defaults(func=doctor)

    rollback_parser = subparsers.add_parser("rollback", help="Restore files from a bootstrap state backup.")
    rollback_parser.add_argument("--project", default=".", help="Target UE project root or .uproject path.")
    rollback_parser.add_argument("--backup", default="latest", help="Backup id or 'latest'.")
    rollback_parser.set_defaults(func=rollback)

    validate_parser = subparsers.add_parser("validate-tspecs", help="Run the installed TSpec validator.")
    validate_parser.add_argument("--project", default=".", help="Target UE project root or .uproject path.")
    validate_parser.add_argument("--profile", choices=sorted(PROFILE_DEFAULTS), default="commonui")
    validate_parser.add_argument("--plugins", default=",".join(DEFAULT_PLUGINS), help="Comma-separated plugin list.")
    validate_parser.add_argument("--spec-directory", default=None, help="Override config uiSpecDir.")
    validate_parser.add_argument("--timeout-seconds", type=int, default=60)
    validate_parser.set_defaults(func=validate_tspecs)

    manifest_parser = subparsers.add_parser("manifest", help="Print a deterministic source distribution manifest.")
    manifest_parser.add_argument("--source-root", default=None, help="Source repo root containing plugins and workflow files.")
    manifest_parser.add_argument("--mcp-source-root", default=None, help="MCPToolkit repo/plugin root override.")
    manifest_parser.add_argument("--asset-source-root", default=None, help="AIAssetPipeline repo/plugin root override.")
    manifest_parser.add_argument("--profile", choices=sorted(PROFILE_DEFAULTS), default="commonui")
    manifest_parser.add_argument("--plugins", default=",".join(DEFAULT_PLUGINS), help="Comma-separated plugin list.")
    manifest_parser.set_defaults(func=manifest)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
