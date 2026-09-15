"""Credential-free bridge primitives for exact-origin browser transport.

The MCP process never receives cookies, browser storage, authorization headers,
or protected workflow variable values.  A Codex browser controller fetches and
writes the draft in the already signed-in page.  MCP only patches the safe
graph/features projection and compares opaque hashes for protected sections.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from .dsl import SENSITIVE_KEYS
from .patching import WorkflowPatchError, workflow_fingerprint

BRIDGE_ROOTS = {"graph", "features"}
BRIDGE_PROTOCOL_VERSION = "2"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def bridge_projection(graph: dict[str, Any], features: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(graph, dict):
        raise WorkflowPatchError("graph must be an object")
    if not isinstance(features, dict):
        raise WorkflowPatchError("features must be an object")
    _reject_sensitive_snapshot(graph, "graph")
    _reject_sensitive_snapshot(features, "features")
    return {"graph": copy.deepcopy(graph), "features": copy.deepcopy(features)}


def bridge_projection_fingerprint(projection: dict[str, Any]) -> str:
    return workflow_fingerprint(projection)


def validate_sha256(value: str, field_name: str) -> str:
    normalized = value.strip().lower() if isinstance(value, str) else ""
    if not _SHA256_RE.fullmatch(normalized):
        raise WorkflowPatchError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return normalized


def validate_protected_sections_sha256(value: str) -> str:
    return validate_sha256(value, "protected_sections_sha256")


def validate_bridge_operations(operations: list[dict[str, Any]]) -> None:
    if not isinstance(operations, list):
        raise WorkflowPatchError("operations must be a list")
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise WorkflowPatchError(f"operations[{index}] must be an object")
        path = operation.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            raise WorkflowPatchError(f"operations[{index}].path must be a JSON Pointer")
        root = path[1:].split("/", 1)[0].replace("~1", "/").replace("~0", "~")
        if root not in BRIDGE_ROOTS:
            raise WorkflowPatchError(
                "Browser bridge patches are limited to /graph and /features; "
                "protected variables remain inside the page context"
            )


def _reject_sensitive_snapshot(value: Any, location: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                raise WorkflowPatchError(
                    f"Browser bridge snapshot contains sensitive field {key!r} at {location}; "
                    "use direct MCP authentication instead"
                )
            _reject_sensitive_snapshot(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_snapshot(child, f"{location}[{index}]")
