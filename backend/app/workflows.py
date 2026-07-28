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


def _same_link(val: Any, link: Any) -> bool:
    return (
        isinstance(val, list)
        and len(val) == 2
        and isinstance(link, list)
        and len(link) == 2
        and str(val[0]) == str(link[0])
        and val[1] == link[1]
    )


def _rewire_link(graph: dict[str, Any], old: Any, new: list[Any], skip: set[str]) -> None:
    """Repoint every input carrying link `old` at `new`, except inside `skip` nodes."""
    if old is None:
        return
    for node_id, node in graph.items():
        if node_id in skip:
            continue
        for field, val in (node.get("inputs") or {}).items():
            if _same_link(val, old):
                node["inputs"][field] = list(new)


def apply_lora_stack(
    graph: dict[str, Any], manifest: dict[str, Any], stack: list[dict[str, Any]]
) -> dict[str, Any]:
    """Splice a chain of `LoraLoader` nodes into a workflow that declares `lora_chain`.

    One node per stack entry, model+CLIP threaded through in order, and every downstream
    consumer repointed at the chain tail. Two shapes, because the two workflows differ:

    - **anchor** (`{"node": "13"}`) — the template already has a loader; entry 0 reuses it
      and the rest are clones. Used by `base-character-lora`, whose loader exists so the
      no-stack case still renders.
    - **inject** (`{"model_source": [...], "clip_source": [...]}`) — the template has no
      loader to spare; the whole chain is created here. Lets `pose-with-lora` (whose own
      character LoRA is model-only, with CLIP straight off the checkpoint) gain a full
      model+CLIP concept stack without a second template file, and leaves the graph
      untouched when the stack is empty.

    Deliberately built from **core `LoraLoader` nodes only** — a "power LoRA loader"
    custom node would be tidier, but `custom_nodes` is read-only over SMB on UR1, so a
    workflow that needs nothing installed is worth the extra wiring here.
    """
    chain = manifest.get("lora_chain")
    if not chain or not stack:
        return graph

    anchor = str(chain["node"]) if chain.get("node") else ""
    if anchor:
        if anchor not in graph:
            raise WorkflowError(f"lora_chain points at node '{anchor}' which is not in the template")
        base = graph[anchor]
        class_type = base["class_type"]
        model_src = (base.get("inputs") or {}).get("model")
        clip_src = (base.get("inputs") or {}).get("clip")
        # consumers link to the anchor's own outputs
        old_model, old_clip = [anchor, 0], [anchor, 1]
        node_ids = [anchor]
        start = 1
    else:
        class_type = chain.get("class_type", "LoraLoader")
        model_src = chain.get("model_source")
        clip_src = chain.get("clip_source")
        if model_src is None or clip_src is None:
            raise WorkflowError("lora_chain needs either 'node' or both 'model_source' and 'clip_source'")
        # consumers currently link to whatever fed the chain; they move to the tail
        old_model, old_clip = model_src, clip_src
        node_ids = []
        start = 0

    prefix = chain.get("id_prefix") or (f"{anchor}s" if anchor else "loraX")
    for i in range(start, len(stack)):
        new_id = f"{prefix}{i}"
        if new_id in graph:
            raise WorkflowError(f"lora chain node id '{new_id}' collides with the template")
        graph[new_id] = {"class_type": class_type, "inputs": {}}
        node_ids.append(new_id)

    # Repoint downstream consumers at the tail *before* the chain links itself up, so the
    # chain's own internal links aren't caught by the rewire.
    tail = node_ids[-1]
    skip = set(node_ids)
    _rewire_link(graph, old_model, [tail, 0], skip)
    _rewire_link(graph, old_clip, [tail, 1], skip)

    prev_model, prev_clip = model_src, clip_src
    for node_id, entry in zip(node_ids, stack):
        inputs = graph[node_id].setdefault("inputs", {})
        inputs["model"] = prev_model
        inputs["clip"] = prev_clip
        inputs["lora_name"] = entry["lora_name"]
        inputs["strength_model"] = float(entry.get("strength_model", 1.0))
        inputs["strength_clip"] = float(entry.get("strength_clip", 1.0))
        prev_model, prev_clip = [node_id, 0], [node_id, 1]
    return graph


def apply_controlnet(
    graph: dict[str, Any], manifest: dict[str, Any], cfg: dict[str, Any]
) -> dict[str, Any]:
    """Splice OpenPose ControlNet into a workflow that declares `controlnet` (Phase H3).

    Three nodes — `LoadImage` (the skeleton), `ControlNetLoader`, `ControlNetApplyAdvanced`
    — inserted between the text encoders and whatever consumes their conditioning. The
    manifest names the two conditioning links; every consumer of them is repointed at the
    apply node's paired outputs, reusing the same `_rewire_link` machinery the LoRA chain
    uses.

    Spliced rather than shipped as a second template family because ControlNet touches
    **conditioning only** while the LoRA chain touches **model/CLIP only** — they are
    orthogonal, so one splice serves `pose-with-lora`, `base-character` and (Phase H3c)
    the dataset graphs without any of them gaining a variant file.

    A union ControlNet additionally needs `SetUnionControlNetType`; `kind` carries it.
    """
    spec = manifest.get("controlnet")
    if not spec or not cfg or not cfg.get("skeleton") or not cfg.get("controlnet_name"):
        return graph

    pos_src, neg_src = spec.get("positive"), spec.get("negative")
    if not pos_src or not neg_src:
        raise WorkflowError("controlnet manifest block needs both 'positive' and 'negative'")

    prefix = spec.get("id_prefix") or "cn"
    img_id, load_id, apply_id = f"{prefix}_img", f"{prefix}_load", f"{prefix}_apply"
    union_id = f"{prefix}_union"
    for nid in (img_id, load_id, apply_id, union_id):
        if nid in graph:
            raise WorkflowError(f"controlnet node id '{nid}' collides with the template")

    graph[img_id] = {"class_type": "LoadImage", "inputs": {"image": cfg["skeleton"]}}
    graph[load_id] = {"class_type": "ControlNetLoader",
                      "inputs": {"control_net_name": cfg["controlnet_name"]}}
    control_src: list[Any] = [load_id, 0]

    # A union model is one file covering many control types and has to be told which.
    if (cfg.get("kind") or "").lower() == "union":
        graph[union_id] = {"class_type": "SetUnionControlNetType",
                           "inputs": {"control_net": [load_id, 0], "type": "openpose"}}
        control_src = [union_id, 0]

    graph[apply_id] = {
        "class_type": "ControlNetApplyAdvanced",
        "inputs": {
            "positive": list(pos_src),
            "negative": list(neg_src),
            "control_net": control_src,
            "image": [img_id, 0],
            "strength": float(cfg.get("strength", 0.7)),
            "start_percent": float(cfg.get("start_percent", 0.0)),
            "end_percent": float(cfg.get("end_percent", 0.7)),
        },
    }

    skip = {img_id, load_id, apply_id, union_id}
    _rewire_link(graph, pos_src, [apply_id, 0], skip)
    _rewire_link(graph, neg_src, [apply_id, 1], skip)
    return graph


def bypass_optional_lora(graph: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """Remove a template's LoRA loader and wire its consumers to the raw model.

    Templates carry a character-LoRA loader because that is the normal case, but a persona
    with no trained LoRA still has to render. An empty `lora_name` is not an option —
    ComfyUI rejects a COMBO value that isn't in the list — so the node has to go. The
    manifest names it (`lora_optional`) rather than the caller hardcoding a node id.
    """
    spec = manifest.get("lora_optional")
    if not spec:
        raise WorkflowError(f"'{manifest.get('id')}' does not declare lora_optional")
    node_id = str(spec["node"])
    if node_id not in graph:
        return graph
    _rewire_link(graph, [node_id, 0], list(spec["fallback_source"]), {node_id})
    graph.pop(node_id, None)
    return graph


def build_graph(
    workflow_id: str,
    params: dict[str, Any],
    lora_stack: list[dict[str, Any]] | None = None,
    controlnet: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load the template and patch the requested parameters into it.

    `lora_stack` (Phase H1b) overlays N LoRAs on workflows declaring `lora_chain`; it
    supersedes the single-LoRA `lora_name`/`lora_strength_*` params, which stay for the
    one-LoRA case and older callers.

    `controlnet` (Phase H3) splices OpenPose conditioning into workflows declaring
    `controlnet`. The two are independent — LoRAs touch model/CLIP, ControlNet touches
    conditioning — so a graph can carry both.
    """
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

    if lora_stack:
        apply_lora_stack(graph, manifest, lora_stack)
    if controlnet:
        apply_controlnet(graph, manifest, controlnet)

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
