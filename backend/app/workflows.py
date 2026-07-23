"""Workflow templates + parameter manifests.

Workflows are stored as API-format ComfyUI JSON. Rather than hardcoding node IDs in
application code (brittle — IDs shift whenever a workflow is edited), each template
ships a manifest mapping friendly parameter names to a node id + input field. The UI
can then generate its controls straight from the manifest.

See PROJECT_PLAN 5.2.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Resolves in both layouts: the container (/app/app/... -> /app/workflows) and the
# repo checkout (backend/app/... -> <repo>/workflows). Override with WORKFLOW_DIR.
_HERE = Path(__file__).resolve()
_CANDIDATES = [
    _HERE.parent.parent / "workflows",          # container: /app/workflows
    _HERE.parent.parent.parent / "workflows",   # repo: <root>/workflows
]
WORKFLOW_DIR = Path(os.getenv("WORKFLOW_DIR")) if os.getenv("WORKFLOW_DIR") else next(
    (c for c in _CANDIDATES if c.is_dir()), _CANDIDATES[0]
)


class WorkflowError(RuntimeError):
    pass


def _manifest_paths() -> list[Path]:
    if not WORKFLOW_DIR.is_dir():
        return []
    return sorted(WORKFLOW_DIR.glob("*.manifest.json"))


def list_manifests() -> list[dict[str, Any]]:
    out = []
    for p in _manifest_paths():
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
            m["_manifest_file"] = p.name
            m["_available"] = (WORKFLOW_DIR / m.get("file", "")).is_file()
            out.append(m)
        except json.JSONDecodeError as exc:
            out.append({"id": p.stem, "error": f"invalid manifest JSON: {exc}"})
    return out


def get_manifest(workflow_id: str) -> dict[str, Any]:
    for m in list_manifests():
        if m.get("id") == workflow_id:
            return m
    raise WorkflowError(f"unknown workflow '{workflow_id}'")


def build_graph(workflow_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Load the template and patch the requested parameters into it."""
    manifest = get_manifest(workflow_id)
    template_path = WORKFLOW_DIR / manifest["file"]
    if not template_path.is_file():
        raise WorkflowError(f"template file missing: {manifest['file']}")

    graph = json.loads(template_path.read_text(encoding="utf-8"))
    spec: dict[str, Any] = manifest.get("params", {})

    unknown = set(params) - set(spec)
    if unknown:
        raise WorkflowError(f"unknown parameter(s) for '{workflow_id}': {sorted(unknown)}")

    for name, value in params.items():
        if value is None:
            continue
        target = spec[name]
        node_id, field = str(target["node"]), target["input"]
        if node_id not in graph:
            raise WorkflowError(
                f"manifest for '{workflow_id}' points at node '{node_id}' which is not in the template"
            )
        graph[node_id].setdefault("inputs", {})[field] = value

    # strip UI-only metadata before submitting
    for node in graph.values():
        node.pop("_meta", None)
    return graph


def defaults_for(workflow_id: str) -> dict[str, Any]:
    """Read current template values for each declared parameter."""
    manifest = get_manifest(workflow_id)
    template_path = WORKFLOW_DIR / manifest["file"]
    if not template_path.is_file():
        return {}
    graph = json.loads(template_path.read_text(encoding="utf-8"))
    out: dict[str, Any] = {}
    for name, target in manifest.get("params", {}).items():
        node = graph.get(str(target["node"]), {})
        val = (node.get("inputs") or {}).get(target["input"])
        if not isinstance(val, list):  # skip node links
            out[name] = val
    return out


def validate_manifest(workflow_id: str) -> list[str]:
    """Check every manifest pointer resolves — catches drift after a workflow edit."""
    problems: list[str] = []
    manifest = get_manifest(workflow_id)
    template_path = WORKFLOW_DIR / manifest["file"]
    if not template_path.is_file():
        return [f"template file missing: {manifest['file']}"]
    graph = json.loads(template_path.read_text(encoding="utf-8"))
    for name, target in manifest.get("params", {}).items():
        node_id, field = str(target["node"]), target["input"]
        if node_id not in graph:
            problems.append(f"param '{name}' -> node '{node_id}' does not exist")
        elif field not in (graph[node_id].get("inputs") or {}):
            problems.append(f"param '{name}' -> node '{node_id}' has no input '{field}'")
    out_node = str(manifest.get("output_node", ""))
    if out_node and out_node not in graph:
        problems.append(f"output_node '{out_node}' does not exist")
    return problems
