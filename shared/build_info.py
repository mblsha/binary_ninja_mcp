"""Versioned capability diagnostics and import-time source snapshots."""

import hashlib
from pathlib import Path


TOOL_VERSION = "0.2.8"
CAPABILITY_PROTOCOL_VERSION = 1
REQUIRED_CAPABILITIES = {
    "annotation_edits_version": 1,
    "analysis_reads_version": 1,
    "builtin_mutations_version": 1,
    "signature_workflow_version": 2,
    "python_serialization_version": 2,
    "analysis_skip_guard_version": 1,
}


def _digest(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def snapshot_source(path):
    """Call at module import, never at request time, to identify loaded source."""
    return {"path": str(path), "sha256": _digest(path)}


def source_diagnostics(snapshots):
    sources = {}
    changed = []
    unverifiable = []
    for name, snapshot in snapshots.items():
        snapshot = snapshot or {}
        loaded = snapshot.get("sha256")
        current = _digest(snapshot["path"]) if snapshot.get("path") else None
        sources[name] = {"loaded_sha256": loaded, "disk_sha256": current}
        if loaded is None or current is None:
            unverifiable.append(name)
        elif loaded != current:
            changed.append(name)
    return {
        "build_id": hashlib.sha256(
            "\n".join(
                f"{name}:{item['loaded_sha256']}" for name, item in sorted(sources.items())
            ).encode()
        ).hexdigest()[:16],
        "sources": sources,
        "reload_required": bool(changed),
        "changed_modules": changed,
        "unverifiable_modules": unverifiable,
    }


def assess_compatibility(metadata):
    protocol = metadata.get("capability_protocol_version")
    warnings = []
    if protocol is None:
        warnings.append(
            "Loaded server predates capability diagnostics; reload the plugin to use new commands."
        )
    elif protocol != CAPABILITY_PROTOCOL_VERSION:
        warnings.append(
            f"Capability protocol mismatch: client={CAPABILITY_PROTOCOL_VERSION}, server={protocol}; use matching client/plugin versions."
        )
    else:
        capabilities = metadata.get("capabilities", {})
        for name, required in REQUIRED_CAPABILITIES.items():
            value = capabilities.get(name)
            if not isinstance(value, int) or value < required:
                warnings.append(
                    f"Missing capability {name}>={required}; loaded server reports {value!r}. Reload matching plugin code."
                )
    runtime = metadata.get("runtime", {})
    if runtime.get("reload_required"):
        warnings.append(
            "Server source changed after import; reload the plugin before testing new behavior. Restarting only its HTTP listener is insufficient."
        )
    if runtime.get("unverifiable_modules"):
        warnings.append(
            "Some loaded modules cannot be verified: " + ", ".join(runtime["unverifiable_modules"])
        )
    return {"compatible": not warnings, "warnings": warnings}


LOADED_SOURCE = snapshot_source(__file__)
