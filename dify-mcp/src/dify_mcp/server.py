"""MCP tools for Dify-native natural-language generation and guarded draft writes."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP

from .artifacts import PreviewStore
from .browser_bridge import (
    BRIDGE_PROTOCOL_VERSION,
    bridge_projection,
    bridge_projection_fingerprint,
    validate_bridge_operations,
    validate_protected_sections_sha256,
    validate_sha256,
)
from .client import DifyAPIError, DifyClient
from .config import Settings
from .dsl import (
    GRAPH_MODES,
    compose_document,
    dump_yaml,
    graph_fingerprint,
    graph_summary,
    make_portable_document,
    validate_document,
    validate_yaml,
)
from .patching import apply_workflow_patch, workflow_fingerprint

mcp = FastMCP(
    "dify-workflow-mcp",
    instructions=(
        "Generate Dify 1.16.1 / App DSL 0.7.0 workflow drafts. Generation is read-only. "
        "When direct credentials are absent, default to the credential-free browser bridge: use an "
        "already signed-in exact-origin page for Dify GET/POST, never export cookies, storage, or "
        "authorization headers, and use the workflow_browser_bridge_patch_* tools to prepare and "
        "verify the patch. Browser bridge v2 requires page-computed raw projection hashes and "
        "page-local field patching; never replace the page draft with a transported full graph. "
        "Use workflow_patch_draft_preview for deterministic field-level changes that preserve every "
        "unspecified field. Call an explicit *_apply tool with the returned preview_id to create or "
        "update a draft. "
        "Publishing is intentionally not supported."
    ),
)


@dataclass(slots=True)
class Runtime:
    settings: Settings
    client: DifyClient
    previews: PreviewStore


_runtime_instance: Runtime | None = None


def _runtime() -> Runtime:
    global _runtime_instance
    if _runtime_instance is None:
        settings = Settings.from_env()
        _runtime_instance = Runtime(
            settings=settings,
            client=DifyClient(settings),
            previews=PreviewStore(ttl_seconds=settings.preview_ttl_seconds),
        )
    return _runtime_instance


def _without_http_marker(value: dict[str, Any]) -> dict[str, Any]:
    return {key: child for key, child in value.items() if key != "_http_status"}


def _generator_failure(response: dict[str, Any]) -> dict[str, Any] | None:
    error = response.get("error")
    errors = response.get("errors")
    graph = response.get("graph")
    if error or errors or not isinstance(graph, dict):
        return {
            "status": "generation_failed",
            "error": str(error or "Dify generator did not return a graph"),
            "errors": errors if isinstance(errors, list) else [],
        }
    return None


def _existing_context(app_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime = _runtime()
    app = runtime.client.get_app(app_id)
    draft = runtime.client.get_draft(app_id)
    mode = app.get("mode")
    if mode not in GRAPH_MODES:
        raise ValueError(f"App {app_id} mode {mode!r} cannot be edited as a workflow graph")
    if not isinstance(draft.get("graph"), dict) or not isinstance(draft.get("hash"), str):
        raise TypeError(f"App {app_id} has no usable draft workflow")
    return app, draft


def _draft_workflow_payload(draft: dict[str, Any]) -> dict[str, Any]:
    graph = draft.get("graph")
    features = draft.get("features")
    environment_variables = draft.get("environment_variables")
    conversation_variables = draft.get("conversation_variables")
    if not isinstance(graph, dict):
        raise TypeError("Draft graph must be an object")
    if not isinstance(features, dict):
        raise TypeError("Draft features must be an object")
    if not isinstance(environment_variables, list):
        raise TypeError("Draft environment_variables must be a list")
    if not isinstance(conversation_variables, list):
        raise TypeError("Draft conversation_variables must be a list")
    return {
        "graph": copy.deepcopy(graph),
        "features": copy.deepcopy(features),
        "environment_variables": copy.deepcopy(environment_variables),
        "conversation_variables": copy.deepcopy(conversation_variables),
    }


def _document_from_existing(app: dict[str, Any], workflow: dict[str, Any]) -> dict[str, Any]:
    document = compose_document(
        graph=workflow["graph"],
        mode=str(app["mode"]),
        app_name=str(app.get("name") or "Existing Workflow"),
        description=str(app.get("description") or ""),
        icon=str(app.get("icon") or "🧩"),
        icon_type=str(app.get("icon_type") or "emoji"),
        icon_background=str(app.get("icon_background") or "#E4FBCC"),
        use_icon_as_answer_icon=bool(app.get("use_icon_as_answer_icon", False)),
        features=workflow["features"],
        environment_variables=workflow["environment_variables"],
        conversation_variables=workflow["conversation_variables"],
        dependencies=[],
    )
    # compose_document supplies portable defaults. The online patch must retain
    # the exact server payload, including key absence/order and secret values.
    document["workflow"] = copy.deepcopy(workflow)
    return document


def _patch_inventory(workflow: dict[str, Any]) -> dict[str, Any]:
    graph = workflow["graph"]
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    def selector(value: Any) -> str:
        return str(value).replace("~", "~0").replace("/", "~1")

    return {
        "nodes": [
            {
                "id": node.get("id"),
                "type": node.get("data", {}).get("type") if isinstance(node.get("data"), dict) else None,
                "title": node.get("data", {}).get("title") if isinstance(node.get("data"), dict) else None,
                "patch_path": f"/graph/nodes/@{selector(node.get('id'))}",
            }
            for node in nodes
            if isinstance(node, dict) and isinstance(node.get("id"), str)
        ],
        "edges": [
            {
                "id": edge.get("id"),
                "source": edge.get("source"),
                "target": edge.get("target"),
                "patch_path": f"/graph/edges/@{selector(edge.get('id'))}",
            }
            for edge in edges
            if isinstance(edge, dict) and isinstance(edge.get("id"), str)
        ],
    }


@mcp.tool()
def dify_connection_check() -> dict[str, Any]:
    """Check the configured Dify console connection without returning credentials."""

    runtime = _runtime()
    if not runtime.settings.direct_auth_configured:
        return {
            "connected": False,
            "target": runtime.settings.console_api_url,
            "auth_mode": "browser_bridge",
            "browser_bridge_protocol_version": BRIDGE_PROTOCOL_VERSION,
            "browser_bridge_required": True,
            "message": (
                "Open the matching Dify origin in a signed-in browser page and use the "
                "workflow_browser_bridge_patch_* tools. Credentials must remain in page context."
            ),
        }
    response = runtime.client.connection_check()
    data = response.get("data")
    return {
        "connected": True,
        "target": runtime.settings.console_api_url,
        "auth_mode": runtime.settings.auth_mode,
        "visible_app_count_in_page": len(data) if isinstance(data, list) else None,
    }


@mcp.tool()
def workflow_validate(dsl_yaml: str) -> dict[str, Any]:
    """Statically validate a complete Dify App DSL 0.7.0 YAML document."""

    document, report = validate_yaml(dsl_yaml)
    result = report.as_dict()
    if isinstance(document, dict):
        result["graph"] = graph_summary(document)
    return result


@mcp.tool()
def workflow_get_draft(app_id: str) -> dict[str, Any]:
    """Read a workflow/Chatflow draft summary and its optimistic-concurrency hash."""

    app, draft = _existing_context(app_id)
    workflow = _draft_workflow_payload(draft)
    document = _document_from_existing(app, workflow)
    return {
        "app_id": app_id,
        "draft_hash": draft["hash"],
        "draft_version": draft.get("version"),
        "graph": graph_summary(make_portable_document(document)),
        "patch_inventory": _patch_inventory(workflow),
        "published": bool(app.get("status") == "normal" and draft.get("tool_published")),
    }


@mcp.tool()
def workflow_patch_draft_preview(
    app_id: str,
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Preview deterministic draft changes without writing to Dify.

    Paths are rooted at the draft workflow payload. Use stable id selectors such
    as ``/graph/nodes/@node-id/data/title`` or standard JSON Pointer indices.
    Supported operations are add, replace, remove, and test. Secret values and
    whole-section replacement/removal are rejected.
    """

    runtime = _runtime()
    app, draft = _existing_context(app_id)
    base_workflow = _draft_workflow_payload(draft)
    patch = apply_workflow_patch(base_workflow, operations)
    if workflow_fingerprint(base_workflow) == workflow_fingerprint(patch.workflow):
        raise ValueError("Patch does not change the draft")

    document = _document_from_existing(app, patch.workflow)
    portable_document = make_portable_document(document)
    validation = validate_document(portable_document).as_dict()
    base_fingerprint = workflow_fingerprint(base_workflow)
    patched_fingerprint = workflow_fingerprint(patch.workflow)
    artifact = runtime.previews.put(
        document,
        target_app_id=app_id,
        base_hash=str(draft["hash"]),
        metadata={
            "kind": "workflow_patch",
            "base_workflow": base_workflow,
            "operations": patch.operations,
            "base_workflow_sha256": base_fingerprint,
            "patched_workflow_sha256": patched_fingerprint,
        },
    )
    return {
        "status": "patch_preview_created",
        "preview_id": artifact.preview_id,
        "target_app_id": app_id,
        "base_hash": draft["hash"],
        "base_workflow_sha256": base_fingerprint,
        "patched_workflow_sha256": patched_fingerprint,
        "changes": patch.change_summary(),
        "invariants": {
            "unspecified_fields_unchanged": patch.round_trip_verified,
            "base_snapshot_pinned": True,
            "optimistic_hash_required": True,
        },
        "graph": graph_summary(portable_document),
        "validation": validation,
        "write_performed": False,
    }


@mcp.tool()
def workflow_browser_bridge_patch_preview(
    app_id: str,
    draft_hash: str,
    mode: str,
    graph: dict[str, Any],
    features: dict[str, Any],
    raw_projection_sha256: str,
    protected_sections_sha256: str,
    operations: list[dict[str, Any]],
    app_name: str = "Browser Bridge Workflow",
) -> dict[str, Any]:
    """Preview a patch from a safe browser-fetched projection without Dify credentials.

    The browser computes raw_projection_sha256 before graph/features cross the
    browser transport boundary. It must keep protected variables in page context
    and provide only their canonical SHA-256 digest. Operations are limited to
    /graph and /features. This tool never performs a network write.
    """

    if mode not in GRAPH_MODES:
        raise ValueError(f"mode must be one of {sorted(GRAPH_MODES)!r}")
    if not isinstance(draft_hash, str) or not draft_hash:
        raise ValueError("draft_hash must be a non-empty string")
    raw_projection_hash = validate_sha256(raw_projection_sha256, "raw_projection_sha256")
    protected_hash = validate_protected_sections_sha256(protected_sections_sha256)
    validate_bridge_operations(operations)
    base_projection = bridge_projection(graph, features)
    patch = apply_workflow_patch(base_projection, operations)
    if workflow_fingerprint(base_projection) == workflow_fingerprint(patch.workflow):
        raise ValueError("Patch does not change the draft projection")

    document = compose_document(
        graph=patch.workflow["graph"],
        mode=mode,
        app_name=app_name,
        features=patch.workflow["features"],
        environment_variables=[],
        conversation_variables=[],
        dependencies=[],
    )
    validation = validate_document(make_portable_document(document)).as_dict()
    base_fingerprint = bridge_projection_fingerprint(base_projection)
    patched_fingerprint = bridge_projection_fingerprint(patch.workflow)
    runtime = _runtime()
    artifact = runtime.previews.put(
        document,
        target_app_id=app_id,
        base_hash=draft_hash,
        metadata={
            "kind": "browser_bridge_patch",
            "base_projection": base_projection,
            "operations": patch.operations,
            "base_projection_sha256": base_fingerprint,
            "patched_projection_sha256": patched_fingerprint,
            "raw_base_projection_sha256": raw_projection_hash,
            "protected_sections_sha256": protected_hash,
        },
    )
    return {
        "status": "browser_bridge_patch_preview_created",
        "browser_bridge_protocol_version": BRIDGE_PROTOCOL_VERSION,
        "preview_id": artifact.preview_id,
        "target_app_id": app_id,
        "base_hash": draft_hash,
        "base_projection_sha256": base_fingerprint,
        "patched_projection_sha256": patched_fingerprint,
        "raw_base_projection_sha256": raw_projection_hash,
        "protected_sections_sha256": protected_hash,
        "changes": patch.change_summary(),
        "invariants": {
            "unspecified_projected_fields_unchanged": patch.round_trip_verified,
            "protected_sections_opaque": True,
            "credentials_remain_in_browser": True,
            "base_snapshot_pinned": True,
            "raw_page_snapshot_pinned": True,
            "page_local_field_patch_required": True,
        },
        "graph": graph_summary(make_portable_document(document)),
        "validation": validation,
        "write_performed": False,
    }


@mcp.tool()
def workflow_browser_bridge_patch_apply_prepare(
    preview_id: str,
    app_id: str,
    expected_hash: str,
    current_hash: str,
    current_graph: dict[str, Any],
    current_features: dict[str, Any],
    current_raw_projection_sha256: str,
    current_protected_sections_sha256: str,
) -> dict[str, Any]:
    """Lock a bridge preview and return page-local field patch operations.

    The caller must fetch the latest draft in the signed-in page immediately
    before this call. The page must apply the returned operations to that exact
    in-page object; replacing graph/features with a transported snapshot is
    forbidden because transport serialization can change floating-point values.
    """

    runtime = _runtime()
    artifact = runtime.previews.get(preview_id)
    if artifact.metadata.get("kind") != "browser_bridge_patch":
        raise ValueError("preview_id is not a browser bridge patch preview")
    if artifact.target_app_id != app_id:
        raise ValueError("preview_id was not generated for this app_id")
    if not artifact.base_hash or expected_hash != artifact.base_hash:
        raise ValueError("expected_hash must exactly match the base_hash returned with the preview")
    if current_hash != expected_hash:
        return {
            "status": "conflict",
            "message": "The Dify draft hash changed after preview generation; create a new preview.",
            "expected_hash": expected_hash,
            "current_hash": current_hash,
            "draft_synced": False,
            "write_performed": False,
        }

    current_raw_hash = validate_sha256(
        current_raw_projection_sha256, "current_raw_projection_sha256"
    )
    protected_hash = validate_protected_sections_sha256(current_protected_sections_sha256)
    current_projection = bridge_projection(current_graph, current_features)
    base_projection = artifact.metadata.get("base_projection")
    operations = artifact.metadata.get("operations")
    if not isinstance(base_projection, dict) or not isinstance(operations, list):
        raise ValueError("Browser bridge preview metadata is incomplete")
    current_fingerprint = bridge_projection_fingerprint(current_projection)
    if (
        current_fingerprint != artifact.metadata.get("base_projection_sha256")
        or current_raw_hash != artifact.metadata.get("raw_base_projection_sha256")
        or protected_hash != artifact.metadata.get("protected_sections_sha256")
    ):
        return {
            "status": "conflict",
            "message": "The browser-fetched draft changed after preview generation; create a new preview.",
            "expected_hash": expected_hash,
            "draft_synced": False,
            "write_performed": False,
        }

    replayed = apply_workflow_patch(base_projection, operations)
    patched_projection = {
        "graph": copy.deepcopy(artifact.document["workflow"]["graph"]),
        "features": copy.deepcopy(artifact.document["workflow"]["features"]),
    }
    patched_fingerprint = bridge_projection_fingerprint(patched_projection)
    if (
        patched_fingerprint != artifact.metadata.get("patched_projection_sha256")
        or bridge_projection_fingerprint(replayed.workflow) != patched_fingerprint
        or not replayed.round_trip_verified
    ):
        raise ValueError("Browser bridge preview failed deterministic replay verification")

    return {
        "status": "browser_bridge_apply_prepared",
        "browser_bridge_protocol_version": BRIDGE_PROTOCOL_VERSION,
        "app_id": app_id,
        "expected_hash": expected_hash,
        "page_patch": {
            "method": "POST",
            "path": f"/console/api/apps/{app_id}/workflows/draft",
            "field_patch_only": True,
            "supported_operations": ["add", "replace", "remove", "test"],
            "path_semantics": (
                "JSON Pointer rooted at graph/features; list segments support numeric indexes, "
                "final '-' append, and stable '@<id>' object selectors."
            ),
            "expected_draft_hash": expected_hash,
            "expected_raw_projection_sha256": current_raw_hash,
            "expected_protected_sections_sha256": protected_hash,
            "operations": copy.deepcopy(replayed.operations),
            "allowed_roots": sorted(["graph", "features"]),
            "execution_contract": [
                "Fetch the latest draft inside the signed-in exact-origin page.",
                "Require draft hash, raw projection SHA-256, and protected-sections SHA-256 to match.",
                "Apply test operations first and abort without POST on any failure.",
                "Apply only these operations to the in-page graph/features object; never replace it with a transported snapshot.",
                "Compute expected_raw_patched_projection_sha256 before POST.",
                "POST patched graph/features plus untouched protected sections and expected hash.",
                "GET readback and compute readback_raw_projection_sha256 for MCP verification.",
            ],
        },
        "raw_base_projection_sha256": current_raw_hash,
        "patched_projection_sha256": patched_fingerprint,
        "protected_sections_sha256": protected_hash,
        "changes": replayed.change_summary(),
        "write_performed": False,
        "browser_write_required": True,
    }


@mcp.tool()
def workflow_browser_bridge_patch_verify(
    preview_id: str,
    app_id: str,
    readback_graph: dict[str, Any],
    readback_features: dict[str, Any],
    readback_protected_sections_sha256: str,
    expected_raw_patched_projection_sha256: str,
    readback_raw_projection_sha256: str,
    readback_hash: str,
) -> dict[str, Any]:
    """Verify exact page-context readback and protected-section preservation."""

    runtime = _runtime()
    artifact = runtime.previews.get(preview_id)
    if artifact.metadata.get("kind") != "browser_bridge_patch":
        raise ValueError("preview_id is not a browser bridge patch preview")
    if artifact.target_app_id != app_id:
        raise ValueError("preview_id was not generated for this app_id")
    projection = bridge_projection(readback_graph, readback_features)
    projection_fingerprint = bridge_projection_fingerprint(projection)
    protected_hash = validate_protected_sections_sha256(readback_protected_sections_sha256)
    expected_raw_patched_hash = validate_sha256(
        expected_raw_patched_projection_sha256,
        "expected_raw_patched_projection_sha256",
    )
    readback_raw_hash = validate_sha256(
        readback_raw_projection_sha256, "readback_raw_projection_sha256"
    )
    projection_match = projection_fingerprint == artifact.metadata.get("patched_projection_sha256")
    protected_match = protected_hash == artifact.metadata.get("protected_sections_sha256")
    raw_projection_match = (
        expected_raw_patched_hash == readback_raw_hash
        and expected_raw_patched_hash != artifact.metadata.get("raw_base_projection_sha256")
    )
    verified = projection_match and protected_match and raw_projection_match
    if verified:
        runtime.previews.delete(preview_id)
    return {
        "status": "browser_bridge_verified" if verified else "browser_bridge_readback_mismatch",
        "browser_bridge_protocol_version": BRIDGE_PROTOCOL_VERSION,
        "app_id": app_id,
        "previous_hash": artifact.base_hash,
        "draft_hash": readback_hash,
        "patched_projection_sha256": artifact.metadata.get("patched_projection_sha256"),
        "readback_projection_sha256": projection_fingerprint,
        "raw_base_projection_sha256": artifact.metadata.get("raw_base_projection_sha256"),
        "expected_raw_patched_projection_sha256": expected_raw_patched_hash,
        "readback_raw_projection_sha256": readback_raw_hash,
        "raw_projection_match": raw_projection_match,
        "protected_sections_sha256": protected_hash,
        "draft_synced": verified,
        "unspecified_fields_unchanged": verified,
        "credentials_remained_in_browser": True,
        "write_performed_by_mcp": False,
        "browser_write_observed": verified,
    }


@mcp.tool()
def workflow_generate_dsl(
    instruction: str,
    mode: str = "workflow",
    ideal_output: str = "",
    app_id: str | None = None,
    app_name: str | None = None,
    description: str = "",
    model_provider: str | None = None,
    model_name: str | None = None,
    model_mode: str | None = None,
    completion_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a complete DSL preview from natural language.

    Set app_id to refine an existing draft. This tool never writes to Dify.
    The returned preview_id pins the exact generated graph for a later apply call.
    """

    instruction = instruction.strip()
    if not instruction:
        raise ValueError("instruction is required")
    if len(instruction) > 10_000 or len(ideal_output) > 10_000:
        raise ValueError("instruction and ideal_output must each be at most 10000 characters")
    runtime = _runtime()
    model_config = runtime.settings.resolve_generator_model(model_provider, model_name, model_mode)
    if completion_params:
        model_config["completion_params"] = completion_params

    app: dict[str, Any] = {}
    draft: dict[str, Any] = {}
    current_graph = None
    base_hash = None
    requested_mode = mode
    if app_id:
        app, draft = _existing_context(app_id)
        requested_mode = str(app["mode"])
        current_graph = draft["graph"]
        base_hash = str(draft["hash"])
    elif requested_mode not in GRAPH_MODES | {"auto"}:
        raise ValueError("mode must be workflow, advanced-chat, or auto")

    response = runtime.client.generate_graph(
        instruction=instruction,
        mode=requested_mode,
        model_config=model_config,
        ideal_output=ideal_output,
        current_graph=current_graph,
    )
    failure = _generator_failure(response)
    if failure:
        return failure
    generated_mode = response.get("mode") or (app.get("mode") if app else requested_mode)
    if generated_mode not in GRAPH_MODES:
        raise ValueError(f"Dify generator returned unsupported mode {generated_mode!r}")

    resolved_name = (
        app_name
        or (str(app.get("name")) if app.get("name") else None)
        or str(response.get("app_name") or "Generated Workflow")
    )
    document = compose_document(
        graph=response["graph"],
        mode=str(generated_mode),
        app_name=resolved_name,
        description=description or str(app.get("description") or response.get("message") or ""),
        icon=str(app.get("icon") or response.get("icon") or "🧩"),
        icon_type=str(app.get("icon_type") or "emoji"),
        icon_background=str(app.get("icon_background") or "#E4FBCC"),
        use_icon_as_answer_icon=bool(app.get("use_icon_as_answer_icon", False)),
        features=draft.get("features") if isinstance(draft.get("features"), dict) else {},
        environment_variables=draft.get("environment_variables")
        if isinstance(draft.get("environment_variables"), list)
        else [],
        conversation_variables=draft.get("conversation_variables")
        if isinstance(draft.get("conversation_variables"), list)
        else [],
        dependencies=[],
    )
    portable_document = make_portable_document(document)
    validation = validate_document(portable_document).as_dict()
    artifact = runtime.previews.put(
        document,
        target_app_id=app_id,
        base_hash=base_hash,
        metadata={"kind": "generated"},
    )
    return {
        "status": "preview_created",
        "preview_id": artifact.preview_id,
        "target_app_id": app_id,
        "base_hash": base_hash,
        "target": {"dify": "1.16.1", "dsl": "0.7.0", "graphon": "0.6.0"},
        "graph": graph_summary(portable_document),
        "validation": validation,
        "unresolved_dependencies": (
            ["Verify generated model/tool providers in the target workspace"]
            if not portable_document.get("dependencies")
            else []
        ),
        "dsl_yaml": dump_yaml(portable_document),
        "write_performed": False,
    }


@mcp.tool()
def workflow_create_draft_apply(preview_id: str) -> dict[str, Any]:
    """Create a new Dify app from a generated preview. This is an explicit write."""

    runtime = _runtime()
    artifact = runtime.previews.get(preview_id)
    if artifact.target_app_id is not None:
        raise ValueError("This preview targets an existing app; use workflow_update_draft_apply")
    portable_document = make_portable_document(artifact.document)
    report = validate_document(portable_document)
    if report.errors:
        return {"status": "rejected", "validation": report.as_dict(), "write_performed": False}
    response = runtime.client.import_dsl(dump_yaml(portable_document))
    http_status = int(response.get("_http_status", 200))
    if http_status == 202 or str(response.get("status", "")).lower() == "pending":
        return {
            "status": "pending_confirmation",
            "import_id": response.get("id") or response.get("import_id"),
            "draft_synced": False,
            "write_performed": True,
            "message": "Dify requires a separate version-confirmation operation; it was not auto-confirmed.",
        }
    app_id = response.get("app_id")
    if not isinstance(app_id, str) or not app_id:
        return {
            "status": "import_returned_without_app_id",
            "response": _without_http_marker(response),
            "draft_synced": False,
            "write_performed": True,
        }
    draft = runtime.client.get_draft(app_id)
    readback_match = graph_fingerprint(draft.get("graph", {})) == graph_fingerprint(
        portable_document["workflow"]["graph"]
    )
    try:
        dependencies = _without_http_marker(runtime.client.check_dependencies(app_id))
    except DifyAPIError as exc:
        dependencies = {"check_error": str(exc)}
    if readback_match:
        runtime.previews.delete(preview_id)
    return {
        "status": "created" if readback_match else "created_readback_mismatch",
        "app_id": app_id,
        "draft_hash": draft.get("hash"),
        "draft_synced": readback_match,
        "publish_ready": "unknown",
        "runtime_ready": "unknown",
        "dependencies": dependencies,
        "write_performed": True,
    }


@mcp.tool()
def workflow_update_draft_apply(preview_id: str, app_id: str, expected_hash: str) -> dict[str, Any]:
    """Replace an existing draft graph using a pinned preview and matching draft hash."""

    runtime = _runtime()
    artifact = runtime.previews.get(preview_id)
    if artifact.metadata.get("kind") == "workflow_patch":
        raise ValueError("Patch previews must be applied with workflow_patch_draft_apply")
    if artifact.target_app_id != app_id:
        raise ValueError("preview_id was not generated for this app_id")
    if not artifact.base_hash or expected_hash != artifact.base_hash:
        raise ValueError("expected_hash must exactly match the base_hash returned with the preview")
    current = runtime.client.get_draft(app_id)
    if current.get("hash") != expected_hash:
        return {
            "status": "conflict",
            "message": "The Dify draft changed after preview generation; generate a new preview.",
            "expected_hash": expected_hash,
            "current_hash": current.get("hash"),
            "draft_synced": False,
            "write_performed": False,
        }
    portable_document = make_portable_document(artifact.document)
    report = validate_document(portable_document)
    if report.errors:
        return {"status": "rejected", "validation": report.as_dict(), "write_performed": False}
    workflow = artifact.document["workflow"]
    response = runtime.client.sync_draft(
        app_id,
        graph=workflow["graph"],
        features=workflow.get("features", {}),
        environment_variables=workflow.get("environment_variables", []),
        conversation_variables=workflow.get("conversation_variables", []),
        expected_hash=expected_hash,
    )
    readback = runtime.client.get_draft(app_id)
    readback_match = graph_fingerprint(readback.get("graph", {})) == graph_fingerprint(workflow["graph"])
    if readback_match:
        runtime.previews.delete(preview_id)
    return {
        "status": "updated" if readback_match else "updated_readback_mismatch",
        "app_id": app_id,
        "previous_hash": expected_hash,
        "draft_hash": readback.get("hash") or response.get("hash"),
        "draft_synced": readback_match,
        "publish_ready": "unknown",
        "runtime_ready": "unknown",
        "write_performed": True,
    }


@mcp.tool()
def workflow_patch_draft_apply(preview_id: str, app_id: str, expected_hash: str) -> dict[str, Any]:
    """Apply a pinned deterministic patch when the complete base snapshot still matches."""

    runtime = _runtime()
    artifact = runtime.previews.get(preview_id)
    if artifact.metadata.get("kind") != "workflow_patch":
        raise ValueError("preview_id is not a workflow patch preview")
    if artifact.target_app_id != app_id:
        raise ValueError("preview_id was not generated for this app_id")
    if not artifact.base_hash or expected_hash != artifact.base_hash:
        raise ValueError("expected_hash must exactly match the base_hash returned with the preview")

    current = runtime.client.get_draft(app_id)
    if current.get("hash") != expected_hash:
        return {
            "status": "conflict",
            "message": "The Dify draft changed after patch preview generation; create a new preview.",
            "expected_hash": expected_hash,
            "current_hash": current.get("hash"),
            "draft_synced": False,
            "unspecified_fields_unchanged": False,
            "write_performed": False,
        }

    base_workflow = artifact.metadata.get("base_workflow")
    operations = artifact.metadata.get("operations")
    if not isinstance(base_workflow, dict) or not isinstance(operations, list):
        raise ValueError("Patch preview metadata is incomplete")
    current_workflow = _draft_workflow_payload(current)
    base_fingerprint = workflow_fingerprint(base_workflow)
    if base_fingerprint != artifact.metadata.get("base_workflow_sha256"):
        raise ValueError("Patch preview base snapshot failed integrity verification")
    if workflow_fingerprint(current_workflow) != base_fingerprint:
        return {
            "status": "conflict",
            "message": "Draft content no longer matches the pinned base snapshot.",
            "expected_hash": expected_hash,
            "current_hash": current.get("hash"),
            "draft_synced": False,
            "unspecified_fields_unchanged": False,
            "write_performed": False,
        }

    replayed = apply_workflow_patch(base_workflow, operations)
    patched_workflow = artifact.document.get("workflow")
    if not isinstance(patched_workflow, dict):
        raise ValueError("Patch preview document is incomplete")
    patched_fingerprint = workflow_fingerprint(patched_workflow)
    if (
        patched_fingerprint != artifact.metadata.get("patched_workflow_sha256")
        or workflow_fingerprint(replayed.workflow) != patched_fingerprint
        or not replayed.round_trip_verified
    ):
        raise ValueError("Patch preview failed deterministic replay verification")

    report = validate_document(make_portable_document(artifact.document))
    if report.errors:
        return {
            "status": "rejected",
            "validation": report.as_dict(),
            "draft_synced": False,
            "unspecified_fields_unchanged": True,
            "write_performed": False,
        }

    response = runtime.client.sync_draft(
        app_id,
        graph=patched_workflow["graph"],
        features=patched_workflow["features"],
        environment_variables=patched_workflow["environment_variables"],
        conversation_variables=patched_workflow["conversation_variables"],
        expected_hash=expected_hash,
    )
    readback = runtime.client.get_draft(app_id)
    readback_workflow = _draft_workflow_payload(readback)
    readback_fingerprint = workflow_fingerprint(readback_workflow)
    readback_match = readback_fingerprint == patched_fingerprint
    if readback_match:
        runtime.previews.delete(preview_id)
    return {
        "status": "patched" if readback_match else "patched_readback_mismatch",
        "app_id": app_id,
        "previous_hash": expected_hash,
        "draft_hash": readback.get("hash") or response.get("hash"),
        "base_workflow_sha256": base_fingerprint,
        "patched_workflow_sha256": patched_fingerprint,
        "readback_workflow_sha256": readback_fingerprint,
        "changes": replayed.change_summary(),
        "draft_synced": readback_match,
        "unspecified_fields_unchanged": readback_match,
        "publish_ready": "unknown",
        "runtime_ready": "unknown",
        "write_performed": True,
    }


def main() -> None:
    try:
        mcp.run(transport="stdio")
    finally:
        if _runtime_instance is not None:
            _runtime_instance.client.close()


__all__ = ["main", "mcp"]
