"""Deterministic, reversible patches for Dify draft workflow payloads."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from .dsl import SENSITIVE_KEYS, is_safe_no_auth_authorization

ALLOWED_ROOTS = {"graph", "features", "environment_variables", "conversation_variables"}
SUPPORTED_OPERATIONS = {"add", "replace", "remove", "test"}
MAX_OPERATIONS = 100
MAX_PATCH_BYTES = 1_000_000
_INVALID_ESCAPE_RE = re.compile(r"~(?![01])")
_MISSING = object()


class WorkflowPatchError(ValueError):
    """Raised when a workflow patch is invalid or cannot be applied safely."""


@dataclass(frozen=True, slots=True)
class AppliedChange:
    op: str
    path: str
    resolved_path: str
    before_present: bool
    before: Any
    after_present: bool
    after: Any

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "op": self.op,
            "path": self.path,
            "resolved_path": self.resolved_path,
            "before_present": self.before_present,
            "after_present": self.after_present,
        }
        if self.before_present:
            result["before"] = _redact_for_output(self.before)
        if self.after_present:
            result["after"] = _redact_for_output(self.after)
        return result


@dataclass(frozen=True, slots=True)
class PatchResult:
    workflow: dict[str, Any]
    operations: list[dict[str, Any]]
    changes: list[AppliedChange]
    round_trip_verified: bool

    def change_summary(self) -> list[dict[str, Any]]:
        return [change.as_dict() for change in self.changes]


def workflow_fingerprint(workflow: dict[str, Any]) -> str:
    canonical = json.dumps(workflow, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def apply_workflow_patch(
    workflow: dict[str, Any], operations: list[dict[str, Any]]
) -> PatchResult:
    """Apply explicit JSON Pointer operations to a deep copy of a workflow payload.

    Paths are rooted at the workflow payload, for example
    ``/graph/nodes/@node-id/data/title``. A list segment beginning with ``@``
    selects the unique object whose ``id`` matches the suffix. Standard numeric
    indices and the final ``-`` append segment are also supported.
    """

    if not isinstance(workflow, dict):
        raise WorkflowPatchError("workflow must be an object")
    normalized = _normalize_operations(operations)
    working = copy.deepcopy(workflow)
    changes: list[AppliedChange] = []

    for operation in normalized:
        op = operation["op"]
        path = operation["path"]
        segments = _parse_pointer(path)
        _validate_allowed_path(segments, operation)
        parent, token, resolved_parent = _resolve_parent(working, segments, op=op)
        change = _apply_one(parent, token, resolved_parent, operation)
        if change is not None:
            changes.append(change)

    restored = copy.deepcopy(working)
    for change in reversed(changes):
        _reverse_change(restored, change)
    round_trip_verified = restored == workflow
    if not round_trip_verified:
        raise WorkflowPatchError("Patch round-trip verification failed")

    return PatchResult(
        workflow=working,
        operations=normalized,
        changes=changes,
        round_trip_verified=True,
    )


def _normalize_operations(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(operations, list) or not operations:
        raise WorkflowPatchError("operations must be a non-empty list")
    if len(operations) > MAX_OPERATIONS:
        raise WorkflowPatchError(f"operations must contain at most {MAX_OPERATIONS} items")
    try:
        size = len(json.dumps(operations, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise WorkflowPatchError(f"operations must be JSON serializable: {exc}") from exc
    if size > MAX_PATCH_BYTES:
        raise WorkflowPatchError(f"operations must be at most {MAX_PATCH_BYTES} UTF-8 bytes")

    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(operations):
        if not isinstance(raw, dict):
            raise WorkflowPatchError(f"operations[{index}] must be an object")
        extra = set(raw) - {"op", "path", "value"}
        if extra:
            raise WorkflowPatchError(
                f"operations[{index}] contains unsupported keys: {sorted(extra)!r}"
            )
        op = raw.get("op")
        path = raw.get("path")
        if op not in SUPPORTED_OPERATIONS:
            raise WorkflowPatchError(
                f"operations[{index}].op must be one of {sorted(SUPPORTED_OPERATIONS)!r}"
            )
        if not isinstance(path, str):
            raise WorkflowPatchError(f"operations[{index}].path must be a string")
        requires_value = op in {"add", "replace", "test"}
        if requires_value and "value" not in raw:
            raise WorkflowPatchError(f"operations[{index}] with op {op!r} requires value")
        if not requires_value and "value" in raw:
            raise WorkflowPatchError(f"operations[{index}] with op {op!r} must not include value")
        item = {"op": op, "path": path}
        if requires_value:
            item["value"] = copy.deepcopy(raw["value"])
            _reject_embedded_secrets(item["value"], f"operations[{index}].value")
        normalized.append(item)
    return normalized


def _parse_pointer(path: str) -> list[str]:
    if not path.startswith("/"):
        raise WorkflowPatchError("Patch path must be a non-root JSON Pointer beginning with '/'")
    if path == "/":
        raise WorkflowPatchError("Replacing the entire workflow payload is not allowed")
    segments: list[str] = []
    for raw in path[1:].split("/"):
        if _INVALID_ESCAPE_RE.search(raw):
            raise WorkflowPatchError(f"Invalid JSON Pointer escape in path {path!r}")
        segments.append(raw.replace("~1", "/").replace("~0", "~"))
    return segments


def _validate_allowed_path(segments: list[str], operation: dict[str, Any]) -> None:
    root = segments[0] if segments else ""
    if root not in ALLOWED_ROOTS:
        raise WorkflowPatchError(
            f"Patch root {root!r} is not allowed; use one of {sorted(ALLOWED_ROOTS)!r}"
        )
    for segment in segments:
        normalized = segment[1:] if segment.startswith("@") else segment
        if normalized.lower() in SENSITIVE_KEYS:
            raise WorkflowPatchError(f"Patching sensitive field {normalized!r} is not allowed")
    if root == "environment_variables" and segments[-1].lower() == "value":
        raise WorkflowPatchError("Environment-variable values cannot be patched")
    if root == "environment_variables" and "value" in operation:
        _reject_environment_variable_values(operation["value"])
    if operation["op"] in {"remove", "replace"} and len(segments) == 1:
        raise WorkflowPatchError(f"Replacing or removing the entire {root!r} section is not allowed")


def _resolve_parent(
    document: Any, segments: list[str], *, op: str
) -> tuple[Any, str, list[str]]:
    current = document
    resolved: list[str] = []
    for segment in segments[:-1]:
        current, actual = _resolve_child(current, segment)
        resolved.append(actual)
    token = segments[-1]
    if token == "-" and op != "add":
        raise WorkflowPatchError("The '-' list segment is only valid for add")
    return current, token, resolved


def _resolve_child(container: Any, token: str) -> tuple[Any, str]:
    if isinstance(container, dict):
        if token not in container:
            raise WorkflowPatchError(f"Path segment {token!r} does not exist")
        return container[token], token
    if isinstance(container, list):
        index = _list_index(container, token, allow_append=False)
        return container[index], str(index)
    raise WorkflowPatchError(f"Cannot traverse into {type(container).__name__}")


def _list_index(values: list[Any], token: str, *, allow_append: bool) -> int:
    if token == "-":
        if allow_append:
            return len(values)
        raise WorkflowPatchError("The '-' list segment is only valid for a final add")
    if token.startswith("@"):
        identifier = token[1:]
        matches = [
            index
            for index, item in enumerate(values)
            if isinstance(item, dict) and str(item.get("id")) == identifier
        ]
        if len(matches) != 1:
            raise WorkflowPatchError(
                f"List id selector {token!r} matched {len(matches)} items; exactly one is required"
            )
        return matches[0]
    if not token.isdigit():
        raise WorkflowPatchError(f"List segment {token!r} must be an index, '-', or '@<id>'")
    index = int(token)
    upper = len(values) if allow_append else len(values) - 1
    if index < 0 or index > upper:
        raise WorkflowPatchError(f"List index {index} is out of range")
    return index


def _pointer(segments: list[str]) -> str:
    encoded = [segment.replace("~", "~0").replace("/", "~1") for segment in segments]
    return "/" + "/".join(encoded)


def _apply_one(
    parent: Any,
    token: str,
    resolved_parent: list[str],
    operation: dict[str, Any],
) -> AppliedChange | None:
    op = operation["op"]
    path = operation["path"]
    value = copy.deepcopy(operation.get("value"))

    if isinstance(parent, dict):
        exists = token in parent
        resolved_path = _pointer([*resolved_parent, token])
        before = copy.deepcopy(parent[token]) if exists else _MISSING
        if op == "test":
            if not exists or parent[token] != value:
                raise WorkflowPatchError(f"Test operation failed at {path!r}")
            return None
        if op == "add":
            if exists:
                raise WorkflowPatchError(f"Add target already exists at {path!r}; use replace")
            parent[token] = value
        elif op == "replace":
            if not exists:
                raise WorkflowPatchError(f"Replace target does not exist at {path!r}")
            parent[token] = value
        elif op == "remove":
            if not exists:
                raise WorkflowPatchError(f"Remove target does not exist at {path!r}")
            del parent[token]
        return AppliedChange(
            op=op,
            path=path,
            resolved_path=resolved_path,
            before_present=before is not _MISSING,
            before=None if before is _MISSING else before,
            after_present=op != "remove",
            after=None if op == "remove" else copy.deepcopy(parent[token]),
        )

    if isinstance(parent, list):
        index = _list_index(parent, token, allow_append=op == "add")
        resolved_path = _pointer([*resolved_parent, str(index)])
        before = copy.deepcopy(parent[index]) if index < len(parent) else _MISSING
        if op == "test":
            if before is _MISSING or parent[index] != value:
                raise WorkflowPatchError(f"Test operation failed at {path!r}")
            return None
        if op == "add":
            parent.insert(index, value)
        elif op == "replace":
            parent[index] = value
        elif op == "remove":
            parent.pop(index)
        return AppliedChange(
            op=op,
            path=path,
            resolved_path=resolved_path,
            before_present=before is not _MISSING,
            before=None if before is _MISSING else before,
            after_present=op != "remove",
            after=None if op == "remove" else copy.deepcopy(parent[index]),
        )

    raise WorkflowPatchError(f"Patch target parent at {path!r} is not a container")


def _reverse_change(document: dict[str, Any], change: AppliedChange) -> None:
    segments = _parse_pointer(change.resolved_path)
    parent, token, _ = _resolve_parent(document, segments, op="add")
    if isinstance(parent, dict):
        if change.op == "add":
            if token not in parent:
                raise WorkflowPatchError("Cannot reverse add; target is missing")
            del parent[token]
        elif change.op in {"replace", "remove"}:
            parent[token] = copy.deepcopy(change.before)
        return
    if isinstance(parent, list):
        index = _list_index(parent, token, allow_append=change.op == "remove")
        if change.op == "add":
            parent.pop(index)
        elif change.op == "replace":
            parent[index] = copy.deepcopy(change.before)
        elif change.op == "remove":
            parent.insert(index, copy.deepcopy(change.before))
        return
    raise WorkflowPatchError("Cannot reverse patch against a non-container")


def _reject_embedded_secrets(value: Any, location: str) -> None:
    if isinstance(value, dict):
        if value.get("value_type") == "secret" and "value" in value:
            raise WorkflowPatchError(f"Secret environment-variable values are not allowed at {location}")
        for key, child in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                if str(key).lower() == "authorization" and is_safe_no_auth_authorization(child):
                    continue
                raise WorkflowPatchError(f"Sensitive field {key!r} is not allowed at {location}")
            _reject_embedded_secrets(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_embedded_secrets(child, f"{location}[{index}]")


def _reject_environment_variable_values(value: Any) -> None:
    if isinstance(value, dict):
        if "value" in value:
            raise WorkflowPatchError("Environment-variable values cannot be patched")
        for child in value.values():
            _reject_environment_variable_values(child)
    elif isinstance(value, list):
        for child in value:
            _reject_environment_variable_values(child)


def _redact_for_output(value: Any) -> Any:
    result = copy.deepcopy(value)

    def scrub(item: Any) -> None:
        if isinstance(item, dict):
            if item.get("value_type") == "secret" and "value" in item:
                item["value"] = "<redacted>"
            for key in list(item):
                if str(key).lower() in SENSITIVE_KEYS:
                    if str(key).lower() == "authorization" and is_safe_no_auth_authorization(item[key]):
                        continue
                    item[key] = "<redacted>"
                else:
                    scrub(item[key])
        elif isinstance(item, list):
            for child in item:
                scrub(child)

    scrub(result)
    return result
